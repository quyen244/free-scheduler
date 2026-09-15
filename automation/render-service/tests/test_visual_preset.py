import pytest

import visual_preset
from errors import RenderError


pytestmark = pytest.mark.no_pipeline


def _layout(canvas: str) -> dict:
    return {
        "canvas": canvas,
        "background_asset": "background.png",
        "host_asset": "host.mp4",
        "host": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.5},
        "logo_asset": "logo.png",
        "logo": {"x": 0.04, "y": 0.04, "w": 0.16, "h": 0.1},
        "watermark_asset": "watermark.png",
        "watermark": {"x": 0.8, "y": 0.04, "w": 0.16, "h": 0.1},
        "blur_regions": [],
        "caption": {"text": "Ẩn Số", "y": 0.8, "size": 42, "color": "#FFFFFF"},
        "mask_corrections": [{"x": 0.5, "y": 0.5, "radius": 0.03, "mode": "remove"}],
        "intro_enabled": True,
        "intro_duration_s": 8,
    }


def _draft() -> dict:
    return {
        "schema_version": 1,
        "preset_id": "an-so",
        "name": "Ẩn Số",
        "status": "draft",
        "vertical": _layout("vertical_9_16"),
        "landscape": _layout("landscape_16_9"),
    }


def test_draft_then_published_revision_is_immutable(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_preset, "root", lambda: tmp_path)
    draft = visual_preset.save_draft(_draft())
    assert visual_preset.load_draft("an-so") == draft

    assets = visual_preset.assets_dir("an-so")
    assets.mkdir(parents=True)
    for name in ("background.png", "host.mp4", "logo.png", "watermark.png"):
        (assets / name).write_bytes(b"fixture")

    first = visual_preset.publish(_draft())
    second = visual_preset.publish(_draft())

    assert (first.status, first.revision) == ("published", 1)
    assert (second.status, second.revision) == ("published", 2)
    assert first.content_sha256
    assert visual_preset.load_published("an-so", 1).content_sha256 == first.content_sha256


def test_publish_rejects_missing_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_preset, "root", lambda: tmp_path)
    with pytest.raises(RenderError, match="missing assets"):
        visual_preset.publish(_draft())


def test_rectangle_cannot_run_outside_canvas():
    body = _draft()
    body["vertical"]["host"] = {"x": 0.8, "y": 0.2, "w": 0.3, "h": 0.3}
    with pytest.raises(ValueError, match="inside its canvas"):
        visual_preset.VisualPreset.model_validate(body)
