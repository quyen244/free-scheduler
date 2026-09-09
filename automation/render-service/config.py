import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    # Where the ZeroTTS weights are cached. Bind-mounted, so a rebuild does not
    # re-download 900 MB.
    model_dir: Path
    voice: str
    # The preset a render falls back to when the request does not name one.
    # A default rather than a required field: this box runs one channel, and
    # making every caller repeat its name is how they drift apart.
    preset: str
    # ONNX Runtime intra-op threads. More is slower, measurably: on this
    # 12-core box, 4 threads runs at 11 s per segment, 8 at 16 s and 12 at
    # 24 s. The graph is small enough that the synchronisation costs more than
    # the parallelism buys, so ZeroTTS's own default of 4 is kept.
    tts_threads: int
    # How many ONNX sessions may run at once. Raising `tts_threads` makes the
    # graph slower, so this is the only knob that uses the idle cores: one
    # session at four threads leaves seven of twelve busy doing nothing.
    # Each session holds its own copy of the weights, so this is bounded by
    # memory, not by cores - see render-service/README.md.
    tts_workers: int
    # Which ONNX Runtime execution provider the sessions must use: "cpu" or
    # "cuda". Explicit, and not "whichever is available", because ONNX Runtime
    # builds a session on the CPU without complaining when the CUDA libraries
    # are missing. That is how a GPU image reports a plausible CPU number under
    # a GPU heading - see reports/tts-spike.md. Asking by name lets the loader
    # check what it actually got.
    tts_provider: str
    # The needed-rate band. Outside it the segment is still fitted — timing is
    # never sacrificed — but it is reported. See timing.py.
    min_ratio: float
    max_ratio: float


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


def _provider(name: str, default: str) -> str:
    """The execution provider, lower-cased, or a clear reason why not."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value not in ("cpu", "cuda"):
        raise ValueError(f"{name} must be 'cpu' or 'cuda', not {raw!r}")
    return value


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        model_dir=Path(os.environ.get("TTS_MODEL_DIR", "/models")),
        voice=os.environ.get("TTS_VOICE", "maichi"),
        preset=os.environ.get("DEFAULT_PRESET", "bi-mat-bi-an"),
        tts_threads=_int("TTS_THREADS", 4),
        tts_workers=_int("TTS_WORKERS", 1),
        tts_provider=_provider("TTS_PROVIDER", "cpu"),
        min_ratio=_float("TTS_MIN_RATIO", 0.75),
        max_ratio=_float("TTS_MAX_RATIO", 1.35),
    )


settings = load_settings()

# Set before anything imports huggingface_hub, which reads it once at import
# time. Without it the ~900 MB of ZeroTTS weights land in the container's own
# filesystem and are re-downloaded on every rebuild.
os.environ.setdefault("HF_HOME", str(settings.model_dir))
