"""Validate root render-plan.yaml and submit a selective render preview.

Run from the repository root:
    python scripts/submit_render_plan.py

Set RENDER_SERVICE_URL only when the local default is not appropriate. No
credentials are read or sent by this utility.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "render-plan.yaml"
ENDPOINT = os.environ.get("RENDER_SERVICE_URL", "http://127.0.0.1:8003")


def fail(message: str) -> None:
    raise ValueError(f"render-plan.yaml: {message}")


def normalize(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        fail("must be a mapping")
    allowed = {"version", "video_id", "request_id", "brands"}
    unknown = set(raw) - allowed
    if unknown:
        fail(f"unknown top-level keys: {', '.join(sorted(unknown))}")
    if raw.get("version") != 1:
        fail("version must be 1")
    video_id = raw.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        fail("video_id must be a non-empty string")
    brands = raw.get("brands")
    if not isinstance(brands, dict) or not brands or len(brands) > 10:
        fail("brands must contain one to ten entries")
    result: dict[str, Any] = {"video_id": video_id, "brands": {}}
    if "request_id" in raw:
        if not isinstance(raw["request_id"], str) or not raw["request_id"]:
            fail("request_id must be a non-empty string")
        result["request_id"] = raw["request_id"]
    for brand_id, selection in brands.items():
        if not isinstance(brand_id, str) or not isinstance(selection, dict):
            fail("each brand id and selection must be a mapping entry")
        unknown_selection = set(selection) - {"revision", "variants", "chunks"}
        if unknown_selection:
            fail(f"brands.{brand_id}: unknown keys: {', '.join(sorted(unknown_selection))}")
        revision = selection.get("revision", "latest")
        variants = selection.get("variants", "all")
        chunks = selection.get("chunks")
        if revision != "latest" and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 1):
            fail(f"brands.{brand_id}.revision must be a positive integer or latest")
        if variants not in {"all", "landscape", "vertical"}:
            fail(f"brands.{brand_id}.variants must be all, landscape, or vertical")
        if chunks is not None:
            if variants == "landscape":
                fail(f"brands.{brand_id}.chunks cannot be used with landscape")
            if not isinstance(chunks, list) or not chunks:
                fail(f"brands.{brand_id}.chunks must be a non-empty list")
            if any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in chunks):
                fail(f"brands.{brand_id}.chunks must contain one-based positive integers")
            if len(chunks) != len(set(chunks)):
                fail(f"brands.{brand_id}.chunks must not contain duplicates")
        result["brands"][brand_id] = {"revision": revision, "variants": variants}
        if chunks is not None:
            result["brands"][brand_id]["chunks"] = chunks
    return result


def main() -> int:
    try:
        payload = normalize(yaml.safe_load(PLAN_PATH.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    request = urllib.request.Request(
        f"{ENDPOINT.rstrip('/')}/media-revision/preview-jobs",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            accepted = json.load(response)
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"render-service unavailable: {exc.reason}", file=sys.stderr)
        return 1
    job_id = accepted["job_id"]
    print(json.dumps({
        "job_id": job_id,
        "state": accepted["state"],
        "brand_revisions": accepted.get("brand_revisions"),
        "poll_url": f"{ENDPOINT.rstrip('/')}/jobs/{job_id}",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
