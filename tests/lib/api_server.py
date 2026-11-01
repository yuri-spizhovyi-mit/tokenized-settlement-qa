"""Run a FastAPI app on a real port in a background thread (uvicorn).

Concurrency tests need real HTTP: FastAPI runs sync endpoints on a thread pool, so
ten concurrent requests really do race into the service and the database.
"""

from __future__ import annotations

import socket
import threading
import time

import httpx
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ApiServer:
    def __init__(self, app):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> ApiServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return self
            except httpx.TransportError:
                time.sleep(0.05)
        raise RuntimeError("transfer API did not start")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
