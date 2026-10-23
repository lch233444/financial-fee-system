from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..services.datasets import get_datasets
from ..services.storage import store_stream

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


class NewDataset(BaseModel):
    name: str


class SwitchDataset(BaseModel):
    id: str


@router.get("")
def list_datasets():
    return get_datasets().snapshot()


@router.post("")
def create_dataset(body: NewDataset):
    try:
        return get_datasets().create(body.name)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/import")
def import_dataset(name: str = Form(...), file: UploadFile = File(...)):
    manager = get_datasets()
    uploaded = None
    try:
        uploaded = store_stream(stream=file.file, directory=manager.root / "tmp", max_bytes=2 * 1024**3, suffix=".zip")
        return manager.create(name, uploaded)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if uploaded is not None:
            uploaded.unlink(missing_ok=True)


@router.post("/switch")
async def switch_dataset(body: SwitchDataset, request: Request):
    manager = get_datasets()
    sessions = getattr(request.app.state, "browser_sessions", None)
    try:
        if sessions is not None:
            await sessions.prepare_switch()
        await run_in_threadpool(manager.switch, body.id)
        return manager.snapshot()
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        if sessions is not None:
            await sessions.finish_switch(manager.generation)
