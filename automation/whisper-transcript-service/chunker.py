"""Build balanced, transcript-safe Facebook/TikTok chunks.

Sources from five through nine minutes remain one chunk. Longer sources are
split into a variable number of balanced parts targeting four to five minutes.
Every internal cut lands on a transcript segment end within 15 seconds of its
balanced target, so speech is not cut mid-segment and tiny tails cannot appear.
"""

from dataclasses import dataclass
from math import ceil

from errors import ChunkBoundaryError, EmptyTranscriptError


MIN_CHUNK_S = 240.0
MAX_CHUNK_S = 300.0
SINGLE_CHUNK_MAX_S = 540.0
BOUNDARY_SHIFT_MAX_S = 15.0


@dataclass(frozen=True)
class Chunk:
    idx: int
    name: str
    start_s: float
    end_s: float
    duration_s: float
    text: str
    char_count: int
    boundary_shift_s: float


def _chunk(
    idx: int,
    segments: list[dict],
    start_s: float,
    end_s: float,
    boundary_shift_s: float,
) -> Chunk:
    text = " ".join(str(segment["text"]).strip() for segment in segments).strip()
    return Chunk(
        idx=idx,
        name=f"part_{idx + 1}",
        start_s=start_s,
        end_s=end_s,
        duration_s=end_s - start_s,
        text=text,
        char_count=len(text),
        boundary_shift_s=boundary_shift_s,
    )


def expected_chunk_count(
    duration_s: float,
    min_chunk_s: float = MIN_CHUNK_S,
    max_chunk_s: float = MAX_CHUNK_S,
    single_chunk_max_s: float = SINGLE_CHUNK_MAX_S,
) -> int:
    """Choose a balanced count without creating a tiny remainder."""
    if min_chunk_s <= 0 or max_chunk_s <= 0 or min_chunk_s > max_chunk_s:
        raise ValueError("chunk duration bounds must be positive and min <= max")
    if duration_s <= single_chunk_max_s:
        return 1

    target_mid = (min_chunk_s + max_chunk_s) / 2
    max_count = max(2, ceil(duration_s / min_chunk_s) + 1)

    def score(count: int) -> tuple[float, float, int]:
        average = duration_s / count
        if average < min_chunk_s:
            outside = min_chunk_s - average
        elif average > max_chunk_s:
            outside = average - max_chunk_s
        else:
            outside = 0.0
        return outside, abs(average - target_mid), count

    return min(range(2, max_count + 1), key=score)


def build_chunks(
    segments: list[dict] | None,
    max_chunk_s: float = MAX_CHUNK_S,
    min_chunk_s: float = MIN_CHUNK_S,
    single_chunk_max_s: float = SINGLE_CHUNK_MAX_S,
    boundary_shift_max_s: float = BOUNDARY_SHIFT_MAX_S,
    source_duration_s: float | None = None,
) -> list[Chunk]:
    if not segments:
        raise EmptyTranscriptError("no segments to chunk")
    if boundary_shift_max_s < 0:
        raise ValueError("boundary shift limit cannot be negative")

    ordered = sorted(segments, key=lambda segment: float(segment["start"]))
    source_start = 0.0 if source_duration_s is not None else float(ordered[0]["start"])
    source_end = (
        float(source_duration_s)
        if source_duration_s is not None
        else float(ordered[-1]["end"])
    )
    if source_end < float(ordered[-1]["end"]):
        raise ChunkBoundaryError("source duration ends before the final transcript segment")
    duration_s = source_end - source_start
    if duration_s <= 0:
        raise EmptyTranscriptError("transcript has no positive duration")

    count = expected_chunk_count(
        duration_s,
        min_chunk_s=min_chunk_s,
        max_chunk_s=max_chunk_s,
        single_chunk_max_s=single_chunk_max_s,
    )
    if count == 1:
        return [_chunk(0, ordered, source_start, source_end, 0.0)]

    split_indexes: list[int] = []
    cut_times: list[float] = []
    shifts: list[float] = []
    previous_index = 0

    for cut_number in range(1, count):
        ideal = source_start + duration_s * cut_number / count
        remaining_chunks = count - cut_number
        last_allowed = len(ordered) - remaining_chunks
        candidates = range(previous_index + 1, last_allowed + 1)
        if not candidates:
            raise ChunkBoundaryError(
                f"not enough transcript segments to create {count} chunks"
            )

        split_index = min(
            candidates,
            key=lambda index: abs(float(ordered[index - 1]["end"]) - ideal),
        )
        cut_time = float(ordered[split_index - 1]["end"])
        shift = cut_time - ideal
        if abs(shift) > boundary_shift_max_s:
            raise ChunkBoundaryError(
                "no transcript boundary falls within "
                f"{boundary_shift_max_s:g}s of the balanced cut at {ideal:.3f}s"
            )
        if cut_times and cut_time <= cut_times[-1]:
            raise ChunkBoundaryError("transcript boundaries are not strictly increasing")

        split_indexes.append(split_index)
        cut_times.append(cut_time)
        shifts.append(shift)
        previous_index = split_index

    chunks: list[Chunk] = []
    segment_start = 0
    chunk_start = source_start
    for idx, (segment_end, chunk_end, shift) in enumerate(
        zip(split_indexes, cut_times, shifts)
    ):
        chunks.append(
            _chunk(
                idx,
                ordered[segment_start:segment_end],
                chunk_start,
                chunk_end,
                shift,
            )
        )
        segment_start = segment_end
        chunk_start = chunk_end

    chunks.append(
        _chunk(
            count - 1,
            ordered[segment_start:],
            chunk_start,
            source_end,
            0.0,
        )
    )
    return chunks
