"""Generated canvas frames must not increase the source cadence."""

import render
import preset
import pytest


pytestmark = pytest.mark.no_pipeline


def test_black_canvas_uses_the_source_frame_rate():
    geometry = preset.Geometry(canvas_w=1080, canvas_h=1920, video=None, blur_regions=[])
    args = render._background_args({}, geometry, 20.0, "24000/1001")

    assert args[-1].endswith(":r=24000/1001")


def test_invalid_source_rate_falls_back_to_a_safe_canvas_rate():
    geometry = preset.Geometry(canvas_w=1920, canvas_h=1080, video=None, blur_regions=[])

    assert render._background_args({}, geometry, 1.0, "0/0")[-1].endswith(":r=30")
    assert render._valid_frame_rate("not-a-rate") == "30"
