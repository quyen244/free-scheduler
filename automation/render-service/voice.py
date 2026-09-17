"""Vietnamese speech, batched.

VieNeu-TTS v3 Turbo on CUDA. It replaced ZeroTTS on 2026-09-17 because the
voice stage was the longest in the pipeline and batching moves it by an order
of magnitude: on 64 real cues from this project's own transcript, ZeroTTS ran
at RTF 0.86x in its shipped configuration and VieNeu at 20.62x with
`batch_size=32`. Numbers in reports/vieneu-tts-gpu-benchmark-2026-09-17.md.

The shape of the work changed with it. ZeroTTS was one segment per call, so
concurrency meant a pool of ONNX sessions racing on the idle cores. VieNeu's
speed comes from the opposite direction: it flattens the chunks of many texts
into one forward pass, so this module hands it whole groups of segments and
keeps a single model. There is no session pool and no thread pool here any
more - `TTS_THREADS`, `TTS_WORKERS` and `TTS_PROVIDER` were ONNX knobs and are
gone.

Text normalisation is also the model's job now. ZeroTTS needed
`normalize_vi_text` called by hand or it read `31/12/2026` as digits; VieNeu
delegates to sea-g2p internally, so there is no separate normalise step to
forget.
"""

import json
import logging
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

import numpy as np

import library
import timing
from config import settings
from errors import EmptyTranscriptError, SynthesisError, UnknownVoiceError

logger = logging.getLogger(__name__)

# One model, not a pool. Loading is expensive (~25 s warm) and a second copy
# would buy nothing: the batch already fills the card.
_model = None
_model_lock = threading.RLock()

# Cached preset table, read from the installed package rather than from a
# loaded model - see `_presets`.
_preset_cache: tuple[dict[str, str], str] | None = None


def is_loaded() -> bool:
    return _model is not None


def reset_model() -> None:
    """Drop the model. Tests only - a running service never needs this."""
    global _model
    with _model_lock:
        _model = None


def _presets() -> tuple[dict[str, str], str]:
    """Every accepted voice name mapped to its canonical preset, and the default.

    Read straight from the package's `voices_v3_turbo.json` rather than from a
    loaded model, so a typo in a voice name is rejected in milliseconds instead
    of after the twenty-five seconds it takes to bring the weights up.
    """
    global _preset_cache
    if _preset_cache is not None:
        return _preset_cache

    import vieneu

    path = Path(vieneu.__file__).parent / "assets" / "voices_v3_turbo.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SynthesisError(f"could not read VieNeu's voice list at {path}: {exc}") from exc

    presets = data.get("presets") or {}
    if not presets:
        raise SynthesisError(f"VieNeu's voice list at {path} contains no presets")

    # A real preset name always wins over an alias pointing elsewhere, so the
    # canonical names go in first and aliases only fill gaps.
    resolved = {name: name for name in presets}
    for name, entry in presets.items():
        for alias in entry.get("aliases") or []:
            resolved.setdefault(alias, name)

    _preset_cache = (resolved, data.get("default_voice") or "")
    return _preset_cache


def available_voices() -> tuple[str, ...]:
    """The canonical preset names, sorted."""
    resolved, _ = _presets()
    return tuple(sorted(set(resolved.values())))


def check_voice(voice: str) -> str:
    """Resolve a preset name or alias, or say what the real ones are.

    Returns the canonical preset rather than what was asked for: `Minh Quân` is
    an alias of `Minh Quân Pro`, and the manifest should record the name that
    reproduces the track even if the alias is later repointed.
    """
    resolved, _ = _presets()
    canonical = resolved.get(voice)
    if canonical is None:
        raise UnknownVoiceError(
            f"unknown voice {voice!r}; VieNeu ships {', '.join(available_voices())}"
        )
    return canonical


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            _model = _load_model()
        return _model


