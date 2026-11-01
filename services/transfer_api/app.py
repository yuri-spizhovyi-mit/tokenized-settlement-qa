"""HTTP layer for TransferService (FastAPI).

    POST /transfers        Idempotency-Key header + {"to": "0x...", "amount": "123"}
                           202 new | 200 replay of the same request | 409 key reused for
                           a different request | 422 invalid, or would revert now |
                           503 + Retry-After: backlog full, retry with the same key
    GET  /transfers/{id}   200 status, attempts and history | 404
    GET  /health           200

`amount` is a JSON **string** of base units: a JSON number above 2^53 loses precision
in most clients before it ever reaches us (TAPI-D3, F-16).
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from .service import Busy, IdempotencyConflict, InvalidRequest, Rejected, TransferService


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: StrictStr
    amount: StrictStr  # StrictStr: a JSON number is a 422, not silently converted


def create_app(service: TransferService, *, worker_interval: float | None = None) -> FastAPI:
    """Build the app. With `worker_interval`, a background thread calls `tick()`."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(worker_interval):
            try:
                service.tick()
            except Exception as exc:  # keep the worker alive; the next tick retries
                print(f"worker tick failed: {exc!r}", flush=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if worker_interval is not None:
            threading.Thread(target=loop, name="transfer-worker", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="transfer-api", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/transfers")
    def post_transfer(body: TransferRequest, idempotency_key: str = Header(...)):
        try:
            view, created = service.accept(idempotency_key, body.to, body.amount)
        except InvalidRequest as exc:
            raise HTTPException(422, {"error": "InvalidRequest", "detail": str(exc)}) from exc
        except Rejected as exc:
            args = [str(a) for a in exc.args_]
            raise HTTPException(422, {"error": "WouldRevert", "name": exc.name, "args": args}) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(409, {"error": "IdempotencyKeyReused", "detail": str(exc)}) from exc
        except Busy as exc:  # backpressure: retry later, with the same key (TAPI-D8)
            raise HTTPException(503, {"error": "Busy", "detail": str(exc)}, headers={"Retry-After": "1"}) from exc
        return JSONResponse(view, status_code=202 if created else 200)

    @app.get("/transfers/{transfer_id}")
    def get_transfer(transfer_id: str):
        view = service.get(transfer_id)
        if view is None:
            raise HTTPException(404, {"error": "NotFound"})
        return view

    return app
