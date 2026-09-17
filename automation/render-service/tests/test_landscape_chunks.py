"""The v2 delivery shape: one 16:9 whole per brand, every part cut out of it.

`test_brand_topology.py` still holds the v1 brand-owned shape, which stays
readable because the files it describes are still on disk. This file is about
what replaced it: nothing is rendered twice, so a part is only ever a section
of its own brand's finished whole, and the manifest has to be able to say so.

The cut tests encode real video. That is the point - a duration in a manifest
row proves nothing about which frame the cut actually started on, and starting
on the wrong one is the failure this change can most easily introduce.
"""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import manifest
import render
from errors import RenderError

pytestmark = pytest.mark.no_pipeline

VIDEO_ID = "landChunkAA"
SOURCE_SHA = "ab" * 32
CHUNKS = ["part_1", "part_2"]

# One solid colour per second, so a frame identifies its own timestamp.
COLOURS = {
    "red": (255, 0, 0),
    "lime": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "magenta": (255, 0, 255),
    "cyan": (0, 255, 255),
}


def _asset(
    role,
    content_item_id,
    *,
    brand_id=None,
    lineage_asset_id=None,
    size=None,
    path=None,
    duration_s=12.0,
    version=manifest.SCHEMA_VERSION,
):
    width, height = size or ((1080, 1920) if role.endswith("vertical") else (1920, 1080))
    return manifest.MediaAsset(
        asset_id=manifest.deterministic_asset_id(
            VIDEO_ID, 1, role, content_item_id, brand_id, version
        ),
        video_id=VIDEO_ID,
        render_revision=1,
        role=role,
        content_item_id=content_item_id,
        brand_id=brand_id,
        lineage_asset_id=lineage_asset_id,
        path=path
        or str(manifest.expected_asset_path(VIDEO_ID, 1, role, content_item_id, brand_id)),
        sha256="cd" * 32,
        bytes=4096,
        probe=manifest.ProbeEvidence(
            duration_s=duration_s,
            width=width,
            height=height,
            video_codec="h264",
            audio_codec="aac",
        ),
    )


def _brand_assets(*brand_ids):
    assets = []
    for brand_id in brand_ids:
        whole = _asset("branded_whole", manifest.WHOLE_ITEM, brand_id=brand_id)
        assets.append(whole)
        assets.extend(
            _asset(
                "branded_landscape_chunk",
                part,
                brand_id=brand_id,
                lineage_asset_id=whole.asset_id,
            )
            for part in CHUNKS
        )
    return assets


def _manifest(**overrides):
    body = {
        "schema_version": manifest.SCHEMA_VERSION,
        "video_id": VIDEO_ID,
        "render_revision": 1,
        "source_sha256": SOURCE_SHA,
        "state": "ready",
        "topology": "landscape_chunks",
        "chunk_names": CHUNKS,
        "brand_ids": ["an-so", "mock-brand"],
        "brand_revisions": {"an-so": 1, "mock-brand": 3},
        "assets": _brand_assets("an-so", "mock-brand"),
    }
    body.update(overrides)
    return manifest.MediaManifest(**body)


