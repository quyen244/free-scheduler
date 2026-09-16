"""Build a complete, resumable media revision from clean and branded assets."""

from __future__ import annotations

from collections.abc import Callable

import brand
import brands as brand_layouts
import library
import manifest
import render
from errors import NoChunksError, RenderError


Progress = Callable[[float], None]


def _failure(
    video_id: str,
    render_revision: int,
    role: manifest.AssetRole,
    content_item_id: str,
    brand_id: str | None,
    exc: Exception,
) -> manifest.ManifestFailure:
    retryable = isinstance(exc, (RenderError, manifest.ManifestValidationError, OSError))
    return manifest.ManifestFailure(
        asset_id=manifest.deterministic_asset_id(
            video_id, render_revision, role, content_item_id, brand_id
        ),
        code=f"{role}_failed",
        message=f"{type(exc).__name__}: {exc}",
        retryable=retryable,
    )


def _reuse_or_render(
    *,
    video_id: str,
    render_revision: int,
    role: manifest.AssetRole,
    content_item_id: str,
    expected_duration_s: float,
    brand_id: str | None = None,
    lineage_asset_id: str | None = None,
    build: Callable[[], manifest.MediaAsset],
) -> manifest.MediaAsset:
    """Reuse a verified output after restart; rerender an invalid partial result."""
    path = manifest.expected_asset_path(
        video_id, render_revision, role, content_item_id, brand_id
    )
    if path.is_file():
        try:
            return manifest.inspect_expected_asset(
                video_id,
                render_revision,
                role,
                content_item_id,
                expected_duration_s,
                brand_id=brand_id,
                lineage_asset_id=lineage_asset_id,
            )
        except manifest.ManifestValidationError:
            # Until a ready manifest is committed, a corrupt output from an
            # interrupted attempt is replaceable while verified peers remain.
            pass
    return build()


