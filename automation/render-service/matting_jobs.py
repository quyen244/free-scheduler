"""Small durable status records for editor-only RVM work.

These are not pipeline jobs: a host is a reusable brand asset, not a source
video. Keeping its lifecycle outside ``pipeline.db`` prevents an editor upload
from pretending to be part of an n8n production run.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import matting
import visual_preset


def create(
    preset_id: str, host_asset: str, corrections: list[dict[str, object]]
) -> dict[str, object]:
    if "/" in host_asset or "\\" in host_asset or host_asset in ("", ".", ".."):
        raise ValueError("host_asset must be a plain filename")
    source = visual_preset.assets_dir(preset_id) / host_asset
    if not source.is_file():
        raise FileNotFoundError(f"host asset is missing: {host_asset}")
    job_id = uuid.uuid4().hex
    body: dict[str, object] = {"job_id": job_id, "state": "queued", "preset_id": preset_id, "host_asset": host_asset, "corrections": corrections}
    _write(job_id, body)
    return body


def run(job_id: str) -> None:
    body = read(job_id)
    body["state"] = "running"
    _write(job_id, body)
    try:
        preset_id = str(body["preset_id"])
        host_asset = str(body["host_asset"])
        result = matting.mat_file(
            visual_preset.assets_dir(preset_id) / host_asset,
            visual_preset.root() / "matting" / preset_id / job_id,
            corrections=list(body.get("corrections") or []),
        )
    except Exception as exc:  # top of background worker, persisted for UI polling
        body.update({"state": "failed", "error": f"{type(exc).__name__}: {exc}"})
    else:
        body.update({"state": "done", "result": result})
    _write(job_id, body)


def read(job_id: str) -> dict[str, object]:
    path = _path(job_id)
    if not path.is_file():
        raise FileNotFoundError(f"no matting job {job_id!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def _path(job_id: str) -> Path:
    if len(job_id) != 32 or any(character not in "0123456789abcdef" for character in job_id):
        raise FileNotFoundError(f"not a valid matting job id: {job_id!r}")
    return visual_preset.root() / "matting-jobs" / f"{job_id}.json"


def _write(job_id: str, body: dict[str, object]) -> None:
    path = _path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
