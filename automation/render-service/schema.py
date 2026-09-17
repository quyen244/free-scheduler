from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VoiceJobRequest(BaseModel):
    video_id: str
    # Defaults to the service's configured voice. Validated against VieNeu's
    # preset table, aliases included, before any weights load — see
    # voice.check_voice.
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
    # Present on a brand-owned job: the concrete revision each brand resolved
    # to, which is what a caller that asked for "latest" needs to record.
    brand_revisions: dict[str, int] | None = None
    reused: bool = False


class PreviewBrandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int | Literal["latest"] = "latest"
    variants: Literal["all", "landscape", "vertical"] = "all"
    chunks: list[int] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_targets(self) -> "PreviewBrandRequest":
        if self.revision != "latest" and self.revision < 1:
            raise ValueError("a brand revision is a positive integer or 'latest'")
        if self.chunks is not None:
            if self.variants == "landscape":
                raise ValueError("chunks may only be used with all or vertical variants")
            if any(chunk < 1 for chunk in self.chunks):
                raise ValueError("chunks are one-based positive integers")
            if len(self.chunks) != len(set(self.chunks)):
                raise ValueError("chunks must not contain duplicates")
        return self


class MediaPreviewJobRequest(BaseModel):
    """A selective operator preview; never a delivery revision."""

    model_config = ConfigDict(extra="forbid")

    video_id: str
    brands: dict[str, PreviewBrandRequest] = Field(min_length=1, max_length=10)
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")


class PreviewRetryRequest(BaseModel):
    """A new request id makes a retry safe against a lost HTTP response."""

    model_config = ConfigDict(extra="forbid")

    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")


class CancelledJob(BaseModel):
    job_id: str
    state: Literal["cancelled"]


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
    # its own published layout straight from the source. A number names one
    # published revision; "latest" asks the service to resolve the highest one
    # at acceptance and answer with the number it chose. A draft is never a
    # valid render input, and "latest" never falls back to one.
    brand_revisions: dict[str, int | Literal["latest"]] | None = Field(
        default=None, max_length=10
    )
    callback_url: str | None = None

    @model_validator(mode="after")
    def editor_preset_pair(self) -> "MediaRevisionJobRequest":
        if (self.preset_id is None) != (self.preset_revision is None):
            raise ValueError("preset_id and preset_revision must be supplied together")
        if self.brand_revisions is not None:
            if not self.brand_revisions:
                raise ValueError("brand_revisions must name at least one brand")
            if any(
                revision != "latest" and revision < 1
                for revision in self.brand_revisions.values()
            ):
                raise ValueError("a brand revision is a positive integer or 'latest'")
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
