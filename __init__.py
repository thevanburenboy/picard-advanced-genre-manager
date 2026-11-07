"""Genre Hierarchy Manager — Picard 3.x source-aware genre selection.

The plugin reads raw MusicBrainz JSON and can preview or replace the in-memory
genre tag with a source-aware selection. Writing the audio file remains an
explicit action performed by the user through Picard.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .config_access import initialize_plugin_config, picard_uses_folksonomy, read_config_value
from .genre_engine import SOURCE_ORDER, VALID_ARTIST_POLICIES, VALID_MODES, select_genres
from .mb_data import describe_artist_source, extract_source_counters
from .options import DEFAULTS, GenreHierarchyOptionsPage

PLUGIN_VERSION = "0.6.0"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _format_counter(counter: Counter) -> str:
    if not counter:
        return "<none>"
    pairs = sorted(counter.items(), key=lambda pair: (-pair[1], str(pair[0]).casefold()))
    return ", ".join(f"{name!r} ({count})" for name, count in pairs)


def _format_genres(values: list[str]) -> str:
    return " · ".join(values) if values else "<none>"


def _metadata_values(metadata: Any, key: str) -> list[str]:
    try:
        return [str(value) for value in metadata.getall(key) if value not in (None, "")]
    except Exception:
        try:
            value = metadata.get(key)
            return [str(value)] if value not in (None, "") else []
        except Exception:
            return []


def _display_values(selected: list[str], joiner: str) -> list[str]:
    try:
        from picard.util import titlecase
    except Exception:
        titlecase = str.title
    display = [titlecase(name) for name in selected]
    # Preserve the selection engine's chosen order (vote-ranked or alphabetical).
    return [joiner.join(display)] if joiner else display


def _setting(section: Any, key: str, default: Any) -> Any:
    try:
        return section.get(key, default)
    except Exception:
        try:
            value = section[key]
        except Exception:
            return default
        return default if value is None else value


def _various_artists(metadata) -> bool:
    try:
        from picard.const import VARIOUS_ARTISTS_ID
        artist_ids = _metadata_values(metadata, "musicbrainz_albumartistid")
        return any(value.casefold() == VARIOUS_ARTISTS_ID.casefold() for value in artist_ids)
    except Exception:
        return False


def _current_filter(api, global_settings, enabled: bool):
    if not enabled:
        return None
    filter_text = str(_setting(global_settings, "genres_filter", "") or "")
    try:
        from picard.track import TagGenreFilter
        return TagGenreFilter(filter_text).skip
    except Exception as exc:
        api.logger.warning(
            "Genre Hierarchy Manager: could not load Picard's genre-name filter (%s); "
            "continuing without it",
            exc,
        )
        return None


def _same_genres(left: list[str], right: list[str]) -> bool:
    return sorted(str(value).casefold() for value in left) == sorted(
        str(value).casefold() for value in right
    )


def _read_options(api) -> dict[str, Any]:
    cfg = api.plugin_config
    options = {key: read_config_value(cfg, key, default) for key, default in DEFAULTS.items()}
    options["selection_mode"] = options["selection_mode"] if options["selection_mode"] in VALID_MODES else "cascade"
    options["artist_policy"] = options["artist_policy"] if options["artist_policy"] in VALID_ARTIST_POLICIES else "fallback"
    options["max_genres"] = max(0, min(100, _safe_int(options["max_genres"], 5)))
    options["min_genre_usage"] = max(0, min(100, _safe_int(options["min_genre_usage"], 0)))
    options["exclusion_rules"] = str(options["exclusion_rules"] or "")
    return options


def process_track(api, track, metadata, track_node, release_node=None):
    """Preview or apply the selected genre list using raw MusicBrainz JSON."""
    try:
        options = _read_options(api)
        write_requested = bool(options["write_genres_enabled"])
        preview_enabled = bool(options["preview_enabled"])
        if not write_requested and not preview_enabled:
            return

        global_settings = api.global_config.setting
        global_genres_enabled = bool(_setting(global_settings, "use_genres", True))
        if not global_genres_enabled:
            api.logger.warning(
                "[Genre Hierarchy Manager %s] Picard's 'Use genres from MusicBrainz' is disabled. "
                "The plugin will not replace the genre tag; enable that Picard option to retrieve genre data.",
                PLUGIN_VERSION,
            )

        write_enabled = write_requested and global_genres_enabled
        folksonomy = picard_uses_folksonomy(global_settings)
        is_various_artists = _various_artists(metadata)
        source_counters = extract_source_counters(
            track_node,
            release_node,
            use_folksonomy=folksonomy,
            various_artists=is_various_artists,
        )
        artist_scope, artist_names = describe_artist_source(
            track_node,
            release_node,
            various_artists=is_various_artists,
        )
        enabled = {
            "recording": bool(options["source_recording"]),
            "release": bool(options["source_release"]),
            "release_group": bool(options["source_release_group"]),
            "artist": bool(options["source_artist"]),
        }

        title = metadata.get("title", "(untitled)")
        recording_id = metadata.get("musicbrainz_recordingid", "") or getattr(track, "id", "")
        release_id = getattr(getattr(track, "album", None), "id", "")
        release_group_id = getattr(getattr(getattr(track, "album", None), "release_group", None), "id", "")
        joiner = str(_setting(global_settings, "join_genres", "") or "")
        core_filter = _current_filter(api, global_settings, bool(options["apply_picard_filter"]))
        max_genres = options["max_genres"]
        min_usage = options["min_genre_usage"]
        mode = options["selection_mode"]

        if preview_enabled or write_enabled:
            api.logger.info(
                "[Genre Hierarchy Manager %s] %s | recording=%s release=%s release-group=%s | "
                "mode=%s order=%s artist_policy=%s max=%d min=%d%% folksonomy=%s enabled=%s action=%s",
                PLUGIN_VERSION,
                title,
                recording_id or "?",
                release_id or "?",
                release_group_id or "?",
                mode,
                options["output_order"],
                options["artist_policy"],
                max_genres,
                min_usage,
                folksonomy,
                ",".join(source for source in SOURCE_ORDER if enabled[source]) or "<none>",
                "WRITE" if write_enabled else "PREVIEW",
            )

        if options["verbose_logging"] and (preview_enabled or write_enabled):
            api.logger.info("  Source-specific exclusions:\n%s", options["exclusion_rules"] or "  <none>")
            api.logger.info("  Artist source: %s | credited artists: %s",
                            artist_scope, ", ".join(artist_names) or "<none>")
            api.logger.info("  Raw source counters (%s):", "folksonomy tags" if folksonomy else "MusicBrainz genres")
            for source in SOURCE_ORDER:
                suffix = " [disabled]" if not enabled[source] else ""
                api.logger.info("    %s%s: %s", source, suffix, _format_counter(source_counters[source]))

        selected, eligible = select_genres(
            source_counters,
            mode=mode,
            enabled=enabled,
            max_genres=max_genres,
            min_usage=min_usage,
            exclusion_rules=options["exclusion_rules"],
            core_filter=core_filter,
            output_order=options["output_order"],
            artist_policy=options["artist_policy"],
        )
        proposed = _display_values(selected, joiner)
        current = _metadata_values(metadata, "genre")
        matches_current = _same_genres(proposed, current)

        if write_enabled:
            # The plugin processor runs after Track._customize_metadata() has
            # produced Picard's built-in genre tag and before user tagging
            # scripts. Replace only the public genre tag; leave ~genres and all
            # other MusicBrainz metadata intact for scripts and diagnostics.
            metadata["genre"] = list(proposed)
            api.logger.info(
                "  Applied selected genres to in-memory genre tag: %s (previous: %s)",
                _format_genres(proposed),
                _format_genres(current),
            )
        elif preview_enabled:
            api.logger.info(
                "  Proposed %s selection: %s | matches Picard: %s",
                mode,
                _format_genres(proposed),
                "YES" if matches_current else "no",
            )

        if options["verbose_logging"] and (preview_enabled or write_enabled):
            for source in SOURCE_ORDER:
                api.logger.info("    eligible %s genres: %s", source, ", ".join(eligible[source]) or "<none>")
            for alternative_mode in ("cascade", "strict", "combined"):
                if alternative_mode == mode:
                    continue
                alternative, _ = select_genres(
                    source_counters,
                    mode=alternative_mode,
                    enabled=enabled,
                    max_genres=max_genres,
                    min_usage=min_usage,
                    exclusion_rules=options["exclusion_rules"],
                    core_filter=core_filter,
                    output_order=options["output_order"],
                    artist_policy=options["artist_policy"],
                )
                api.logger.info(
                    "  Alternative %-8s: %s",
                    alternative_mode,
                    _format_genres(_display_values(alternative, joiner)),
                )
    except Exception:
        api.logger.exception(
            "Genre Hierarchy Manager failed while processing %s",
            metadata.get("title", "(untitled)"),
        )


def enable(api):
    """Register plugin settings, options page, and genre metadata processor."""
    reset_performed = initialize_plugin_config(api.plugin_config, DEFAULTS)
    if reset_performed:
        api.logger.warning(
            "Genre Hierarchy Manager: reset settings to safe defaults once because "
            "0.4.0 could save untouched/invalid options-page widget values."
        )
    api.register_options_page(GenreHierarchyOptionsPage)
    api.register_track_metadata_processor(process_track, priority=-100)
    api.logger.info(
        "Genre Hierarchy Manager %s loaded — genre-tag replacement is %s",
        PLUGIN_VERSION,
        "ENABLED" if read_config_value(api.plugin_config, "write_genres_enabled", False) else "disabled (preview only)",
    )
