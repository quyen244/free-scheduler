"""Independent delivery assets encode side by side without losing order.

One ffmpeg render keeps about 2.5 of this box's 12 logical cores busy: its
filter graph is a chain, and `libass`, `drawtext`, `alphamerge` and `overlay`
do not slice across cores. The assets of a revision are separate files with no
data dependency, so the idle cores are reachable by running several at once.

Concurrency is only worth having when it changes nothing observable, so the
properties the serial loop gave for free are pinned here: manifest order
follows build order rather than completion order, one failing asset does not
take its neighbours down, and progress still ends at 1.0.
"""

import dataclasses
import threading
import time

import pytest

import manifest
import variants
from errors import RenderError

pytestmark = pytest.mark.no_pipeline

VIDEO_ID = "hS3VXBeEv0I"
BRAND_ID = "an-so"


def _asset(part: int) -> manifest.MediaAsset:
    """A stand-in for a rendered file. Only its identity matters here."""
    return manifest.MediaAsset(
        asset_id=f"{part:032x}",
        video_id=VIDEO_ID,
        render_revision=1,
        role="branded_vertical",
        content_item_id=f"part_{part}",
        brand_id=BRAND_ID,
        path=f"/data/{VIDEO_ID}/part_{part}.mp4",
        sha256=f"{part:064x}",
        bytes=1,
        probe=manifest.ProbeEvidence(
            duration_s=1.0,
            width=1080,
            height=1920,
            video_codec="h264",
            audio_codec="aac",
        ),
    )


def _build(part: int, *, delay: float = 0.0, fail: Exception | None = None):
    def run() -> manifest.MediaAsset:
        if delay:
            time.sleep(delay)
        if fail is not None:
            raise fail
        return _asset(part)

    return ("branded_vertical", f"part_{part}", BRAND_ID, run)


def _use_workers(monkeypatch, count: int) -> None:
    """`Settings` is a frozen dataclass, so swap the whole object."""
    monkeypatch.setattr(
        variants, "settings", dataclasses.replace(variants.settings, render_workers=count)
    )


def _parts(assets: list[manifest.MediaAsset]) -> list[str]:
    return [asset.content_item_id for asset in assets]


@pytest.fixture(autouse=True)
def _no_encoder_probe(monkeypatch):
    """The real probe is a real NVENC encode. These tests only count calls."""
    monkeypatch.setattr(variants.render, "encoder_choice", lambda: None)


class TestOrdering:
    def test_manifest_order_follows_build_order_not_completion_order(self, monkeypatch):
        """The slowest asset is built first; it must still be listed first."""
        _use_workers(monkeypatch, 4)
        builds = [_build(1, delay=0.20), _build(2, delay=0.10), _build(3)]

        assets, failures = variants._render_in_parallel(builds, None, VIDEO_ID, 1)

        assert _parts(assets) == ["part_1", "part_2", "part_3"]
        assert failures == []

    def test_failures_keep_build_order_too(self, monkeypatch):
        _use_workers(monkeypatch, 4)
        builds = [
            _build(1, delay=0.20, fail=RenderError("first")),
            _build(2, fail=RenderError("second")),
        ]

        _, failures = variants._render_in_parallel(builds, None, VIDEO_ID, 1)

        assert [failure.message for failure in failures] == [
            "RenderError: first",
            "RenderError: second",
        ]


class TestFailureIsolation:
    def test_one_failed_asset_does_not_cancel_the_others(self, monkeypatch):
        """A partial revision is the existing contract: assets plus failures."""
        _use_workers(monkeypatch, 4)
        builds = [_build(1), _build(2, fail=RenderError("boom")), _build(3)]

        assets, failures = variants._render_in_parallel(builds, None, VIDEO_ID, 1)

        assert _parts(assets) == ["part_1", "part_3"]
        assert len(failures) == 1
        assert failures[0].code == "branded_vertical_failed"
        assert failures[0].retryable is True

    def test_a_non_render_exception_is_recorded_as_not_retryable(self, monkeypatch):
        _use_workers(monkeypatch, 2)

        _, failures = variants._render_in_parallel(
            [_build(1, fail=ValueError("bad plan"))], None, VIDEO_ID, 1
        )

        assert failures[0].retryable is False
        assert failures[0].message == "ValueError: bad plan"

    def test_a_failed_asset_is_identified_by_its_deterministic_id(self, monkeypatch):
        """Retrying must target the same asset the serial path would have."""
        _use_workers(monkeypatch, 2)

        _, failures = variants._render_in_parallel(
            [_build(2, fail=RenderError("boom"))], None, VIDEO_ID, 7
        )

        assert failures[0].asset_id == manifest.deterministic_asset_id(
            VIDEO_ID, 7, "branded_vertical", "part_2", BRAND_ID
        )


