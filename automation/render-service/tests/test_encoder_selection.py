"""The encoder is chosen by a real encode, and a CPU fallback is never silent.

Two independent faults produced the same symptom on this host: the container
was missing `libnvidia-encode.so.1` because the NVIDIA driver capabilities
omitted `video`, and the probe canvas was 64x64, which NVENC rejects outright.
Either one alone drops every render to `libx264`, so both are pinned here.
"""

import subprocess

import pytest

import render


pytestmark = pytest.mark.no_pipeline


@pytest.fixture(autouse=True)
def _forget_cached_choice():
    """The choice is cached for the process; each case needs its own answer."""
    render.encoder_choice.cache_clear()
    yield
    render.encoder_choice.cache_clear()


def _fail(stderr: str):
    def run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr=stderr)

    return run


class TestProbeShape:
    def test_probe_canvas_clears_the_nvenc_minimum_frame_size(self):
        """A 64x64 probe fails on a working GPU and hides the hardware path."""
        width, height = (int(part) for part in render._PROBE_SIZE.split("x"))
        assert width >= 145 and height >= 49

    def test_probe_uses_the_same_encoder_arguments_production_uses(self, monkeypatch):
        """An argument the GPU rejects must surface in the probe, not mid-render."""
        seen = []
        monkeypatch.setattr(
            render.subprocess,
            "run",
            lambda command, **kwargs: seen.append(command) or subprocess.CompletedProcess(command, 0),
        )
        render.encoder_choice()

        probe_command = seen[0]
        assert render._PROBE_SIZE in " ".join(probe_command)
        for argument in render._encoder_args(render.PREFERRED_ENCODER, {}):
            assert argument in probe_command


class TestSelection:
    def test_a_successful_probe_selects_hardware_with_no_fallback_reason(self, monkeypatch):
        monkeypatch.setattr(
            render.subprocess,
            "run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
        )
        choice = render.encoder_choice()

        assert choice.selected == render.PREFERRED_ENCODER
        assert choice.requested == render.PREFERRED_ENCODER
        assert choice.is_hardware is True
        assert choice.fallback_reason is None
        assert render.encoder() == render.PREFERRED_ENCODER

    def test_a_failed_probe_falls_back_to_cpu_and_keeps_the_reason(self, monkeypatch):
        monkeypatch.setattr(
            render.subprocess,
            "run",
            _fail("[h264_nvenc @ 0x1] Cannot load libnvidia-encode.so.1"),
        )
        choice = render.encoder_choice()

        assert choice.selected == render.FALLBACK_ENCODER
        assert choice.requested == render.PREFERRED_ENCODER
        assert choice.is_hardware is False
        assert choice.fallback_reason is not None
        assert "libnvidia-encode" in choice.fallback_reason

    def test_a_missing_ffmpeg_binary_is_reported_rather_than_raised(self, monkeypatch):
        def run(command, **kwargs):
            raise FileNotFoundError("ffmpeg")

        monkeypatch.setattr(render.subprocess, "run", run)
        choice = render.encoder_choice()

        assert choice.selected == render.FALLBACK_ENCODER
        assert "not on PATH" in choice.fallback_reason

    def test_encode_arguments_follow_the_selected_encoder(self, monkeypatch):
        monkeypatch.setattr(
            render.subprocess, "run", _fail("Cannot load libnvidia-encode.so.1")
        )
        assert render._encode_args({"crf": 19})[:2] == ["-c:v", render.FALLBACK_ENCODER]
        assert "19" in render._encode_args({"crf": 19})

        render.encoder_choice.cache_clear()
        monkeypatch.setattr(
            render.subprocess,
            "run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
        )
        assert render._encode_args({"crf": 19})[:2] == ["-c:v", render.PREFERRED_ENCODER]


class TestFallbackReasonIsTyped:
    @pytest.mark.parametrize(
        "stderr, code",
        [
            ("Cannot load libnvidia-encode.so.1", "driver_encode_library_missing"),
            (
                "InitializeEncoder failed: invalid param (8): Frame Dimension less "
                "than the minimum supported value.",
                "probe_dimensions_rejected",
            ),
            ("Unknown encoder 'h264_nvenc'", "encoder_not_built_into_ffmpeg"),
            ("No capable devices found", "no_gpu_visible_to_container"),
            ("OpenEncodeSessionEx failed: out of memory", "gpu_out_of_memory"),
            ("something nobody has classified yet", "hardware_encoder_open_failed"),
        ],
    )
    def test_each_distinct_cause_gets_its_own_code(self, stderr, code):
        """Each class points at a different fix, so they must stay separable."""
        assert render._fallback_reason(stderr).startswith(code)

    def test_the_reason_keeps_the_ffmpeg_text_for_diagnosis(self):
        reason = render._fallback_reason("Cannot load libnvidia-encode.so.1")
        assert "libnvidia-encode.so.1" in reason

    def test_an_empty_stderr_still_yields_a_usable_code(self):
        assert render._fallback_reason("") == "hardware_encoder_open_failed"


class TestProvenanceIsExposed:
    def test_the_choice_serialises_for_the_job_result_and_health_payload(self, monkeypatch):
        monkeypatch.setattr(
            render.subprocess, "run", _fail("Cannot load libnvidia-encode.so.1")
        )
        payload = render.encoder_choice().as_dict()

        assert payload["requested_encoder"] == render.PREFERRED_ENCODER
        assert payload["selected_encoder"] == render.FALLBACK_ENCODER
        assert payload["hardware"] is False
        assert "libnvidia-encode" in payload["fallback_reason"]

    def test_a_hardware_run_reports_no_fallback(self, monkeypatch):
        monkeypatch.setattr(
            render.subprocess,
            "run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
        )
        payload = render.encoder_choice().as_dict()

        assert payload["hardware"] is True
        assert payload["fallback_reason"] is None


class TestEncodeLogging:
    def test_a_finished_encode_logs_stage_duration_encoder_and_speed(self, monkeypatch, caplog):
        monkeypatch.setattr(
            render.subprocess,
            "run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
        )
        with caplog.at_level("INFO", logger="render"):
            render._run_encode(
                ["ffmpeg", "-version"],
                stage="clean_whole",
                content_item_id="whole",
                media_duration_s=19.0,
            )

        done = [r.getMessage() for r in caplog.records if "encode done" in r.getMessage()]
        assert done, caplog.text
        assert "stage=clean_whole" in done[0]
        assert "elapsed_s=" in done[0] and "speed_x=" in done[0]
        assert f"selected_encoder={render.encoder()}" in done[0]

    def test_a_failed_encode_logs_the_stage_before_reraising(self, monkeypatch, caplog):
        monkeypatch.setattr(render.subprocess, "run", _fail("boom"))
        with caplog.at_level("INFO", logger="render"):
            with pytest.raises(subprocess.CalledProcessError):
                render._run_encode(
                    ["ffmpeg", "-version"],
                    stage="branded_vertical",
                    content_item_id="part_1",
                    media_duration_s=5.0,
                )

        assert any(
            "encode failed" in r.getMessage() and "stage=branded_vertical" in r.getMessage()
            for r in caplog.records
        ), caplog.text