def _load_model():
    # Imported here rather than at module scope: VieNeu pulls in torch, which
    # is seconds of import time the health endpoint should not wait for.
    from vieneu import Vieneu

    device = settings.tts_device
    if device == "cuda":
        import torch

        # Asked for by name, then checked. A container with no usable GPU would
        # otherwise run the whole stage on the CPU and report a plausible
        # number under a GPU heading - the same trap the old ONNX provider
        # check existed to close.
        if not torch.cuda.is_available():
            raise SynthesisError(
                "TTS_DEVICE=cuda but torch sees no CUDA device. This container "
                "has no usable GPU runtime; run it with TTS_DEVICE=cpu rather "
                "than pretending this one is faster."
            )

    logger.info(
        "loading VieNeu v3 Turbo on %s (batch_size=%d)", device, settings.tts_batch_size
    )
    try:
        model = Vieneu(mode="v3turbo", device=device)
    except Exception as exc:  # noqa: BLE001 - any load failure is one failure
        raise SynthesisError(f"could not load VieNeu: {exc}") from exc

    if device == "cuda":
        import torch

        logger.info("VieNeu running on %s", torch.cuda.get_device_name(0))

    rate = int(getattr(model, "sample_rate", timing.SAMPLE_RATE))
    if rate != timing.SAMPLE_RATE:
        # Everything downstream - slot fitting, atempo, assembly - is written
        # in samples at one rate. A model at a different rate would place every
        # segment at the wrong time rather than merely sound wrong.
        raise SynthesisError(
            f"VieNeu returns {rate} Hz but this service assembles at "
            f"{timing.SAMPLE_RATE} Hz"
        )
    return model


def synthesise(texts: list[str], voice: str) -> list[np.ndarray]:
    """Speak several texts in one batched pass, in input order.

    `infer_batch` returns one waveform per input text, whatever the internal
    chunking did, which is what lets the per-segment timing model survive
    batching unchanged.
    """
    if not texts:
        return []
    model = get_model()
    try:
        wavs = model.infer_batch(texts, voice=voice, batch_size=settings.tts_batch_size)
    except Exception as exc:  # noqa: BLE001 - any model failure is one failure
        raise SynthesisError(
            f"the model failed on a batch of {len(texts)} "
            f"starting {texts[0][:60]!r}: {exc}"
        ) from exc

    if len(wavs) != len(texts):
        # Padding this to length would put silence where speech belongs and
        # leave the track looking complete. Better to fail the job.
        raise SynthesisError(
            f"asked VieNeu for {len(texts)} waveforms and got {len(wavs)}"
        )
    return [np.asarray(wav).reshape(-1) for wav in wavs]


def build_track(
    video_id: str,
    voice: str,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, object]:
    """Speak every segment and lay the results on the source's own timeline."""
    voice = check_voice(voice)

    transcript = library.load_transcript(video_id)
    segments = transcript.get("segments") or []
    if not segments:
        raise EmptyTranscriptError(f"nothing to speak for {video_id!r}")

    total_s = float(transcript.get("duration_s") or 0.0)
    total_s = max(total_s, float(segments[-1]["end"]))

    workspace = Path(tempfile.mkdtemp(prefix=f"voice-{video_id}-"))
    try:
        fits, pieces = speak_segments(segments, voice, workspace, on_progress)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    track = timing.assemble(pieces, total_s)
    path = library.voice_path(video_id)
    timing.write_wav(path, track)

    warnings = [fit.warning for fit in fits if fit.warning]
    manifest = {
        "video_id": video_id,
        "voice": voice,
        "sample_rate": timing.SAMPLE_RATE,
        "duration_s": round(track.size / timing.SAMPLE_RATE, 3),
        "total_segments": len(fits),
        "warnings": warnings,
        "segments": [
            {
                "idx": fit.idx,
                "start": round(fit.start_s, 3),
                "end": round(fit.end_s, 3),
                "slot_s": round(fit.slot_s, 3),
                "spoken_s": round(fit.raw_s, 3),
                "ratio": round(fit.ratio, 3),
                "atempo": round(fit.atempo, 3),
                "warning": fit.warning,
            }
            for fit in fits
        ],
    }
    library.save_voice_manifest(video_id, manifest)
    return manifest


