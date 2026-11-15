"""Attachments from the + menu: decoded, read to text, and appended to the question."""
import base64

import pytest

from guidebot import attachments, llm, server


def _pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a real text layer."""
    text = " ".join(f"({l}) Tj 0 -16 Td" for l in lines)
    stream = f"BT /F1 12 Tf 72 720 Td {text} ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def _item(name, kind, data):
    return {"name": name, "type": kind, "data": base64.b64encode(data).decode()}


def test_decode_checks_type_size_and_count():
    with pytest.raises(attachments.AttachmentError, match="only photos"):
        attachments.decode([_item("a.docx", "application/msword", b"x")])
    with pytest.raises(attachments.AttachmentError, match="Up to 5"):
        attachments.decode([_item("a.png", "image/png", b"x")] * 6)
    with pytest.raises(attachments.AttachmentError, match="over 10 MB"):
        attachments.decode([_item("a.png", "image/png", b"x" * (attachments.MAX_BYTES + 1))])
    with pytest.raises(attachments.AttachmentError, match="didn't come through"):
        attachments.decode([{"name": "a.png", "type": "image/png", "data": "not base64!!"}])
    assert attachments.decode([_item("a]b.png", "image/png", b"x")])[0]["name"] == "a)b.png"


def test_text_pdf_is_read_in_python(monkeypatch):
    monkeypatch.setattr(llm, "transcribe", lambda *a, **k: pytest.fail("a text PDF needs no model"))
    words = "Borrower credit score 720 loan amount 650000 purchase price 812500 primary residence single family"
    pdf = _pdf([words, words, words])
    att = attachments.decode([_item("1003.pdf", "application/pdf", pdf)])
    block = attachments.block(att)
    assert block.startswith(attachments.MARK + "1003.pdf]\n(page 1)")
    assert "credit score 720" in block


def test_photo_and_scan_go_to_the_model(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "transcribe", lambda files, local_images=None: seen.append(files[0][0]) or "FICO 700")
    atts = attachments.decode([_item("photo.jpg", "image/jpeg", b"\xff\xd8jpeg"),
                               _item("scan.pdf", "application/pdf", _pdf(["x"]))])   # too few words: a scan
    block = attachments.block(atts)
    assert seen == ["image/jpeg", "application/pdf"]
    assert block.count("FICO 700") == 2 and "[Attachment: scan.pdf]" in block


def test_server_appends_text_and_reports_it(monkeypatch):
    monkeypatch.setattr(attachments, "read", lambda a: "Loan amount $650,000")
    atts = [{"name": "a.jpg", "type": "image/jpeg", "data": b"x"}]
    asked = []
    events = list(server._with_attachments(atts, "Is this eligible?", lambda q: asked.append(q) or iter([{"text": "ok"}])))
    full = "Is this eligible?" + attachments.MARK + "a.jpg]\nLoan amount $650,000"
    assert events[1] == {"asked": full} and asked == [full] and events[-1] == {"text": "ok"}


def test_unreadable_attachment_is_an_error_not_an_answer(monkeypatch):
    def broken(a):
        raise attachments.AttachmentError("a.pdf couldn't be opened as a PDF.")
    monkeypatch.setattr(attachments, "read", broken)
    events = list(server._with_attachments([{"name": "a.pdf", "type": "application/pdf", "data": b"x"}], "q",
                                           lambda q: pytest.fail("nothing to answer")))
    assert events[-1] == {"error": "a.pdf couldn't be opened as a PDF."}
