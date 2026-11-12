"""Local backend for ui/guidebot.html. Run: python -m guidebot.server, then open http://localhost:8600

Standard library only. Listens on 127.0.0.1, so nothing outside this PC can reach it.
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from guidebot import admin, chat, config, history

PORT = 8600
PAGE = config.ROOT / "ui" / "guidebot.html"
ADMIN_PAGE = config.ROOT / "ui" / "admin.html"


def _page(path) -> bytes:
    """A page with its shared scripts stamped by modified time (md.js?v=...), so a browser or Cloudflare never runs a
    stale copy against new page code. That mismatch broke the program list once and showed "Can't reach AskNick"."""
    html = path.read_text(encoding="utf-8")
    for name in ("md.js", "viewer.js"):
        v = int((path.parent / name).stat().st_mtime)
        html = re.sub(rf'src="(/?){re.escape(name)}"', rf'src="\g<1>{name}?v={v}"', html)
    return html.encode("utf-8")
# The page may also be opened straight from disk (file://), which sends Origin "null".
ALLOWED_ORIGINS = {"null", f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if ctype.startswith(("text/html", "text/javascript", "application/json")):
            self.send_header("Cache-Control", "no-cache")   # pages, scripts and API answers always come fresh
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _stream(self, events):
        """Newline-delimited JSON, one event per line, flushed as it comes."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True
        try:
            for ev in events:
                self.wfile.write((json.dumps(ev) + "\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            self.wfile.write((json.dumps({"error": str(e)}) + "\n").encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def _admin_get(self, path: str, query: dict):
        if not admin.allowed(self.headers):
            return self._json({"error": "admin only"}, 403)
        if path == "/admin":
            return self._send(200, _page(ADMIN_PAGE), "text/html; charset=utf-8")
        if path == "/admin/api/overview":
            return self._json({"programs": admin.overview()})
        if path == "/admin/api/chats":
            return self._json({"chats": history.list_chats(None, limit=500)})
        if path == "/admin/api/chat":
            c = history.get_chat(query.get("id", [""])[0])
            return self._json(c) if c else self._json({"error": "not found"}, 404)
        prog, file = query.get("program", [""])[0], query.get("file", [""])[0]
        if path == "/admin/api/doc":
            doc = admin.document(prog, file, fresh=query.get("fresh", [""])[0] == "1")
            return self._json(doc) if doc else self._json({"error": "not found"}, 404)
        if path == "/admin/page.png":
            png = admin.page_png(prog, file, int(query.get("page", ["1"])[0]))
            return self._send(200, png, "image/png") if png else self._json({"error": "not found"}, 404)
        self._json({"error": "not found"}, 404)

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        if path == "/admin" or path.startswith("/admin/"):
            self._admin_get(path, parse_qs(url.query))
        elif path in ("/", "/index.html"):
            self._send(200, _page(PAGE), "text/html; charset=utf-8")
        elif path in ("/md.js", "/viewer.js"):
            self._send(200, (PAGE.parent / path[1:]).read_bytes(), "text/javascript; charset=utf-8")
        elif path == "/logo.png":
            self._send(200, (PAGE.parent / "logo.png").read_bytes(), "image/png")
        elif path == "/api/chats":
            self._json({"chats": history.list_chats(history.user(self.headers))})
        elif path.startswith("/api/chats/"):
            c = history.get_chat(path.rsplit("/", 1)[1], history.user(self.headers))
            self._json(c) if c else self._json({"error": "not found"}, 404)
        elif path == "/api/programs":
            self._json({"programs": chat.programs_info(), "model": config.CHAT_MODEL})
        elif path.startswith("/pdf/"):
            pdf = chat.find_pdf(Path(path[5:]).name)
            if pdf:
                self._send(200, pdf.read_bytes(), "application/pdf")
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        # Pages here always send JSON. A plain cross-site form can't, so this stops another site posting
        # through a signed-in browser.
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json({"error": "json only"}, 415)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        if path.startswith("/admin/"):
            # The custom header can't be sent cross-site without a preflight this server never allows,
            # so another site can't make a signed-in browser approve something.
            if not admin.allowed(self.headers) or self.headers.get("X-Admin") != "1":
                return self._json({"error": "admin only"}, 403)
            prog, file = body.get("program") or "", body.get("file") or ""
            if path == "/admin/api/approve-doc":
                return self._json(admin.approve_document(prog, file, bool(body.get("fresh")), body.get("edits") or []))
            if path == "/admin/api/approve-rules":
                return self._json(admin.approve_rules(prog, file))
            if path == "/admin/api/index":
                return self._stream(_reindex())
            return self._json({"error": "not found"}, 404)

        if path == "/api/chat":
            question = (body.get("question") or "").strip()
            if not question:
                return self._json({"error": "empty question"}, 400)
            turns = [{"role": m["role"], "content": m["content"]} for m in body.get("history", [])
                     if m.get("role") in ("user", "assistant")]
            program = body.get("program") or ""
            events = chat.respond(question, turns, program, bool(body.get("mode_program")), bool(body.get("mode_scenario")))
            self._stream(_saving(events, str(body.get("chat_id") or "")[:64], history.user(self.headers), question,
                                 program, "scenario" if body.get("mode_scenario") else "guidelines"))
        elif path == "/api/chats/delete":
            self._json({"ok": history.delete_chat(str(body.get("id") or ""), history.user(self.headers))})
        else:
            self._json({"error": "not found"}, 404)


def _saving(events, chat_id, email, question, program, mode):
    """Passes the answer's events through, then saves the finished turn to that person's chat history."""
    text, sources = "", []
    for ev in events:
        if "text" in ev:
            text += ev["text"]
        if "replace" in ev:
            text = ev["replace"]
        if "sources" in ev:
            sources = ev["sources"]
        yield ev
    history.save_turn(chat_id, email, question, text, program, mode, sources)


def _reindex():
    """Updates the search index, streaming each line of its log as it happens."""
    import queue
    import threading

    from guidebot.ingest.indexer import sync_index
    lines, done = queue.Queue(), object()

    def work():
        try:
            sync_index(log=lambda m: lines.put(str(m)))
            chat.reset_retriever()
            lines.put(done)
        except Exception as e:
            lines.put(e)
    threading.Thread(target=work, daemon=True).start()
    log = []
    while (item := lines.get()) is not done:
        if isinstance(item, Exception):
            raise item
        log.append(item)
        yield {"status": item}
    log = [line for line in log if "exact copy of" not in line]   # the Ignite duplicates, skipped every run
    summary = "\n".join(f"- {line}" for line in log)
    yield {"replace": f"Index up to date.\n\n{summary}" if log else "Index up to date. Nothing had changed."}


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Guidebot running at http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
