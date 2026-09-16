"""Preview retry, isolation and restart behaviour without long source media."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import jobs
import library
import main
import manifest
import render
import variants
from shared import pipeline_db


pytestmark = pytest.mark.no_pipeline
VIDEO_ID = "jNQXAC9IVRw"


@pytest.fixture
def preview_db(tmp_path, monkeypatch):
    """An isolated job store, including the schema migration path."""
    monkeypatch.setattr(pipeline_db, "DB_PATH", tmp_path / "pipeline.db")
    monkeypatch.setattr(
        pipeline_db,
        "SCHEMA_PATH",
        Path(__file__).resolve().parents[2] / "data" / "schema.sql",
    )
    pipeline_db.init()
    return tmp_path


def _plan(*, inherited_assets=None):
    return {
        "video_id": VIDEO_ID,
        "brand_revisions": {"an-so": 4},
        "selections": {"an-so": {"variants": "vertical", "chunks": [1]}},
        "retry_of": None,
        "inherited_assets": inherited_assets or [],
    }


def _asset(content_item_id="part_1", path="/preview/part_1.mp4"):
    vertical = content_item_id != "whole"
    return manifest.MediaAsset(
        asset_id="a1" * 16 if vertical else "b2" * 16,
        video_id=VIDEO_ID,
        render_revision=1,
        role="branded_vertical" if vertical else "branded_whole",
        content_item_id=content_item_id,
        brand_id="an-so",
        path=path,
        sha256="c3" * 32,
        bytes=1234,
        probe=manifest.ProbeEvidence(
            duration_s=1.0,
            width=1080 if vertical else 1920,
            height=1920 if vertical else 1080,
            video_codec="h264",
            audio_codec="aac",
        ),
    )


def test_request_id_stores_the_frozen_plan_and_reuses_only_an_identical_request(preview_db):
    plan = _plan()
    job_id, state, reused = pipeline_db.create_or_reuse_preview_job(
        VIDEO_ID, "preview-idempotent", "fingerprint-a", plan
    )
    assert (state, reused) == ("queued", False)
    assert pipeline_db.preview_plan_for_job(job_id) == plan

    same_id, same_state, same_reused = pipeline_db.create_or_reuse_preview_job(
        VIDEO_ID, "preview-idempotent", "fingerprint-a", plan
    )
    assert (same_id, same_state, same_reused) == (job_id, "queued", True)
    with pytest.raises(ValueError, match="different preview plan"):
        pipeline_db.create_or_reuse_preview_job(
            VIDEO_ID, "preview-idempotent", "fingerprint-b", plan
        )


def test_restart_marks_a_preview_failed_with_its_frozen_plan_for_retry(preview_db):
    job_id, _, _ = pipeline_db.create_or_reuse_preview_job(
        VIDEO_ID, None, "restart-plan", _plan()
    )

    jobs.reap_orphans()

    job = pipeline_db.get_job(job_id)
    assert job["state"] == "failed"
    assert job["result"]["brand_revisions"] == {"an-so": 4}
    assert job["result"]["selections"]["an-so"]["chunks"] == [1]


def test_retry_plan_contains_only_the_typed_failed_targets_and_keeps_successes():
    successful = _asset(path="/first-preview/part_1.mp4").model_dump(mode="json")
    revisions, selections = main._retry_targets(
        {
            "brand_revisions": {"an-so": 4, "kenh-b": 3},
            "selections": {
                "an-so": {"variants": "all", "chunks": [1, 2]},
                "kenh-b": {"variants": "vertical", "chunks": [1]},
            },
            "assets": [successful],
            "failures": [
                {"brand_id": "an-so", "content_item_id": "whole", "error": "x"},
                {"brand_id": "an-so", "content_item_id": "part_2", "error": "x"},
            ],
        }
    )

    assert revisions == {"an-so": 4}
    assert selections == {"an-so": {"variants": "all", "chunks": [2]}}


def test_retry_worker_preserves_successful_peer_evidence_without_rendering_it(preview_db, monkeypatch):
    inherited = _asset(path="/first-preview/part_1.mp4").model_dump(mode="json")
    plan = _plan(inherited_assets=[inherited])
    plan["retry_of"] = "first-preview"
    job_id, _, _ = pipeline_db.create_or_reuse_preview_job(
        VIDEO_ID, None, "retry-plan", plan
    )
    calls = []

    def render_only_failed_target(video_id, preview_id, **kwargs):
        calls.append((video_id, preview_id, kwargs["selections"]))
        return [_asset(path="/retry-preview/part_2.mp4")], [], False

    monkeypatch.setattr(variants, "render_brand_preview", render_only_failed_target)
    jobs.run_media_preview(job_id)

    result = pipeline_db.get_job(job_id)
    assert result["state"] == "done"
    assert [asset["path"] for asset in result["result"]["assets"]] == [
        "/first-preview/part_1.mp4", "/retry-preview/part_2.mp4"
    ]
    assert calls[0][2] == {"an-so": {"variants": "vertical", "chunks": [1]}}


def test_all_with_one_chunk_uses_only_two_isolated_targets(tmp_path, monkeypatch):
    """The selective renderer neither writes a delivery manifest nor extra chunks."""
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    chunks = [
        {"idx": 0, "name": "part_1", "start_s": 0.0, "end_s": 1.0},
        {"idx": 1, "name": "part_2", "start_s": 1.0, "end_s": 2.0},
    ]
    monkeypatch.setattr(variants, "library_chunks", lambda _video_id: chunks)
    monkeypatch.setattr(library, "load_transcript", lambda _video_id: {"segments": []})
    monkeypatch.setattr(library, "load_voice_manifest", lambda _video_id: {"warnings": []})
    monkeypatch.setattr(library, "raw_path", lambda _video_id: tmp_path / "raw.mp4")
    monkeypatch.setattr(render, "probe", lambda _path: {"duration_s": 2.0})
    monkeypatch.setattr(pipeline_db, "title_for", lambda _video_id: "fixture")
    monkeypatch.setattr(variants.brand_layouts, "load_published", lambda *_: object())
    monkeypatch.setattr(
        variants.brand_layouts, "render_config", lambda _published, aspect: {"aspect": aspect}
    )
    outputs = []

    def fake_render(video_id, revision, config, brand_id, segments, **kwargs):
        output = kwargs["output_path"]
        outputs.append(output)
        item = "whole" if kwargs["chunk"] is None else kwargs["chunk"]["name"]
        return _asset(item, str(output))

    monkeypatch.setattr(render, "render_brand_variant", fake_render)
    assets, failures, cancelled = variants.render_brand_preview(
        VIDEO_ID,
        "preview-fixture",
        brand_revisions={"an-so": 4},
        selections={"an-so": {"variants": "all", "chunks": [1]}},
    )

    assert not failures and not cancelled
    assert [asset.content_item_id for asset in assets] == ["whole", "part_1"]
    assert [path.relative_to(tmp_path).as_posix() for path in outputs] == [
        f"{VIDEO_ID}/previews/preview-fixture/brands/an-so/whole-16x9.mp4",
        f"{VIDEO_ID}/previews/preview-fixture/brands/an-so/vertical/part_1-9x16.mp4",
    ]
    assert not (tmp_path / VIDEO_ID / "media-manifest.json").exists()