class TestTopology:
    def test_a_revision_is_ready_with_a_whole_and_its_cuts_per_brand(self):
        result = _manifest()

        assert result.state == "ready"
        # Unchanged from the delivery contract: brands * (1 + N).
        assert len(result.assets) == 2 * (1 + len(CHUNKS))
        assert {asset.probe.width for asset in result.assets} == {1920}

    def test_a_chunk_must_name_its_own_brands_whole(self):
        assets = _brand_assets("an-so", "mock-brand")
        an_so_whole, other_whole = assets[0], assets[3]
        assert an_so_whole.brand_id == "an-so"
        assert other_whole.brand_id == "mock-brand"
        assets[1] = _asset(
            "branded_landscape_chunk",
            "part_1",
            brand_id="an-so",
            lineage_asset_id=other_whole.asset_id,
        )
        with pytest.raises(ValidationError, match="must reference branded whole"):
            _manifest(assets=assets)

    def test_a_chunk_with_no_lineage_at_all_is_refused(self):
        assets = _brand_assets("an-so", "mock-brand")
        assets[1] = _asset("branded_landscape_chunk", "part_1", brand_id="an-so")
        with pytest.raises(ValidationError, match="must reference branded whole"):
            _manifest(assets=assets)

    def test_a_whole_may_not_claim_a_parent(self):
        assets = _brand_assets("an-so", "mock-brand")
        assets[0] = _asset(
            "branded_whole",
            manifest.WHOLE_ITEM,
            brand_id="an-so",
            lineage_asset_id="ef" * 16,
        )
        with pytest.raises(ValidationError, match="no parent asset"):
            _manifest(assets=assets)

    def test_a_v2_manifest_cannot_declare_an_older_topology(self):
        with pytest.raises(ValidationError, match="landscape_chunks topology"):
            _manifest(topology="brand_owned")

    def test_a_vertical_asset_cannot_ride_along_in_a_v2_revision(self):
        assets = [
            *_brand_assets("an-so", "mock-brand"),
            _asset("branded_vertical", "part_1", brand_id="an-so"),
        ]
        with pytest.raises(ValidationError, match="unexpected"):
            _manifest(assets=assets)

    def test_a_landscape_chunk_that_is_not_16_by_9_is_refused(self):
        with pytest.raises(ValidationError, match="must be 1920x1080"):
            _asset(
                "branded_landscape_chunk", "part_1", brand_id="an-so", size=(1080, 1920)
            )

    def test_the_v1_brand_owned_shape_still_validates_as_history(self):
        """Old manifests are read, never rewritten, so they must still parse."""
        assets = []
        for brand_id in ("an-so", "mock-brand"):
            assets.append(
                _asset(
                    "branded_whole",
                    manifest.WHOLE_ITEM,
                    brand_id=brand_id,
                    version=manifest.LEGACY_SCHEMA_VERSION,
                )
            )
            assets.extend(
                _asset(
                    "branded_vertical",
                    part,
                    brand_id=brand_id,
                    version=manifest.LEGACY_SCHEMA_VERSION,
                )
                for part in CHUNKS
            )
        result = _manifest(
            schema_version=manifest.LEGACY_SCHEMA_VERSION,
            topology="brand_owned",
            assets=assets,
        )
        assert result.state == "ready"


class TestPathsAndIdentity:
    def test_a_landscape_chunk_never_lands_on_a_vertical_path(self):
        landscape = manifest.expected_asset_path(
            VIDEO_ID, 4, "branded_landscape_chunk", "part_1", "an-so"
        )
        vertical = manifest.expected_asset_path(
            VIDEO_ID, 4, "branded_vertical", "part_1", "an-so"
        )
        assert landscape.parent.name == "landscape"
        assert landscape.name == "part_1-16x9.mp4"
        assert landscape != vertical

    def test_the_same_identity_gets_a_different_id_under_each_schema(self):
        """A v1 whole and a v2 whole are different assets with different rules.

        Without the version in the hash, a resumed revision could match a v1
        row to a v2 file and reuse footage produced under the old contract.
        """
        args = (VIDEO_ID, 1, "branded_whole", manifest.WHOLE_ITEM, "an-so")
        assert manifest.deterministic_asset_id(
            *args, manifest.SCHEMA_VERSION
        ) != manifest.deterministic_asset_id(*args, manifest.LEGACY_SCHEMA_VERSION)

    def test_an_unknown_schema_version_is_refused(self):
        with pytest.raises(ValueError, match="unknown manifest schema version"):
            manifest.deterministic_asset_id(
                VIDEO_ID, 1, "branded_whole", manifest.WHOLE_ITEM, "an-so", "v3"
            )


