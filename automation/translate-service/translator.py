"""HY-MT1.5 on CPU, one Whisper segment at a time.

**One segment per call, on purpose.** Asking the model for a numbered list of
40 lines is faster and is exactly how alignment breaks: it merges two short
lines into one, renumbers, or drops a line that looked like a duplicate. A
per-segment call cannot merge anything, so the count is right by construction
and the check below is a backstop rather than the only defence.
"""

import logging
import re
import threading
from typing import Iterable

import budget
from config import settings
from errors import (
    MisalignedTranslationError,
    TranslationError,
    TruncatedTranslationError,
)

logger = logging.getLogger(__name__)

_model = None

# One llama.cpp context, used from a thread pool: FastAPI runs each background
# job in its own thread, and two of them generating on the same context wedges
# the process — no error, no progress, and a `/health` that stops answering.
# Serialised per line rather than per job, so a second video queues behind the
# first line by line instead of behind the whole first video.
_lock = threading.RLock()

TARGET_LANGUAGE = "vi"

# The prompt shapes from the Hunyuan-MT model card. Chinese sources get the
# Chinese template; everything else gets the English one.
_ZH_PROMPT = "把下面的文本翻译成越南语，不要额外解释。\n\n{text}"
_EN_PROMPT = (
    "Translate the following segment into Vietnamese, without additional "
    "explanation.\n\n{text}"
)

# The second pass. Used only on segments whose first translation is too long for
# the time the source gives them, so the great majority of segments never see
# this prompt and keep exactly what the single pass produced for them.
_ZH_BUDGET_PROMPT = (
    "把下面的文本翻译成越南语，不要额外解释。"
    "译文必须简洁，不超过 {budget} 个字符，同时保留完整意思。\n\n{text}"
)
_EN_BUDGET_PROMPT = (
    "Translate the following segment into Vietnamese, without additional "
    "explanation. The translation must be concise and no longer than {budget} "
    "characters, while keeping the full meaning.\n\n{text}"
)

# Sampling from the same model card. `_SEED` is applied on every call, not only
# when the context is built - see `_translate_line` for why that distinction is
# the difference between a reproducible pipeline and one that only looks it.
_SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05}
_SEED = 1337

# Seeds for the budgeted retry, tried in order until one produces a shorter
# translation. Fixed and not random, so the stage stays reproducible.
#
# Measured on the six worst segments of the reference run: the budget prompt on
# the loaded seed shortened 3 of 6, and the same prompt on a second seed
# shortened 4 of 6 - the model often re-emits its own previous answer, and
# resampling is what breaks that. Asking it to shorten its own Vietnamese
# instead of re-translating shortened only 1 of 6, because HY-MT1.5 translates
# and does not edit.
_RETRY_SEEDS = (20260907, 8675309)


def _seeds_for_retry() -> tuple[int, ...]:
    """The seeds this run may spend, capped by how many are actually defined.

    `TRANSLATE_RETRIES` above `len(_RETRY_SEEDS)` cannot do what it asks for.
    Silently slicing it would leave the setting looking effective, so the
    shortfall is said out loud once, at the point it is decided.
    """
    wanted = max(1, settings.retry_attempts)
    if wanted > len(_RETRY_SEEDS):
        logger.warning(
            "TRANSLATE_RETRIES is %d but only %d retry seeds are defined; "
            "using %d",
            wanted, len(_RETRY_SEEDS), len(_RETRY_SEEDS),
        )
    return _RETRY_SEEDS[:wanted]

# Models asked to translate sometimes narrate the fact first.
_PREAMBLE = re.compile(
    r"^\s*(translation|translated text|vietnamese|bản dịch|dịch)\s*[:：]\s*",
    re.IGNORECASE,
)


def get_model():
    global _model
    with _lock:
        if _model is None:
            _model = _load_model()
        return _model


def is_loaded() -> bool:
    return _model is not None


