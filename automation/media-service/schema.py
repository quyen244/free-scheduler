from typing import Literal

from pydantic import BaseModel


RightsStatus = Literal["owned", "licensed", "permission", "public_domain", "unknown"]


class DownloadRequest(BaseModel):
    url: str
    rights_status: RightsStatus = "unknown"
    rights_evidence: str | None = None


class JobRequest(BaseModel):
    url: str
    rights_status: RightsStatus = "unknown"
    rights_evidence: str | None = None
    # n8n's Wait-node resume URL. Optional so the endpoint stays usable from a
    # terminal, where there is nothing to resume.
    callback_url: str | None = None


class JobAccepted(BaseModel):
    job_id: str
    video_id: str
    state: str


class MediaResponse(BaseModel):
    video_id: str
    title: str
    duration_s: float
    # Container-absolute paths under the shared volume. Every service mounts
    # /data at the same place, so these are meaningful to the caller as-is —
    # which is the point: n8n moves paths, never bytes.
    raw_path: str
    audio_path: str
    cached: bool
    width: int
    height: int
    source_hash: str
    rights_status: str
