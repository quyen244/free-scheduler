"""Model-specific translation profiles injected into the shared service.

The FastAPI routes, job lifecycle, alignment checks and llama.cpp runtime do
not know which translation model is loaded.  A profile supplies only the
prompt and decoding policy required by that model, so adding a model is an
extension here rather than a branch through the production pipeline.
"""

from dataclasses import dataclass
from typing import Protocol


class TranslationProfile(Protocol):
    """The dependency the generic translator needs from a model family."""

    name: str
    budget_first_pass: bool

    def prompt(
        self,
        text: str,
        source_language: str,
        target_language: str,
        budget_chars: int | None,
    ) -> str:
        """Return a single-turn prompt that must yield only the translation."""

    def sampling(self, seed: int) -> dict[str, float | int]:
        """Return llama.cpp decoding options for a reproducible call."""


@dataclass(frozen=True)
class HunyuanMTProfile:
    """Tencent HY-MT1.5 prompt and sampling contract."""

    name: str = "hunyuan-mt"
    budget_first_pass: bool = False

    def prompt(
        self,
        text: str,
        source_language: str,
        target_language: str,
        budget_chars: int | None,
    ) -> str:
        chinese = source_language.startswith("zh")
        if budget_chars is None:
            if chinese:
                return f"把下面的文本翻译成越南语，不要额外解释。\n\n{text}"
            return (
                "Translate the following segment into Vietnamese, without "
                f"additional explanation.\n\n{text}"
            )
        if chinese:
            return (
                "把下面的文本翻译成越南语，不要额外解释。"
                f"译文必须简洁，不超过 {budget_chars} 个字符，同时保留完整意思。\n\n{text}"
            )
        return (
            "Translate the following segment into Vietnamese, without additional "
            f"explanation. The translation must be concise and no longer than {budget_chars} "
            f"characters, while keeping the full meaning.\n\n{text}"
        )

    def sampling(self, seed: int) -> dict[str, float | int]:
        return {
            "temperature": 0.7,
            "top_p": 0.6,
            "top_k": 20,
            "repeat_penalty": 1.05,
            "seed": seed,
        }


@dataclass(frozen=True)
class LMT60Profile:
    """Officially documented NiuTrans LMT-60 translation prompt."""

    name: str = "lmt-60"
    budget_first_pass: bool = False

    def prompt(
        self,
        text: str,
        source_language: str,
        target_language: str,
        budget_chars: int | None,
    ) -> str:
        source = "Chinese" if source_language.startswith("zh") else "English"
        target = "Vietnamese" if target_language == "vi" else target_language
        concise = ""
        if budget_chars is not None:
            concise = f" Keep the translation under {budget_chars} characters."
        return (
            f"Translate the following text from {source} into {target}.{concise}\n"
            f"{source}: {text}\n{target}:"
        )

    def sampling(self, seed: int) -> dict[str, float | int]:
        # Greedy decoding is intentional for the speed benchmark.  It is
        # deterministic, avoids Qwen3 thinking output, and is the fastest
        # llama.cpp mode.  A later quality decision may inject a beam-capable
        # profile without changing the job/API pipeline.
        return {"temperature": 0.0, "seed": seed}


@dataclass(frozen=True)
class LMT60SubtitleProfile(LMT60Profile):
    """LMT-60 profile for concise Vietnamese speech that must fit each cue."""

    name: str = "lmt-60-subtitle"
    budget_first_pass: bool = True

    def prompt(
        self,
        text: str,
        source_language: str,
        target_language: str,
        budget_chars: int | None,
    ) -> str:
        source = "Chinese" if source_language.startswith("zh") else "English"
        target = "Vietnamese" if target_language == "vi" else target_language
        limit = (
            f" Keep it within {budget_chars} Vietnamese characters."
            if budget_chars is not None
            else ""
        )
        return (
            f"Translate this spoken subtitle from {source} into {target}.\n"
            "Output only the translation. Preserve facts, names, numbers, "
            "uncertainty and tone. Use concise, natural spoken Vietnamese; "
            "remove only nonessential spoken filler. Do not add explanation or detail."
            f"{limit}\n{source}: {text}\n{target}:"
        )


_PROFILES: dict[str, TranslationProfile] = {
    "hunyuan-mt": HunyuanMTProfile(),
    "lmt-60": LMT60Profile(),
    "lmt-60-subtitle": LMT60SubtitleProfile(),
}


def resolve_profile(name: str) -> TranslationProfile:
    """Compose a profile by explicit configuration, failing closed on typos."""
    try:
        return _PROFILES[name]
    except KeyError as exc:
        available = ", ".join(sorted(_PROFILES))
        raise ValueError(
            f"TRANSLATE_PROFILE must be one of {available}, not {name!r}"
        ) from exc
