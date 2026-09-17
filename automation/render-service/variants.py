"""Build a complete, resumable media revision from clean and branded assets."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import brand
import brands as brand_layouts
import library
import manifest
import render
from config import settings
from errors import NoChunksError, RenderError


Progress = Callable[[float], None]


def _render_in_parallel(
    builds: list[tuple[manifest.AssetRole, str, str | None, Callable[[], manifest.MediaAsset]]],
    on_progress: Progress | None,
    video_id: str,
    render_revision: int,
    schema_version: str | None = None,
) -> tuple[list[manifest.MediaAsset], list[manifest.ManifestFailure]]:
    """Encode independent delivery assets side by side, in a fixed order.

    Every asset of a revision is its own file built from its own frozen
    layout, so nothing here shares state and the only reason they ran one
    after another was that the loop was written that way. One ffmpeg leaves
    most of this box idle - see `Settings.render_workers` - so the spare cores
    are worth more than the simplicity of a serial loop.

    Results are collected by position rather than by completion, so the
    manifest lists the same assets in the same order whatever the workers do,
    and one asset failing still leaves its verified peers in place.
    """
    assets: list[manifest.MediaAsset | None] = [None] * len(builds)
    failures: list[tuple[int, manifest.ManifestFailure]] = []
    lock = threading.Lock()
    completed = 0
    total = len(builds) or 1

    # Probed once here rather than raced inside the workers: the probe runs a
    # real encode, and several at once would report the same answer after
    # doing the same work several times.
    render.encoder_choice()

    def work(index: int) -> None:
        nonlocal completed
        role, content_item_id, brand_id, build = builds[index]
        try:
            asset = build()
        except Exception as exc:  # one bad asset must not discard successful peers
            failure = _failure(
                video_id, render_revision, role, content_item_id, brand_id, exc,
                schema_version,
            )
            with lock:
                failures.append((index, failure))
        else:
            assets[index] = asset
        with lock:
            completed += 1
            done = completed
        if on_progress:
            on_progress(done / total)

    workers = max(min(settings.render_workers, len(builds)), 1)
    if workers == 1:
        for index in range(len(builds)):
            work(index)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, range(len(builds))))

    return (
        [asset for asset in assets if asset is not None],
        [failure for _, failure in sorted(failures)],
    )


def _failure(
    video_id: str,
    render_revision: int,
    role: manifest.AssetRole,
    content_item_id: str,
    brand_id: str | None,
    exc: Exception,
    schema_version: str | None = None,
) -> manifest.ManifestFailure:
    retryable = isinstance(exc, (RenderError, manifest.ManifestValidationError, OSError))
    return manifest.ManifestFailure(
        asset_id=manifest.deterministic_asset_id(
            video_id, render_revision, role, content_item_id, brand_id, schema_version
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
    schema_version: str | None = None,
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
                schema_version=schema_version,
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
    """Render a v2 landscape whole, then cut every delivery chunk from it."""
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
            existing.schema_version != manifest.SCHEMA_VERSION
            or existing.topology != "landscape_chunks"
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
    configs: dict[str, dict] = {}
    for brand_id, brand_revision in sorted(brand_revisions.items()):
        published = brand_layouts.load_published(brand_id, brand_revision)
        configs[brand_id] = brand_layouts.render_config(published, "landscape")

    transcript = library.load_transcript(video_id)
    segments = transcript.get("segments") or []
    voice_manifest = library.load_voice_manifest(video_id) or {}
    source = render.probe(library.raw_path(video_id))

    from shared import pipeline_db

    fields = {"title": pipeline_db.title_for(video_id)}
    base_warnings = list(voice_manifest.get("warnings") or [])

    whole_builds: list[
        tuple[manifest.AssetRole, str, str | None, Callable[[], manifest.MediaAsset]]
    ] = []
    for brand_id in brand_ids:
        landscape = configs[brand_id]
        whole_builds.append(
            (
                "branded_whole",
                manifest.WHOLE_ITEM,
                brand_id,
                lambda brand_id=brand_id, landscape=landscape: _reuse_or_render(
                    video_id=video_id,
                    render_revision=render_revision,
                    role="branded_whole",
                    content_item_id=manifest.WHOLE_ITEM,
                    expected_duration_s=source.duration_s,
                    brand_id=brand_id,
                    build=lambda: render.render_brand_variant(
                        video_id,
                        render_revision,
                        landscape,
                        brand_id,
                        segments,
                        fields=fields,
                        warnings=base_warnings,
                        schema_version=manifest.SCHEMA_VERSION,
                    ),
                    schema_version=manifest.SCHEMA_VERSION,
                ),
            )
        )
    wholes, failures = _render_in_parallel(
        whole_builds, on_progress, video_id, render_revision, manifest.SCHEMA_VERSION
    )
    whole_by_brand = {asset.brand_id: asset for asset in wholes}

    cut_builds: list[
        tuple[manifest.AssetRole, str, str | None, Callable[[], manifest.MediaAsset]]
    ] = []
    for brand_id in brand_ids:
        whole = whole_by_brand.get(brand_id)
        for chunk in chunks:
            name = str(chunk["name"])
            duration_s = float(chunk["end_s"]) - float(chunk["start_s"])
            if whole is None:
                failures.append(
                    manifest.ManifestFailure(
                        asset_id=manifest.deterministic_asset_id(
                            video_id, render_revision, "branded_landscape_chunk", name,
                            brand_id, manifest.SCHEMA_VERSION,
                        ),
                        code="whole_dependency_failed",
                        message=(f"cannot cut {brand_id}/{name} until its branded whole succeeds"),
                        retryable=True,
                    )
                )
                if on_progress:
                    on_progress(1.0)
                continue
            cut_builds.append(
                (
                    "branded_landscape_chunk",
                    name,
                    brand_id,
                    lambda whole=whole, chunk=chunk, name=name, duration_s=duration_s:
                    _reuse_or_render(
                        video_id=video_id,
                        render_revision=render_revision,
                        role="branded_landscape_chunk",
                        content_item_id=name,
                        expected_duration_s=duration_s,
                        brand_id=whole.brand_id,
                        lineage_asset_id=whole.asset_id,
                        schema_version=manifest.SCHEMA_VERSION,
                        build=lambda: render.cut_branded_landscape_chunk(whole, chunk),
                    ),
                )
            )
    cut_assets, cut_failures = _render_in_parallel(
        cut_builds, on_progress, video_id, render_revision, manifest.SCHEMA_VERSION
    )
    assets = [*wholes, *cut_assets]
    failures.extend(cut_failures)

    state: manifest.ManifestState = "needs_action" if failures else "ready"
    result = manifest.MediaManifest(
        schema_version=manifest.SCHEMA_VERSION,
        video_id=video_id,
        render_revision=render_revision,
        metadata_revision_id=metadata_revision_id,
        source_sha256=manifest.sha256_file(library.raw_path(video_id)),
        state=state,
        topology="landscape_chunks",
        chunk_names=chunk_names,
        brand_ids=brand_ids,
        brand_revisions=brand_revisions,
        assets=assets,
        failures=failures,
    )
    manifest.write_manifest(result)
    return result


def render_brand_preview(
    video_id: str,
    preview_id: str,
    *,
    brand_revisions: dict[str, int],
    selections: dict[str, dict[str, object]],
    on_progress: Progress | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[manifest.MediaAsset], list[dict[str, str]], bool]:
    """Render an operator preview without touching delivery paths or manifests.

    Same shape as delivery: the brand's landscape whole is rendered once and
    every requested part is cut out of that file. A chunk is never rendered
    from the source on its own, even when it is the only thing asked for,
    because the music bed fades in at the start of the whole and loops from
    there - a chunk rendered in isolation would carry a different bed than the
    one that ships, which is exactly what a preview exists to rule out.

    So `variants: "chunks"` still costs one whole render. The whole is written
    into the preview directory as the cutting parent and simply not returned.
    """
    chunks = library_chunks(video_id)
    by_number = {int(chunk["idx"]) + 1: chunk for chunk in chunks}
    transcript = library.load_transcript(video_id)
    segments = transcript.get("segments") or []
    voice_manifest = library.load_voice_manifest(video_id) or {}
    render.probe(library.raw_path(video_id))
    from shared import pipeline_db

    fields = {"title": pipeline_db.title_for(video_id)}
    warnings = list(voice_manifest.get("warnings") or [])

    # Resolve the whole selection before any encoding starts: a chunk number
    # nobody cut is the caller's mistake, and finding it after a full whole
    # render has already been paid for helps nobody.
    wanted: list[tuple[str, bool, list[dict[str, object]]]] = []
    for brand_id in sorted(brand_revisions):
        variant = str(selections[brand_id]["variants"])
        if variant not in ("all", "whole", "chunks"):
            raise RenderError(f"unknown preview variant {variant!r}")
        numbers: list[int] = []
        if variant in ("all", "chunks"):
            numbers = [
                int(number)
                for number in (selections[brand_id].get("chunks") or sorted(by_number))
            ]
        for number in numbers:
            if number not in by_number:
                raise RenderError(f"requested chunk {number} does not exist")
        wanted.append(
            (brand_id, variant in ("all", "whole"), [by_number[n] for n in numbers])
        )

    configs = {
        brand_id: brand_layouts.render_config(
            brand_layouts.load_published(brand_id, revision), "landscape"
        )
        for brand_id, revision in brand_revisions.items()
    }

    assets: list[manifest.MediaAsset] = []
    failures: list[dict[str, str]] = []
    root = library.video_dir(video_id) / "previews" / preview_id / "brands"
    # The parent counts as work whether or not it is delivered, so progress
    # does not stall through a full render that reports nothing.
    total = sum(1 + len(parts) for _brand, _whole, parts in wanted)
    done = 0

    def advance() -> None:
        nonlocal done
        done += 1
        if on_progress:
            on_progress(done / total)

    for brand_id, want_whole, parts in wanted:
        if cancelled and cancelled():
            return assets, failures, True
        brand_root = root / brand_id
        try:
            whole = render.render_brand_variant(
                video_id,
                1,
                configs[brand_id],
                brand_id,
                segments,
                chunk=None,
                fields=fields,
                warnings=warnings,
                output_path=brand_root / "whole-16x9.mp4",
                cancelled=cancelled,
            )
        except Exception as exc:  # preserve successful preview peers
            reason = f"{type(exc).__name__}: {exc}"
            if want_whole:
                failures.append(
                    {"brand_id": brand_id, "content_item_id": manifest.WHOLE_ITEM,
                     "error": reason}
                )
            for chunk in parts:
                failures.append(
                    {
                        "brand_id": brand_id,
                        "content_item_id": str(chunk["name"]),
                        "error": f"whole parent failed: {reason}",
                    }
                )
            advance()
            for _ in parts:
                advance()
            continue

        if want_whole:
            assets.append(whole)
        advance()

        for chunk in parts:
            if cancelled and cancelled():
                return assets, failures, True
            name = str(chunk["name"])
            try:
                assets.append(
                    render.cut_branded_landscape_chunk(
                        whole,
                        chunk,
                        output_path=brand_root / "landscape" / f"{name}-16x9.mp4",
                        cancelled=cancelled,
                    )
                )
            except Exception as exc:  # one bad cut must not discard its peers
                failures.append(
                    {"brand_id": brand_id, "content_item_id": name,
                     "error": f"{type(exc).__name__}: {exc}"}
                )
            advance()

    return assets, failures, False


def library_chunks(video_id: str) -> list[dict[str, object]]:
    """Read chunks at the service boundary so rendering never trusts request spans."""
    from shared import pipeline_db

    chunks = pipeline_db.chunks_for(video_id)
    if not chunks:
        raise NoChunksError(
            f"no chunks for {video_id!r} — POST it to the transcript service's /chunk first"
        )
    return chunks
