"""A brand owns its artwork and its layout, and both have to stay honest.

Storage and validation only: no ingested video and no sibling service, which
is what the marker below opts out of.
"""

import json

import pytest

import brands
import main
from errors import BrandConfigError, PresetNotFoundError, RenderError
from schema import MediaRevisionJobRequest

pytestmark = pytest.mark.no_pipeline


@pytest.fixture
def brand_root(monkeypatch, tmp_path):
    monkeypatch.setattr(brands, "root", lambda: tmp_path)
    return tmp_path


def _write_files(brand_root, brand_id, *names):
    directory = brand_root / brand_id / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"x")


def _draft(brand_id="an-so", **overrides):
    draft = {
        "schema_version": "brand.v1",
        "brand_id": brand_id,
        "display_name": "Ẩn Số",
        "status": "draft",
        "assets": [
            {"id": "bg", "kind": "image", "file": "bg.png", "role": "background"},
            {"id": "logo", "kind": "image", "file": "logo.png", "role": "logo"},
            {
                "id": "host",
                "kind": "video",
                "file": "host.mp4",
                "role": "host",
                "alpha_file": "host-alpha.mp4",
                "alpha_revision": "c8ff7ee1f20d4260bff18f4c74a8da2a",
            },
            {"id": "mock", "kind": "image", "file": "mock.png", "role": "mock_main"},
        ],
        "mock_main_asset": "mock",
        "vertical": {
            "canvas": "vertical_9_16",
            "layers": [
                {"kind": "image", "id": "bg", "asset": "bg", "x": 0, "y": 0, "w": 1, "h": 1, "z": 0, "fit": "fill"},
                {"kind": "main_video", "id": "video", "x": 0, "y": 0.28, "w": 1, "h": 0.44, "z": 10},
                {"kind": "blur", "id": "plate", "x": 0.1, "y": 0.8, "w": 0.4, "h": 0.15},
                {"kind": "host", "id": "host", "asset": "host", "x": 0.06, "y": 0.43, "w": 0.38, "h": 0.46, "z": 20},
                {"kind": "image", "id": "logo", "asset": "logo", "x": 0.04, "y": 0.04, "w": 0.16, "h": 0.1, "z": 30},
                {"kind": "text", "id": "title", "source": "title", "text": "Xem trước", "x": 0.08, "y": 0.02, "w": 0.84, "z": 50},
                {"kind": "subtitle", "id": "subtitle", "z": 60},
            ],
        },
        "landscape": {"canvas": "landscape_16_9", "layers": []},
    }
    draft.update(overrides)
    return draft


ALL_FILES = ("bg.png", "logo.png", "host.mp4", "host-alpha.mp4", "mock.png")


