from pydantic import BaseModel


class VoiceJobRequest(BaseModel):
    video_id: str
    # Defaults to the service's configured voice. An enum over the eight
    # ZeroTTS ships, not a free string — see voice.SHIPPED_VOICES.
    voice: str | None = None
    # n8n's Wait-node resume URL. Optional so the endpoint stays usable from a
    # terminal, where there is nothing to resume.
    callback_url: str | None = None


class RenderJobRequest(BaseModel):
    video_id: str
    preset: str | None = None
    # Render one chunk instead of all of them. For trying a preset change
    # against a four-minute clip rather than a forty-minute video.
    only_chunk: int | None = None
    callback_url: str | None = None


class JobAccepted(BaseModel):
    job_id: str
    video_id: str
    state: str
