"""Media manifest identity, topology, probing, and atomic persistence."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import library
import manifest


pytestmark = pytest.mark.no_pipeline

VIDEO_ID = "manifestAAA"
SOURCE_SHA = "ab" * 32
GENERATED_AT = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _probe(role: manifest.AssetRole) -> manifest.ProbeEvidence:
    width, height = (1080, 1920) if role.endswith("vertical") else (1920, 1080)
    return manifest.ProbeEvidence(
        duration_s=10.0,
        width=width,
        height=height,
        video_codec="h264",
        audio_codec="aac",
    )


def _asset(
    role: manifest.AssetRole,
    content_item_id: str,
    *,
    brand_id: str | None = None,
    lineage_asset_id: str | None = None,
    revision: int = 1,
) -> manifest.MediaAsset:
    return manifest.MediaAsset(
        asset_id=manifest.deterministic_asset_id(
            VIDEO_ID, revision, role, content_item_id, brand_id
        ),
        video_id=VIDEO_ID,
        render_revision=revision,
        role=role,
        content_item_id=content_item_id,
        brand_id=brand_id,
        lineage_asset_id=lineage_asset_id,
        path=str(
            manifest.expected_asset_path(
                VIDEO_ID, revision, role, content_item_id, brand_id
            )
        ),
        sha256="cd" * 32,
        bytes=1234,
        probe=_probe(role),
    )


def _ready_manifest() -> manifest.MediaManifest:
    clean_whole = _asset("clean_whole", "whole")
    clean_parts = {
        part: _asset("clean_vertical", part) for part in ("part_1", "part_2")
    }
    assets = [clean_whole, *clean_parts.values()]
    assets.append(
        _asset(
            "branded_whole",
            "whole",
            brand_id="mock-brand",
            lineage_asset_id=clean_whole.asset_id,
        )
    )
    assets.extend(
        _asset(
            "branded_vertical",
            part,
            brand_id="mock-brand",
            lineage_asset_id=clean_parts[part].asset_id,
        )
        for part in ("part_1", "part_2")
    )
    return manifest.MediaManifest(
        video_id=VIDEO_ID,
        render_revision=1,
        metadata_revision_id="metadata-revision-1",
        source_sha256=SOURCE_SHA,
        state="ready",
        chunk_names=["part_1", "part_2"],
        brand_ids=["mock-brand"],
        assets=assets,
        generated_at=GENERATED_AT,
    )


def _configure_data_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=root))


def _make_media(path: Path, size: str, *, audio: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={size}:r=25:d=0.4",
    ]
    if audio:
        command.extend(
            ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:d=0.4"]
        )
    command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    if audio:
        command.extend(["-c:a", "aac", "-shortest"])
    else:
        command.append("-an")
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True)


def test_paths_are_revisioned_and_platform_neutral(tmp_path, monkeypatch):
    _configure_data_root(monkeypatch, tmp_path)

    assert manifest.expected_asset_path(
        VIDEO_ID, 3, "clean_whole", "whole"
    ) == tmp_path / VIDEO_ID / "outputs/clean/revision/3/whole-16x9.mp4"
    assert manifest.expected_asset_path(
        VIDEO_ID, 3, "clean_vertical", "part_2"
    ) == tmp_path / VIDEO_ID / "outputs/clean/revision/3/vertical/part_2-9x16.mp4"
    assert manifest.expected_asset_path(
        VIDEO_ID, 3, "branded_vertical", "part_2", "brand-a"
    ) == tmp_path / VIDEO_ID / "outputs/brands/brand-a/revision/3/vertical/part_2-9x16.mp4"


@pytest.mark.parametrize("brand_id", ["../escape", "Brand A", "", "a/b"])
def test_unsafe_brand_ids_are_rejected(tmp_path, monkeypatch, brand_id):
    _configure_data_root(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        manifest.expected_asset_path(
            VIDEO_ID, 1, "branded_whole", "whole", brand_id
        )


def test_asset_identity_is_stable_across_safe_retry(tmp_path, monkeypatch):
    _configure_data_root(monkeypatch, tmp_path)
    first = manifest.deterministic_asset_id(
        VIDEO_ID, 2, "branded_vertical", "part_1", "brand-a"
    )
    second = manifest.deterministic_asset_id(
        VIDEO_ID, 2, "branded_vertical", "part_1", "brand-a"
    )
    next_revision = manifest.deterministic_asset_id(
        VIDEO_ID, 3, "branded_vertical", "part_1", "brand-a"
    )
    assert first == second
    assert first != next_revision


def test_ready_manifest_requires_complete_topology_and_clean_lineage(tmp_path, monkeypatch):
    _configure_data_root(monkeypatch, tmp_path)
    ready = _ready_manifest()
    assert len(ready.assets) == 6

    with pytest.raises(ValidationError, match="ready topology mismatch"):
        manifest.MediaManifest.model_validate(
            {**ready.model_dump(), "assets": ready.model_dump()["assets"][:-1]}
        )

    bad_assets = ready.model_dump()["assets"]
    bad_assets[-1]["lineage_asset_id"] = bad_assets[0]["asset_id"]
    with pytest.raises(ValidationError, match="must reference clean asset"):
        manifest.MediaManifest.model_validate(
            {**ready.model_dump(), "assets": bad_assets}
        )


def test_ffprobe_hash_and_stream_gate_accepts_a_valid_vertical_asset(tmp_path, monkeypatch):
    _configure_data_root(monkeypatch, tmp_path)
    path = manifest.expected_asset_path(
        VIDEO_ID, 1, "clean_vertical", "part_1"
    )
    _make_media(path, "1080x1920")

    asset = manifest.inspect_expected_asset(
        VIDEO_ID,
        1,
        "clean_vertical",
        "part_1",
        expected_duration_s=0.4,
        duration_tolerance_s=0.1,
    )

    assert (asset.probe.width, asset.probe.height) == (1080, 1920)
    assert asset.probe.video_codec == "h264"
    assert asset.probe.audio_codec == "aac"
    assert asset.bytes == path.stat().st_size
    assert len(asset.sha256) == 64


def test_ffprobe_gate_rejects_wrong_dimensions(tmp_path, monkeypatch):
    _configure_data_root(monkeypatch, tmp_path)
    path = manifest.expected_asset_path(VIDEO_ID, 1, "clean_whole", "whole")
    _make_media(path, "1080x1920")

    with pytest.raises(manifest.ManifestValidationError, match="dimensions"):
        manifest.inspect_expected_asset(
            VIDEO_ID, 1, "clean_whole", "whole", expected_duration_s=0.4
        )


def test_ffprobe_gate_rejects_missing_audio(tmp_path, monkeypatch):
    _configure_data_root(monkeypatch, tmp_path)
    path = manifest.expected_asset_path(VIDEO_ID, 1, "clean_whole", "whole")
    _make_media(path, "1920x1080", audio=False)

    with pytest.raises(manifest.ManifestValidationError, match="video and audio"):
        manifest.inspect_expected_asset(
            VIDEO_ID, 1, "clean_whole", "whole", expected_duration_s=0.4
        )


def test_manifest_write_is_atomic_idempotent_and_revision_immutable(tmp_path, monkeypatch):
    _configure_data_root(monkeypatch, tmp_path)
    ready = _ready_manifest()

    revision_path, current_path = manifest.write_manifest(ready)
    manifest.write_manifest(ready)

    assert manifest.MediaManifest.model_validate_json(
        revision_path.read_text(encoding="utf-8")
    ) == ready
    assert json.loads(current_path.read_text(encoding="utf-8"))["state"] == "ready"
    assert not list((tmp_path / VIDEO_ID).rglob("*.part"))

    changed = ready.model_copy(update={"warnings": ["changed after ready"]})
    with pytest.raises(manifest.ManifestConflictError, match="immutable"):
        manifest.write_manifest(changed)


def test_needs_action_can_be_replaced_by_ready_without_freezing_bad_history(
    tmp_path, monkeypatch
):
    _configure_data_root(monkeypatch, tmp_path)
    ready = _ready_manifest()
    needs_action = ready.model_copy(
        update={
            "state": "needs_action",
            "failures": [
                manifest.ManifestFailure(
                    code="asset_missing",
                    message="part_2 must be rerendered",
                    retryable=True,
                )
            ],
        }
    )

    revision_path, current_path = manifest.write_manifest(needs_action)
    assert not revision_path.exists()
    assert json.loads(current_path.read_text(encoding="utf-8"))["state"] == "needs_action"

    manifest.write_manifest(ready)
    assert revision_path.exists()
    assert json.loads(current_path.read_text(encoding="utf-8"))["state"] == "ready"


def test_committed_json_schema_matches_the_models():
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "media-manifest.v1.schema.json"
    )
    assert json.loads(schema_path.read_text(encoding="utf-8")) == manifest.json_schema()