def speak_segments(
    segments: list[dict],
    voice: str,
    workspace: Path,
    on_progress: Callable[[float], None] | None = None,
    group_size: int | None = None,
) -> tuple[list[timing.Fit], list[tuple[float, np.ndarray]]]:
    """Speak every segment in batched groups, reassembled in segment order.

    Segments go to the model in groups rather than all at once. Two batch sizes
    are in play and they are not the same thing: `batch_size` is how many
    *chunks* share a forward pass, which is what the speed came from, while
    `group_size` is how many *segments* one call covers. Grouping bounds how
    much decoded audio is held at once and is the only place a progress report
    can happen, since a single call over a whole video would sit silent for
    minutes. Groups of twice the batch size are the shape the benchmark
    measured.
    """
    if group_size is None:
        group_size = settings.tts_batch_size * 2
    group_size = max(1, int(group_size))

    fits: dict[int, timing.Fit] = {}
    spoken: dict[int, np.ndarray] = {}
    plans: list[tuple[int, float, float, str]] = []

    for idx, segment in enumerate(segments):
        try:
            start_s = float(segment["start"])
            end_s = float(segment["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SynthesisError(f"segment {idx} has no usable timing: {exc}") from exc
        text = str(segment.get("text") or "").strip()

        if not text:
            # Whisper emits these for music and applause. Silence is the right
            # output; the gap is still reported so it is not a mystery later.
            fits[idx] = timing.Fit(
                idx=idx, start_s=start_s, end_s=end_s, slot_s=end_s - start_s,
                raw_s=0.0, ratio=0.0, atempo=1.0,
                warning=f"segment {idx} at {start_s:.1f}s has no text to speak",
            )
            continue
        plans.append((idx, start_s, end_s, text))

    done = len(fits)
    if on_progress is not None and done:
        on_progress(done / len(segments))

    for start in range(0, len(plans), group_size):
        group = plans[start:start + group_size]
        audios = synthesise([plan[3] for plan in group], voice)

        for (idx, start_s, end_s, _text), audio in zip(group, audios):
            audio = timing.trim_silence(audio)
            raw_s = audio.size / timing.SAMPLE_RATE
            fit = timing.plan_fit(
                idx, start_s, end_s, raw_s, settings.min_ratio, settings.max_ratio
            )
            fits[idx] = fit
            spoken[idx] = _fit_audio(audio, fit, workspace)
            done += 1

        if on_progress is not None:
            on_progress(done / len(segments))

    if len(fits) != len(segments):
        missing = sorted(set(range(len(segments))) - set(fits))
        raise SynthesisError(f"segments {missing} produced no audio and no error")

    ordered = [fits[idx] for idx in range(len(segments))]
    pieces = [(fits[idx].start_s, spoken[idx]) for idx in sorted(spoken)]
    return ordered, pieces


def _fit_audio(spoken: np.ndarray, fit: timing.Fit, workspace: Path) -> np.ndarray:
    """Apply the planned rate, through ffmpeg so the pitch survives it.

    Resampling in numpy would be one line and would raise the pitch with the
    speed — a chipmunk on every dense segment. `atempo` is a proper
    time-stretch, which is why this is worth a subprocess per segment.
    """
    pcm = timing.to_pcm16(spoken)
    if fit.atempo <= 1.0:
        return pcm

    # Named by segment index, so nothing is shared between groups.
    source = workspace / f"{fit.idx:04d}.wav"
    stretched = workspace / f"{fit.idx:04d}.fit.wav"
    try:
        timing.write_wav(source, pcm)
        timing.stretch(source, stretched, fit.atempo)
        return timing.read_wav(stretched)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()[-300:]
        raise SynthesisError(
            f"could not fit segment {fit.idx} to {fit.atempo:.2f}x: {detail}"
        ) from exc
    except OSError as exc:
        raise SynthesisError(
            f"could not write the working audio for segment {fit.idx}: {exc}"
        ) from exc
    finally:
        # Freed as they are consumed rather than at the end of the job: a long
        # video is thousands of these, and the workspace is a container's disk.
        source.unlink(missing_ok=True)
        stretched.unlink(missing_ok=True)
