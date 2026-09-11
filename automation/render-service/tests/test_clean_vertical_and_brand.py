"""Revisioned clean vertical media and brand derivation use real ffmpeg."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import brand
import library
import render
import variants
from errors import PresetNotFoundError, RenderError


pytestmark = pytest.mark.no_pipeline
VIDEO_ID = "verticalAAA"


def _preset(name: str) -> dict:
    return json.loads(
        (Path("/data/presets") / f"{name}.json").read_text(encoding="utf-8")
    )


def _build_inputs(root: Path) -> None:
    video = root / VIDEO_ID
    video.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=1.2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(video / "raw.mp4"),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1.2",
            "-c:a",
            "pcm_s16le",
            str(video / "voice.wav"),
        ],
        check=True,
        capture_output=True,
    )
    (root / "presets" / "backgrounds").mkdir(parents=True)
    shutil.copy(
        "/data/presets/backgrounds/bg-mystery.png",
        root / "presets" / "backgrounds" / "bg-mystery.png",
    )
    (root / "presets" / "brands").mkdir(parents=True)
    for preset_name in ("vertical-clean.json", "yt-landscape.json"):
        shutil.copy(
            Path("/data/presets") / preset_name,
            root / "presets" / preset_name,
        )
    shutil.copy(
        "/data/presets/brands/mock-brand.json",
        root / "presets" / "brands" / "mock-brand.json",
    )
    (root / "music").mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=0.5",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(root / "music" / "mock-signature.wav"),
        ],
        check=True,
        capture_output=True,
    )
    transcript = {
        "transcript": "Phần một. Phần hai.",
        "segments": [
            {"start": 0.0, "end": 0.6, "text": "Phần một."},
            {"start": 0.6, "end": 1.2, "text": "Phần hai."},
        ],
    }
    (video / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
    )
    (video / "voice.json").write_text(
        json.dumps({"warnings": []}), encoding="utf-8"
    )


def _audio(path: Path) -> np.ndarray:
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(decoded.stdout, dtype=np.int16).astype(np.float64)


def _tone_level(samples: np.ndarray, frequency: float, start_s: float, end_s: float) -> float:
    start = int(start_s * 48_000)
    end = int(end_s * 48_000)
    windowed = samples[start:end] / 32_768.0
    window = np.hanning(windowed.size)
    phase = np.exp(
        -2j * np.pi * frequency * np.arange(windowed.size) / 48_000
    )
    return float(2 * np.abs(np.sum(windowed * window * phase)) / np.sum(window))


def _frame(path: Path) -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(1920, 1080)


def test_clean_part_then_mock_brand_adds_watermark_and_music(tmp_path, monkeypatch):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(render, "encoder", lambda: "libx264")
    clean = render.render_clean_vertical(
        VIDEO_ID,
        2,
        {
            "idx": 0,
            "name": "part_1",
            "start_s": 0.0,
            "end_s": 1.2,
        },
        {
            **_preset("vertical-clean"),
            "background": "bg-mystery.png",
        },
        [{"start": 0.0, "end": 1.2, "text": "Phụ đề thử nghiệm."}],
        texts={"caption_top": "Bạn có nhận ra?", "caption_bottom": "Phần 1"},
    )
    profile = brand.load("mock-brand")
    branded = render.render_branded_variant(clean, profile)

    assert Path(clean.path) == (
        tmp_path
        / VIDEO_ID
        / "outputs/clean/revision/2/vertical/part_1-9x16.mp4"
    )
    assert Path(branded.path) == (
        tmp_path
        / VIDEO_ID
        / "outputs/brands/mock-brand/revision/2/vertical/part_1-9x16.mp4"
    )
    assert branded.lineage_asset_id == clean.asset_id
    assert branded.brand_id == "mock-brand"
    assert branded.sha256 != clean.sha256
    assert (branded.probe.width, branded.probe.height) == (1080, 1920)

    clean_audio = _audio(Path(clean.path))
    branded_audio = _audio(Path(branded.path))
    count = min(clean_audio.size, branded_audio.size)
    difference_rms = np.sqrt(np.mean((branded_audio[:count] - clean_audio[:count]) ** 2))
    assert difference_rms > 20

    difference = np.abs(
        _frame(Path(branded.path)).astype(np.int16)
        - _frame(Path(clean.path)).astype(np.int16)
    )
    top_right = difference[:180, 650:]
    assert int((top_right > 20).sum()) > 100


def test_signature_music_ducks_loops_and_fades_without_lowering_speech(
    tmp_path, monkeypatch
):
    _build_inputs(tmp_path)
    video = tmp_path / VIDEO_ID
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=4",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(video / "raw.mp4"),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-af",
            "adelay=1000:all=1,apad=whole_dur=4",
            "-t",
            "4",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(video / "voice.wav"),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(render, "encoder", lambda: "libx264")

    clean = render.render_clean_vertical(
        VIDEO_ID,
        6,
        {"idx": 0, "name": "part_1", "start_s": 0.0, "end_s": 4.0},
        {**_preset("vertical-clean"), "background": "bg-mystery.png"},
        [],
    )
    branded = render.render_branded_variant(clean, brand.load("mock-brand"))
    clean_audio = _audio(Path(clean.path))
    branded_audio = _audio(Path(branded.path))

    speech_music = _tone_level(branded_audio, 880, 1.3, 1.7)
    recovered_music = _tone_level(branded_audio, 880, 2.55, 2.85)
    fade_in_music = _tone_level(branded_audio, 880, 0.05, 0.2)
    fade_out_music = _tone_level(branded_audio, 880, 3.8, 3.95)
    clean_speech = _tone_level(clean_audio, 440, 1.3, 1.7)
    branded_speech = _tone_level(branded_audio, 440, 1.3, 1.7)

    assert recovered_music > 0.0005
    assert speech_music < recovered_music * 0.65
    assert fade_in_music < recovered_music * 0.65
    assert fade_out_music < recovered_music * 0.65
    assert 0.8 < branded_speech / clean_speech < 1.1


def test_clean_vertical_rejects_brand_fields(tmp_path, monkeypatch):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    preset = _preset("vertical-clean")
    preset["watermark"] = {"text": "brand"}
    with pytest.raises(RenderError, match="cannot contain brand"):
        render.render_clean_vertical(
            VIDEO_ID,
            1,
            {"idx": 0, "name": "part_1", "start_s": 0.0, "end_s": 1.2},
            {**preset, "background": "bg-mystery.png"},
            [],
        )


def test_brand_music_path_cannot_escape_allowlisted_folder(tmp_path, monkeypatch):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    with pytest.raises(PresetNotFoundError, match="valid music file"):
        library.music_path("../outside.wav")


def test_missing_signature_music_fails_during_brand_preflight(tmp_path, monkeypatch):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    (tmp_path / "music" / "mock-signature.wav").unlink()

    with pytest.raises(PresetNotFoundError, match="signature music is missing.*retry"):
        brand.load("mock-brand")


def test_complete_revision_creates_every_part_and_reuses_same_brand_verticals(
    tmp_path, monkeypatch
):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(render, "encoder", lambda: "libx264")
    chunks = [
        {
            "idx": 0,
            "name": "part_1",
            "start_s": 0.0,
            "end_s": 0.6,
            "hook": "Mở đầu",
            "caption": "Phần 1",
        },
        {
            "idx": 1,
            "name": "part_2",
            "start_s": 0.6,
            "end_s": 1.2,
            "hook": "Tiếp theo",
            "caption": "Phần 2",
        },
    ]
    monkeypatch.setattr(variants, "library_chunks", lambda _video_id: chunks)

    media_manifest = variants.render_media_revision(
        VIDEO_ID,
        3,
        brand_ids=["mock-brand"],
        metadata_revision_id="metadata-fixture-1",
    )

    assert media_manifest.state == "ready"
    assert media_manifest.chunk_names == ["part_1", "part_2"]
    assert len(media_manifest.assets) == 6
    identities = {
        (asset.role, asset.content_item_id, asset.brand_id)
        for asset in media_manifest.assets
    }
    assert identities == {
        ("clean_whole", "whole", None),
        ("clean_vertical", "part_1", None),
        ("clean_vertical", "part_2", None),
        ("branded_whole", "whole", "mock-brand"),
        ("branded_vertical", "part_1", "mock-brand"),
        ("branded_vertical", "part_2", "mock-brand"),
    }
    verticals = [
        asset for asset in media_manifest.assets if asset.role == "branded_vertical"
    ]
    assert all("facebook" not in asset.path and "tiktok" not in asset.path for asset in verticals)
    assert (tmp_path / VIDEO_ID / "manifests/revision/3.json").is_file()
    assert (tmp_path / VIDEO_ID / "media-manifest.json").is_file()

    mtimes = {asset.path: Path(asset.path).stat().st_mtime_ns for asset in media_manifest.assets}
    retried = variants.render_media_revision(
        VIDEO_ID,
        3,
        brand_ids=["mock-brand"],
        metadata_revision_id="metadata-fixture-1",
    )
    assert retried == media_manifest
    assert mtimes == {
        asset.path: Path(asset.path).stat().st_mtime_ns for asset in retried.assets
    }


def test_corrupt_brand_asset_retry_reuses_verified_peers(tmp_path, monkeypatch):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(render, "encoder", lambda: "libx264")
    chunks = [
        {
            "idx": 0,
            "name": "part_1",
            "start_s": 0.0,
            "end_s": 0.6,
            "hook": "Mo dau",
            "caption": "Phan 1",
        },
        {
            "idx": 1,
            "name": "part_2",
            "start_s": 0.6,
            "end_s": 1.2,
            "hook": "Tiep theo",
            "caption": "Phan 2",
        },
    ]
    monkeypatch.setattr(variants, "library_chunks", lambda _video_id: chunks)

    original_whole = render.render_clean_whole
    original_vertical = render.render_clean_vertical
    original_branded = render.render_branded_variant
    calls = {"clean_whole": 0, "clean_vertical": 0, "branded": {}}
    corrupted_once = False

    def counted_whole(*args, **kwargs):
        calls["clean_whole"] += 1
        return original_whole(*args, **kwargs)

    def counted_vertical(*args, **kwargs):
        calls["clean_vertical"] += 1
        return original_vertical(*args, **kwargs)

    def corrupt_once(clean_asset, profile, warnings=None):
        nonlocal corrupted_once
        item = clean_asset.content_item_id
        calls["branded"][item] = calls["branded"].get(item, 0) + 1
        outcome = original_branded(clean_asset, profile, warnings)
        if item == "part_2" and not corrupted_once:
            corrupted_once = True
            Path(outcome.path).write_bytes(b"corrupt")
            raise RenderError("injected corrupt branded output")
        return outcome

    monkeypatch.setattr(render, "render_clean_whole", counted_whole)
    monkeypatch.setattr(render, "render_clean_vertical", counted_vertical)
    monkeypatch.setattr(render, "render_branded_variant", corrupt_once)

    failed = variants.render_media_revision(
        VIDEO_ID,
        5,
        brand_ids=["mock-brand"],
        metadata_revision_id="metadata-fixture-recovery",
    )

    assert failed.state == "needs_action"
    assert len(failed.failures) == 1
    assert failed.failures[0].asset_id
    assert "corrupt branded output" in failed.failures[0].message
    peer_mtimes = {asset.path: Path(asset.path).stat().st_mtime_ns for asset in failed.assets}

    recovered = variants.render_media_revision(
        VIDEO_ID,
        5,
        brand_ids=["mock-brand"],
        metadata_revision_id="metadata-fixture-recovery",
    )

    assert recovered.state == "ready"
    assert len(recovered.assets) == 6
    assert recovered.failures == []
    assert calls == {
        "clean_whole": 1,
        "clean_vertical": 2,
        "branded": {"whole": 1, "part_1": 1, "part_2": 2},
    }
    assert peer_mtimes == {
        path: Path(path).stat().st_mtime_ns for path in peer_mtimes
    }
    assert (tmp_path / VIDEO_ID / "manifests/revision/5.json").is_file()


def test_two_brands_keep_separate_paths_and_signature_music(tmp_path, monkeypatch):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(render, "encoder", lambda: "libx264")
    chunks = [
        {
            "idx": 0,
            "name": "part_1",
            "start_s": 0.0,
            "end_s": 1.2,
            "hook": "Một lát cắt",
            "caption": "Phần 1",
        }
    ]
    monkeypatch.setattr(variants, "library_chunks", lambda _video_id: chunks)

    second_music = tmp_path / "music" / "second-signature.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1760:sample_rate=48000:duration=0.5",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(second_music),
        ],
        check=True,
        capture_output=True,
    )
    second_profile = json.loads(
        (tmp_path / "presets" / "brands" / "mock-brand.json").read_text(
            encoding="utf-8"
        )
    )
    second_profile["brand_id"] = "second-brand"
    second_profile["display_name"] = "Second Brand"
    second_profile["watermark"]["text"] = "SECOND MOCK"
    second_profile["signature_music"]["file"] = second_music.name
    (tmp_path / "presets" / "brands" / "second-brand.json").write_text(
        json.dumps(second_profile, ensure_ascii=False), encoding="utf-8"
    )

    media_manifest = variants.render_media_revision(
        VIDEO_ID,
        4,
        brand_ids=["mock-brand", "second-brand"],
        metadata_revision_id="metadata-fixture-2",
    )

    assert media_manifest.state == "ready"
    branded_verticals = {
        asset.brand_id: asset
        for asset in media_manifest.assets
        if asset.role == "branded_vertical"
    }
    assert set(branded_verticals) == {"mock-brand", "second-brand"}
    assert branded_verticals["mock-brand"].path != branded_verticals["second-brand"].path
    assert branded_verticals["mock-brand"].lineage_asset_id == branded_verticals[
        "second-brand"
    ].lineage_asset_id

    first_audio = _audio(Path(branded_verticals["mock-brand"].path))
    second_audio_data = _audio(Path(branded_verticals["second-brand"].path))
    count = min(first_audio.size, second_audio_data.size)
    difference_rms = np.sqrt(
        np.mean((first_audio[:count] - second_audio_data[:count]) ** 2)
    )
    assert difference_rms > 5
