"""The brand-owned topology, and the rules that keep it apart from lineage.

A `brand.v1` layout owns the footage rectangle, the blur regions and the
subtitle, so brands cannot share a clean master and every delivery asset is
rendered once per brand straight from the source. That makes two shapes a
manifest can legitimately have, and each must reject the other's evidence.
"""

import pytest
from pydantic import ValidationError

import manifest
import render

pytestmark = pytest.mark.no_pipeline

VIDEO_ID = "brandTopoAA"
SOURCE_SHA = "ab" * 32
CHUNKS = ["part_1", "part_2"]


def _asset(role, content_item_id, *, brand_id=None, lineage_asset_id=None):
    width, height = (1080, 1920) if role.endswith("vertical") else (1920, 1080)
    return manifest.MediaAsset(
        asset_id=manifest.deterministic_asset_id(
            VIDEO_ID, 1, role, content_item_id, brand_id
        ),
        video_id=VIDEO_ID,
        render_revision=1,
        role=role,
        content_item_id=content_item_id,
        brand_id=brand_id,
        lineage_asset_id=lineage_asset_id,
        path=str(
            manifest.expected_asset_path(VIDEO_ID, 1, role, content_item_id, brand_id)
        ),
        sha256="cd" * 32,
        bytes=4096,
        probe=manifest.ProbeEvidence(
            duration_s=12.0,
            width=width,
            height=height,
            video_codec="h264",
            audio_codec="aac",
        ),
    )


def _brand_owned_assets(*brand_ids):
    assets = []
    for brand_id in brand_ids:
        assets.append(_asset("branded_whole", manifest.WHOLE_ITEM, brand_id=brand_id))
        assets.extend(
            _asset("branded_vertical", part, brand_id=brand_id) for part in CHUNKS
        )
    return assets


def _manifest(**overrides):
    body = {
        "video_id": VIDEO_ID,
        "render_revision": 1,
        "source_sha256": SOURCE_SHA,
        "state": "ready",
        "topology": "brand_owned",
        "chunk_names": CHUNKS,
        "brand_ids": ["an-so", "mock-brand"],
        "brand_revisions": {"an-so": 1, "mock-brand": 3},
        "assets": _brand_owned_assets("an-so", "mock-brand"),
    }
    body.update(overrides)
    return manifest.MediaManifest(**body)


def test_a_brand_owned_revision_is_ready_without_any_clean_master():
    """The point of the topology: no shared master, and none is demanded."""
    result = _manifest()

    assert result.state == "ready"
    # Unchanged from the delivery contract: brands * (1 + N).
    assert len(result.assets) == 2 * (1 + len(CHUNKS))
    assert all(asset.role.startswith("branded") for asset in result.assets)


def test_a_clean_asset_cannot_appear_in_a_brand_owned_revision():
    with pytest.raises(ValidationError, match="unexpected"):
        _manifest(
            assets=[
                *_brand_owned_assets("an-so", "mock-brand"),
                _asset("clean_whole", manifest.WHOLE_ITEM),
            ]
        )


def test_a_brand_owned_asset_cannot_invent_lineage():
    """There is nothing to derive from, so a lineage id would be a fiction."""
    assets = _brand_owned_assets("an-so", "mock-brand")
    assets[0] = _asset(
        "branded_whole", manifest.WHOLE_ITEM, brand_id="an-so", lineage_asset_id="ef" * 16
    )
    with pytest.raises(ValidationError, match="no clean master to reference"):
        _manifest(assets=assets)


def test_every_brand_must_name_the_published_revision_it_rendered():
    """Republishing a brand has to be visible as a different render."""
    with pytest.raises(ValidationError, match="one published revision per brand"):
        _manifest(brand_revisions={"an-so": 1})


def test_brand_revisions_belong_only_to_the_brand_owned_topology():
    with pytest.raises(ValidationError, match="belongs to the brand-owned topology"):
        _manifest(
            topology="clean_lineage",
            state="needs_action",
            assets=[],
            failures=[
                manifest.ManifestFailure(code="x", message="y", retryable=True)
            ],
        )


def test_a_lineage_revision_still_demands_its_clean_masters():
    """Relaxing the per-asset rule must not relax the topology it belonged to."""
    clean_whole = _asset("clean_whole", manifest.WHOLE_ITEM)
    cleans = {manifest.WHOLE_ITEM: clean_whole}
    assets = [clean_whole]
    for part in CHUNKS:
        cleans[part] = _asset("clean_vertical", part)
        assets.append(cleans[part])
    assets.append(
        _asset("branded_whole", manifest.WHOLE_ITEM, brand_id="an-so")
    )
    assets.extend(
        _asset("branded_vertical", part, brand_id="an-so", lineage_asset_id=cleans[part].asset_id)
        for part in CHUNKS
    )

    with pytest.raises(ValidationError, match="requires clean-master lineage"):
        _manifest(
            topology="clean_lineage",
            brand_ids=["an-so"],
            brand_revisions={},
            assets=assets,
        )


def test_a_manifest_written_before_topology_existed_still_loads():
    """Old revision files are immutable history; they must stay readable."""
    legacy = _manifest(
        topology="clean_lineage",
        state="needs_action",
        brand_revisions={},
        assets=[],
        failures=[manifest.ManifestFailure(code="x", message="y", retryable=True)],
    ).model_dump_json()
    without = legacy.replace('"topology":"clean_lineage",', "")

    assert manifest.MediaManifest.model_validate_json(without).topology == "clean_lineage"


# ---------------------------------------------------------------------------
# the brand's music bed
# ---------------------------------------------------------------------------

MUSIC = {
    "path": "/data/presets/brand/mock-brand/assets/mock-signature.wav",
    "volume_db": -24.0,
    "fade_in_s": 0.75,
    "fade_out_s": 1.0,
    "ducking": {"threshold": 0.02, "ratio": 8.0, "attack_ms": 20.0, "release_ms": 450.0},
}


def test_the_music_bed_is_ducked_by_the_voice_itself():
    graph = render._music_graph(MUSIC, duration_s=30.0, music_input=5)

    assert f"[{render._VOICE_INPUT}:a]" in graph
    assert "[5:a]" in graph and "volume=-24.0dB" in graph
    assert "asplit=2[speech][sidechain]" in graph
    assert "[music][sidechain]sidechaincompress=threshold=0.020000:ratio=8.000" in graph
    assert graph.endswith("[aout]")


def test_fades_never_overrun_a_short_asset():
    """A 1-second chunk cannot carry a 0.75s fade in and a 1.0s fade out."""
    graph = render._music_graph(MUSIC, duration_s=1.0, music_input=3)

    assert "afade=t=in:st=0:d=0.500" in graph
    assert "afade=t=out:st=0.500:d=0.500" in graph