def _strings(value) -> list[str]:
    """Every string anywhere in a nested structure."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [found for item in value.values() for found in _strings(item)]
    if isinstance(value, list):
        return [found for item in value for found in _strings(item)]
    return []


# ---------------------------------------------------------------------------
# a new brand is an empty black canvas
# ---------------------------------------------------------------------------


def test_a_new_brand_starts_with_no_layers_at_all(brand_root):
    """The editor opens on black and grows.

    A fixed set of named slots would have to be drawn as empty boxes before
    anything is uploaded, and that clutter is what this design replaces.
    """
    brand = brands.create("kenh-moi", "Kênh Mới")
    assert brand.vertical.layers == []
    assert brand.landscape.layers == []
    assert brand.assets == []
    assert brands.assets_dir("kenh-moi").is_dir()


def test_a_brand_cannot_be_created_twice(brand_root):
    brands.create("kenh-moi", "Kênh Mới")
    with pytest.raises(RenderError):
        brands.create("kenh-moi", "Kênh Mới")


# ---------------------------------------------------------------------------
# layers may only name assets that exist, of the right kind
# ---------------------------------------------------------------------------


def test_a_layer_naming_an_unknown_asset_is_rejected(brand_root):
    draft = _draft()
    draft["vertical"]["layers"][0]["asset"] = "khong-co"
    with pytest.raises(Exception, match="unknown asset"):
        brands.save_draft(draft)


def test_a_host_layer_needs_a_video_asset_not_an_image(brand_root):
    draft = _draft()
    draft["vertical"]["layers"][3]["asset"] = "logo"
    with pytest.raises(Exception, match="needs video asset"):
        brands.save_draft(draft)


def test_a_host_without_an_alpha_mask_is_refused(brand_root):
    """An unmatted presenter composites as an opaque rectangle.

    That is visibly wrong rather than merely imperfect, so it is refused at
    save time instead of being discovered in the delivered video.
    """
    draft = _draft()
    draft["assets"][2].pop("alpha_file")
    draft["assets"][2].pop("alpha_revision")
    with pytest.raises(Exception, match="no alpha mask"):
        brands.save_draft(draft)


def test_an_alpha_mask_and_its_revision_travel_together(brand_root):
    draft = _draft()
    draft["assets"][2].pop("alpha_revision")
    with pytest.raises(Exception, match="travel together"):
        brands.save_draft(draft)


# ---------------------------------------------------------------------------
# the mock is preview scaffolding and must never be rendered
# ---------------------------------------------------------------------------


def test_the_mock_asset_cannot_be_placed_as_a_layer(brand_root):
    draft = _draft()
    draft["vertical"]["layers"].append(
        {"kind": "image", "id": "sneaky", "asset": "mock", "x": 0, "y": 0, "w": 1, "h": 1, "z": 5}
    )
    with pytest.raises(Exception, match="preview-only"):
        brands.save_draft(draft)


def test_the_mock_file_never_reaches_the_render_contract(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())
    published = brands.publish(_draft())
    config = brands.render_config(published, "vertical")
    # Matching on the filename rather than the bare word: pytest names tmp_path
    # after the test, so a substring search for "mock" finds the directory and
    # would pass for the wrong reason.
    paths = _strings(config)
    assert any(path.endswith("bg.png") for path in paths), "expected real files"
    assert not any(path.endswith("mock.png") for path in paths)


# ---------------------------------------------------------------------------
# a blur belongs to the footage, so it needs footage
# ---------------------------------------------------------------------------


def test_a_blur_without_a_main_video_is_refused(brand_root):
    draft = _draft()
    draft["vertical"]["layers"] = [
        {"kind": "blur", "id": "plate", "x": 0.1, "y": 0.8, "w": 0.4, "h": 0.15}
    ]
    with pytest.raises(Exception, match="needs a main_video"):
        brands.save_draft(draft)


def test_blur_coordinates_stay_out_of_the_canvas_image_list(brand_root):
    """A blur is normalised against the source frame, not the canvas.

    Emitting it beside the canvas-space images would invite the renderer to
    multiply it by the wrong number, so it travels in its own list.
    """
    _write_files(brand_root, "an-so", *ALL_FILES)
    config = brands.render_config(brands.publish(_draft()), "vertical")
    assert config["blur_regions"] == [{"x": 0.1, "y": 0.8, "w": 0.4, "h": 0.15}]
    assert [image["id"] for image in config["images"]] == ["bg", "logo"]


# ---------------------------------------------------------------------------
# publishing
# ---------------------------------------------------------------------------


def test_publishing_fails_when_a_file_is_missing(brand_root):
    _write_files(brand_root, "an-so", "bg.png")
    with pytest.raises(RenderError, match="missing files"):
        brands.publish(_draft())


def test_each_publish_is_a_new_immutable_revision_with_its_own_hash(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    first = brands.publish(_draft())
    second = brands.publish(_draft(display_name="Ẩn Số v2"))
    assert (first.revision, second.revision) == (1, 2)
    assert first.content_sha256 != second.content_sha256
    assert brands.load_published("an-so", 1).display_name == "Ẩn Số"
    assert brands.latest_revision("an-so") == 2


def test_a_draft_is_not_a_render_input(brand_root):
    """Only a published revision may be rendered.

    A half-dragged layout in the editor must not become a production layout,
    and the draft lives under a different filename so it cannot be loaded by
    the published path at all.
    """
    _write_files(brand_root, "an-so", *ALL_FILES)
    draft = brands.save_draft(_draft())
    with pytest.raises(PresetNotFoundError):
        brands.load_published("an-so", 1)
    with pytest.raises(RenderError, match="only a published brand"):
        brands.render_config(draft, "vertical")


def test_a_layout_with_no_main_video_cannot_render(brand_root):
    """The landscape layout in the fixture is empty on purpose."""
    _write_files(brand_root, "an-so", *ALL_FILES)
    published = brands.publish(_draft())
    with pytest.raises(RenderError, match="no visible main_video"):
        brands.render_config(published, "landscape")


# ---------------------------------------------------------------------------
# brands stay separate
# ---------------------------------------------------------------------------


def test_two_brands_resolve_to_their_own_asset_folders(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    _write_files(brand_root, "kenh-b", *ALL_FILES)
    an_so = brands.render_config(brands.publish(_draft("an-so")), "vertical")
    kenh_b = brands.render_config(brands.publish(_draft("kenh-b")), "vertical")
    assert "/an-so/assets/logo.png" in an_so["images"][1]["path"].replace("\\", "/")
    assert "/kenh-b/assets/logo.png" in kenh_b["images"][1]["path"].replace("\\", "/")
    assert an_so["host"]["path"] != kenh_b["host"]["path"]


def test_listing_reports_every_brand_and_its_revisions(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())
    brands.publish(_draft())
    brands.create("kenh-b", "Kênh B")
    listing = {item["brand_id"]: item for item in brands.list_brands()}
    assert listing["an-so"]["revisions"] == [1]
    assert listing["kenh-b"]["latest_revision"] is None
    assert listing["kenh-b"]["display_name"] == "Kênh B"


# ---------------------------------------------------------------------------
# layers are drawn in the order the editor shows
# ---------------------------------------------------------------------------


def test_the_render_contract_carries_z_for_every_visible_layer(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    config = brands.render_config(brands.publish(_draft()), "vertical")
    order = {image["id"]: image["z"] for image in config["images"]}
    assert order == {"bg": 0, "logo": 30}
    assert config["video_z"] == 10
    assert config["host"]["z"] == 20
    assert config["text_layers"][0]["z"] == 50
    assert config["subtitle"]["z"] == 60


def test_an_invisible_layer_is_left_out_of_the_contract(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    draft = _draft()
    draft["vertical"]["layers"][4]["visible"] = False
    config = brands.render_config(brands.publish(draft), "vertical")
    assert [image["id"] for image in config["images"]] == ["bg"]


def test_a_text_layer_stores_a_palette_name_and_renders_its_hex(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    draft = _draft()
    draft["vertical"]["layers"][5]["color"] = "yellow"
    config = brands.render_config(brands.publish(draft), "vertical")
    assert config["text_layers"][0]["color"] == "#FFD60A"


# ---------------------------------------------------------------------------
# the editing round trip: create, reopen, change, save, reopen again
# ---------------------------------------------------------------------------


def test_a_created_brand_reopens_as_exactly_what_was_created(brand_root):
    created = brands.create("kenh-moi", "Kênh Mới")
    reopened = brands.load_draft("kenh-moi")
    assert reopened.model_dump(mode="json") == created.model_dump(mode="json")


def test_a_reopened_draft_saves_back_without_losing_a_field(brand_root):
    """The editor's whole loop is load -> model_dump -> POST the same shape.

    Anything the schema drops on the way out would be deleted from the brand
    the first time someone opened it and pressed save, which is silent damage
    rather than a visible error.
    """
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())
    body = brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    brands.save_draft(body)
    again = brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    assert again == body


def test_an_edit_to_a_reopened_draft_survives_the_next_reopen(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())

    body = brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    body["display_name"] = "Ẩn Số 2026"
    body["vertical"]["layers"][4]["x"] = 0.5  # drag the logo across the canvas
    body["vertical"]["layers"][4]["z"] = 45  # and send it under the title
    brands.save_draft(body)

    again = brands.load_draft("an-so")
    logo = next(layer for layer in again.vertical.layers if layer.id == "logo")
    assert again.display_name == "Ẩn Số 2026"
    assert (logo.x, logo.z) == (0.5, 45)
    # Editing one layer must not disturb the others.
    assert [layer.id for layer in again.vertical.layers] == [
        layer["id"] for layer in _draft()["vertical"]["layers"]
    ]


def test_an_asset_and_its_layer_added_to_an_existing_draft_persist(brand_root):
    """"Place into layout" on a brand that already has a layout."""
    _write_files(brand_root, "an-so", *ALL_FILES, "cta.png")
    brands.save_draft(_draft())

    body = brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    body["assets"].append(
        {"id": "cta", "kind": "image", "file": "cta.png", "role": "other"}
    )
    body["vertical"]["layers"].append(
        {"kind": "image", "id": "cta", "asset": "cta", "x": 0.3, "y": 0.9, "w": 0.4, "h": 0.06, "z": 40}
    )
    brands.save_draft(body)

    again = brands.load_draft("an-so")
    assert [asset.id for asset in again.assets] == ["bg", "logo", "host", "mock", "cta"]
    config = brands.render_config(brands.publish(
        again.model_dump(mode="json", exclude_none=True)
    ), "vertical")
    assert [image["id"] for image in config["images"]] == ["bg", "logo", "cta"]


def test_a_rejected_edit_leaves_the_stored_draft_untouched(brand_root):
    """A save either validates whole or changes nothing.

    Validation happens before the write, so a bad edit cannot half-land and
    leave the editor unable to reopen the brand at all.
    """
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())
    stored = brands.draft_path("an-so").read_text(encoding="utf-8")

    body = brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    body["vertical"]["layers"][0]["asset"] = "khong-co"
    with pytest.raises(Exception, match="unknown asset"):
        brands.save_draft(body)

    assert brands.draft_path("an-so").read_text(encoding="utf-8") == stored


def test_reopening_a_published_brand_gives_the_draft_not_the_revision(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())
    brands.publish(brands.load_draft("an-so").model_dump(mode="json", exclude_none=True))
    reopened = brands.load_draft("an-so")
    assert (reopened.status, reopened.revision, reopened.content_sha256) == (
        "draft",
        None,
        None,
    )


def test_editing_a_published_brand_adds_a_revision_and_leaves_the_old_one_alone(
    brand_root,
):
    """The round trip a live brand actually goes through on its second pass."""
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())
    first = brands.publish(
        brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    )
    on_disk = brands.published_path("an-so", 1).read_text(encoding="utf-8")

    body = brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    body["vertical"]["layers"][4]["x"] = 0.5
    brands.save_draft(body)
    second = brands.publish(
        brands.load_draft("an-so").model_dump(mode="json", exclude_none=True)
    )

    assert (first.revision, second.revision) == (1, 2)
    assert first.content_sha256 != second.content_sha256
    # A render pinned to v1 must keep rendering v1's layout.
    assert brands.published_path("an-so", 1).read_text(encoding="utf-8") == on_disk
    v1_logo = next(
        layer for layer in brands.load_published("an-so", 1).vertical.layers
        if layer.id == "logo"
    )
    v2_logo = next(
        layer for layer in brands.load_published("an-so", 2).vertical.layers
        if layer.id == "logo"
    )
    assert (v1_logo.x, v2_logo.x) == (0.04, 0.5)
    assert brands.latest_revision("an-so") == 2


# ---------------------------------------------------------------------------
# a blur in the stack, and duplicates of the same kind of asset
# ---------------------------------------------------------------------------


def test_a_blur_with_a_z_travels_as_a_layer_and_one_without_it_does_not(brand_root):
    """The z is the whole difference: same rectangle, different moment.

    Without a z the blur is burned into the source before the footage is
    placed, which is how every revision published so far was approved. With
    one it is composited in the stack, so it can cover a logo drawn under it.
    """
    _write_files(brand_root, "an-so", *ALL_FILES)
    draft = _draft()
    draft["vertical"]["layers"].append(
        {"kind": "blur", "id": "over-logo", "x": 0.2, "y": 0.1, "w": 0.3, "h": 0.1, "z": 35}
    )
    config = brands.render_config(brands.publish(draft), "vertical")

    assert config["blur_regions"] == [{"x": 0.1, "y": 0.8, "w": 0.4, "h": 0.15}]
    assert config["blur_layers"] == [
        {"id": "over-logo", "x": 0.2, "y": 0.1, "w": 0.3, "h": 0.1, "z": 35}
    ]
    # Still not in the canvas-space image list, whichever list it is in.
    assert [image["id"] for image in config["images"]] == ["bg", "logo"]


def test_a_blur_keeps_its_z_across_a_save_and_a_reopen(brand_root):
    draft = _draft()
    draft["vertical"]["layers"][2]["z"] = 42
    brands.save_draft(draft)

    stored = brands.load_draft("an-so")
    blur = next(layer for layer in stored.vertical.layers if layer.kind == "blur")
    assert blur.z == 42


def test_an_invisible_blur_reaches_neither_list(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    draft = _draft()
    draft["vertical"]["layers"][2].update({"z": 35, "visible": False})
    config = brands.render_config(brands.publish(draft), "vertical")

    assert config["blur_regions"] == []
    assert config["blur_layers"] == []


def test_a_brand_may_hold_several_logos_and_place_each_of_them(brand_root):
    """A role is a label, not a slot: nothing counts logos."""
    _write_files(brand_root, "an-so", *ALL_FILES, "logo-2.png", "logo-3.png")
    draft = _draft()
    draft["assets"] += [
        {"id": "logo-2", "kind": "image", "file": "logo-2.png", "role": "logo"},
        {"id": "logo-3", "kind": "image", "file": "logo-3.png", "role": "watermark"},
    ]
    draft["vertical"]["layers"] += [
        {"kind": "image", "id": "logo-2", "asset": "logo-2", "x": 0.4, "y": 0.04, "w": 0.16, "h": 0.1, "z": 31},
        {"kind": "image", "id": "logo-3", "asset": "logo-3", "x": 0.7, "y": 0.04, "w": 0.16, "h": 0.1, "z": 32},
    ]
    config = brands.render_config(brands.publish(draft), "vertical")

    assert [image["id"] for image in config["images"]] == ["bg", "logo", "logo-2", "logo-3"]
    assert len({image["path"] for image in config["images"]}) == 4


def test_the_same_asset_may_be_placed_twice_under_two_layer_ids(brand_root):
    """Two corners, one file. Only the layer id has to be unique."""
    _write_files(brand_root, "an-so", *ALL_FILES)
    draft = _draft()
    draft["vertical"]["layers"].append(
        {"kind": "image", "id": "logo-2", "asset": "logo", "x": 0.8, "y": 0.9, "w": 0.16, "h": 0.1, "z": 31}
    )
    config = brands.render_config(brands.publish(draft), "vertical")

    placed = [image for image in config["images"] if image["id"].startswith("logo")]
    assert [image["id"] for image in placed] == ["logo", "logo-2"]
    assert placed[0]["path"] == placed[1]["path"]


def test_two_layers_with_the_same_id_are_still_refused(brand_root):
    """Duplicating an asset is fine; duplicating a name is not."""
    draft = _draft()
    draft["vertical"]["layers"].append(
        {"kind": "image", "id": "logo", "asset": "logo", "x": 0.8, "y": 0.9, "w": 0.16, "h": 0.1, "z": 31}
    )
    with pytest.raises(Exception, match="ids must be unique"):
        brands.save_draft(draft)


# ---------------------------------------------------------------------------
# resolving "latest"
# ---------------------------------------------------------------------------


def test_latest_resolves_to_the_highest_published_revision(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.publish(_draft())
    brands.publish(_draft(display_name="Ẩn Số v2"))
    brands.publish(_draft(display_name="Ẩn Số v3"))

    assert main._revision("an-so", "latest") == 3
    assert main._revision("an-so", "2") == 2


def test_latest_never_falls_back_to_a_draft(brand_root):
    """A brand that was never published has no latest, and says so.

    The dangerous reading of "latest" is "whatever is newest, draft included",
    which would put a half-dragged layout into a render nobody approved.
    """
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.save_draft(_draft())

    with pytest.raises(PresetNotFoundError, match="no published revision"):
        main._revision("an-so", "latest")


def test_a_revision_that_is_neither_a_number_nor_latest_is_refused(brand_root):
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.publish(_draft())

    for spelling in ("newest", "v2", "-1", "1.0", ""):
        with pytest.raises(BrandConfigError, match="positive integer or 'latest'"):
            main._revision("an-so", spelling)


def test_latest_is_resolved_before_it_is_stored(brand_root):
    """The word must not survive into a job's stored inputs.

    A manifest carrying "latest" would silently mean a different layout after
    the next publish, and an approved campaign would stop matching what was
    approved.
    """
    _write_files(brand_root, "an-so", *ALL_FILES)
    brands.publish(_draft())
    brands.publish(_draft(display_name="Ẩn Số v2"))

    request = MediaRevisionJobRequest(
        video_id="jNQXAC9IVRw",
        render_revision=1,
        brand_ids=["an-so"],
        brand_revisions={"an-so": "latest"},
    )
    resolved = main._resolve_revisions(request.brand_revisions)

    assert resolved == {"an-so": 2}
    assert all(isinstance(revision, int) for revision in resolved.values())


def test_a_brand_revision_of_zero_is_still_refused():
    with pytest.raises(ValueError, match="positive integer or 'latest'"):
        MediaRevisionJobRequest(
            video_id="jNQXAC9IVRw",
            render_revision=1,
            brand_revisions={"an-so": 0},
        )


def test_a_job_cannot_ask_for_a_revision_spelled_any_other_way():
    with pytest.raises(ValueError):
        MediaRevisionJobRequest(
            video_id="jNQXAC9IVRw",
            render_revision=1,
            brand_revisions={"an-so": "newest"},
        )
