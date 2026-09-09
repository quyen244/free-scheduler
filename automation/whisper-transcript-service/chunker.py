"""Cut a transcript into publishable chunks.

A port of the `normalize transcript` Code node, with its four defects fixed.
Pure: no I/O, no database, no clock. Everything here is decided by the segment
list and two thresholds, so the awkward cases are testable without a video.

Boundaries always fall on Whisper segment edges. A chunk is never cut mid
sentence, which is why an over-long single segment comes out whole rather than
split — see `build_chunks`.
"""

from dataclasses import dataclass

from errors import EmptyTranscriptError

# The original script's `60 * 4`. Kept as the default, and overridable per
# request so the threshold can still be tuned without a rebuild — that was the
# one good reason it lived in a Code node.
MAX_CHUNK_S = 240.0
# New. The original had no floor at all, so a 4:05 video shipped a 5-second
# second chunk.
MIN_CHUNK_S = 60.0


@dataclass(frozen=True)
class Chunk:
    idx: int
    start_s: float
    end_s: float
    duration_s: float
    text: str
    char_count: int


def _chunk(idx: int, segments: list[dict]) -> Chunk:
    start = float(segments[0]["start"])
    end = float(segments[-1]["end"])
    # Trim once, at the end. The original counted the untrimmed buffer, so
    # char_count never matched the text it was reported next to.
    text = " ".join(str(segment["text"]).strip() for segment in segments).strip()
    return Chunk(
        idx=idx,
        start_s=start,
        end_s=end,
        duration_s=end - start,
        text=text,
        char_count=len(text),
    )


def _split_point(segments: list[dict]) -> int:
    """The segment boundary nearest the midpoint of `segments`."""
    start = float(segments[0]["start"])
    midpoint = start + (float(segments[-1]["end"]) - start) / 2
    candidates = range(1, len(segments))
    return min(candidates, key=lambda i: abs(float(segments[i]["start"]) - midpoint))


def build_chunks(
    segments: list[dict] | None,
    max_chunk_s: float = MAX_CHUNK_S,
    min_chunk_s: float = MIN_CHUNK_S,
) -> list[Chunk]:
    """Group `segments` into chunks of at most `max_chunk_s`.

    Greedy accumulation, exactly as the original: keep adding segments until
    the next one would carry the chunk past the ceiling, then start a new one.

    The tail is the part that needed fixing. A greedy pass alone leaves
    whatever is left over, which for a 4:05 video is five seconds — not a
    publishable chunk. Rather than merge that tail into its predecessor (which
    would push the predecessor past the ceiling and break the other half of the
    rule), the last two chunks are **redistributed** across their shared
    midpoint, so both land inside the floor and the ceiling. Merging is kept
    only as the fallback for the degenerate case where redistributing cannot
    satisfy the floor either.
    """
    if not segments:
        # The original read `$input.first().json.segments` with no guard, so an
        # upstream failure surfaced as an unrelated crash inside the Code node.
        raise EmptyTranscriptError("no segments to chunk")

    groups: list[list[dict]] = []
    current: list[dict] = []
    for segment in segments:
        # `current` is checked first: without it, a first segment longer than
        # the ceiling flushed an empty chunk ahead of itself.
        if current and float(segment["end"]) - float(current[0]["start"]) > max_chunk_s:
            groups.append(current)
            current = [segment]
        else:
            current.append(segment)
    if current:
        groups.append(current)

    groups = _fix_tail(groups, max_chunk_s, min_chunk_s)
    return [_chunk(idx, group) for idx, group in enumerate(groups)]


def _fix_tail(
    groups: list[list[dict]], max_chunk_s: float, min_chunk_s: float
) -> list[list[dict]]:
    """Make the last chunk usable, if it is too short and there is a neighbour.

    A single group is left alone whatever its length: the floor exists to
    remove unusable *tails*, not to delete a short video.
    """
    if len(groups) < 2:
        return groups

    tail = groups[-1]
    if float(tail[-1]["end"]) - float(tail[0]["start"]) >= min_chunk_s:
        return groups

    combined = groups[-2] + tail
    if len(combined) > 1:
        at = _split_point(combined)
        left, right = combined[:at], combined[at:]
        both_ok = all(
            min_chunk_s
            <= float(part[-1]["end"]) - float(part[0]["start"])
            <= max_chunk_s
            for part in (left, right)
        )
        if both_ok:
            return groups[:-2] + [left, right]

    # Redistributing cannot satisfy the floor — a very long segment, or a very
    # short predecessor. One over-long chunk beats an unusable stub.
    return groups[:-2] + [combined]
