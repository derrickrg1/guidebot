"""Everything that talks to a model lives here: Claude (API) for chat when a key is set, Ollama for embeddings
and as the chat fallback. Callers pass Ollama-style messages; the model name picks the backend."""
import sys

import ollama

from guidebot import claude_status, config, gemini, usage

FALLBACK_BETA = "server-side-fallback-2026-07-01"   # a declined request reruns on another Claude model
FALLBACK_SHOWN = "*Answered by the backup model. Double-check this one.*\n\n"


def embed_docs(texts: list[str]) -> list[list[float]]:
    inputs = [config.EMBED_DOC_PREFIX + t for t in texts]
    return ollama.embed(model=config.EMBED_MODEL, input=inputs, options={"num_ctx": 8192})["embeddings"]


def embed_query(text: str) -> list[float]:
    # On the CPU (num_gpu 0): loading it on the GPU pushed the local model out, and the local model then reloaded
    # for every question, 15-20 s each (2026-11-13). One short query embeds in milliseconds on the CPU.
    return ollama.embed(model=config.EMBED_MODEL, input=[config.EMBED_QUERY_PREFIX + text],
                        options={"num_gpu": 0})["embeddings"][0]


# ---------- Claude ----------

_client = None


def _is_claude(model: str) -> bool:
    return model.startswith("claude")


def _anthropic():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def _down(e: Exception) -> bool:
    """Errors where answering from the local model beats no answer: no network, bad key, rate limit, outage."""
    import anthropic
    if isinstance(e, anthropic.BadRequestError) and "credit balance" in str(e):
        return True   # out of API credit (2026-11-12): every answer errored instead of falling back
    return isinstance(e, (anthropic.APIConnectionError, anthropic.AuthenticationError, anthropic.RateLimitError,
                          anthropic.InternalServerError, anthropic.PermissionDeniedError))


def _split(messages: list[dict]) -> tuple[str | list[dict], list[dict]]:
    """Claude takes the system prompt separately and rejects empty turns. A system message marked "cache": True
    gets its own cache point, so a big fixed block (every program sheet) is reused across different questions."""
    sys_msgs = [m for m in messages if m["role"] == "system"]
    if any(m.get("cache") for m in sys_msgs):
        system = [{"type": "text", "text": m["content"], **({"cache_control": {"type": "ephemeral"}} if m.get("cache") else {})}
                  for m in sys_msgs]
    else:
        system = "\n\n".join(m["content"] for m in sys_msgs)
    rest = [{"role": m["role"], "content": m["content"]} for m in messages
            if m["role"] in ("user", "assistant") and (m["content"] or "").strip()]
    return system, rest


def _claude_args(model: str, messages: list[dict]) -> dict:
    system, rest = _split(messages)
    return dict(model=model, max_tokens=16000, system=system, messages=rest,
                output_config={"effort": config.CLAUDE_EFFORT},
                cache_control={"type": "ephemeral"},   # the guidelines repeat on every question about a program
                betas=[FALLBACK_BETA], fallbacks="default")


def _claude_stream(messages: list[dict], model: str):
    with _anthropic().beta.messages.stream(**_claude_args(model, messages)) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
        usage.add("claude", usage.cost(usage.claude_tokens(final.usage), final.model))
        if final.stop_reason == "refusal":
            yield "\n\n*Claude declined to answer this one.*"


