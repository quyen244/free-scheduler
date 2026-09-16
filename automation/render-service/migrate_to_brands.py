"""Fold the old visual presets and brand profiles into brand folders.

The old model kept a layout in ``preset-editor/published/<id>/v<n>.json`` and
the artwork in ``presets/brand-assets/<brand>/``, joined at render time.  The
new model keeps both in ``presets/brand/<brand_id>/``.  One old layout could
dress several brands, so this produces one new brand per (layout, brand) pair
the operator actually had.

Nothing is deleted.  The old directories stay exactly where they are until the
new ones have been rendered from and the result inspected; re-running this
script is safe and simply rewrites the draft.

    docker compose exec render-service python migrate_to_brands.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import brands
import visual_preset
from config import settings

# The old shared music folder.  A brand now owns its audio like it owns its
# artwork, so the file is copied in rather than referenced across the tree.
MUSIC_DIR = settings.data_dir / "music"


def _image_layer(layer_id: str, asset: str, rect: dict, fit: str = "contain") -> dict:
    return {
        "kind": "image",
        "id": layer_id,
        "asset": asset,
        "x": rect["x"],
        "y": rect["y"],
        "w": rect["w"],
        "h": rect["h"],
        "z": rect.get("z", 10),
        "visible": rect.get("visible", True),
        "fit": fit,
        "opacity": rect.get("opacity", 1.0),
    }


def _layers(layout, has: dict[str, str]) -> list[dict]:
    """Turn one old AspectLayout into the new flat, ordered layer list."""
    result: list[dict] = []
    if "background" in has:
        # The background was an implicit floor in the old schema.  Here it is
        # an ordinary image layer at z=0, which is what lets the editor show it
        # in the layer list and lets an operator put something under it.
        result.append(
            _image_layer(
                "background",
                has["background"],
                {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "z": 0},
                fit="fill",
            )
        )
    main = layout.main_video
    result.append(
        {
            "kind": "main_video",
            "id": "video",
            "x": main.x,
            "y": main.y,
            "w": main.w,
            "h": main.h,
            "z": main.z,
            "visible": main.visible,
            "fit": "contain",
        }
    )
    for index, region in enumerate(layout.blur_regions, start=1):
        result.append(
            {
                "kind": "blur",
                "id": f"blur-{index}",
                "x": region.x,
                "y": region.y,
                "w": region.w,
                "h": region.h,
            }
        )
    if "host" in has:
        host = layout.host
        result.append(
            {
                "kind": "host",
                "id": "host",
                "asset": has["host"],
                "x": host.x,
                "y": host.y,
                "w": host.w,
                "h": host.h,
                "z": host.z,
                "visible": host.visible,
            }
        )
    for slot in ("logo", "watermark"):
        if slot not in has:
            continue
        rect = getattr(layout, slot)
        result.append(
            _image_layer(
                slot,
                has[slot],
                {
                    "x": rect.x,
                    "y": rect.y,
                    "w": rect.w,
                    "h": rect.h,
                    "z": rect.z,
                    "visible": rect.visible,
                    "opacity": has.get(f"{slot}_opacity", 1.0),
                },
            )
        )
    for text in layout.text_layers:
        body = text.model_dump(mode="json")
        body["kind"] = "text"
        result.append(body)
    subtitle = layout.subtitle.model_dump(mode="json")
    subtitle.update({"kind": "subtitle", "id": "subtitle"})
    result.append(subtitle)
    return result


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        raise SystemExit(f"missing source file: {source}")
    shutil.copy2(source, destination)


def migrate(preset_id: str, revision: int, brand_id: str, display_name: str) -> str:
    preset = visual_preset.load_published(preset_id, revision)
    old_assets = visual_preset.assets_dir(preset_id)
    new_assets = brands.assets_dir(brand_id)
    new_assets.mkdir(parents=True, exist_ok=True)

    assets: list[dict] = []
    has: dict[str, str] = {}

    background = preset.vertical.background_asset or preset.landscape.background_asset
    if background:
        _copy(old_assets / background, new_assets / background)
        assets.append(
            {"id": "background", "kind": "image", "file": background, "role": "background"}
        )
        has["background"] = "background"

    if preset.host_asset and preset.host_alpha_revision:
        _copy(old_assets / preset.host_asset, new_assets / preset.host_asset)
        alpha_source = visual_preset.alpha_mask_path(preset_id, preset.host_alpha_revision)
        _copy(alpha_source, new_assets / "host-alpha.mp4")
        assets.append(
            {
                "id": "host",
                "kind": "video",
                "file": preset.host_asset,
                "role": "host",
                "alpha_file": "host-alpha.mp4",
                "alpha_revision": preset.host_alpha_revision,
            }
        )
        has["host"] = "host"

    # Where the artwork comes from: the brand profile if one exists under this
    # id, otherwise the editor's own asset folder.  The old `an-so` layout had
    # both a logo and a watermark sitting beside its background.
    profile_path = settings.data_dir / "presets" / "brands" / f"{brand_id}.json"
    profile = (
        json.loads(profile_path.read_text(encoding="utf-8"))
        if profile_path.is_file()
        else {}
    )
    for slot, default in (("logo", "logo.png"), ("watermark", "watermark.png")):
        entry = profile.get(slot) or {}
        if entry.get("file"):
            source = (
                settings.data_dir / "presets" / "brand-assets" / brand_id / entry["file"]
            )
            filename = entry["file"] if slot in entry["file"] else f"{slot}.png"
        else:
            source = old_assets / default
            filename = default
        if not source.is_file():
            continue
        _copy(source, new_assets / filename)
        assets.append({"id": slot, "kind": "image", "file": filename, "role": slot})
        has[slot] = slot
        has[f"{slot}_opacity"] = float(entry.get("opacity", 1.0))

    music = profile.get("signature_music")
    signature = None
    if music and (MUSIC_DIR / music["file"]).is_file():
        _copy(MUSIC_DIR / music["file"], new_assets / music["file"])
        signature = music

    draft = {
        "schema_version": brands.SCHEMA_VERSION,
        "brand_id": brand_id,
        "display_name": display_name,
        "status": "draft",
        "assets": assets,
        "signature_music": signature,
        "vertical": {
            "canvas": "vertical_9_16",
            "layers": _layers(preset.vertical, has),
            "intro_enabled": preset.vertical.intro_enabled,
            "intro_duration_s": preset.vertical.intro_duration_s,
        },
        "landscape": {
            "canvas": "landscape_16_9",
            "layers": _layers(preset.landscape, has),
            "intro_enabled": preset.landscape.intro_enabled,
            "intro_duration_s": preset.landscape.intro_duration_s,
        },
    }
    brands.save_draft(draft)
    published = brands.publish(draft)
    return f"{brand_id}@{published.revision} sha {published.content_sha256[:16]}"


def main() -> int:
    pairs = [
        # (source layout, revision, new brand id, display name)
        ("an-so", 2, "an-so", "Ẩn Số"),
        ("an-so", 2, "mock-brand", "Mystery VN (Mock)"),
    ]
    for preset_id, revision, brand_id, display_name in pairs:
        try:
            print("migrated", migrate(preset_id, revision, brand_id, display_name))
        except Exception as exc:  # a one-shot script; the message is the output
            print(f"FAILED {brand_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
