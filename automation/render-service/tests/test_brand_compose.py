"""A brand's layer list has to survive the trip into a filtergraph.

`brands.render_config` is checked elsewhere as arithmetic. What is checked here
is the next step: that the stills a brand placed become real ffmpeg inputs, in
the order the editor showed them, and that a layout which placed no subtitle
does not get one anyway. Strings only — no encode, so this stays fast.
"""

import pytest

import preset as presets
import render

pytestmark = pytest.mark.no_pipeline

SOURCE = render.Source(width=1920, height=1080, duration_s=4.0)


def _config(**overrides):
    config = {
        "canvas": {"w": 1080, "h": 1920},
        "video_rect": {"x": 0.0, "y": 0.28, "w": 1.0, "h": 0.44},
        "video_z": 10,
        "video_visible": True,
        "blur_regions": [],
        "images": [],
        "host": None,
        "text_layers": [],
        "subtitle": None,
    }
    config.update(overrides)
    return config


def _compose(config, directory):
    geometry = presets.resolve(config, SOURCE.width, SOURCE.height)
    return render.compose_clean(
        config, geometry, SOURCE, directory, "check", "check.ass", 4.0
    )


def _image(layer_id, z, **overrides):
    image = {
        "id": layer_id,
        "path": f"/data/presets/brand/an-so/assets/{layer_id}.png",
        "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0,
        "opacity": 1.0, "fit": "contain", "z": z,
    }
    image.update(overrides)
    return image


def test_each_image_layer_becomes_its_own_input(tmp_path):
    config = _config(images=[_image("bg", 0, fit="fill"), _image("logo", 30)])
    graph, extra_inputs, _ = _compose(config, tmp_path)

    assert extra_inputs.count("-i") == 2
    assert extra_inputs[extra_inputs.index("-i") + 1].endswith("bg.png")
    # Inputs 0-2 are fixed (source, canvas, voice), so a brand's own stills
    # start at 3 and the graph must address them by that number.
    assert "[3:v]" in graph and "[4:v]" in graph


def test_a_fill_image_is_stretched_and_a_contain_image_is_not(tmp_path):
    config = _config(images=[_image("bg", 0, fit="fill"), _image("logo", 30, w=0.2, h=0.1)])
    graph, _, _ = _compose(config, tmp_path)

    assert "[3:v]scale=1080:1920,setsar=1,format=rgba" in graph
    assert "[4:v]scale=216:192:force_original_aspect_ratio=decrease" in graph
    # Fitted images are centred in their rectangle rather than pinned to its
    # corner, so a logo keeps its own shape inside whatever box was dragged.
    assert "overlay=0+(216-w)/2:0+(192-h)/2" in graph


def test_opacity_reaches_the_filtergraph(tmp_path):
    graph, _, _ = _compose(_config(images=[_image("wm", 40, opacity=0.35)]), tmp_path)
    assert "colorchannelmixer=aa=0.35" in graph


def test_layers_composite_in_ascending_z(tmp_path):
    """The stack in the editor and the stack ffmpeg builds are one fact."""
    config = _config(
        images=[_image("over", 99, w=0.2, h=0.1), _image("under", 0, fit="fill")]
    )
    graph, _, _ = _compose(config, tmp_path)

    # under(0) -> main video(10) -> over(99). Only the compositing steps carry
    # the drawing order; the scale steps that define each label come first and
    # in whatever order the inputs were appended, which is not the same thing.
    composited = [step for step in graph.split(";") if step.startswith(("[bg]", "[img", "[main"))]
    drawn = [step for step in composited if "overlay" in step]
    assert [step.split("[")[2].split("]")[0] for step in drawn] == ["img4", "vid", "img3"], graph


def test_a_layout_with_no_subtitle_layer_gets_no_subtitle_filter(tmp_path):
    """A brand that never placed a subtitle must not have one burned in.

    `render_config` sends `subtitle: None`, and an empty dict defaulting to
    "visible" would put burned-in text into a layout nobody asked for it in.
    """
    graph, _, _ = _compose(_config(images=[_image("bg", 0, fit="fill")]), tmp_path)
    assert "ass=" not in graph


def test_a_subtitle_layer_still_gets_one(tmp_path):
    config = _config(
        images=[_image("bg", 0, fit="fill")],
        subtitle={"font": "DejaVu Sans", "y": 0.755, "size": 46, "z": 60},
    )
    graph, _, _ = _compose(config, tmp_path)
    assert "ass=check.ass" in graph


def test_a_brand_host_uses_the_files_in_its_own_folder(tmp_path):
    """No preset id and no shared asset directory: the pair is already resolved."""
    video = tmp_path / "host.mp4"
    alpha = tmp_path / "host-alpha.mp4"
    video.write_bytes(b"x")
    alpha.write_bytes(b"x")

    config = _config(
        host={
            "id": "host",
            "path": str(video),
            "alpha_path": str(alpha),
            "rect": {"x": 0.06, "y": 0.43, "w": 0.38, "h": 0.46},
            "z": 20,
        }
    )
    graph, extra_inputs, _ = _compose(config, tmp_path)

    assert str(video) in extra_inputs and str(alpha) in extra_inputs
    assert "alphamerge[hostrgba]" in graph


def test_a_brand_host_without_its_mask_on_disk_is_refused(tmp_path):
    video = tmp_path / "host.mp4"
    video.write_bytes(b"x")
    config = _config(
        host={
            "id": "host",
            "path": str(video),
            "alpha_path": str(tmp_path / "gone.mp4"),
            "rect": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
            "z": 20,
        }
    )
    with pytest.raises(render.RenderError, match="alpha mask is missing"):
        _compose(config, tmp_path)


def test_a_layout_that_draws_nothing_says_so(tmp_path):
    _, _, warnings = _compose(_config(video_visible=False), tmp_path)
    assert any("no visible layer" in warning for warning in warnings)