def _load_model():
    from llama_cpp import Llama

    logger.info(
        "loading %s (gpu_layers=%d)", settings.model_path, settings.gpu_layers
    )
    model = Llama(
        model_path=str(settings.model_path),
        n_ctx=settings.context_tokens,
        n_threads=settings.threads or None,
        # 0 on the CPU image, -1 on the GPU one. llama.cpp accepts the request
        # on a build without CUDA and simply keeps every layer on the CPU, so
        # asking is safe - but it is also silent, which is why the check below
        # exists rather than trusting the setting.
        n_gpu_layers=settings.gpu_layers,
        seed=_SEED,
        verbose=False,
    )
    if settings.gpu_layers != 0:
        _warn_if_not_offloaded(model)
    return model


def _warn_if_not_offloaded(model) -> None:
    """Say so when layers were asked for on the GPU and did not get there.

    A CPU-only llama.cpp build ignores `n_gpu_layers` without raising, so a GPU
    deployment can run entirely on the CPU and report nothing but a
    disappointing stage time. This turns that into a line in the log.

    Best-effort by design: llama-cpp-python does not promise this attribute
    across versions, and a translation that works must not fail because an
    introspection did not.
    """
    try:
        import llama_cpp

        offloaded = llama_cpp.llama_model_n_layer(model.model)
        supports_gpu = bool(llama_cpp.llama_supports_gpu_offload())
    except Exception as exc:  # noqa: BLE001 - any probe failure is one failure
        logger.warning("could not confirm GPU offload: %s", exc)
        return
    if not supports_gpu:
        logger.warning(
            "LLAMA_GPU_LAYERS=%d but this llama.cpp build has no GPU offload "
            "support; the model is running on the CPU",
            settings.gpu_layers,
        )
    else:
        logger.info(
            "llama.cpp GPU offload available; model has %d layers", offloaded
        )


def _clean(text: str) -> str:
    text = text.strip()
    text = _PREAMBLE.sub("", text)
    # A model that quotes its own output turns every subtitle into a quotation.
    if len(text) > 1 and text[0] in '"“' and text[-1] in '"”':
        text = text[1:-1].strip()
    return text


def _translate_line(
    text: str,
    source_language: str,
    budget_chars: int | None = None,
    max_tokens: int | None = None,
    seed: int | None = None,
) -> str:
    """One segment, one call. With `budget_chars`, ask for a shorter rendering.

    Raises `TruncatedTranslationError` when generation stopped at the token cap,
    because a subtitle cut off in the middle is worse than one that is rushed.
    """
    chinese = source_language.startswith("zh")
    if budget_chars is None:
        template = _ZH_PROMPT if chinese else _EN_PROMPT
        content = template.format(text=text)
        cap = settings.max_output_tokens
    else:
        template = _ZH_BUDGET_PROMPT if chinese else _EN_BUDGET_PROMPT
        content = template.format(text=text, budget=budget_chars)
        cap = max_tokens or settings.max_output_tokens

    # Seed every call, not just the constructor. llama.cpp seeds its RNG once at
    # construction and then lets it run, so without this a segment's translation
    # depends on how many calls came before it in the process - the same
    # sentence translated twice in one process came back 99 and 95 characters
    # long. Seeded per call it is a function of its own text alone, which is
    # what makes a re-run reproducible and a before/after comparison honest.
    sampling = dict(_SAMPLING)
    sampling["seed"] = _SEED if seed is None else seed
    try:
        with _lock:
            reply = get_model().create_chat_completion(
                messages=[{"role": "user", "content": content}],
                max_tokens=cap,
                **sampling,
            )
    except Exception as exc:  # noqa: BLE001 — llama.cpp raises plain exceptions
        raise TranslationError(f"the model failed on {text[:60]!r}: {exc}") from exc

    choice = reply["choices"][0]
    if choice.get("finish_reason") == "length":
        if budget_chars is None:
            # The first pass keeps the behaviour it shipped with: a runaway at
            # 512 tokens is logged, not raised, because raising here would fail
            # videos that succeed today.
            logger.warning(
                "translation hit the %d token cap on %r - it may be cut short",
                cap,
                text[:60],
            )
        else:
            raise TruncatedTranslationError(
                f"the budgeted retry hit its {cap} token cap on {text[:60]!r}"
            )
    return _clean(choice["message"]["content"] or "")


def translate_lines(lines: Iterable[str], source_language: str) -> list[str]:
    return [_translate_line(line, source_language) for line in lines]


