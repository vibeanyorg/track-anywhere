from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue
from threading import Thread
from urllib.parse import parse_qs, urlsplit


class CallbackTimeout(TimeoutError):
    pass


class BrowserCallbackListener:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._queue: Queue[str] = Queue(maxsize=1)
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._expected_state: str | None = None
        self.redirect_uri = ""

    def __enter__(self) -> BrowserCallbackListener:
        queue = self._queue
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.split("?", 1)[0] != "/callback":
                    self.send_error(404)
                    return
                if owner._expected_state is not None:
                    states = parse_qs(urlsplit(self.path).query).get("state", [])
                    if states != [owner._expected_state]:
                        self.send_error(400, "Invalid OAuth state")
                        return
                callback_url = f"{self.server.callback_base}{self.path}"  # type: ignore[attr-defined]
                try:
                    queue.put_nowait(callback_url)
                except Full:
                    self.send_error(409, "OAuth callback already received")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'",
                )
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(
                    b"<!doctype html><title>Track Anywhere CLI</title><h1>Authorized</h1><p>You can return to the CLI.</p>"
                )

            def log_message(self, _format: str, *_args) -> None:
                return

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        port = self._server.server_address[1]
        self.redirect_uri = f"http://{self._host}:{port}/callback"
        self._server.callback_base = f"http://{self._host}:{port}"  # type: ignore[attr-defined]
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def expect_state(self, state: str) -> None:
        if not state:
            raise ValueError("expected OAuth state must not be empty")
        self._expected_state = state

    def wait_for_callback(self, timeout_seconds: int = 300) -> str:
        try:
            return self._queue.get(timeout=timeout_seconds)
        except Empty as exc:
            raise CallbackTimeout("timed out waiting for browser callback") from exc

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