def render_media_revision(
    video_id: str,
    render_revision: int,
    *,
    brand_ids: list[str],
    vertical_preset_name: str = "vertical-clean",
    landscape_preset_name: str = "yt-landscape",
    vertical_preset_override: dict | None = None,
    landscape_preset_override: dict | None = None,
    metadata_revision_id: str | None = None,
    on_progress: Progress | None = None,
) -> manifest.MediaManifest:
    """Render or resume the complete topology for one immutable revision."""
    chunks = library_chunks(video_id)
    chunk_names = [str(chunk["name"]) for chunk in chunks]
    expected_names = [f"part_{index}" for index in range(1, len(chunks) + 1)]
    if chunk_names != expected_names:
        raise RenderError(
            f"chunk names must be contiguous {expected_names}, got {chunk_names}"
        )
    if not brand_ids or len(brand_ids) != len(set(brand_ids)):
        raise RenderError("brand_ids must contain one or more unique brand IDs")

    revision_path = manifest.manifest_revision_path(video_id, render_revision)
    if revision_path.is_file():
        existing = manifest.MediaManifest.model_validate_json(
            revision_path.read_text(encoding="utf-8")
        )
        if (
            existing.chunk_names != chunk_names
            or existing.brand_ids != brand_ids
            or existing.metadata_revision_id != metadata_revision_id
        ):
            raise manifest.ManifestConflictError(
                f"ready render revision {render_revision} already exists with different inputs"
            )
        if on_progress:
            on_progress(1.0)
        return existing

    vertical_preset = vertical_preset_override or library.load_preset(vertical_preset_name)
    landscape_preset = landscape_preset_override or library.load_preset(landscape_preset_name)
    profiles = [brand.load(brand_id) for brand_id in brand_ids]
    transcript = library.load_transcript(video_id)
    segments = transcript.get("segments") or []
    voice_manifest = library.load_voice_manifest(video_id) or {}
    source = render.probe(library.raw_path(video_id))
    # Values a preset text layer can bind to. A layer bound to a field that is
    # empty is skipped and reported, never filled with its editor preview text.
    from shared import pipeline_db

    fields = {"title": pipeline_db.title_for(video_id)}

    total_assets = (1 + len(chunks)) * (1 + len(profiles))
    completed = 0
    assets: list[manifest.MediaAsset] = []
    failures: list[manifest.ManifestFailure] = []
    clean_by_item: dict[str, manifest.MediaAsset] = {}

    def progressed() -> None:
        nonlocal completed
        completed += 1
        if on_progress:
            on_progress(completed / total_assets)

    try:
        clean_whole = _reuse_or_render(
            video_id=video_id,
            render_revision=render_revision,
            role="clean_whole",
            content_item_id=manifest.WHOLE_ITEM,
            expected_duration_s=source.duration_s,
            build=lambda: render.render_clean_whole(
                video_id,
                render_revision,
                landscape_preset,
                segments,
                fields=fields,
                warnings=list(voice_manifest.get("warnings") or []),
            ),
        )
        assets.append(clean_whole)
        clean_by_item[manifest.WHOLE_ITEM] = clean_whole
    except Exception as exc:  # one bad asset must not discard successful peers
        failures.append(
            _failure(
                video_id,
                render_revision,
                "clean_whole",
                manifest.WHOLE_ITEM,
                None,
                exc,
            )
        )
    progressed()

    for chunk in chunks:
        name = str(chunk["name"])
        duration_s = float(chunk["end_s"]) - float(chunk["start_s"])
        try:
            clean = _reuse_or_render(
                video_id=video_id,
                render_revision=render_revision,
                role="clean_vertical",
                content_item_id=name,
                expected_duration_s=duration_s,
                build=lambda chunk=chunk: render.render_clean_vertical(
                    video_id,
                    render_revision,
                    chunk,
                    vertical_preset,
                    segments,
                    texts={
                        "caption_top": str(chunk.get("hook") or ""),
                        "caption_bottom": str(chunk.get("caption") or ""),
                    },
                    fields=fields,
                    warnings=list(voice_manifest.get("warnings") or []),
                ),
            )
            assets.append(clean)
            clean_by_item[name] = clean
        except Exception as exc:
            failures.append(
                _failure(
                    video_id,
                    render_revision,
                    "clean_vertical",
                    name,
                    None,
                    exc,
                )
            )
        progressed()

    for profile in profiles:
        for item_name in [manifest.WHOLE_ITEM, *chunk_names]:
            clean = clean_by_item.get(item_name)
            role: manifest.AssetRole = (
                "branded_whole"
                if item_name == manifest.WHOLE_ITEM
                else "branded_vertical"
            )
            if clean is None:
                failures.append(
                    manifest.ManifestFailure(
                        asset_id=manifest.deterministic_asset_id(
                            video_id,
                            render_revision,
                            role,
                            item_name,
                            profile.brand_id,
                        ),
                        code="clean_dependency_failed",
                        message=(
                            f"cannot derive {profile.brand_id}/{item_name} until its "
                            "clean master succeeds"
                        ),
                        retryable=True,
                    )
                )
                progressed()
                continue
            try:
                branded = _reuse_or_render(
                    video_id=video_id,
                    render_revision=render_revision,
                    role=role,
                    content_item_id=item_name,
                    expected_duration_s=clean.probe.duration_s,
                    brand_id=profile.brand_id,
                    lineage_asset_id=clean.asset_id,
                    build=lambda clean=clean, profile=profile, role=role: render.render_branded_variant(
                        clean,
                        profile,
                        brand_slots=(
                            landscape_preset if role == "branded_whole" else vertical_preset
                        ).get("brand_slots"),
                    ),
                )
                assets.append(branded)
            except Exception as exc:
                failures.append(
                    _failure(
                        video_id,
                        render_revision,
                        role,
                        item_name,
                        profile.brand_id,
                        exc,
                    )
                )
            progressed()

    state: manifest.ManifestState = "needs_action" if failures else "ready"
    result = manifest.MediaManifest(
        video_id=video_id,
        render_revision=render_revision,
        metadata_revision_id=metadata_revision_id,
        source_sha256=manifest.sha256_file(library.raw_path(video_id)),
        state=state,
        chunk_names=chunk_names,
        brand_ids=brand_ids,
        assets=assets,
        failures=failures,
        warnings=[
            "mock brand assets are for pipeline verification only; replace them before publishing"
        ],
    )
    manifest.write_manifest(result)
    return result


