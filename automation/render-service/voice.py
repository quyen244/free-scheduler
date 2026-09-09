"""Vietnamese speech, one Whisper segment at a time.

ZeroTTS runs on ONNX Runtime with no torch, which is why it was picked over
XTTS-v2 on this box: it runs well on the CPU and it can take the GPU when
there is one. Which of the two it uses is `TTS_PROVIDER`, a deployment
decision - the CPU image ships only the CPU wheel, so it is the only answer
there.
"""

import logging
import queue
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

import numpy as np

import library
import timing
from config import settings
from errors import EmptyTranscriptError, SynthesisError, UnknownVoiceError

logger = logging.getLogger(__name__)

# The eight voices ZeroTTS 0.1.2 ships, verified against `tts.list_voices()`
# and re-checked at load time below. An enum rather than a free string because
# the voice encoder is still unpublished: the package can load voices but
# cannot make one from a reference clip, so anything outside this set is a typo
# and deserves a 400 rather than a failure 40 segments into a job.
SHIPPED_VOICES = (
    "baotrang",
    "giahuy",
    "hamy",
    "huuduc",
    "kimoanh",
    "maichi",
    "quangminh",
    "tiendat",
)

# A pool of ONNX sessions rather than one behind a lock.
#
# One session with four threads leaves seven of this box's twelve cores idle -
# measured, not guessed - and raising `intra_op_num_threads` makes the graph
# *slower*, so the only way to use those cores is more sessions. Sessions are
# built on demand and never torn down, so a service configured for one worker
# behaves exactly as it did before.
_sessions: list = []
_available: "queue.Queue" = queue.Queue()
# Lowered from `settings.tts_workers` when a session cannot be built, which on
# this box means it did not fit in memory.
_cap: int | None = None
_pool_lock = threading.RLock()

# A borrower always returns its session in a `finally`, so waiting forever
# would mean a worker thread died without unwinding. Bounded so that shows up
# as an error rather than as a job that never ends.
_BORROW_TIMEOUT_S = 3600.0


def is_loaded() -> bool:
    return bool(_sessions)


def reset_pool() -> None:
    """Drop every session. Tests only - a running service never needs this."""
    global _cap
    with _pool_lock:
        _sessions.clear()
        _cap = None
        while True:
            try:
                _available.get_nowait()
            except queue.Empty:
                break


def check_voice(voice: str) -> str:
    if voice not in SHIPPED_VOICES:
        raise UnknownVoiceError(
            f"unknown voice {voice!r}; ZeroTTS ships {', '.join(SHIPPED_VOICES)}"
        )
    return voice


def get_model():
    """One session, for callers that only need the model to exist."""
    with _pool_lock:
        if not _sessions:
            if not fill_pool(1):
                raise SynthesisError("no ZeroTTS session could be built")
        return _sessions[0]


def fill_pool(target: int) -> int:
    """Build sessions up to `target` and return how many the pool now has.

    Built up front rather than on first borrow. A borrower only grows the pool
    when it finds the queue empty, so on short segments the first session comes
    back before the second borrower ever looks - and a job asking for four
    workers would quietly run on one. Building here also pays the load cost
    once, before any work is timed, and surfaces a session that does not fit
    while there is still nothing to lose.

    Every session built here goes straight into the queue, because nobody is
    holding it. `_build_session` leaves that to the caller.
    """
    with _pool_lock:
        while len(_sessions) < target:
            session = _build_session(target)
            if session is None:
                break
            _available.put(session)
        return len(_sessions)


def _build_session(limit: int):
    """Add a session to the pool, or return None if it may not or cannot.

    Returns None rather than raising when the pool already has something to
    work with: a second session that will not fit is a reason to run slower,
    not a reason to lose a twenty-minute job. The first one is different - with
    no session at all there is nothing to fall back to.
    """
    global _cap
    with _pool_lock:
        # `_cap` is what this box turned out to allow, so it only ever lowers
        # the caller's request.
        if _cap is not None:
            limit = min(limit, _cap)
        if len(_sessions) >= max(1, limit):
            return None
        try:
            session = _load_model()
        except Exception as exc:  # noqa: BLE001 - any load failure is one failure
            if not _sessions:
                raise SynthesisError(f"could not load ZeroTTS: {exc}") from exc
            logger.warning(
                "could not build TTS session %d (%s); continuing with %d",
                len(_sessions) + 1, exc, len(_sessions),
            )
            _cap = len(_sessions)
            return None
        _sessions.append(session)
        return session