class TestConcurrency:
    def test_workers_actually_overlap(self, monkeypatch):
        """Without overlap this is the old serial loop with extra machinery."""
        _use_workers(monkeypatch, 3)
        live = 0
        peak = 0
        lock = threading.Lock()

        def counting(part: int):
            def build() -> manifest.MediaAsset:
                nonlocal live, peak
                with lock:
                    live += 1
                    peak = max(peak, live)
                time.sleep(0.15)
                with lock:
                    live -= 1
                return _asset(part)

            return ("branded_vertical", f"part_{part}", BRAND_ID, build)

        variants._render_in_parallel(
            [counting(part) for part in (1, 2, 3)], None, VIDEO_ID, 1
        )

        assert peak == 3

    def test_worker_count_is_a_cap_not_a_target(self, monkeypatch):
        """Two assets on a three-worker box must not start a third thread."""
        _use_workers(monkeypatch, 3)
        threads: set[int] = set()

        def recording(part: int):
            def build() -> manifest.MediaAsset:
                threads.add(threading.get_ident())
                time.sleep(0.05)
                return _asset(part)

            return ("branded_vertical", f"part_{part}", BRAND_ID, build)

        variants._render_in_parallel(
            [recording(1), recording(2)], None, VIDEO_ID, 1
        )

        assert len(threads) <= 2

    def test_a_single_worker_stays_on_the_calling_thread(self, monkeypatch):
        """RENDER_WORKERS=1 must behave exactly like the previous serial loop."""
        _use_workers(monkeypatch, 1)
        seen: list[int] = []

        def recording(part: int):
            def build() -> manifest.MediaAsset:
                seen.append(threading.get_ident())
                return _asset(part)

            return ("branded_vertical", f"part_{part}", BRAND_ID, build)

        variants._render_in_parallel(
            [recording(1), recording(2)], None, VIDEO_ID, 1
        )

        assert seen == [threading.get_ident(), threading.get_ident()]


class TestEncoderProbe:
    def test_the_encoder_is_probed_once_before_the_workers_start(self, monkeypatch):
        """The probe is a real encode. Racing it in N threads probes N times."""
        _use_workers(monkeypatch, 4)
        probes = 0

        def probe():
            nonlocal probes
            probes += 1

        monkeypatch.setattr(variants.render, "encoder_choice", probe)

        variants._render_in_parallel(
            [_build(part) for part in (1, 2, 3, 4)], None, VIDEO_ID, 1
        )

        assert probes == 1


class TestProgress:
    def test_progress_reports_each_step_once_and_ends_at_one(self, monkeypatch):
        _use_workers(monkeypatch, 3)
        seen: list[float] = []
        lock = threading.Lock()

        def on_progress(fraction: float) -> None:
            with lock:
                seen.append(fraction)

        builds = [_build(1, delay=0.05), _build(2), _build(3)]
        variants._render_in_parallel(builds, on_progress, VIDEO_ID, 1)

        assert sorted(seen) == [1 / 3, 2 / 3, 1.0]

    def test_a_failed_asset_still_advances_progress(self, monkeypatch):
        """A revision that ends in failures must not leave the bar stuck."""
        _use_workers(monkeypatch, 2)
        seen: list[float] = []

        variants._render_in_parallel(
            [_build(1, fail=RenderError("boom")), _build(2)], seen.append, VIDEO_ID, 1
        )

        assert max(seen) == 1.0


class TestEmptyInput:
    def test_no_builds_produces_no_assets_and_no_division_by_zero(self, monkeypatch):
        _use_workers(monkeypatch, 3)

        assets, failures = variants._render_in_parallel([], lambda _: None, VIDEO_ID, 1)

        assert assets == [] and failures == []
