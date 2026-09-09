"""How much Vietnamese fits in the time the source segment allows.

The pipeline speeds a segment up with `atempo` when the Vietnamese takes longer
to say than the slot it belongs to. Measured on a full run, 23 of 104 segments
needed more than 1.35x and the worst needed 1.62x, which is what makes the voice
sound rushed. The timeline is not the problem - it is copied exactly at every
stage - the text is simply too long for it.

This module turns a slot duration into a character budget, so the translator can
ask for a shorter rendering of the segments that do not fit.

## Where the constants come from

`benchmark/duration_model.py` fits measured speech length against text length on
a finished run: `voice.json` holds the measured `spoken_s` for every segment and
`transcript.vi.json` holds the text that produced it.

**Characters, not tokens.** The same script fits both. On the reference run
characters explain the variance better than the model's own tokens do - R^2
0.954 against 0.929, and a worst-case error of 1.8 s against 3.2 s - so the
budget is in characters and the token count is used only to cap generation. A
budget in the worse-fitting unit would reject good translations and accept bad
ones.

Re-fit with:

    docker compose exec -T translate-service python /tmp/duration_model.py \
        --video-id <a video that has been through F6>
"""

import math

# spoken_s = INTERCEPT_S + SECONDS_PER_UNIT * characters
# ZeroTTS `maichi` at 48 kHz: 19.04 characters per second.
INTERCEPT_S = 0.138673
SECONDS_PER_UNIT = 0.05252747
R_SQUARED = 0.9543
FIXTURE = "3gi_15UH9fQ, 104 segments"

# The densest of those 104 segments was 2.255 characters per token. The cap is
# built from the worst case, not the median, because the cap must never be the
# thing that stops a legitimate translation.
TOKENS_PER_CHAR_WORST = 0.4435
CAP_SAFETY = 1.25
CAP_FLOOR_TOKENS = 16

# A slot can be shorter than the intercept. Without a floor the budget would go
# to zero or below, every candidate would fail, and the second pass would be
# dead code on exactly the segments that need it most.
MIN_BUDGET_CHARS = 8

# A retry shorter than this share of its budget dropped content rather than
# tightened it. Measured: one prompt shape turned a 203-character segment into
# 44 characters against a budget of 202 - it fits, and it no longer says what
# the source said. The budget is the length that fits, so a candidate far under
# it threw away room it was allowed to use.
MIN_KEEP_FRACTION = 0.55


class BudgetError(ValueError):
    """The budget cannot be computed from these inputs."""


def speech_seconds(text: str) -> float:
    """Predicted seconds to speak `text`, from the measured fit.

    Empty text costs nothing rather than the intercept: a segment with no words
    is never spoken, and charging it 0.14 s would make it look like it needs a
    slot of its own.
    """
    body = (text or "").strip()
    if not body:
        return 0.0
    return INTERCEPT_S + SECONDS_PER_UNIT * len(body)


def chars_for_slot(slot_s: float, target_ratio: float) -> int:
    """The most characters that fit in `slot_s` at `target_ratio` speed-up."""
    try:
        slot_s = float(slot_s)
        target_ratio = float(target_ratio)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"slot and ratio must be numbers: {exc}") from exc
    if not math.isfinite(slot_s) or slot_s <= 0.0:
        raise BudgetError(f"a slot of {slot_s} seconds has no budget")
    if not math.isfinite(target_ratio) or target_ratio < 1.0:
        # Below 1.0 the caller is asking for speech shorter than its own slot,
        # which is not what a slot means.
        raise BudgetError(f"target ratio {target_ratio} is below 1.0")

    allowed_s = slot_s * target_ratio - INTERCEPT_S
    return max(MIN_BUDGET_CHARS, int(allowed_s / SECONDS_PER_UNIT))


def token_cap(budget_chars: int, ceiling: int) -> int:
    """A per-segment `max_tokens`, sized so it only ever stops a runaway.

    Derived from the slot the caller is translating into, but deliberately
    generous: a cap that binds truncates a sentence in the middle, and a
    truncated subtitle is worse than a rushed one. `ceiling` is the service-wide
    guard, which this never exceeds.
    """
    try:
        budget_chars = int(budget_chars)
        ceiling = int(ceiling)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"budget and ceiling must be whole numbers: {exc}") from exc
    if ceiling <= 0:
        raise BudgetError(f"a ceiling of {ceiling} tokens leaves nothing to generate")

    wanted = math.ceil(max(budget_chars, 0) * TOKENS_PER_CHAR_WORST * CAP_SAFETY)
    return min(ceiling, max(CAP_FLOOR_TOKENS, wanted))


def fits(text: str, slot_s: float, target_ratio: float) -> bool:
    """Whether `text` can be spoken in `slot_s` without exceeding the ratio."""
    body = (text or "").strip()
    if not body:
        return True
    return speech_seconds(body) <= float(slot_s) * float(target_ratio)


def choose(
    first: str, second: str | None, budget_chars: int | None = None
) -> tuple[str, str]:
    """Pick between the first pass and the budgeted retry. Returns (text, why).

    The retry wins only when it is shorter, not empty and not suspiciously
    short, so the stage can never come out worse than the pipeline it replaced.
    Every other path keeps the first pass unchanged, so the second pass cannot
    make a segment worse than the single-pass pipeline makes it.

    A shorter candidate is kept even when it still misses the budget: less
    rushed is better than more rushed, and there is no third option to hold out
    for. `budget_chars` turns on the lower guard; without it only length is
    compared.
    """
    if second is None:
        return first, "no candidate"
    if not second.strip():
        return first, "empty candidate"

    kept = second.strip()
    saved = len(first.strip()) - len(kept)
    if saved <= 0:
        return first, "not shorter"
    if budget_chars is not None and len(kept) < MIN_KEEP_FRACTION * budget_chars:
        return first, (
            f"too short - {len(kept)} characters against a budget of "
            f"{budget_chars}, so it dropped meaning rather than tightened it"
        )
    return kept, f"shorter by {saved} characters"
