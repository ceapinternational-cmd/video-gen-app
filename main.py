import os
import uuid
import shutil
import httpx
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from jobs import create_job, get_job, update_job, JobStatus

load_dotenv()

AGNES_API_KEY = os.getenv("AGNES_API_KEY")
AGNES_BASE_URL = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "agnes-video-2.5-flash")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Generateur de videos IA - Agnes", version="1.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


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


def run_generation(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return

    if not AGNES_API_KEY:
        update_job(job_id, status=JobStatus.FAILED, error="AGNES_API_KEY manquant.")
        return

    update_job(job_id, status=JobStatus.RUNNING, error=None)

    import time

    try:
        with httpx.Client() as client:
            headers = {
                "Authorization": f"Bearer {AGNES_API_KEY}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": job.model,
                "prompt": job.prompt,
                "seconds": job.params.get("seconds", "5"),
                "size": "720P",
                "aspect_ratio": job.params.get("aspect_ratio", "16:9"),
            }

            image_urls = job.params.get("image_urls") or []
            if image_urls:
                payload["mode"] = "reference"
                payload["images"] = image_urls
            else:
                payload["mode"] = "text"

            max_retries = 8
            base_delay = 10
            video_id = None
            last_error = ""

            for attempt in range(max_retries):
                try:
                    response = client.post(
                        f"{AGNES_BASE_URL}/videos",
                        headers=headers,
                        json=payload,
                        timeout=30.0,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        video_id = data.get("video_id") or data.get("id")
                        if video_id:
                            update_job(job_id, status=JobStatus.RUNNING, error=None)
                            break
                        else:
                            update_job(
                                job_id,
                                status=JobStatus.FAILED,
                                error=f"Pas de video_id : {data}",
                            )
                            return

                    if response.status_code == 503:
                        try:
                            err_data = response.json()
                            code = err_data.get("code", "")
                            if code == "video_queue_full":
                                wait = base_delay * (attempt + 1)
                                last_error = "File d'attente Agnes pleine"
                                update_job(
                                    job_id,
                                    status=JobStatus.RUNNING,
                                    error=f"File d'attente pleine, nouvelle tentative dans {wait}s (essai {attempt+1}/{max_retries})",
                                )
                                time.sleep(wait)
                                continue
                        except Exception:
                            pass
                        wait = base_delay * (attempt + 1)
                        last_error = "503 Service Unavailable"
                        update_job(
                            job_id,
                            status=JobStatus.RUNNING,
                            error=f"Serveur Agnes occupe, nouvelle tentative dans {wait}s (essai {attempt+1}/{max_retries})",
                        )
                        time.sleep(wait)
                        continue

                    response.raise_for_status()

                except (httpx.RequestError, httpx.TimeoutException) as e:
                    last_error = str(e)
                    if attempt < max_retries - 1:
                        wait = base_delay * (attempt + 1)
                        update_job(
                            job_id,
                            status=JobStatus.RUNNING,
                            error=f"Erreur reseau, nouvelle tentative dans {wait}s (essai {attempt+1}/{max_retries})",
                        )
                        time.sleep(wait)
                        continue
                    else:
                        raise

            if not video_id:
                update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    error=f"Impossible de creer la video apres {max_retries} essais. Derniere erreur : {last_error}",
                )
                return

            max_attempts = 150
            for attempt in range(max_attempts):
                time.sleep(2)
                status_url = f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}&model_name={job.model}"

                try:
                    status_response = client.get(status_url, headers=headers, timeout=30.0)
                    if status_response.status_code != 200:
                        continue
                    status_data = status_response.json()
                except Exception:
                    continue

                current_status = status_data.get("status")
                if current_status == "completed":
                    video_url = status_data.get("url")
                    if video_url:
                        update_job(
                            job_id,
                            status=JobStatus.SUCCEEDED,
                            video_url=video_url,
                            error=None,
                        )
                    else:
                        update_job(job_id, status=JobStatus.FAILED, error="URL video non trouvee.")
                    return
                elif current_status == "failed":
                    error_msg = status_data.get("error", "Erreur inconnue.")
                    update_job(job_id, status=JobStatus.FAILED, error=error_msg)
                    return

            update_job(job_id, status=JobStatus.FAILED, error="Delai depasse.")

    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "replicate_configured": bool(AGNES_API_KEY),
        "default_model": DEFAULT_MODEL,
    }


@app.post("/api/upload")
async def upload_images(files: List[UploadFile] = File(...)):
    urls = []
    for file in files[:5]:
        ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
        unique_name = f"{uuid.uuid4()}.{ext}"
        file_path = UPLOAD_DIR / unique_name
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        if APP_BASE_URL:
            urls.append(f"{APP_BASE_URL}/uploads/{unique_name}")
        else:
            urls.append(f"/uploads/{unique_name}")
    return {"urls": urls}


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    if not AGNES_API_KEY:
        raise HTTPException(status_code=500, detail="AGNES_API_KEY non configure.")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)