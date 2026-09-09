"""The ASS script, checked as text before libass ever sees it."""

import subs

STYLE = {
    "y": 0.755,
    "font": "DejaVu Sans",
    "size": 46,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 3,
    "max_chars_per_line": 20,
    "max_lines": 2,
}

SEGMENTS = [
    {"start": 100.0, "end": 104.0, "text": "Tám người đi, bốn người về."},
    {"start": 104.0, "end": 106.0, "text": "Không ai biết vì sao."},
]


def test_colours_are_written_in_ass_byte_order():
    # ASS is &HAABBGGRR — reversed, with an alpha where 0 means opaque. Getting
    # this wrong swaps red and blue, which looks deliberate.
    assert subs.ass_colour("#FF8000") == "&H000080FF"


def test_cue_times_are_relative_to_the_clip_not_the_source():
    # The chunk is cut out of the video, so it starts at zero. Absolute times
    # would put every subtitle in a three-minute chunk off the end of it.
    script = subs.build(SEGMENTS, STYLE, 1080, 1920, offset_s=100.0)
    assert "0:00:00.00,0:00:04.00" in script
    assert "0:01:40" not in script


def test_the_canvas_is_the_play_resolution_so_one_size_fits_both_sources():
    script = subs.build(SEGMENTS, STYLE, 1080, 1920)
    assert "PlayResX: 1080" in script
    assert "PlayResY: 1920" in script


def test_the_margin_is_measured_from_the_bottom():
    # `y` is from the top like every other preset coordinate; ASS alignment 2
    # measures from the bottom. 1920 * (1 - 0.755) = 470.
    script = subs.build(SEGMENTS, STYLE, 1080, 1920)
    assert ",470,1" in script


def test_long_lines_wrap_on_word_boundaries():
    assert subs.wrap("mot hai ba bon nam sau bay", 10) == ["mot hai ba", "bon nam", "sau bay"]


def test_an_over_long_segment_becomes_two_cues_rather_than_losing_words():
    # Truncating would drop text the voice is speaking; showing six lines would
    # cover the picture. So the cue is split in time instead.
    long_segment = [{"start": 0.0, "end": 6.0, "text": " ".join(["word"] * 30)}]
    script = subs.build(long_segment, STYLE, 1080, 1920)
    assert script.count("Dialogue:") > 1
    assert script.count("word") == 30


def test_an_empty_segment_produces_no_cue():
    assert "Dialogue:" not in subs.build([{"start": 0.0, "end": 2.0, "text": "  "}], STYLE, 1080, 1920)


def test_braces_in_the_text_cannot_become_ass_override_tags():
    # `{\an8}` inside a line would move the subtitle. The text here is written
    # by a translation model, so it is not trusted to be tag-free.
    script = subs.build([{"start": 0.0, "end": 2.0, "text": r"{\an8}hello"}], STYLE, 1080, 1920)
    assert "{" not in script.split("[Events]")[1]


def test_vietnamese_survives_into_the_script():
    assert "Tám người đi" in subs.build(SEGMENTS, STYLE, 1080, 1920)
