"""Photos and files sent with a question (the composer's + menu, 2026-11-15).

Each attachment becomes text before anything else happens, and that text rides along with the question, so the
rest of AskNick (routing, search, the deal reader, the matrix lookup) works on it unchanged:
  * PDFs with a text layer are read here in Python (pypdfium2), no model involved.
  * Photos and scanned PDFs are transcribed by a model (llm.transcribe: Claude, then the local model).
    The model is told to copy the text out exactly, never to summarize or interpret it.
"""
import base64
import io

from guidebot import llm, mismo
from guidebot.extract_pdf import PDFIUM_LOCK

IMAGES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
PDF = "application/pdf"
XML = "application/xml"   # a MISMO loan file (mismo.py): read in Python, only the deal facts come out
MAX_FILES = 5
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 30          # text pages read from one PDF
MAX_SCAN_PAGES = 8      # pages sent for transcription when a PDF has no text layer
MAX_CHARS = 40_000      # per attachment, so one long PDF can't crowd out the guidelines
MARK = "\n\n[Attachment: "   # the page hides everything from here on in the question bubble


class AttachmentError(ValueError):
    pass


def decode(items: list) -> list[dict]:
    """The page's [{name, type, data (base64)}] checked and decoded. Raises AttachmentError with a plain reason."""
    if len(items) > MAX_FILES:
        raise AttachmentError(f"Up to {MAX_FILES} attachments at a time.")
    out = []
    for it in items:
        name = str(it.get("name") or "attachment")[:120].replace("]", ")")
        kind = str(it.get("type") or "")
        if kind in ("text/xml", XML) or name.lower().endswith(".xml"):
            kind = XML
        if kind not in IMAGES and kind not in (PDF, XML):
            raise AttachmentError(f"{name}: only photos (JPEG, PNG, WebP, GIF), PDFs and MISMO loan files (.xml) can be attached.")
        try:
            data = base64.b64decode(str(it.get("data") or ""), validate=True)
        except ValueError:
            raise AttachmentError(f"{name}: the file didn't come through. Try attaching it again.")
        if not data:
            raise AttachmentError(f"{name} is empty.")
        if len(data) > MAX_BYTES:
            raise AttachmentError(f"{name} is over {MAX_BYTES // (1024 * 1024)} MB.")
        out.append({"name": name, "type": kind, "data": data})
    return out


def _pdf_pages(data: bytes, limit: int) -> tuple[list[str], int]:
    import pypdfium2 as pdfium
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            n = len(pdf)
            texts = []
            for i in range(min(n, limit)):
                page = pdf[i]
                tp = page.get_textpage()
                texts.append(tp.get_text_range())
                tp.close()
                page.close()
            return texts, n
        finally:
            pdf.close()


def _pdf_images(data: bytes, limit: int) -> list[tuple[str, bytes]]:
    """Page images of a scanned PDF, for the local model (it can't take a PDF itself)."""
    import pypdfium2 as pdfium
    out = []
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            for i in range(min(len(pdf), limit)):
                buf = io.BytesIO()
                pdf[i].render(scale=1.6).to_pil().convert("RGB").save(buf, "JPEG", quality=85)
                out.append(("image/jpeg", buf.getvalue()))
        finally:
            pdf.close()
    return out


def read(att: dict) -> str:
    """One attachment's text."""
    if att["type"] == XML:
        try:
            return mismo.read(att["data"])
        except mismo.NotMismo as e:
            raise AttachmentError(f"{att['name']} isn't a MISMO loan file ({e}).")
    if att["type"] == PDF:
        try:
            texts, n = _pdf_pages(att["data"], MAX_PAGES)
        except Exception:
            raise AttachmentError(f"{att['name']} couldn't be opened as a PDF.")
        words = sum(len(t.split()) for t in texts)
        if words >= 25 * len(texts):   # a real text layer: read it as is
            body = "\n\n".join(f"(page {i + 1})\n{t.strip()}" for i, t in enumerate(texts) if t.strip())
            if n > MAX_PAGES:
                body += f"\n\n(Only the first {MAX_PAGES} of {n} pages were read.)"
            return body[:MAX_CHARS]
        scanned = att["data"]
        text = llm.transcribe([(PDF, scanned)], lambda: _pdf_images(scanned, MAX_SCAN_PAGES))
        if n > MAX_SCAN_PAGES:
            text += f"\n\n(A scan: only the first {MAX_SCAN_PAGES} of {n} pages were read.)"
        return text[:MAX_CHARS]
    return llm.transcribe([(att["type"], att["data"])])[:MAX_CHARS]


def block(atts: list[dict]) -> str:
    """The text appended to the question: each attachment's name, then what it says."""
    return "".join(f"{MARK}{a['name']}]\n{read(a).strip() or '(no text found)'}" for a in atts)
