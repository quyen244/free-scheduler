"""The slot budget: how much Vietnamese fits in the time the source allows.

Pure arithmetic and pure decisions. Nothing here loads a model, so the whole
file runs in milliseconds and can be trusted to say why a segment was rejected.

The numbers these tests pin come from `benchmark/duration_model.py` fitted on
the 104 measured segments of `3gi_15UH9fQ`; see `budget.py` for the fit.
"""

import json

import pytest

import budget
import library
from errors import InvalidVideoIdError

# The default the service ships with, and the same band `TTS_MAX_RATIO` warns
# on in the render service. Written out here so a change to the default cannot
# quietly change what these tests mean.
RATIO = 1.35


class TestSpeechSeconds:
    def test_longer_text_takes_longer_to_say(self):
        assert budget.speech_seconds("a" * 100) > budget.speech_seconds("a" * 50)

    def test_the_rate_matches_the_measured_fit(self):
        # 19.04 chars/second plus the fixed intercept.
        assert budget.speech_seconds("a" * 190) == pytest.approx(10.12, abs=0.05)

    def test_empty_text_takes_no_time_at_all(self):
        # Not the intercept: a segment with no words is not spoken, so charging
        # it 0.14 s would make an empty segment look like it needs a slot.
        assert budget.speech_seconds("") == 0.0
        assert budget.speech_seconds("   ") == 0.0


class TestCharsForSlot:
    def test_a_longer_slot_allows_more_text(self):
        assert budget.chars_for_slot(10.0, RATIO) > budget.chars_for_slot(5.0, RATIO)

    def test_the_budget_is_what_fits_at_exactly_the_target_ratio(self):
        allowed = budget.chars_for_slot(6.0, RATIO)
        # Speaking `allowed` characters takes at most ratio * slot seconds.
        assert budget.speech_seconds("a" * allowed) <= 6.0 * RATIO
        # And one character more would not fit, so the budget is not slack.
        assert budget.speech_seconds("a" * (allowed + 2)) > 6.0 * RATIO

    def test_a_looser_ratio_allows_more_text(self):
        assert budget.chars_for_slot(6.0, 1.5) > budget.chars_for_slot(6.0, 1.2)

    def test_a_slot_with_no_duration_is_an_error_not_a_negative_budget(self):
        with pytest.raises(budget.BudgetError):
            budget.chars_for_slot(0.0, RATIO)
        with pytest.raises(budget.BudgetError):
            budget.chars_for_slot(-3.0, RATIO)

    def test_a_ratio_below_one_is_refused(self):
        # Below 1.0 the budget would ask for speech shorter than the slot, which
        # is not what the slot means and is never what the caller wants.
        with pytest.raises(budget.BudgetError):
            budget.chars_for_slot(6.0, 0.9)

    def test_a_very_short_slot_still_allows_something(self):
        # The intercept can exceed a tiny slot. A budget of zero or less would
        # make every candidate fail and every segment fall back for ever, so the
        # floor keeps the second pass meaningful.
        assert budget.chars_for_slot(0.05, RATIO) >= budget.MIN_BUDGET_CHARS


class TestTokenCap:
    def test_the_cap_is_generous_enough_for_the_worst_measured_segment(self):
        # 2.255 chars/token was the densest of the 104 measured segments. The cap
        # must not truncate even that one, or the second pass would reject good
        # translations for a reason that has nothing to do with length.
        allowed = budget.chars_for_slot(6.0, RATIO)
        needed = allowed / 2.255
        assert budget.token_cap(allowed, 512) > needed

    def test_the_cap_never_exceeds_the_services_own_ceiling(self):
        assert budget.token_cap(100000, 512) == 512

    def test_the_cap_has_a_floor_so_a_tiny_slot_can_still_say_a_word(self):
        assert budget.token_cap(1, 512) >= budget.CAP_FLOOR_TOKENS

    def test_a_bigger_budget_never_gets_a_smaller_cap(self):
        caps = [budget.token_cap(n, 512) for n in range(1, 400, 17)]
        assert caps == sorted(caps)


