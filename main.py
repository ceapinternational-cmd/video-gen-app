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

try:
    import replicate
    REPLICATE_AVAILABLE = True
except ImportError:
    REPLICATE_AVAILABLE = False

from jobs import create_job, get_job, update_job, JobStatus

load_dotenv()

AGNES_API_KEY = os.getenv("AGNES_API_KEY")
AGNES_BASE_URL = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
DEFAULT_MODEL_AGNES = os.getenv("DEFAULT_MODEL", "agnes-video-2.5-flash")

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
DEFAULT_MODEL_REPLICATE = os.getenv("REPLICATE_MODEL", "minimax/video-01")

DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "replicate").lower()
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Generateur de videos IA - Dual", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=2000)
    provider: Optional[str] = None
    model: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class GenerateResponse(BaseModel):
    job_id: str
    status: str
    provider: str


class JobResponse(BaseModel):
    id: str
    prompt: str
    model: str
    status: str
    video_url: Optional[str]
    error: Optional[str]
    created_at: str
    updated_at: str


# ============ REPLICATE ============
def run_replicate(job_id: str, job) -> None:
    if not REPLICATE_AVAILABLE:
        update_job(job_id, status=JobStatus.FAILED, error="Module replicate non installe.")
        return
    if not REPLICATE_API_TOKEN:
        update_job(job_id, status=JobStatus.FAILED, error="REPLICATE_API_TOKEN manquant.")
        return

    try:
        client = replicate.Client(api_token=REPLICATE_API_TOKEN)
        inputs = dict(job.params)
        inputs["prompt"] = job.prompt

        image_urls = inputs.pop("image_urls", None)
        if image_urls:
            inputs["image"] = image_urls[0]

        output = client.run(job.model, input=inputs)

        video_url = None
        if isinstance(output, str):
            video_url = output
        elif isinstance(output, list) and output:
            video_url = str(output[0])
        elif hasattr(output, "url"):
            video_url = output.url

        if video_url:
            update_job(job_id, status=JobStatus.SUCCEEDED, video_url=video_url, error=None)
        else:
            update_job(job_id, status=JobStatus.FAILED, error=f"Sortie inattendue : {output}")
    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))


# ============ AGNES ============
def run_agnes(job_id: str, job) -> None:
    if not AGNES_API_KEY:
        update_job(job_id, status=JobStatus.FAILED, error="AGNES_API_KEY manquant.")
        return

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
                "seconds": job.params.get("seconds", "10"),
                "size": "720P",
                "aspect_ratio": job.params.get("aspect_ratio", "16:9"),
            }

            image_urls = job.params.get("image_urls") or []
            if image_urls:
                payload["mode"] = "reference"
                payload["images"] = image_urls
            else:
                payload["mode"] = "text"

            max_retries = 15
            base_delay = 20
            max_delay = 120
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
                            update_job(job_id, status=JobStatus.FAILED,
                                       error=f"Pas de video_id : {data}")
                            return

                    if response.status_code in (429, 503):
                        err_code = ""
                        try:
                            err_code = response.json().get("code", "")
                        except Exception:
                            pass

                        if response.status_code == 429:
                            wait = 60
                            last_error = "Limite de debit atteinte (429)"
                            update_job(job_id, status=JobStatus.RUNNING,
                                       error=f"Limite Agnes atteinte. Nouvelle tentative dans 60s (essai {attempt+1}/{max_retries})")
                        elif err_code == "video_queue_full":
                            wait = min(base_delay * (attempt + 1), max_delay)
                            last_error = "File d'attente Agnes pleine"
                            update_job(job_id, status=JobStatus.RUNNING,
                                       error=f"File d'attente pleine, nouvelle tentative dans {wait}s (essai {attempt+1}/{max_retries})")
                        else:
                            wait = min(base_delay * (attempt + 1), max_delay)
                            last_error = "503 Service Unavailable"
                            update_job(job_id, status=JobStatus.RUNNING,
                                       error=f"Serveur Agnes occupe, nouvelle tentative dans {wait}s (essai {attempt+1}/{max_retries})")
                        time.sleep(wait)
                        continue

                    response.raise_for_status()

                except (httpx.RequestError, httpx.TimeoutException) as e:
                    last_error = str(e)
                    if attempt < max_retries - 1:
                        wait = min(base_delay * (attempt + 1), max_delay)
                        update_job(job_id, status=JobStatus.RUNNING,
                                   error=f"Erreur reseau, nouvelle tentative dans {wait}s (essai {attempt+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    else:
                        raise

            if not video_id:
                update_job(job_id, status=JobStatus.FAILED,
                           error=f"Impossible de creer la video apres {max_retries} essais. Derniere erreur : {last_error}")
                return

            max_attempts = 200
            for attempt in range(max_attempts):
                time.sleep(3)
                status_url = f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}&model_name={job.model}"

                try:
                    r = client.get(status_url, headers=headers, timeout=30.0)
                    if r.status_code != 200:
                        continue
                    sd = r.json()
                except Exception:
                    continue

                current_status = sd.get("status")
                if current_status == "completed":
                    url = sd.get("url")
                    if url:
                        update_job(job_id, status=JobStatus.SUCCEEDED, video_url=url, error=None)
                    else:
                        update_job(job_id, status=JobStatus.FAILED, error="URL video non trouvee.")
                    return
                elif current_status == "failed":
                    update_job(job_id, status=JobStatus.FAILED,
                               error=sd.get("error", "Erreur inconnue."))
                    return

            update_job(job_id, status=JobStatus.FAILED, error="Delai depasse.")

    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))


# ============ DISPATCHER ============
def run_generation(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return

    update_job(job_id, status=JobStatus.RUNNING, error=None)

    provider = job.params.get("provider", DEFAULT_PROVIDER).lower()

    if provider == "agnes":
        run_agnes(job_id, job)
    else:
        run_replicate(job_id, job)


# ============ ROUTES ============
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "replicate_configured": bool(REPLICATE_API_TOKEN),
        "agnes_configured": bool(AGNES_API_KEY),
        "default_provider": DEFAULT_PROVIDER,
        "default_model_replicate": DEFAULT_MODEL_REPLICATE,
        "default_model_agnes": DEFAULT_MODEL_AGNES,
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
    provider = (req.provider or DEFAULT_PROVIDER).lower()

    if provider == "agnes" and not AGNES_API_KEY:
        raise HTTPException(status_code=500, detail="AGNES_API_KEY non configure.")
    if provider == "replicate" and not REPLICATE_API_TOKEN:
        raise HTTPException(status_code=500, detail="REPLICATE_API_TOKEN non configure.")

    if provider == "agnes":
        model = req.model or DEFAULT_MODEL_AGNES
    else:
        model = req.model or DEFAULT_MODEL_REPLICATE

    params = dict(req.params)
    params["provider"] = provider

    job = create_job(prompt=req.prompt, model=model, params=params)
    background_tasks.add_task(run_generation, job.id)
    return GenerateResponse(job_id=job.id, status=job.status.value, provider=provider)


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