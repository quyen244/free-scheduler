"""Validated temporary brand profiles for media-variant rendering.

The web application will own durable brand profiles later.  This module keeps
the pipeline-correctness milestone honest now: a request selects a known brand
ID, paths stay inside allowlisted roots, and missing music stops the render
instead of silently creating an unbranded delivery asset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import library
from errors import BrandConfigError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Watermark(StrictModel):
    text: str = Field(min_length=1, max_length=80)
    anchor: Literal["top_left", "top_right", "bottom_left", "bottom_right"]
    margin_ratio: float = Field(ge=0.01, le=0.2)
    font_size_ratio: float = Field(ge=0.01, le=0.08)
    color: str = Field(pattern=r"^#[A-Fa-f0-9]{6}$")
    opacity: float = Field(gt=0, le=1)


class SpeechDucking(StrictModel):
    enabled: Literal[True] = True
    threshold: float = Field(gt=0, le=1)
    ratio: float = Field(ge=1, le=20)
    attack_ms: float = Field(ge=0.01, le=2000)
    release_ms: float = Field(ge=50, le=9000)


class SignatureMusic(StrictModel):
    file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    volume_db: float = Field(ge=-50, le=-12)
    loop: Literal[True] = True
    fade_in_s: float = Field(ge=0, le=5)
    fade_out_s: float = Field(ge=0, le=5)
    ducking: SpeechDucking


class MockBrandProfile(StrictModel):
    schema_version: Literal["mock-brand.v1"] = "mock-brand.v1"
    brand_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=100)
    watermark: Watermark
    signature_music: SignatureMusic

    @model_validator(mode="after")
    def validate_fades(self) -> "MockBrandProfile":
        if self.signature_music.fade_in_s + self.signature_music.fade_out_s > 10:
            raise ValueError("combined music fades must not exceed ten seconds")
        return self


def load(brand_id: str) -> MockBrandProfile:
    path = library.brand_config_path(brand_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile = MockBrandProfile.model_validate(payload)
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        raise BrandConfigError(f"invalid mock brand config {path}: {exc}") from exc
    if profile.brand_id != brand_id:
        raise BrandConfigError(
            f"brand config {path.name} declares {profile.brand_id!r}, expected {brand_id!r}"
        )
    # Resolve eagerly. A missing music file is a user-fixable configuration
    # error and should happen before minutes of video encoding are spent.
    library.music_path(profile.signature_music.file)
    return profile


def music_path(profile: MockBrandProfile) -> Path:
    return library.music_path(profile.signature_music.file)
