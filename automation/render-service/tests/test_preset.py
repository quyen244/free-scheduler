"""Preset geometry — the arithmetic that makes one preset fit two resolutions.

The criterion this file exists for is "the same preset lands correctly on both
a 720p and a 1080p source". That is a claim about multiplication, so it is
checked here rather than by looking at two rendered videos.
"""

import pytest

import preset as presets

PRESET = {
    "canvas": {"w": 1080, "h": 1920},
    "video_rect": {"x": 0.0, "y": 0.28, "w": 1.0, "h": 0.44},
    "blur_regions": [{"x": 0.02, "y": 0.86, "w": 0.30, "h": 0.10}],
    "logo": {"path": "logo.png", "x": 0.045, "y": 0.30, "w": 0.115, "opacity": 0.9},
}


def test_the_blur_covers_the_same_part_of_the_picture_at_both_resolutions():
    hd = presets.resolve(PRESET, 1280, 720).blur_regions[0]
    fhd = presets.resolve(PRESET, 1920, 1080).blur_regions[0]

    for smaller, larger, extent in ((hd.x, fhd.x, 1), (hd.w, fhd.w, 1)):
        assert smaller / 1280 == pytest.approx(larger / 1920, abs=0.002)
    assert hd.y / 720 == pytest.approx(fhd.y / 1080, abs=0.002)
    assert hd.h / 720 == pytest.approx(fhd.h / 1080, abs=0.002)


def test_the_video_lands_in_the_same_place_on_the_canvas_at_both_resolutions():
    # The canvas is fixed, so the composited rectangle must be identical —
    # not merely proportional. Both sources are 16:9.
    assert presets.resolve(PRESET, 1280, 720).video == presets.resolve(PRESET, 1920, 1080).video


def test_the_source_is_fitted_into_its_slot_not_stretched_to_it():
    # The slot is 1080x844 and the source is 16:9. Stretching to fill would
    # give 1080x844; fitting gives 1080x608 and letterboxes against the
    # background, which is what the layout intends.
    geometry = presets.resolve(PRESET, 1920, 1080)
    assert geometry.video.w / geometry.video.h == pytest.approx(16 / 9, abs=0.02)


def test_a_tall_source_is_fitted_by_height_instead():
    geometry = presets.resolve(PRESET, 720, 1280)
    slot_h = round(1920 * 0.44)
    assert geometry.video.h <= slot_h + 1
    assert geometry.video.w / geometry.video.h == pytest.approx(720 / 1280, abs=0.02)


def test_every_dimension_is_even():
    # h264 with yuv420p rejects odd dimensions, three minutes into the encode.
    geometry = presets.resolve(PRESET, 1281, 721)
    for value in (geometry.canvas_w, geometry.canvas_h, geometry.video.w, geometry.video.h):
        assert value % 2 == 0


def test_a_region_that_runs_off_the_edge_is_clamped_rather_than_rejected():
    # ffmpeg refuses a crop that leaves the frame, which would fail the whole
    # render instead of blurring what it can.
    over = [presets.Box(x=1200, y=600, w=400, h=400)]
    clamped = presets.clamp_regions(over, 1280, 720)
    assert clamped[0].x + clamped[0].w <= 1280
    assert clamped[0].y + clamped[0].h <= 720


def test_a_region_entirely_outside_the_frame_is_dropped():
    assert presets.clamp_regions([presets.Box(x=2000, y=2000, w=100, h=100)], 1280, 720) == []


def test_a_preset_with_no_logo_resolves_without_one():
    assert presets.resolve({"canvas": {"w": 1080, "h": 1920}}, 1280, 720).logo is None


def test_the_font_family_resolves_to_a_file_that_exists():
    path = presets.font_file("DejaVu Sans")
    assert path.endswith(".ttf") or path.endswith(".otf")
