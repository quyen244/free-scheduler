import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path


def load_settings() -> Settings:
    return Settings(data_dir=Path(os.environ.get("DATA_DIR", "/data")))


settings = load_settings()
