"""Versioned structured-output contracts for generated social metadata."""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "metadata.v1"
_HASHTAG = re.compile(r"^#[^\s#]+$")
_EMOJI = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]"
)
_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_VIETNAMESE_WORDS = {
    "ban",
    "cau",
    "chuyen",
    "co",
    "cua",
    "de",
    "dieu",
    "duoc",
    "gi",
    "khong",
    "la",
    "mot",
    "nay",
    "nhung",
    "phan",
    "sao",
    "su",
    "that",
    "trong",
    "va",
    "voi",
    "xem",
}


def _emoji_count(*values: str) -> int:
    return sum(len(_EMOJI.findall(value)) for value in values)


def _has_vietnamese_signal(*values: str) -> bool:
    text = " ".join(values).casefold()
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", text.replace("đ", "d"))
        if not unicodedata.combining(character)
    )
    words = set(re.findall(r"[a-z]+", normalized))
    return len(words & _VIETNAMESE_WORDS) >= 3


def _reject_generated_urls(*values: str) -> None:
    if any(_URL.search(value) for value in values):
        raise ValueError("generated metadata must not invent or append URLs")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HashtagSet(StrictModel):
    """Three source-specific tags plus two relevant discovery tags."""

    content_specific: list[str] = Field(min_length=3, max_length=3)
    discovery: list[str] = Field(min_length=2, max_length=2)

    @field_validator("content_specific", "discovery")
    @classmethod
    def validate_hashtags(cls, values: list[str]) -> list[str]:
        if any(not _HASHTAG.fullmatch(value) for value in values):
            raise ValueError("hashtags must start with # and contain no whitespace")
        return values

    @model_validator(mode="after")
    def validate_unique_hashtags(self) -> "HashtagSet":
        values = self.content_specific + self.discovery
        if len({value.casefold() for value in values}) != 5:
            raise ValueError("all five hashtags must be unique")
        return self

    def flattened(self) -> list[str]:
        return self.content_specific + self.discovery


class PlatformCaption(StrictModel):
    caption: str = Field(min_length=1, max_length=2200)
    hashtags: HashtagSet

    @model_validator(mode="after")
    def validate_emoji_limit(self) -> "PlatformCaption":
        if _emoji_count(self.caption, *self.hashtags.flattened()) > 2:
            raise ValueError("a platform metadata object may contain at most two emojis")
        return self


class YouTubeMetadata(StrictModel):
    schema_version: Literal["metadata.v1"]
    language: Literal["vi"]
    summary: str = Field(min_length=1, max_length=4000)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=4000)
    thumbnail_text: str = Field(min_length=1, max_length=100)
    hashtags: HashtagSet

    @model_validator(mode="after")
    def validate_emoji_limit(self) -> "YouTubeMetadata":
        values = (
            self.title,
            self.description,
            self.thumbnail_text,
            *self.hashtags.flattened(),
        )
        if _emoji_count(*values) > 2:
            raise ValueError("YouTube metadata may contain at most two emojis")
        if not _has_vietnamese_signal(self.summary, *values):
            raise ValueError("YouTube metadata must contain Vietnamese text")
        _reject_generated_urls(self.summary, *values)
        return self


class ChunkMetadata(StrictModel):
    schema_version: Literal["metadata.v1"]
    language: Literal["vi"]
    chunk_name: str = Field(pattern=r"^part_[1-9][0-9]*$")
    hook: str = Field(min_length=1, max_length=160)
    visual_caption: str = Field(min_length=1, max_length=500)
    facebook: PlatformCaption
    tiktok: PlatformCaption

    @model_validator(mode="after")
    def validate_visual_emoji_limit(self) -> "ChunkMetadata":
        if _emoji_count(self.hook, self.visual_caption) > 2:
            raise ValueError("chunk visual text may contain at most two emojis")
        values = (
            self.hook,
            self.visual_caption,
            self.facebook.caption,
            self.tiktok.caption,
        )
        if not _has_vietnamese_signal(*values):
            raise ValueError("chunk metadata must contain Vietnamese text")
        _reject_generated_urls(*values)
        return self


class MetadataJobRequest(StrictModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    callback_url: str | None = None


class MetadataJobAccepted(StrictModel):
    job_id: str
    video_id: str
    state: Literal["queued", "running"]
    reused: bool