def _claude_json(system: str, user: str, schema, model: str):
    args = _claude_args(model, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    resp = _anthropic().beta.messages.parse(**args, output_format=schema)
    usage.add("claude", usage.cost(usage.claude_tokens(resp.usage), resp.model))
    if resp.parsed_output is None:
        raise ValueError(f"Claude returned no {schema.__name__} (stop reason: {resp.stop_reason})")
    return resp.parsed_output


def _fallback_note(e: Exception):
    sys.stderr.write(f"Claude unavailable ({type(e).__name__}: {e}); answering with the backup\n")
    claude_status.stale()   # the next status check sees it, and later answers skip Claude


# ---------- Ollama ----------

def _think(model: str):
    """Thinking off for speed. gpt-oss can't turn it off, only down to "low"."""
    return "low" if model.startswith("gpt-oss") else False


def _open_stream(kwargs):
    try:
        stream = ollama.chat(**kwargs, think=_think(kwargs["model"]))   # skip thinking for faster answers
        return next(stream), stream
    except (TypeError, ollama.ResponseError):
        stream = ollama.chat(**kwargs)                # model or client without thinking support
        return next(stream), stream


def _ollama_stream(messages: list[dict], model: str):
    messages = [{"role": m["role"], "content": m["content"]} for m in messages]   # drop Claude-only keys ("cache")
    kwargs = dict(model=model, messages=messages, stream=True,
                  options={**config.CHAT_OPTIONS, "num_predict": config.LOCAL_MAX_ANSWER})
    first, stream = _open_stream(kwargs)
    yield first["message"]["content"] or ""
    last = first
    for part in stream:
        last = part
        yield part["message"]["content"] or ""
    if last.get("done_reason") == "length":
        yield "\n\n*This answer was cut off. Ask again or narrow the question.*"


def _ollama_json(system: str, user: str, schema, model: str, retries: int):
    from pydantic import ValidationError

    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        format=schema.model_json_schema(),
        options={**config.CHAT_OPTIONS, "temperature": 0, "num_predict": config.LOCAL_MAX_JSON},
    )
    last = None
    for _ in range(retries):
        try:
            resp = ollama.chat(**kwargs, think=_think(model))
        except (TypeError, ollama.ResponseError):
            resp = ollama.chat(**kwargs)
        try:
            return schema.model_validate_json(resp["message"]["content"])
        except ValidationError as e:
            last = e
        if resp.get("done_reason") == "length":   # a loop: at temperature 0 a retry loops the same way
            sys.stderr.write(f"{model} ran to the {config.LOCAL_MAX_JSON} token cap on {schema.__name__}\n")
            break
    raise last


# ---------- what the rest of guidebot calls ----------
# Order: Claude, then Gemini (gemini.py), then the local model. While the status check (claude_status, at most 5
# minutes old) says Claude can't answer, answers start at Gemini without a failed Claude call first.

def _backup_stream(messages: list[dict]):
    """Gemini if it's set up and answers, else the local model. A Gemini failure before its first chunk falls through."""
    yield FALLBACK_SHOWN   # chat.respond turns this into {"fallback": True}; the sidebar's model line says who answered
    if gemini.available():
        chunks = gemini.stream(messages)
        try:
            first = next(chunks)   # the request goes out here: a failure now falls through to the local model
        except StopIteration as done:   # an empty answer
            usage.add("gemini", done.value)
            return
        except Exception as g:
            sys.stderr.write(f"Gemini failed too ({type(g).__name__}: {g}); answering with {config.LOCAL_MODEL}\n")
        else:
            yield first
            spent = yield from chunks
            usage.add("gemini", spent)
            return
    usage.add("local")
    yield from _ollama_stream(messages, config.LOCAL_MODEL)


def stream_chat(messages: list[dict], model: str = config.CHAT_MODEL):
    if not _is_claude(model):
        usage.add("local")
        yield from _ollama_stream(messages, model)
        return
    if claude_status.out():
        yield from _backup_stream(messages)
        return
    started = False
    try:
        for chunk in _claude_stream(messages, model):
            started = True
            yield chunk
    except Exception as e:
        if started or not _down(e):
            raise
        _fallback_note(e)
        yield from _backup_stream(messages)


def _backup_json(system: str, user: str, schema, retries: int):
    if gemini.available():
        try:
            parsed, spent = gemini.ask_json(system, user, schema)
            usage.add("gemini", spent)
            return parsed
        except Exception as g:
            sys.stderr.write(f"Gemini failed too on {schema.__name__} ({type(g).__name__}: {g})\n")
    usage.add("local")
    return _ollama_json(system, user, schema, config.LOCAL_MODEL, retries)


def ask_json(system: str, user: str, schema, model: str = config.CHAT_MODEL, retries: int = 2):
    """Structured output: the reply must match the pydantic schema."""
    if not _is_claude(model):
        usage.add("local")
        return _ollama_json(system, user, schema, model, retries)
    if claude_status.out():
        return _backup_json(system, user, schema, retries)
    try:
        return _claude_json(system, user, schema, model)
    except Exception as e:
        if not _down(e):
            raise
        _fallback_note(e)
        return _backup_json(system, user, schema, retries)
