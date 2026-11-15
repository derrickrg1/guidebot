"""Gemini fallback (2026-11-14): when Claude can't answer (out of credits, unreachable), answers go to Google's Gemini
API before the local model. Same system prompt and task Claude gets. Same pattern as AppraisAI's gemini.py.

Key GEMINI_API_KEY in .env, model GUIDEBOT_GEMINI_MODEL (default gemini-3.8-flash, picked by a race 2026-11-14). Off: GUIDEBOT_GEMINI=0.
Gemini only gets real borrower data if the key's Google project has billing linked (paid tier): on the free tier
Google may use prompts to improve its models. Sandbox phase: no borrower data goes in AskNick anyway.
"""
from guidebot import config, usage


def available() -> bool:
    return config.GEMINI_FALLBACK and bool(config.GEMINI_KEY)


_clients = {}


def _client():
    """One client per key, kept: a client nothing holds is closed before its request goes out."""
    if config.GEMINI_KEY not in _clients:
        from google import genai
        from google.genai import types
        # v1beta: preview models (gemini-3.1-pro-preview) aren't on v1. Retries: 3.8 Flash answered 503 "high demand"
        # on 2026-11-14 and the answer fell all the way to gemma; a few retries (about 2+4+8 s) usually get through.
        retry = types.HttpRetryOptions(attempts=4, initial_delay=2, max_delay=10,
                                       http_status_codes=[408, 429, 500, 502, 503, 504])
        _clients[config.GEMINI_KEY] = genai.Client(api_key=config.GEMINI_KEY, http_options=types.HttpOptions(
            api_version="v1beta", retry_options=retry))
    return _clients[config.GEMINI_KEY]


def _split(messages: list[dict]) -> tuple[str, list]:
    """Ollama-style messages to Gemini's system instruction and turns ("assistant" is "model" there)."""
    from google.genai import types
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
             for m in messages if m["role"] in ("user", "assistant") and (m["content"] or "").strip()]
    return system, turns


def _spent(meta) -> dict:
    """Token counts in usage.py's shape. Thinking tokens bill as output."""
    if meta is None:
        return usage.cost({"in": 0, "cached": 0, "wrote": 0, "out": 0}, config.GEMINI_MODEL)
    cached = getattr(meta, "cached_content_token_count", None) or 0
    t = {"in": (meta.prompt_token_count or 0) - cached, "cached": cached, "wrote": 0,
         "out": (meta.candidates_token_count or 0) + (getattr(meta, "thoughts_token_count", None) or 0)}
    return usage.cost(t, config.GEMINI_MODEL)


def stream(messages: list[dict], max_tokens: int = 16000):
    """Yields the answer's text chunks; the generator's return value is what it cost (never mixed into the text)."""
    from google.genai import types
    system, turns = _split(messages)
    meta = None
    for chunk in _client().models.generate_content_stream(
            model=config.GEMINI_MODEL, contents=turns,
            config=types.GenerateContentConfig(system_instruction=system or None, max_output_tokens=max_tokens)):
        meta = chunk.usage_metadata or meta
        if chunk.text:
            yield chunk.text
    return _spent(meta)


def transcribe(prompt: str, files: list[tuple[str, bytes]]) -> tuple[str, dict]:
    """(text, spent) for photos or a PDF sent inline with the prompt (llm.transcribe)."""
    from google.genai import types
    parts = [types.Part.from_bytes(data=data, mime_type=mime) for mime, data in files]
    resp = _client().models.generate_content(model=config.GEMINI_MODEL, contents=[*parts, prompt],
                                             config=types.GenerateContentConfig(max_output_tokens=8000))
    return resp.text or "", _spent(resp.usage_metadata)


def ask_json(system: str, user: str, schema):
    """(parsed, spent): structured output matching the pydantic schema."""
    from google.genai import types
    resp = _client().models.generate_content(
        model=config.GEMINI_MODEL, contents=[user],
        config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                           response_json_schema=schema.model_json_schema(), max_output_tokens=16000))
    if not resp.text:
        why = resp.candidates[0].finish_reason if resp.candidates else resp.prompt_feedback
        raise RuntimeError(f"Gemini returned no {schema.__name__} ({why})")
    return schema.model_validate_json(resp.text), _spent(resp.usage_metadata)
