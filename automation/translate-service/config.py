import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    model_path: Path
    data_dir: Path
    # llama.cpp threads. Left at 0 so llama.cpp picks the core count itself;
    # set it when the box is doing something else at the same time.
    threads: int
    # How many of the model's layers llama.cpp offloads to the GPU. 0 keeps
    # everything on the CPU, which is the only thing the CPU image can do;
    # -1 offloads all of them. A whole 1.8B at Q4_K_M is 1.13 GB of weights
    # plus its KV cache, which fits the 6 GB card beside Whisper's ~231 MiB.
    gpu_layers: int
    context_tokens: int
    max_output_tokens: int
    # Speed-up the voice stage is allowed to apply before a segment sounds
    # rushed. Translation aims to stay inside it; the render service warns on
    # the same band as `TTS_MAX_RATIO`.
    target_ratio: float
    # Off restores the single-pass behaviour exactly, which is what makes a
    # before/after benchmark a one-variable comparison.
    budget_enabled: bool
    # How many seeds the budgeted retry may try before giving up on a segment.
    # Each one costs a model call, and only on segments that do not fit.
    retry_attempts: int


def _int(name: str, default: str, minimum: int) -> int:
    raw = os.environ.get(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number, not {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, not {value}")
    return value


def _float(name: str, default: str, minimum: float) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, not {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, not {value}")
    return value


def _bool(name: str, default: str) -> bool:
    raw = os.environ.get(name, default).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a yes/no value, not {raw!r}")


def load_settings() -> Settings:
    return Settings(
        # A path, not a repo id: the weights are bind-mounted, never fetched at
        # runtime. Swapping to `tencent/Hy-MT2-1.8B-GGUF` (apache-2.0, same
        # size, same languages, same quant filenames) is this one variable.
        model_path=Path(
            os.environ.get("TRANSLATE_MODEL", "models/HY-MT1.5-1.8B-Q4_K_M.gguf")
        ),
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        # 0 means "let llama.cpp pick the core count", so it is a valid floor.
        threads=_int("LLAMA_THREADS", "0", minimum=0),
        # -1 is a real value here ("every layer"), so the floor is not 0.
        gpu_layers=_int("LLAMA_GPU_LAYERS", "0", minimum=-1),
        context_tokens=_int("LLAMA_CONTEXT", "2048", minimum=256),
        max_output_tokens=_int("LLAMA_MAX_OUTPUT", "512", minimum=16),
        target_ratio=_float("TRANSLATE_TARGET_RATIO", "1.35", minimum=1.0),
        budget_enabled=_bool("TRANSLATE_BUDGET", "1"),
        retry_attempts=_int("TRANSLATE_RETRIES", "2", minimum=1),
    )


settings = load_settings()
