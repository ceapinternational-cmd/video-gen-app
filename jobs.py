"""
Gestion simple des jobs en mémoire.
En production, remplace ce module par Redis + Celery ou RQ.
"""
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job:
    def __init__(self, prompt: str, model: str, params: Dict[str, Any]):
        self.id: str = str(uuid.uuid4())
        self.prompt = prompt
        self.model = model
        self.params = params
        self.status: JobStatus = JobStatus.PENDING
        self.video_url: Optional[str] = None
        self.error: Optional[str] = None
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "model": self.model,
            "status": self.status.value,
            "video_url": self.video_url,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# Stockage en mémoire (remplacé par Redis en prod)
JOBS: Dict[str, Job] = {}


def create_job(prompt: str, model: str, params: Dict[str, Any]) -> Job:
    job = Job(prompt=prompt, model=model, params=params)
    JOBS[job.id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    return JOBS.get(job_id)


def update_job(job_id: str, **kwargs) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.updated_at = datetime.utcnow().isoformat()
