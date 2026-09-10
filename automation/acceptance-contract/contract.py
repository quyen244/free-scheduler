"""Executable Step 1 contract for the automated re-up pipeline.

This module is intentionally independent of n8n and provider APIs. It gives
later services a deterministic oracle for source, chunk, asset, metadata,
approval, and delivery behavior.
"""

from __future__ import annotations

from copy import deepcopy
from math import ceil


RUNNABLE_TARGET_STATES = {
    "queued",
    "leased",
    "uploading",
    "published",
    "draft_delivered",
}
TONE_POLICY = "curiosity_driven_engaging_truthful"


class ContractViolation(ValueError):
    """Raised when a snapshot violates the accepted product contract."""


def expected_chunk_count(duration_s: int, policy: dict) -> int:
    """Choose a balanced count, treating 4-5 minutes as the desired range.

    Sources up to nine minutes intentionally remain one chunk. For longer
    sources, choose the count whose average duration is closest to the desired
    4-5 minute interval. This avoids tiny remainder chunks when an exact split
    inside that interval is mathematically impossible.
    """
    if duration_s <= policy["single_chunk_max_source_s"]:
        return 1

    target_min = policy["target_chunk_min_s"]
    target_max = policy["target_chunk_max_s"]
    target_mid = (target_min + target_max) / 2
    max_count = max(2, ceil(duration_s / max(1, target_min)) + 1)

    def score(count: int) -> tuple[float, float, int]:
        average = duration_s / count
        if average < target_min:
            range_distance = target_min - average
        elif average > target_max:
            range_distance = average - target_max
        else:
            range_distance = 0
        return range_distance, abs(average - target_mid), count

    return min(range(2, max_count + 1), key=score)


