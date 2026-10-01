import os
import asyncio
import replicate
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from jobs import create_job, get_job, update_job, JobStatus

# ----------------------------
# Config
# ----------------------------
load_dotenv()

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "minimax/video-01")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Générateur de vidéos IA", version="1.0.0")

# Sert /static/*
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ----------------------------
# Schémas
# ----------------------------
class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=2000)
    model: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class GenerateResponse(BaseModel):
    job_id: str
    status: str


class JobResponse(BaseModel):
    id: str
    prompt: str
    model: str
    status: str
    video_url: Optional[str]
    error: Optional[str]
    created_at: str
    updated_at: str


# ----------------------------
# Logique de génération
# ----------------------------
def _extract_video_url(output: Any) -> Optional[str]:
    """Normalise la sortie Replicate pour retrouver l'URL vidéo."""
    if isinstance(output, str):
        return output
    if isinstance(output, list) and output:
        return _extract_video_url(output[0])
    if isinstance(output, dict):
        for key in ("video", "output", "url", "mp4"):
            if key in output:
                url = _extract_video_url(output[key])
                if url:
                    return url
    if hasattr(output, "url"):
        return output.url
    return None


def run_generation(job_id: str) -> None:
    """Fonction exécutée en arrière-plan par FastAPI."""
    job = get_job(job_id)
    if not job:
        return

    if not REPLICATE_API_TOKEN:
        update_job(
            job_id,
            status=JobStatus.FAILED,
            error="REPLICATE_API_TOKEN manquant dans .env",
        )
        return

    update_job(job_id, status=JobStatus.RUNNING)

    try:
        client = replicate.Client(api_token=REPLICATE_API_TOKEN)
        inputs = dict(job.params)
        inputs["prompt"] = job.prompt

        output = client.run(job.model, input=inputs)
        video_url = _extract_video_url(output)

        if not video_url:
            update_job(
                job_id,
                status=JobStatus.FAILED,
                error=f"Sortie inattendue : {output}",
            )
        else:
            update_job(
                job_id,
                status=JobStatus.SUCCEEDED,
                video_url=video_url,
            )
    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))


# ----------------------------
# Routes
# ----------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "replicate_configured": bool(REPLICATE_API_TOKEN),
        "default_model": DEFAULT_MODEL,
    }


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    if not REPLICATE_API_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="REPLICATE_API_TOKEN non configuré côté serveur.",
        )

    model = req.model or DEFAULT_MODEL
    job = create_job(prompt=req.prompt, model=model, params=req.params)

    background_tasks.add_task(run_generation, job.id)

    return GenerateResponse(job_id=job.id, status=job.status.value)


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable")
    return JobResponse(**job.to_dict())


@app.get("/api/jobs", response_model=List[JobResponse])
async def list_jobs():
    from jobs import JOBS
    return [JobResponse(**j.to_dict()) for j in JOBS.values()]


# ----------------------------
# Lancement direct (dev)
# ----------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
