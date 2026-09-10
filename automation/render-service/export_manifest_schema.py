"""Regenerate the checked-in media-manifest JSON Schema."""

import json
from pathlib import Path

import manifest


target = Path(__file__).parent / "contracts" / "media-manifest.v1.schema.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    json.dumps(manifest.json_schema(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(target)
