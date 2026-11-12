"""Everything that talks to a model lives here: Claude (API) for chat when a key is set, Ollama for embeddings
and as the chat fallback. Callers pass Ollama-style messages; the model name picks the backend."""
import sys

import ollama

from guidebot import config

FALLBACK_BETA = "server-side-fallback-2026-07-01"   # a declined request reruns on another Claude model


def embed_docs(texts: list[str]) -> list[list[float]]:
    inputs = [config.EMBED_DOC_PREFIX + t for t in texts]
    return ollama.embed(model=config.EMBED_MODEL, input=inputs, options={"num_ctx": 8192})["embeddings"]


def embed_query(text: str) -> list[float]:
    return ollama.embed(model=config.EMBED_MODEL, input=[config.EMBED_QUERY_PREFIX + text])["embeddings"][0]


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
    return isinstance(e, (anthropic.APIConnectionError, anthropic.AuthenticationError, anthropic.RateLimitError,
                          anthropic.InternalServerError))


def _split(messages: list[dict]) -> tuple[str, list[dict]]:
    """Claude takes the system prompt separately and rejects empty turns."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
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
        if stream.get_final_message().stop_reason == "refusal":
            yield "\n\n*Claude declined to answer this one.*"


def _claude_json(system: str, user: str, schema, model: str):
    args = _claude_args(model, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    resp = _anthropic().beta.messages.parse(**args, output_format=schema)
    if resp.parsed_output is None:
        raise ValueError(f"Claude returned no {schema.__name__} (stop reason: {resp.stop_reason})")
    return resp.parsed_output


def _fallback_note(e: Exception):
    sys.stderr.write(f"Claude unavailable ({type(e).__name__}: {e}); answering with {config.LOCAL_MODEL}\n")


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
    kwargs = dict(model=model, messages=messages, stream=True, options=config.CHAT_OPTIONS)
    first, stream = _open_stream(kwargs)
    yield first["message"]["content"] or ""
    for part in stream:
        yield part["message"]["content"] or ""


def _ollama_json(system: str, user: str, schema, model: str, retries: int):
    from pydantic import ValidationError

    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        format=schema.model_json_schema(),
        options={**config.CHAT_OPTIONS, "temperature": 0},
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
    raise last


# ---------- what the rest of guidebot calls ----------

def stream_chat(messages: list[dict], model: str = config.CHAT_MODEL):
    if not _is_claude(model):
        yield from _ollama_stream(messages, model)
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
        yield from _ollama_stream(messages, config.LOCAL_MODEL)


def ask_json(system: str, user: str, schema, model: str = config.CHAT_MODEL, retries: int = 2):
    """Structured output: the reply must match the pydantic schema."""
    if not _is_claude(model):
        return _ollama_json(system, user, schema, model, retries)
    try:
        return _claude_json(system, user, schema, model)
    except Exception as e:
        if not _down(e):
            raise
        _fallback_note(e)
        return _ollama_json(system, user, schema, config.LOCAL_MODEL, retries)
