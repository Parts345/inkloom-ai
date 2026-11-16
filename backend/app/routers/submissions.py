from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.routers.demo import Actor, get_actor
from app.schemas import SubmissionResponse
from app.services.orchestrator import AnalysisInputError, analyse_submission

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUBMITTING_ROLES = {"creator", "admin"}

router = APIRouter(tags=["submissions"])


@router.post("/submissions", response_model=SubmissionResponse, status_code=201)
async def create_submission(
    title: str = Form(..., min_length=1, max_length=200),
    text: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    actor: Actor = Depends(get_actor),
) -> SubmissionResponse:
    if actor.role not in SUBMITTING_ROLES:
        raise HTTPException(status_code=403, detail="Your role cannot submit content")

    file_bytes: Optional[bytes] = None
    file_name: Optional[str] = None
    if file is not None and file.filename:
        file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
        file_name = file.filename

    try:
        return await analyse_submission(
            workspace_id=actor.workspace_id,
            actor_id=actor.user_id,
            title=title,
            text=text or None,
            file_name=file_name,
            file_bytes=file_bytes,
        )
    except AnalysisInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
