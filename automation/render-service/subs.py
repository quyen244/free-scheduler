"""Burned subtitles, written as ASS.

ASS rather than SRT because the preset carries a face, a size, a colour and an
outline, and SRT carries none of them — every one of those would have to be
re-stated as ffmpeg flags and would then disagree with the preset.

Sizes are written against `PlayResX/PlayResY`, which are the canvas, not the
source. That is what makes one preset produce the same-looking subtitle on a
720p and a 1080p source: libass scales the whole script, so a size of 46 means
46 canvas pixels either way.
"""

from pathlib import Path

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {canvas_w}
PlayResY: {canvas_h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{colour},{colour},{outline_colour},&H80000000,1,0,0,0,100,100,0,0,1,{outline},0,2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""


def ass_colour(hex_colour: str, alpha: int = 0) -> str:
    """`#RRGGBB` to ASS's `&HAABBGGRR`.

    Reversed byte order and an alpha where 0 is opaque — the two things that
    are wrong every first time.
    """
    value = hex_colour.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours, rest = divmod(seconds, 3600)
    minutes, rest = divmod(rest, 60)
    whole, centiseconds = divmod(round(rest * 100), 100)
    return f"{int(hours)}:{int(minutes):02d}:{int(whole):02d}.{int(centiseconds):02d}"


def wrap(text: str, max_chars: int) -> list[str]:
    """Break a line on word boundaries at `max_chars`."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _cues(start: float, end: float, lines: list[str], max_lines: int) -> list[tuple[float, float, list[str]]]:
    """Split an over-long segment across time instead of dropping the overflow.

    A 5-second segment that wraps to four lines does not fit two lines of
    subtitle, and the alternatives are worse: truncating loses words the
    voice is speaking, and showing four lines covers the picture. So the cue
    becomes two cues, split in proportion to their length.
    """
    if len(lines) <= max_lines:
        return [(start, end, lines)]

    groups = [lines[at : at + max_lines] for at in range(0, len(lines), max_lines)]
    weights = [sum(len(line) for line in group) or 1 for group in groups]
    total = sum(weights)

    cues = []
    at = start
    for group, weight in zip(groups, weights):
        span = (end - start) * weight / total
        cues.append((at, min(at + span, end), group))
        at += span
    return cues


def build(segments: list[dict], style: dict, canvas_w: int, canvas_h: int, offset_s: float = 0.0) -> str:
    """Render an ASS script for one chunk.

    `offset_s` is the chunk's start in the source: cue times are relative to
    the clip, which starts at zero.
    """
    max_chars = int(style.get("max_chars_per_line", 38))
    max_lines = int(style.get("max_lines", 2))
    # `y` is measured from the top like every other coordinate in the preset;
    # ASS alignment 2 measures its margin from the bottom.
    margin_v = max(int(round(canvas_h * (1.0 - float(style.get("y", 0.75))))), 0)

    body = _HEADER.format(
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        font=style.get("font", "DejaVu Sans"),
        size=int(style.get("size", 46)),
        colour=ass_colour(str(style.get("color", "#FFFFFF"))),
        outline_colour=ass_colour(str(style.get("outline_color", "#000000"))),
        outline=int(style.get("outline", 3)),
        margin_h=max(int(round(canvas_w * 0.06)), 0),
        margin_v=margin_v,
    )

    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment["start"]) - offset_s
        end = float(segment["end"]) - offset_s
        for cue_start, cue_end, lines in _cues(start, end, wrap(text, max_chars), max_lines):
            # Commas separate fields, and the text field is last — so only the
            # brace and backslash need escaping, not the comma.
            rendered = "\\N".join(lines).replace("{", "(").replace("}", ")")
            body += (
                f"Dialogue: 0,{timestamp(cue_start)},{timestamp(cue_end)},"
                f"Default,,0,0,,{rendered}\n"
            )

    return body


def write(path: Path, script: str) -> None:
    path.write_text(script, encoding="utf-8")
