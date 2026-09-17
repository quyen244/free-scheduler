import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    # Where the TTS weights are cached. Bind-mounted, so a rebuild does not
    # re-download them.
    model_dir: Path
    voice: str
    # The preset a render falls back to when the request does not name one.
    # A default rather than a required field: this box runs one channel, and
    # making every caller repeat its name is how they drift apart.
    preset: str
    # How many chunks VieNeu folds into one forward pass. 32 is where the
    # throughput curve flattens on this card: measured on 64 real cues, RTF
    # went 5.93x at 1, 9.77x at 10, 12.99x at 20 and 20.62x at 32, then stopped
    # moving while peak VRAM kept climbing (2.0 GB at 32, 3.6 GB at 64). See
    # reports/vieneu-tts-gpu-benchmark-2026-09-17.md.
    tts_batch_size: int
    # "cuda" or "cpu", named explicitly rather than probed. A GPU deployment
    # that quietly fell back to the CPU would report a plausible number under a
    # GPU heading, which is the same trap the old ONNX provider setting existed
    # to avoid; the loader checks that it got what was asked for.
    tts_device: str
    # How many delivery assets may encode at once. One ffmpeg render keeps
    # only 2.5 of this box's 12 logical cores busy: its filter graph is a
    # chain, and `libass`, `drawtext`, `alphamerge` and `overlay` do not slice
    # across cores. The assets of a revision are independent files, so the
    # idle cores are reachable by running several of them. Measured on the
    # 9:16 an-so layout: 12.34 s per chunk at 1 worker, 8.20 s at 2, 6.64 s at
    # 4 - 1.86x throughput. It stops there because 12 logical cores are 6
    # physical ones, and 6 is where the box saturates. NVENC is not the limit:
    # at 4 workers the encoder sat at 33 % and the GPU held 1.1 GB of 6 GB.
    render_workers: int
    # The needed-rate band. Outside it the segment is still fitted — timing is
    # never sacrificed — but it is reported. See timing.py.
    min_ratio: float
    max_ratio: float
    # RVM is deliberately kept out of the Docker layer. The checkpoint is a
    # runtime asset on the bind mount, just like the TTS weights, so rebuilding
    # the service never downloads it again.
    rvm_model_dir: Path
    # Text bindings the renderer refuses to draw, service-wide. The layouts
    # keep their title layer - position, size and colour survive - but nothing
    # is drawn for it, so turning titles back on is one variable and no brand
    # revision. Confirmed 2026-09-17: a re-up carries its title in the platform
    # post, not burned into the picture. Set `RENDER_FROZEN_TEXT=` (empty) to
    # draw them again.
    frozen_text: frozenset[str]


def _int(name: str, default: int, minimum: int = 1) -> int:
    """An integer from the environment, or a clear reason why not.

    A typo in a compose file should say which variable is wrong. Left to
    `int()` it surfaces as a bare ValueError at import time, and the container
    restart-loops with a traceback that names neither the variable nor the
    value.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, not {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, not {value}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, not {raw!r}") from None
    if value <= minimum:
        raise ValueError(f"{name} must be greater than {minimum}, not {value}")
    return value


def _device(name: str, default: str) -> str:
    """The torch device, lower-cased, or a clear reason why not."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value not in ("cpu", "cuda"):
        raise ValueError(f"{name} must be 'cpu' or 'cuda', not {raw!r}")
    return value


def _frozen_text(name: str, default: str) -> frozenset[str]:
    """Text layers to skip, by binding or by id.

    Empty is meaningful here and is *not* the same as unset: an unset variable
    takes the default, while `RENDER_FROZEN_TEXT=` is an operator saying
    "freeze nothing", which is how the title comes back.
    """
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        model_dir=Path(os.environ.get("TTS_MODEL_DIR", "/models")),
        # A VieNeu preset name or one of its aliases. "Minh Quân" is the alias
        # of "Minh Quân Pro", the model's own default preset.
        voice=os.environ.get("TTS_VOICE", "Minh Quân"),
        preset=os.environ.get("DEFAULT_PRESET", "bi-mat-bi-an"),
        tts_batch_size=_int("TTS_BATCH_SIZE", 32),
        tts_device=_device("TTS_DEVICE", "cuda"),
        render_workers=max(_int("RENDER_WORKERS", 3), 1),
        min_ratio=_float("TTS_MIN_RATIO", 0.75),
        max_ratio=_float("TTS_MAX_RATIO", 1.35),
        rvm_model_dir=Path(os.environ.get("RVM_MODEL_DIR", "/models/rvm")),
        frozen_text=_frozen_text("RENDER_FROZEN_TEXT", "title"),
    )


settings = load_settings()

# Set before anything imports huggingface_hub, which reads it once at import
# time. Without it the VieNeu weights land in the container's own filesystem
# and are re-downloaded on every rebuild.
os.environ.setdefault("HF_HOME", str(settings.model_dir))
