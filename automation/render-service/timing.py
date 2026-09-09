"""Fitting synthesised speech into the slots Whisper measured.

No model here on purpose. Every decision about where a segment lands is
arithmetic, and arithmetic can be tested in milliseconds instead of behind a
900 MB ONNX graph — which matters, because this is the file where a mistake
becomes a voice track that drifts away from the picture.
"""

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ZeroTTS emits 48 kHz. Kept as the track's rate rather than downsampled: the
# only consumer is ffmpeg, which resamples for the AAC encode anyway, and a
# resample here would be a lossy step taken for nothing.
SAMPLE_RATE = 48000

# ffmpeg's atempo accepts 0.5-2.0 per instance. Anything faster is a chain.
_ATEMPO_MAX = 2.0


@dataclass(frozen=True)
class Fit:
    """What happened to one segment on its way into the track."""

    idx: int
    start_s: float
    end_s: float
    slot_s: float
    # As synthesised, before any fitting.
    raw_s: float
    # raw_s / slot_s — the rate the segment *needs*. The warning is keyed to
    # this, not to `atempo`, so a segment that was padded rather than slowed is
    # still reported when it barely covers its slot.
    ratio: float
    atempo: float
    warning: str | None


def plan_fit(
    idx: int,
    start_s: float,
    end_s: float,
    raw_s: float,
    min_ratio: float,
    max_ratio: float,
) -> Fit:
    """Decide how one segment is made to occupy `start_s`-`end_s`."""
    slot_s = end_s - start_s
    if slot_s <= 0:
        raise ValueError(f"segment {idx} has no duration: {start_s} to {end_s}")

    ratio = raw_s / slot_s

    # Speech shorter than its slot is padded with silence, never slowed down.
    # An `atempo` below 1.0 stretches every vowel to fill time the speaker did
    # not use, which sounds broken; a pause sounds like a pause. Timing is
    # identical either way, so the choice costs nothing.
    atempo = ratio if ratio > 1.0 else 1.0

    warning = None
    if ratio > max_ratio:
        warning = (
            f"segment {idx} at {start_s:.1f}s needs {ratio:.2f}x to fit "
            f"{slot_s:.1f}s — above the {max_ratio:.2f}x limit, so it will "
            f"sound rushed"
        )
    elif ratio < min_ratio:
        warning = (
            f"segment {idx} at {start_s:.1f}s fills only {ratio:.0%} of its "
            f"{slot_s:.1f}s slot — a long pause, so check the translation did "
            f"not drop anything"
        )

    return Fit(
        idx=idx,
        start_s=start_s,
        end_s=end_s,
        slot_s=slot_s,
        raw_s=raw_s,
        ratio=ratio,
        atempo=atempo,
        warning=warning,
    )


def atempo_chain(rate: float) -> list[str]:
    """Split a rate into atempo factors each inside ffmpeg's 0.5-2.0 range.

    A 3x speed-up is `atempo=1.732,atempo=1.732`, not a filter error two
    minutes into a job.
    """
    if rate <= 1.0:
        return []
    factors: list[float] = []
    remaining = rate
    while remaining > _ATEMPO_MAX:
        factors.append(_ATEMPO_MAX)
        remaining /= _ATEMPO_MAX
    factors.append(remaining)
    return [f"atempo={factor:.6f}" for factor in factors]


def trim_silence(samples: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    """Drop the model's own leading and trailing silence.

    Load-bearing, not tidiness: the fit places a segment at its Whisper start
    time, so silence the model prepended would push every word late by however
    much of it there was, and the 50 ms alignment claim would be a claim about
    the wrong thing.
    """
    loud = np.flatnonzero(np.abs(samples) >= threshold)
    if loud.size == 0:
        return samples[:0]
    return samples[loud[0] : loud[-1] + 1]


def stretch(source: Path, destination: Path, atempo: float) -> None:
    """Time-stretch a wav with ffmpeg, keeping the pitch."""
    chain = atempo_chain(atempo)
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source)]
    if chain:
        command += ["-filter:a", ",".join(chain)]
    command += ["-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(destination)]
    subprocess.run(command, check=True, capture_output=True, text=True)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16)


def write_wav(path: Path, samples: np.ndarray) -> None:
    """Write 16-bit mono PCM, through a partial name.

    Every write to the shared volume goes through a rename: a half-written
    voice track is not a parse error the way JSON is — it is a shorter file
    that renders perfectly and goes quiet halfway through.
    """
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(samples.astype(np.int16).tobytes())
    partial.replace(path)


def to_pcm16(samples: np.ndarray) -> np.ndarray:
    """Float32 in -1..1 to int16, clipped rather than wrapped.

    Without the clip a sample a hair over 1.0 wraps to full-scale negative,
    which is a click in the middle of a word.
    """
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)


def assemble(pieces: list[tuple[float, np.ndarray]], total_s: float) -> np.ndarray:
    """Lay each piece onto a silent track of `total_s` at its own start time.

    Absolute placement, not concatenation. Concatenating would make every
    segment's position depend on the length of everything before it, so one
    segment synthesised 200 ms long would shift the rest of the video — the
    exact drift this stage exists to avoid.
    """
    track = np.zeros(max(int(round(total_s * SAMPLE_RATE)), 1), dtype=np.int16)
    for index, (start_s, samples) in enumerate(pieces):
        at = int(round(start_s * SAMPLE_RATE))
        if at >= track.size:
            continue
        # Capped at the next piece's start so a segment that overran its slot
        # cannot talk over the one after it. `atempo` makes this a no-op to
        # within a sample; it is here for the case where it does not.
        limit = track.size
        if index + 1 < len(pieces):
            limit = min(limit, int(round(pieces[index + 1][0] * SAMPLE_RATE)))
        room = max(limit - at, 0)
        usable = samples[:room]
        track[at : at + usable.size] = usable
    return track
