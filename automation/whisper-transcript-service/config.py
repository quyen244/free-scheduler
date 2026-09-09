import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    whisper_model: str
    model_dir: Path
    data_dir: Path


def load_settings() -> Settings:
    return Settings(
        whisper_model=os.environ.get("WHISPER_MODEL", "base"),
        model_dir=Path(os.environ.get("MODEL_DIR", "models")),
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
    )


settings = load_settings()