def balanced_durations(duration_s: int, count: int) -> list[int]:
    base, remainder = divmod(duration_s, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def expected_target_count(chunk_count: int, accounts: dict[str, list[str]]) -> int:
    return (
        len(accounts["youtube"])
        + chunk_count * len(accounts["facebook"])
        + chunk_count * len(accounts["tiktok"])
    )


def _metadata(platform: str, language: str) -> dict:
    common = {
        "platform": platform,
        "language": language,
        "tone_policy": TONE_POLICY,
        "hashtags": ["#noidung", "#kienthuc", "#video", "#trending", "#xuhuong"],
    }
    if platform == "youtube":
        return {
            **common,
            "title": "Điều đáng chú ý trong video này",
            "description": "Tóm tắt trung thực những nội dung chính của video.",
            "thumbnail_text": "Có gì đáng chú ý?",
        }
    return {
        **common,
        "caption": "Chi tiết nào trong phần này khiến bạn chú ý?",
    }


def build_snapshot(source_fixture: dict, policy: dict) -> dict:
    """Build a deterministic one-brand campaign snapshot from a source fixture."""
    duration_s = int(source_fixture["duration_s"])
    count = expected_chunk_count(duration_s, policy)
    durations = balanced_durations(duration_s, count)
    chunks = []
    cursor = 0
    for index, chunk_duration in enumerate(durations, start=1):
        end = cursor + chunk_duration
        chunks.append(
            {
                "id": f"chunk-{index}",
                "index": index,
                "name": f"part_{index}",
                "start_s": cursor,
                "end_s": end,
                "duration_s": chunk_duration,
                "boundary_shift_s": 0,
                "ends_on_transcript_segment": True,
            }
        )
        cursor = end

    revision_id = "revision-1"
    brand_id = "mock-brand"
    accounts = {
        "youtube": ["youtube-account-1"],
        "facebook": ["facebook-account-1"],
        "tiktok": ["tiktok-account-1"],
    }
    whole_asset = {
        "id": "asset-whole-16x9",
        "kind": "whole",
        "brand_id": brand_id,
        "width": 1920,
        "height": 1080,
        "stale": False,
        "path": f"outputs/brands/{brand_id}/revision/1/whole-16x9.mp4",
    }
    vertical_assets = [
        {
            "id": f"asset-{chunk['name']}-9x16",
            "kind": "chunk",
            "content_item_id": chunk["id"],
            "brand_id": brand_id,
            "width": 1080,
            "height": 1920,
            "stale": False,
            "path": (
                f"outputs/brands/{brand_id}/revision/1/vertical/"
                f"{chunk['name']}-9x16.mp4"
            ),
        }
        for chunk in chunks
    ]

    targets = []
    for account_id in accounts["youtube"]:
        targets.append(
            {
                "id": f"target-youtube-{account_id}",
                "platform": "youtube",
                "account_id": account_id,
                "unit": "whole",
                "content_item_id": "whole-video",
                "asset_id": whole_asset["id"],
                "revision_id": revision_id,
                "status": "pending_approval",
                "accepted_outcome": "published",
                "idempotency_key": f"{revision_id}:youtube:{account_id}:whole-video",
            }
        )
    for chunk, asset in zip(chunks, vertical_assets):
        for platform in ("facebook", "tiktok"):
            for account_id in accounts[platform]:
                targets.append(
                    {
                        "id": f"target-{platform}-{account_id}-{chunk['id']}",
                        "platform": platform,
                        "account_id": account_id,
                        "unit": "chunk",
                        "content_item_id": chunk["id"],
                        "asset_id": asset["id"],
                        "revision_id": revision_id,
                        "status": "pending_approval",
                        "accepted_outcome": (
                            "draft_delivered" if platform == "tiktok" else "published"
                        ),
                        "idempotency_key": (
                            f"{revision_id}:{platform}:{account_id}:{chunk['id']}"
                        ),
                    }
                )

    return {
        "schema_version": "step1.v1",
        "source": {
            "id": source_fixture["id"],
            "youtube_id": source_fixture["youtube_id"],
            "url": f"https://www.youtube.com/watch?v={source_fixture['youtube_id']}",
            "duration_s": duration_s,
            "width": source_fixture["width"],
            "height": source_fixture["height"],
            "readable": source_fixture.get("readable", True),
        },
        "chunks": chunks,
        "brand": {
            "id": brand_id,
            "watermark_path": "presets/mock-brand/watermark.png",
            "signature_music_path": "music/mock-signature.mp3",
            "accounts": accounts,
        },
        "revision": {
            "id": revision_id,
            "number": 1,
            "approval_state": "pending",
            "approved_revision_id": None,
        },
        "metadata": {
            "youtube": _metadata("youtube", policy["metadata_language"]),
            "chunks": [
                {
                    "content_item_id": chunk["id"],
                    "visual": {
                        "language": policy["metadata_language"],
                        "tone_policy": TONE_POLICY,
                        "hook": "Điều gì xảy ra trong phần này?",
                        "supporting_caption": "Nội dung được tóm tắt từ chính video.",
                    },
                    "facebook": _metadata("facebook", policy["metadata_language"]),
                    "tiktok": _metadata("tiktok", policy["metadata_language"]),
                }
                for chunk in chunks
            ],
        },
        "assets": {"whole": whole_asset, "vertical": vertical_assets},
        "targets": targets,
        "audit": [],
    }


def validate_source(source: dict, policy: dict) -> list[str]:
    errors = []
    if not source.get("readable", False):
        errors.append("corrupt: source media is not readable")
    duration = source.get("duration_s", 0)
    if not policy["min_source_duration_s"] <= duration <= policy["max_source_duration_s"]:
        errors.append("duration: source must be between 5 and 20 minutes inclusive")
    if source.get("height", 0) < policy["min_source_height"]:
        errors.append("resolution: source height must be at least 720 pixels")
    if not source.get("youtube_id") or not source.get("url", "").startswith(
        "https://www.youtube.com/watch?v="
    ):
        errors.append("identity: normalized YouTube identity is required")
    return errors


def _emoji_count(value: object) -> int:
    if isinstance(value, dict):
        return sum(_emoji_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_emoji_count(item) for item in value)
    if not isinstance(value, str):
        return 0
    return sum(
        1
        for character in value
        if 0x1F300 <= ord(character) <= 0x1FAFF
        or 0x2600 <= ord(character) <= 0x27BF
    )


def _validate_platform_metadata(item: dict, policy: dict, label: str) -> list[str]:
    errors = []
    if item.get("language") != policy["metadata_language"]:
        errors.append(f"metadata: {label} must use Vietnamese")
    if item.get("tone_policy") != TONE_POLICY:
        errors.append(f"metadata: {label} must use the truthful curiosity tone")
    hashtags = item.get("hashtags", [])
    if len(hashtags) != policy["hashtags_per_platform_object"]:
        errors.append(f"metadata: {label} must contain exactly five hashtags")
    if len(set(hashtags)) != len(hashtags) or any(
        not tag.startswith("#") or any(char.isspace() for char in tag)
        for tag in hashtags
    ):
        errors.append(f"metadata: {label} contains an invalid or duplicate hashtag")
    if _emoji_count(item) > policy["max_emojis_per_platform_object"]:
        errors.append(f"metadata: {label} contains more than two emojis")
    return errors


def validate_snapshot(snapshot: dict, policy: dict) -> list[str]:
    errors = validate_source(snapshot.get("source", {}), policy)
    source = snapshot.get("source", {})
    chunks = snapshot.get("chunks", [])
    duration = source.get("duration_s", 0)

    if not chunks:
        errors.append("chunks: at least one chunk is required")
    elif policy["min_source_duration_s"] <= duration <= policy["max_source_duration_s"]:
        expected_count = expected_chunk_count(duration, policy)
        if len(chunks) != expected_count:
            errors.append(f"chunks: expected {expected_count}, received {len(chunks)}")
        cursor = 0
        for index, chunk in enumerate(chunks, start=1):
            if chunk.get("index") != index or chunk.get("name") != f"part_{index}":
                errors.append("chunks: names and indexes must be contiguous and one-based")
            if chunk.get("start_s") != cursor or chunk.get("end_s", 0) <= cursor:
                errors.append("chunks: chunks must be ordered, unique, and non-overlapping")
            if chunk.get("duration_s") != chunk.get("end_s", 0) - chunk.get("start_s", 0):
                errors.append("chunks: stored duration does not match its boundaries")
            if abs(chunk.get("boundary_shift_s", 0)) > policy["boundary_shift_max_s"]:
                errors.append("chunks: a boundary moved more than 15 seconds")
            if not chunk.get("ends_on_transcript_segment", False):
                errors.append("chunks: a boundary does not end on a transcript segment")
            cursor = chunk.get("end_s", cursor)
        if cursor != duration:
            errors.append("chunks: chunks must cover the complete source exactly once")
        if len(chunks) > 1:
            durations = [chunk["duration_s"] for chunk in chunks]
            if max(durations) - min(durations) > 30:
                errors.append("chunks: balanced chunks may differ by at most 30 seconds")

    metadata = snapshot.get("metadata", {})
    errors.extend(_validate_platform_metadata(metadata.get("youtube", {}), policy, "youtube"))
    chunk_metadata = metadata.get("chunks", [])
    if len(chunk_metadata) != len(chunks):
        errors.append("metadata: every chunk must have exactly one metadata object")
    for item in chunk_metadata:
        visual = item.get("visual", {})
        if visual.get("language") != policy["metadata_language"]:
            errors.append("metadata: visual text must use Vietnamese")
        if visual.get("tone_policy") != TONE_POLICY:
            errors.append("metadata: visual text must use the truthful curiosity tone")
        errors.extend(_validate_platform_metadata(item.get("facebook", {}), policy, "facebook"))
        errors.extend(_validate_platform_metadata(item.get("tiktok", {}), policy, "tiktok"))

    brand = snapshot.get("brand", {})
    assets = snapshot.get("assets", {})
    whole_asset = assets.get("whole")
    vertical_assets = assets.get("vertical", [])
    if not whole_asset or (whole_asset.get("width"), whole_asset.get("height")) != (1920, 1080):
        errors.append("assets: one branded 1920x1080 whole asset is required")
    if len(vertical_assets) != len(chunks):
        errors.append("assets: one branded vertical asset is required per chunk")
    vertical_by_content = {asset.get("content_item_id"): asset for asset in vertical_assets}
    if any((asset.get("width"), asset.get("height")) != (1080, 1920) for asset in vertical_assets):
        errors.append("assets: every chunk asset must be 1080x1920")
    if whole_asset and whole_asset.get("brand_id") != brand.get("id"):
        errors.append("assets: the whole asset belongs to the wrong brand")
    if any(asset.get("brand_id") != brand.get("id") for asset in vertical_assets):
        errors.append("assets: a vertical asset belongs to the wrong brand")
    if whole_asset and whole_asset.get("stale"):
        errors.append("assets: stale whole asset must be rerendered before approval")
    if any(asset.get("stale") for asset in vertical_assets):
        errors.append("assets: stale vertical assets must be rerendered before approval")

    targets = snapshot.get("targets", [])
    accounts = brand.get("accounts", {"youtube": [], "facebook": [], "tiktok": []})
    target_count = expected_target_count(len(chunks), accounts)
    if len(targets) != target_count:
        errors.append(f"targets: expected {target_count}, received {len(targets)}")
    revision = snapshot.get("revision", {})
    keys = [target.get("idempotency_key") for target in targets]
    if len(keys) != len(set(keys)):
        errors.append("targets: idempotency keys must be unique")
    for target in targets:
        platform = target.get("platform")
        if target.get("revision_id") != revision.get("id"):
            errors.append("targets: every target must reference the current revision")
        if revision.get("id") not in target.get("idempotency_key", ""):
            errors.append("targets: idempotency key must include the revision")
        if platform == "youtube":
            if target.get("unit") != "whole" or not whole_asset or target.get("asset_id") != whole_asset.get("id"):
                errors.append("targets: YouTube must reference the branded whole asset")
        elif platform in {"facebook", "tiktok"}:
            asset = vertical_by_content.get(target.get("content_item_id"))
            if target.get("unit") != "chunk" or not asset or target.get("asset_id") != asset.get("id"):
                errors.append(f"targets: {platform} must reference its same-brand chunk asset")
            if platform == "tiktok":
                if target.get("accepted_outcome") != "draft_delivered":
                    errors.append("targets: TikTok success must mean draft_delivered")
                if target.get("status") == "published":
                    errors.append("targets: the MVP must not claim TikTok was publicly published")
        else:
            errors.append("targets: unsupported platform")

    if revision.get("approval_state") != "approved":
        if any(target.get("status") in RUNNABLE_TARGET_STATES for target in targets):
            errors.append("approval: no target may run before revision approval")
    elif revision.get("approved_revision_id") != revision.get("id"):
        errors.append("approval: approval must reference the exact current revision")
    return errors


def assert_valid(snapshot: dict, policy: dict) -> None:
    errors = validate_snapshot(snapshot, policy)
    if errors:
        raise ContractViolation("\n".join(errors))


def approve(snapshot: dict, revision_id: str, policy: dict) -> bool:
    """Approve every target once. Replaying the same approval is a no-op."""
    revision = snapshot["revision"]
    if revision_id != revision["id"]:
        raise ContractViolation("approval: stale or unknown revision")
    if revision["approval_state"] == "approved":
        return False
    errors = validate_snapshot(snapshot, policy)
    if errors:
        raise ContractViolation("approval blocked:\n" + "\n".join(errors))
    revision["approval_state"] = "approved"
    revision["approved_revision_id"] = revision_id
    for target in snapshot["targets"]:
        target["status"] = "queued"
    snapshot["audit"].append({"event": "revision_approved", "revision_id": revision_id})
    return True


def apply_edit(snapshot: dict, edit_kind: str) -> dict:
    """Create a new revision and invalidate prior approval after a material edit."""
    if edit_kind not in {"post_metadata", "visual_text", "brand", "target"}:
        raise ContractViolation(f"edit: unsupported kind {edit_kind}")
    edited = deepcopy(snapshot)
    number = edited["revision"]["number"] + 1
    revision_id = f"revision-{number}"
    edited["revision"] = {
        "id": revision_id,
        "number": number,
        "approval_state": "pending",
        "approved_revision_id": None,
    }
    rerender_required = edit_kind in {"visual_text", "brand"}
    edited["assets"]["whole"]["stale"] = rerender_required
    for asset in edited["assets"]["vertical"]:
        asset["stale"] = rerender_required
    for target in edited["targets"]:
        target["revision_id"] = revision_id
        target["status"] = "pending_approval"
        target["idempotency_key"] = (
            f"{revision_id}:{target['platform']}:{target['account_id']}:"
            f"{target['content_item_id']}"
        )
    edited["audit"].append(
        {
            "event": "revision_created_after_edit",
            "revision_id": revision_id,
            "edit_kind": edit_kind,
            "rerender_required": rerender_required,
        }
    )
    return edited