@contextmanager
def _borrow():
    """Take a session from the pool for the duration of one synthesis."""
    try:
        session = _available.get_nowait()
    except queue.Empty:
        # Not enqueued: this borrower is the one that takes it, and the
        # `finally` below is what puts it in the queue.
        session = _build_session(max(1, settings.tts_workers))
        if session is None:
            try:
                session = _available.get(timeout=_BORROW_TIMEOUT_S)
            except queue.Empty:
                raise SynthesisError(
                    "waited "
                    + str(int(_BORROW_TIMEOUT_S))
                    + "s for a free TTS session and none came back"
                ) from None
    try:
        yield session
    finally:
        _available.put(session)


# ONNX Runtime takes a priority list, not one name. CUDA keeps the CPU entry
# behind it on purpose: a handful of ZeroTTS operators have no CUDA kernel, and
# without a fallback the session refuses to build at all.
_PROVIDER_LISTS = {
    "cpu": ["CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
}


def _session_providers(model) -> set:
    """Which providers the sessions really got, not which were asked for.

    ZeroTTS holds several `InferenceSession`s (text encoder, prefix step, frame
    decode) rather than one, so this collects every provider in use across all
    of them. An empty set means introspection failed, which is not the same as
    "it is on the CPU" and is reported differently below.
    """
    got = set()
    for name in dir(model):
        try:
            inner = getattr(model, name)
        except Exception:  # noqa: BLE001 - a property that raises is not a session
            continue
        getter = getattr(inner, "get_providers", None)
        if not callable(getter):
            continue
        try:
            got.update(getter())
        except Exception:  # noqa: BLE001 - same
            continue
    return got


def _load_model():
    # Imported here rather than at module scope: onnxruntime plus the tokenizer
    # is seconds of import time that the health endpoint should not wait for.
    import zerotts

    provider = settings.tts_provider
    logger.info(
        "loading ZeroTTS from %s (%d threads, provider=%s)",
        settings.model_dir,
        settings.tts_threads,
        provider,
    )
    # HF_HOME is pointed at the bind-mounted cache in config.py, so this
    # resolves to the copy on disk rather than fetching 900 MB again.
    model_dir = zerotts.resolve_model_dir()
    model = zerotts.ZeroTTS(
        model_dir,
        providers=_PROVIDER_LISTS[provider],
        intra_op_num_threads=settings.tts_threads,
    )

    # The point of asking by name. ONNX Runtime accepts CUDA, finds no CUDA
    # libraries, and builds the session on the CPU without raising - so a GPU
    # deployment runs at CPU speed and nothing says so. Checked here, at pool
    # build time, because `_ensure_pool` builds up front: this fails while
    # there is still nothing to lose, not twenty minutes into a job.
    if provider == "cuda":
        in_use = _session_providers(model)
        if in_use and "CUDAExecutionProvider" not in in_use:
            raise SynthesisError(
                "TTS_PROVIDER=cuda but the sessions got "
                f"{sorted(in_use)}. This image has no usable CUDA runtime; "
                "run the CPU image instead of pretending this one is faster."
            )
        if not in_use:
            # Not fatal: a ZeroTTS release that renames its sessions would trip
            # this, and refusing to speak over it would be worse than the risk.
            logger.warning(
                "could not confirm the execution provider; the timings from "
                "this run are not evidence that the GPU was used"
            )
        else:
            logger.info("ZeroTTS sessions running on %s", sorted(in_use))

    shipped = tuple(sorted(model.list_voices()))
    if shipped != SHIPPED_VOICES:
        # Not fatal — a new voice is good news — but it means the constant this
        # service validates requests against no longer describes the model.
        logger.warning(
            "ZeroTTS ships %s; SHIPPED_VOICES says %s", shipped, SHIPPED_VOICES
        )
    return model


def normalise(text: str) -> str:
    """Expand dates, numbers and abbreviations into words.

    `synthesize()` does not do this. Skip it and `31/12/2026` is read as digits
    in a way you only catch by listening to the finished video.
    """
    import zerotts

    return zerotts.normalize_vi_text(text)


def _synthesise(text: str, voice: str) -> np.ndarray:
    try:
        with _borrow() as session:
            audio = session.synthesize(text, voice=voice)
    except SynthesisError:
        raise
    except Exception as exc:  # noqa: BLE001 — any model failure is one failure
        raise SynthesisError(f"the model failed on {text[:60]!r}: {exc}") from exc
    # ZeroTTS returns (1, samples). Taking `len()` of that is 1, which reads as
    # a successful synthesis of nothing.
    return np.asarray(audio).reshape(-1)


def build_track(
    video_id: str,
    voice: str,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, object]:
    """Speak every segment and lay the results on the source's own timeline."""
    check_voice(voice)

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
    workers: int | None = None,
) -> tuple[list[timing.Fit], list[tuple[float, np.ndarray]]]:
    """Speak every segment, in parallel when there is more than one worker.

    Segments are independent - each one is synthesised, fitted to its own slot
    and placed at its own start time - so the only thing concurrency can break
    is the order they come back in. Everything is therefore collected by index
    and reassembled in segment order, never in completion order.
    """
    if workers is None:
        workers = settings.tts_workers
    workers = max(1, int(workers))

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
    progress_lock = threading.Lock()

    def report() -> None:
        nonlocal done
        if on_progress is None:
            return
        # Counted, not positional. The old loop reported `(idx + 1) / total`,
        # which out of order would go backwards - and a progress bar that goes
        # backwards is worse than none.
        #
        # The callback is made inside the lock, not just the counting. Two
        # threads that compute 0.5 and 0.6 outside it can still deliver them in
        # the other order, which is the same bug one level down.
        with progress_lock:
            done += 1
            on_progress(done / len(segments))

    def speak_one(plan: tuple[int, float, float, str]):
        idx, start_s, end_s, text = plan
        audio = timing.trim_silence(_synthesise(normalise(text), voice))
        raw_s = audio.size / timing.SAMPLE_RATE
        fit = timing.plan_fit(
            idx, start_s, end_s, raw_s, settings.min_ratio, settings.max_ratio
        )
        return idx, fit, _fit_audio(audio, fit, workspace)

    if plans and workers > 1:
        # Never more threads than there are sessions to feed them. When only
        # one session could be built - the box is out of memory - this drops
        # back to the serial path below rather than starting three threads
        # that would only queue behind each other.
        wanted = min(workers, len(plans))
        workers = min(wanted, fill_pool(wanted))

    if not plans:
        pass
    elif workers == 1:
        # Kept as a plain loop rather than a one-worker pool: this is the path
        # the service ran on for every video before the pool existed, and it
        # should stay exactly as cheap and as easy to read.
        for plan in plans:
            idx, fit, audio = speak_one(plan)
            fits[idx] = fit
            spoken[idx] = audio
            report()
    else:
        pool = ThreadPoolExecutor(max_workers=min(workers, len(plans)),
                                  thread_name_prefix="tts")
        futures = {pool.submit(speak_one, plan): plan[0] for plan in plans}
        try:
            for future in as_completed(futures):
                idx, fit, audio = future.result()
                fits[idx] = fit
                spoken[idx] = audio
                report()
        finally:
            # Cancel what has not started before shutting down. Without this a
            # failure waits for every queued segment to be spoken first, which
            # on a long video is minutes of work nobody will use.
            for future in futures:
                future.cancel()
            pool.shutdown(wait=True)

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

    # Named by segment index, so parallel workers never share a file.
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