def _build_parent(path: Path, seconds: int = 6) -> None:
    """A 1920x1080 h264/aac clip whose colour changes once per second."""
    names = list(COLOURS)[:seconds]
    inputs: list[str] = []
    for name in names:
        inputs += ["-f", "lavfi", "-i", f"color=c={name}:s=1920x1080:d=1:r=25"]
    inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    chain = "".join(f"[{index}:v]" for index in range(seconds))
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", *inputs,
            "-filter_complex", f"{chain}concat=n={seconds}:v=1:a=0[v]",
            "-map", "[v]", "-map", f"{seconds}:a", "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _colour_at(path: Path, offset_s: float) -> str:
    """Name the solid colour of the frame at `offset_s`, via a 1x1 average."""
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path), "-ss", f"{offset_s:.3f}",
            "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
            "-pix_fmt", "rgb24", "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert len(raw) == 3, f"expected one rgb24 pixel, got {len(raw)} bytes"
    pixel = tuple(raw)
    return min(
        COLOURS,
        key=lambda name: sum((a - b) ** 2 for a, b in zip(COLOURS[name], pixel)),
    )


@pytest.fixture
def parent(tmp_path) -> manifest.MediaAsset:
    path = tmp_path / "whole-16x9.mp4"
    _build_parent(path)
    return _asset(
        "branded_whole",
        manifest.WHOLE_ITEM,
        brand_id="an-so",
        path=str(path),
        duration_s=6.0,
    )


class TestCutting:
    def test_the_cut_starts_on_the_frame_the_boundary_names(self, parent, tmp_path):
        """The claim the whole change rests on: part_2 begins at second 2.

        The parent runs red, lime, blue, yellow, magenta, cyan - one colour per
        second. A cut of 2.0-4.0 must open on blue and still be inside yellow
        1.5 s later. A keyframe `-c copy` cut would snap to the nearest
        keyframe and open on red or lime instead.
        """
        out = tmp_path / "part_2-16x9.mp4"
        asset = render.cut_branded_landscape_chunk(
            parent,
            {"idx": 1, "name": "part_2", "start_s": 2.0, "end_s": 4.0},
            output_path=out,
        )

        assert _colour_at(out, 0.0) == "blue"
        assert _colour_at(out, 1.5) == "yellow"
        assert asset.probe.duration_s == pytest.approx(2.0, abs=0.1)
        assert (asset.probe.width, asset.probe.height) == (1920, 1080)

    def test_the_cut_records_its_parent_as_lineage(self, parent, tmp_path):
        asset = render.cut_branded_landscape_chunk(
            parent,
            {"idx": 0, "name": "part_1", "start_s": 0.0, "end_s": 2.0},
            output_path=tmp_path / "part_1-16x9.mp4",
        )

        assert asset.lineage_asset_id == parent.asset_id
        assert asset.role == "branded_landscape_chunk"
        assert asset.brand_id == parent.brand_id

    def test_a_span_past_the_parent_is_refused_before_encoding(self, parent, tmp_path):
        out = tmp_path / "part_1-16x9.mp4"
        with pytest.raises(RenderError, match="exceeds its branded whole parent"):
            render.cut_branded_landscape_chunk(
                parent,
                {"idx": 0, "name": "part_1", "start_s": 5.0, "end_s": 9.0},
                output_path=out,
            )
        assert not out.exists()

    def test_a_name_that_disagrees_with_its_index_is_refused(self, parent, tmp_path):
        """`part_<n>` is one-based; a mismatch would deliver the wrong section."""
        with pytest.raises(RenderError, match="must be named part_2"):
            render.cut_branded_landscape_chunk(
                parent,
                {"idx": 1, "name": "part_1", "start_s": 2.0, "end_s": 4.0},
                output_path=tmp_path / "part_1-16x9.mp4",
            )

    def test_only_a_branded_whole_may_be_cut(self, tmp_path):
        vertical = _asset(
            "branded_vertical",
            "part_1",
            brand_id="an-so",
            version=manifest.LEGACY_SCHEMA_VERSION,
        )
        with pytest.raises(RenderError, match="cut from a branded whole"):
            render.cut_branded_landscape_chunk(
                vertical,
                {"idx": 0, "name": "part_1", "start_s": 0.0, "end_s": 1.0},
                output_path=tmp_path / "part_1-16x9.mp4",
            )

    def test_an_empty_span_is_refused(self, parent, tmp_path):
        with pytest.raises(RenderError, match="invalid cut span"):
            render.cut_branded_landscape_chunk(
                parent,
                {"idx": 0, "name": "part_1", "start_s": 2.0, "end_s": 2.0},
                output_path=tmp_path / "part_1-16x9.mp4",
            )