class TestFits:
    def test_text_inside_its_budget_fits(self):
        assert budget.fits("a" * 50, 6.0, RATIO)

    def test_text_over_its_budget_does_not(self):
        assert not budget.fits("a" * 400, 6.0, RATIO)

    def test_an_empty_segment_always_fits(self):
        assert budget.fits("", 1.0, RATIO)


class TestChoose:
    """Which of the two passes to keep. The rule is: never come out worse."""

    def test_a_shorter_candidate_wins(self):
        text, reason = budget.choose("mot hai ba bon nam", "mot hai ba")
        assert text == "mot hai ba"
        assert "shorter" in reason

    def test_a_longer_candidate_is_rejected(self):
        text, reason = budget.choose("mot hai", "mot hai ba bon")
        assert text == "mot hai"
        assert "not shorter" in reason

    def test_an_equal_candidate_is_rejected(self):
        # No gain, and keeping the first pass keeps the output identical to the
        # pipeline as it shipped.
        text, _ = budget.choose("mot hai", "mot hai")
        assert text == "mot hai"

    def test_a_missing_candidate_keeps_the_first_pass(self):
        text, reason = budget.choose("mot hai", None)
        assert text == "mot hai"
        assert "no candidate" in reason

    def test_an_empty_candidate_is_never_shipped(self):
        # An empty subtitle is worse than a rushed one, and it is what a
        # truncated generation looks like after cleaning.
        for empty in ("", "   ", "\n"):
            text, reason = budget.choose("mot hai", empty)
            assert text == "mot hai"
            assert "empty" in reason

    def test_a_shorter_candidate_wins_even_when_it_still_misses_the_budget(self):
        # The second pass does not have to reach the budget to be worth keeping.
        # Less rushed is better than more rushed, and the fallback guarantees it
        # can never be worse than the first pass.
        long_first = "a" * 300
        still_long = "a" * 260
        text, _ = budget.choose(long_first, still_long)
        assert text == still_long
        assert not budget.fits(text, 6.0, RATIO)


class TestChooseGuardsAgainstLoss:
    """A retry can come back short because it dropped half the sentence.

    Measured: asking the model to shorten a 203-character segment with a budget
    of 202 produced a 44-character reply. It fits, and it is not a translation
    of the source any more. The budget is the length that fits, so a candidate
    far below it threw away room it was allowed to use.
    """

    def test_a_candidate_far_under_its_budget_is_rejected(self):
        text, reason = budget.choose("a" * 203, "b" * 44, budget_chars=202)
        assert text == "a" * 203
        assert "too short" in reason

    def test_a_candidate_comfortably_under_budget_is_still_kept(self):
        text, _ = budget.choose("a" * 203, "b" * 159, budget_chars=202)
        assert text == "b" * 159

    def test_the_floor_is_only_applied_when_a_budget_is_given(self):
        text, _ = budget.choose("a" * 203, "b" * 44)
        assert text == "b" * 44

    def test_the_floor_sits_where_the_measurement_put_it(self):
        # Just inside and just outside, so the constant cannot drift silently.
        allowed = 200
        floor = int(budget.MIN_KEEP_FRACTION * allowed)
        assert budget.choose("a" * 300, "b" * (floor + 1), budget_chars=allowed)[0] == (
            "b" * (floor + 1)
        )
        assert budget.choose("a" * 300, "b" * (floor - 1), budget_chars=allowed)[0] == (
            "a" * 300
        )


