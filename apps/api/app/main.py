"""Ridian Agency — FastAPI entrypoint."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from .services.workflow_service import run_workflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("ridian.api")

app = FastAPI(title="Ridian Agency API", version="0.1.0")


class WorkflowRequest(BaseModel):
    task: str = Field(..., min_length=10, description="The business task for the agency to handle.")


class WorkflowResponse(BaseModel):
    status: str
    artifact_folder: str
    research_summary: str
    business_document: str
    slide_outline: str
    draft_email: str


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "ridian-agency",
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "openai_key_loaded": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.post("/workflows/run", response_model=WorkflowResponse)
async def workflows_run(payload: WorkflowRequest) -> WorkflowResponse:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set.")

    log.info("workflow.start task=%r", payload.task[:120])
    try:
        result = await run_workflow(payload.task)
    except Exception as exc:  # surface SDK errors as 500s with a useful message
        log.exception("workflow.failed")
        raise HTTPException(status_code=500, detail=f"workflow failed: {exc}") from exc

    log.info("workflow.complete folder=%s", result.artifact_folder)
    return WorkflowResponse(
        status="complete",
        artifact_folder=str(result.artifact_folder),
        research_summary=result.research_summary,
        business_document=result.business_document,
        slide_outline=result.slide_outline,
        draft_email=result.draft_email,
    )