def render_brand_revision(
    video_id: str,
    render_revision: int,
    *,
    brand_revisions: dict[str, int],
    metadata_revision_id: str | None = None,
    on_progress: Progress | None = None,
) -> manifest.MediaManifest:
    """Render or resume one revision from published `brand.v1` layouts.

    The loop the brand-owned topology asks for: for every brand, take that
    brand's own published layout and its own files and render the whole 16:9
    asset plus every 9:16 chunk straight from the source. Nothing is shared
    between brands, so a brand that fails leaves the others intact.
    """
    brand_ids = sorted(brand_revisions)
    if not brand_ids:
        raise RenderError("brand_revisions must name at least one brand")

    chunks = library_chunks(video_id)
    chunk_names = [str(chunk["name"]) for chunk in chunks]
    expected_names = [f"part_{index}" for index in range(1, len(chunks) + 1)]
    if chunk_names != expected_names:
        raise RenderError(
            f"chunk names must be contiguous {expected_names}, got {chunk_names}"
        )

    revision_path = manifest.manifest_revision_path(video_id, render_revision)
    if revision_path.is_file():
        existing = manifest.MediaManifest.model_validate_json(
            revision_path.read_text(encoding="utf-8")
        )
        if (
            existing.topology != "brand_owned"
            or existing.chunk_names != chunk_names
            or existing.brand_revisions != brand_revisions
            or existing.metadata_revision_id != metadata_revision_id
        ):
            raise manifest.ManifestConflictError(
                f"ready render revision {render_revision} already exists with different inputs"
            )
        if on_progress:
            on_progress(1.0)
        return existing

    # Resolve every layout before encoding anything. A brand pointing at a
    # missing file or carrying no visible footage layer is a configuration
    # mistake, and it should surface now rather than after the first brand has
    # already cost minutes of GPU time.
    configs: dict[str, dict[str, dict]] = {}
    for brand_id, brand_revision in sorted(brand_revisions.items()):
        published = brand_layouts.load_published(brand_id, brand_revision)
        configs[brand_id] = {
            aspect: brand_layouts.render_config(published, aspect)
            for aspect in ("landscape", "vertical")
        }

    transcript = library.load_transcript(video_id)
    segments = transcript.get("segments") or []
    voice_manifest = library.load_voice_manifest(video_id) or {}
    source = render.probe(library.raw_path(video_id))

    from shared import pipeline_db

    fields = {"title": pipeline_db.title_for(video_id)}
    base_warnings = list(voice_manifest.get("warnings") or [])

    total_assets = (1 + len(chunks)) * len(brand_ids)
    completed = 0
    assets: list[manifest.MediaAsset] = []
    failures: list[manifest.ManifestFailure] = []

    def progressed() -> None:
        nonlocal completed
        completed += 1
        if on_progress:
            on_progress(completed / total_assets)

    for brand_id in brand_ids:
        landscape = configs[brand_id]["landscape"]
        vertical = configs[brand_id]["vertical"]
        try:
            assets.append(
                _reuse_or_render(
                    video_id=video_id,
                    render_revision=render_revision,
                    role="branded_whole",
                    content_item_id=manifest.WHOLE_ITEM,
                    expected_duration_s=source.duration_s,
                    brand_id=brand_id,
                    build=lambda brand_id=brand_id, landscape=landscape: render.render_brand_variant(
                        video_id,
                        render_revision,
                        landscape,
                        brand_id,
                        segments,
                        fields=fields,
                        warnings=base_warnings,
                    ),
                )
            )
        except Exception as exc:  # one bad asset must not discard successful peers
            failures.append(
                _failure(
                    video_id,
                    render_revision,
                    "branded_whole",
                    manifest.WHOLE_ITEM,
                    brand_id,
                    exc,
                )
            )
        progressed()

        for chunk in chunks:
            name = str(chunk["name"])
            duration_s = float(chunk["end_s"]) - float(chunk["start_s"])
            try:
                assets.append(
                    _reuse_or_render(
                        video_id=video_id,
                        render_revision=render_revision,
                        role="branded_vertical",
                        content_item_id=name,
                        expected_duration_s=duration_s,
                        brand_id=brand_id,
                        build=lambda chunk=chunk, brand_id=brand_id, vertical=vertical: render.render_brand_variant(
                            video_id,
                            render_revision,
                            vertical,
                            brand_id,
                            segments,
                            chunk=chunk,
                            fields=fields,
                            texts={
                                "caption_top": str(chunk.get("hook") or ""),
                                "caption_bottom": str(chunk.get("caption") or ""),
                            },
                            warnings=base_warnings,
                        ),
                    )
                )
            except Exception as exc:
                failures.append(
                    _failure(
                        video_id,
                        render_revision,
                        "branded_vertical",
                        name,
                        brand_id,
                        exc,
                    )
                )
            progressed()

    state: manifest.ManifestState = "needs_action" if failures else "ready"
    result = manifest.MediaManifest(
        video_id=video_id,
        render_revision=render_revision,
        metadata_revision_id=metadata_revision_id,
        source_sha256=manifest.sha256_file(library.raw_path(video_id)),
        state=state,
        topology="brand_owned",
        chunk_names=chunk_names,
        brand_ids=brand_ids,
        brand_revisions=brand_revisions,
        assets=assets,
        failures=failures,
    )
    manifest.write_manifest(result)
    return result


def library_chunks(video_id: str) -> list[dict[str, object]]:
    """Read chunks at the service boundary so rendering never trusts request spans."""
    from shared import pipeline_db

    chunks = pipeline_db.chunks_for(video_id)
    if not chunks:
        raise NoChunksError(
            f"no chunks for {video_id!r} — POST it to the transcript service's /chunk first"
        )
    return chunks
