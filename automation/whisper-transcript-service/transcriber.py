import logging
from pathlib import Path

from faster_whisper import WhisperModel

from config import settings
from errors import TranscriptionError

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = _load_model()
    return _model


def is_loaded() -> bool:
    return _model is not None


def transcribe(path: Path) -> tuple[str, list[dict[str, object]], str]:
    try:
        segments_iter, info = get_model().transcribe(str(path))

        segments: list[dict[str, object]] = []
        texts: list[str] = []
        for segment in segments_iter:
            text = segment.text.strip()
            segments.append({"start": segment.start, "end": segment.end, "text": text})
            texts.append(text)
    except Exception as exc:
        raise TranscriptionError(f"failed to transcribe {path}: {exc}") from exc

    # The detected language decides whether the translate stage runs at all,
    # so it is returned rather than discarded.
    return " ".join(texts), segments, info.language


def _load_model() -> WhisperModel:
    device, compute_type = _resolve_device()
    logger.info(
        "Loading Whisper model %r on device=%s compute_type=%s",
        settings.whisper_model,
        device,
        compute_type,
    )
    return WhisperModel(
        settings.whisper_model,
        device=device,
        compute_type=compute_type,
        download_root=str(settings.model_dir),
    )


def _resolve_device() -> tuple[str, str]:
    import ctranslate2

    if ctranslate2.get_cuda_device_count() > 0:
        return "cuda", "float16"
    return "cpu", "int8"