class TestRetrySeedCeiling:
    """`TRANSLATE_RETRIES` cannot spend more seeds than the module defines.

    A setting that looks effective and is not is worse than one that refuses,
    so the shortfall is logged. These pin the behaviour either way.
    """

    def test_the_number_of_seeds_never_exceeds_what_is_defined(self, caplog):
        import dataclasses

        import translator
        from config import settings

        original = translator.settings
        try:
            translator.settings = dataclasses.replace(settings, retry_attempts=99)
            with caplog.at_level("WARNING"):
                seeds = translator._seeds_for_retry()
        finally:
            translator.settings = original

        assert seeds == translator._RETRY_SEEDS
        assert "only 2 retry seeds are defined" in caplog.text

    def test_one_retry_spends_one_seed_and_says_nothing(self, caplog):
        import dataclasses

        import translator
        from config import settings

        original = translator.settings
        try:
            translator.settings = dataclasses.replace(settings, retry_attempts=1)
            with caplog.at_level("WARNING"):
                seeds = translator._seeds_for_retry()
        finally:
            translator.settings = original

        assert seeds == translator._RETRY_SEEDS[:1]
        assert "retry seeds are defined" not in caplog.text


class TestTheBudgetDecisionsSurviveTheRun:
    """Per-segment decisions belong in a file, not only in a log line.

    The job result carries two counts - how many segments missed their slot and
    how many were shortened. Neither says *which*, by how much, or why a
    candidate was rejected, so a run cannot be audited after its container has
    been restarted and its logs rotated away. This writes the rows beside the
    transcript, in their own file: `transcript.vi.json` is parsed by F4 and F6,
    and this evidence must not be able to break either of them.
    """

    def test_the_rows_are_written_beside_the_transcript(self, tmp_path):
        rows = [{"idx": 3, "slot_s": 4.5, "budget_chars": 80,
                 "before_chars": 120, "after_chars": 76, "decision": "shorter"}]

        path = library.save_budget_report("3gi_15UH9fQ", rows,
                                          directory=tmp_path)

        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["segments"] == rows
        assert written["total"] == 1

    def test_an_empty_report_is_still_written(self, tmp_path):
        # "Nothing needed shortening" is a result, and a missing file is
        # indistinguishable from a stage that never ran.
        path = library.save_budget_report("3gi_15UH9fQ", [], directory=tmp_path)

        assert json.loads(path.read_text(encoding="utf-8"))["total"] == 0

    def test_a_bad_video_id_is_refused_before_it_reaches_a_path(self):
        with pytest.raises(InvalidVideoIdError):
            library.save_budget_report("../../etc", [])

    def test_no_partial_file_is_left_behind(self, tmp_path):
        library.save_budget_report("3gi_15UH9fQ", [], directory=tmp_path)

        assert not list(tmp_path.glob("*.part"))

    def test_a_report_that_cannot_be_written_does_not_fail_the_job(
        self, monkeypatch, tmp_path
    ):
        # The order the two writes happen in is the point: the translation is
        # already on disk, so losing the audit file must cost the audit file
        # only.
        import jobs

        monkeypatch.setattr(jobs.library, "load_transcript",
                            lambda video_id: {"language": "en", "segments": [
                                {"start": 0.0, "end": 4.0, "text": "src"}]})
        monkeypatch.setattr(jobs.translator, "translate_segments",
                            lambda segments, source, report=None: [
                                {"start": 0.0, "end": 4.0, "text": "dịch"}])
        saved = tmp_path / "transcript.vi.json"
        monkeypatch.setattr(jobs.library, "save_translation",
                            lambda video_id, payload: saved)

        def refuse(*args: object, **kwargs: object) -> None:
            raise OSError("the volume is read-only")

        monkeypatch.setattr(jobs.library, "save_budget_report", refuse)

        finished: dict = {}
        monkeypatch.setattr(jobs.pipeline_db, "mark_running", lambda job_id: None)
        monkeypatch.setattr(jobs.pipeline_db, "advance_stage",
                            lambda video_id, stage: None)
        monkeypatch.setattr(jobs.pipeline_db, "finish_job",
                            lambda job_id, state, **kw: finished.update(
                                state=state, **kw))
        monkeypatch.setattr(jobs, "_notify", lambda job_id: None)

        jobs.run_translate("job-1", "3gi_15UH9fQ")

        assert finished["state"] == "done"