def _slot_seconds(segment: dict) -> float:
    """The time the source gives this segment. Zero when it gives none."""
    try:
        return float(segment["end"]) - float(segment["start"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _fit_to_slots(
    segments: list[dict],
    sources: list[str],
    translated: list[str],
    source_language: str,
    report: list | None = None,
) -> list[str]:
    """Re-translate the segments whose text does not fit its slot.

    Only the ones that miss: on the reference run that is 24 of 104, so the
    stage pays about a quarter more calls and the other 77 % of the output stays
    exactly as the single-pass pipeline produced it.

    Nothing here can make the stage worse. A retry that fails, comes back empty,
    comes back truncated or comes back longer is discarded, and the first pass
    stands.
    """
    ratio = settings.target_ratio
    seeds = _seeds_for_retry()
    out = list(translated)
    retried = 0
    improved = 0

    for index, (segment, source, first) in enumerate(zip(segments, sources, translated)):
        slot_s = _slot_seconds(segment)
        if slot_s <= 0.0 or budget.fits(first, slot_s, ratio):
            continue

        try:
            allowed = budget.chars_for_slot(slot_s, ratio)
        except budget.BudgetError as exc:
            logger.warning("segment %d has no budget (%s); keeping the first pass", index, exc)
            continue

        retried += 1
        cap = budget.token_cap(allowed, settings.max_output_tokens)
        chosen, why = first, "no candidate"

        # Stop at the first candidate that fits. The model often re-emits its
        # own previous answer, so a second seed is what gives the retry another
        # chance rather than the same one twice.
        for seed in seeds:
            try:
                candidate = _translate_line(source, source_language, allowed, cap, seed)
            except (TranslationError, TruncatedTranslationError) as exc:
                # Best effort by design: the first pass already succeeded, so a
                # failure here must never fail the job.
                logger.warning("segment %d could not be shortened (%s)", index, exc)
                continue

            better, reason = budget.choose(chosen, candidate, allowed)
            if better != chosen:
                chosen, why = better, reason
            elif why == "no candidate":
                why = reason
            if budget.fits(chosen, slot_s, ratio):
                break

        if chosen != first:
            improved += 1
        out[index] = chosen
        if report is not None:
            report.append(
                {
                    "idx": index,
                    "slot_s": round(slot_s, 3),
                    "budget_chars": allowed,
                    "before_chars": len(first.strip()),
                    "after_chars": len(chosen.strip()),
                    "decision": why,
                }
            )

    logger.info(
        "budget pass: %d of %d segments did not fit, %d were shortened",
        retried,
        len(segments),
        improved,
    )
    return out


def translate_segments(
    segments: list[dict], source_language: str, report: list | None = None
) -> list[dict[str, object]]:
    """Translate `segments` into Vietnamese, keeping their timings exactly.

    Vietnamese input is returned untouched. The n8n branch already skips the
    call, but a service that would happily re-translate Vietnamese into
    Vietnamese when called by hand is one bad IF condition away from garbling a
    whole video.
    """
    if source_language.startswith(TARGET_LANGUAGE):
        return [dict(segment) for segment in segments]

    sources = [str(segment["text"]) for segment in segments]
    translated = translate_lines(sources, source_language)

    if len(translated) != len(sources):
        raise MisalignedTranslationError(
            f"asked for {len(sources)} segments, got {len(translated)}"
        )

    if settings.budget_enabled:
        translated = _fit_to_slots(segments, sources, translated, source_language, report)
        # The second pass rewrites text in place and can never add or drop an
        # entry, but the count is what every later stage trusts, so check it
        # again rather than assume the loop above stayed honest.
        if len(translated) != len(sources):
            raise MisalignedTranslationError(
                f"the budget pass returned {len(translated)} of {len(sources)} segments"
            )

    out: list[dict[str, object]] = []
    for segment, text in zip(segments, translated):
        if not text.strip():
            raise MisalignedTranslationError(
                f"empty translation for segment at {segment['start']}s"
            )
        # start/end copied from the source, never re-derived: identical timings
        # are what the rest of the pipeline is entitled to assume.
        out.append({"start": segment["start"], "end": segment["end"], "text": text})
    return out
