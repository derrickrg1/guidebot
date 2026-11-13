"""Re-scores search hits with a cross-encoder on the GPU: it reads the question and each page together, which
ranks the page that actually states the rule above pages that only share its words. Local model, nothing leaves
this PC. Off (search order kept) when torch, the model or the GPU isn't there.
"""
import sys
import threading

MODEL = "BAAI/bge-reranker-v2-m3"
MAX_TOKENS = 1024   # a guide page is ~800 tokens; the model reads question + page together

_models: dict = {}
_lock = threading.Lock()   # one GPU, one batch at a time


def _load(name: str):
    if name not in _models:
        try:
            import torch
            from sentence_transformers import CrossEncoder
            device = "cuda" if torch.cuda.is_available() else "cpu"
            _models[name] = CrossEncoder(name, device=device, max_length=MAX_TOKENS,
                                         model_kwargs={"torch_dtype": torch.float16} if device == "cuda" else {})
        except Exception as e:
            sys.stderr.write(f"Reranker {name} unavailable ({type(e).__name__}: {e}); keeping search order\n")
            _models[name] = None
    return _models[name]


def order(question: str, texts: list[str], name: str = MODEL) -> list[int]:
    """Indexes into texts, best first. Search order when the reranker can't run."""
    with _lock:
        model = _load(name)
        if model is None or not texts:
            return list(range(len(texts)))
        scores = model.predict([(question, t) for t in texts], batch_size=8, show_progress_bar=False)
    return sorted(range(len(texts)), key=lambda i: -float(scores[i]))
