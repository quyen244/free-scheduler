from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class MediaRevisionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    render_revision: int = Field(ge=1)
    brand_ids: list[str] = Field(
        default_factory=lambda: ["mock-brand"], min_length=1, max_length=10
    )
    vertical_preset: str = "vertical-clean"
    landscape_preset: str = "yt-landscape"
    metadata_revision_id: str | None = None
    preset_id: str | None = None
    preset_revision: int | None = Field(default=None, ge=1)
    # Supplying this selects the brand-owned topology: each named brand renders
    # its own published layout straight from the source. The revision is named
    # explicitly for the same reason a preset revision is — a draft or a
    # floating "latest" is never a valid render input.
    brand_revisions: dict[str, int] | None = Field(default=None, max_length=10)
    callback_url: str | None = None

    @model_validator(mode="after")
    def editor_preset_pair(self) -> "MediaRevisionJobRequest":
        if (self.preset_id is None) != (self.preset_revision is None):
            raise ValueError("preset_id and preset_revision must be supplied together")
        if self.brand_revisions is not None:
            if not self.brand_revisions:
                raise ValueError("brand_revisions must name at least one brand")
            if any(revision < 1 for revision in self.brand_revisions.values()):
                raise ValueError("a brand revision is a positive integer")
            if self.preset_id is not None:
                raise ValueError(
                    "a brand-owned render owns its own layout; it cannot also "
                    "take a visual preset"
                )
        return self


class PresetMattingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str
    host_asset: str
    corrections: list[dict[str, object]] = Field(default_factory=list, max_length=50)
