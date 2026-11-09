"""Everything that talks to Ollama lives here."""
import ollama

from guidebot import config


def embed_docs(texts: list[str]) -> list[list[float]]:
    inputs = [config.EMBED_DOC_PREFIX + t for t in texts]
    return ollama.embed(model=config.EMBED_MODEL, input=inputs, options={"num_ctx": 8192})["embeddings"]


def embed_query(text: str) -> list[float]:
    return ollama.embed(model=config.EMBED_MODEL, input=[config.EMBED_QUERY_PREFIX + text])["embeddings"][0]


def _open_stream(kwargs):
    try:
        stream = ollama.chat(**kwargs, think=False)   # skip qwen3 thinking for faster answers
        return next(stream), stream
    except (TypeError, ollama.ResponseError):
        stream = ollama.chat(**kwargs)                # model or client without thinking support
        return next(stream), stream


def stream_chat(messages: list[dict], model: str = config.CHAT_MODEL):
    kwargs = dict(model=model, messages=messages, stream=True, options=config.CHAT_OPTIONS)
    first, stream = _open_stream(kwargs)
    yield first["message"]["content"] or ""
    for part in stream:
        yield part["message"]["content"] or ""


def ask_json(system: str, user: str, schema, model: str = config.CHAT_MODEL, retries: int = 2):
    """Structured output: the reply must match the pydantic schema."""
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
            resp = ollama.chat(**kwargs, think=False)
        except (TypeError, ollama.ResponseError):
            resp = ollama.chat(**kwargs)
        try:
            return schema.model_validate_json(resp["message"]["content"])
        except ValidationError as e:
            last = e
    raise last
