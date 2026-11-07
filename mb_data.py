"""Extract source-separated MusicBrainz genre counters from raw JSON nodes."""

from __future__ import annotations

from collections import Counter
from typing import Any


def _entity_counter(entity: Any, *, use_folksonomy: bool = False) -> Counter:
    """Read an entity's ``genres`` or folksonomy ``tags`` list as a Counter."""
    result = Counter()
    if not isinstance(entity, dict):
        return result
    field = "tags" if use_folksonomy else "genres"
    values = entity.get(field) or []
    if isinstance(values, dict):
        # Be liberal with alternate JSON shapes in fixtures / future API builds.
        values = [{"name": name, "count": count} for name, count in values.items()]
    if not isinstance(values, (list, tuple)):
        return result
    for value in values:
        if isinstance(value, str):
            name, count = value, 1
        elif isinstance(value, dict):
            name = value.get("name")
            count = value.get("count", 1)
        else:
            continue
        if not name:
            continue
        try:
            count = int(count)
        except (TypeError, ValueError, OverflowError):
            continue
        if count > 0:
            result[str(name).strip()] += count
    return result


def _unwrap_recording(track_node: Any) -> dict:
    if not isinstance(track_node, dict):
        return {}
    nested = track_node.get("recording")
    return nested if isinstance(nested, dict) else track_node


def _artist_credit_nodes(entity: Any) -> list[dict]:
    if not isinstance(entity, dict):
        return []
    credits = entity.get("artist-credit") or []
    nodes = []
    if not isinstance(credits, (list, tuple)):
        return nodes
    for credit in credits:
        if isinstance(credit, dict) and isinstance(credit.get("artist"), dict):
            nodes.append(credit["artist"])
    return nodes


def _unique_artist_nodes(entity: Any) -> list[dict]:
    """Return all distinct artists in a credit, preserving credit order.

    The same artist can occur more than once in malformed or unusual credits.
    Prefer the MusicBrainz artist ID for identity; fall back to normalized name.
    """
    unique = []
    seen = set()
    for artist in _artist_credit_nodes(entity):
        artist_id = artist.get("id")
        identity = ("id", artist_id) if artist_id else (
            "name", str(artist.get("name", "")).strip().casefold()
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(artist)
    return unique


def _artist_source_entity(track_node: Any, recording: dict, release: dict, various_artists: bool):
    """Return the correct artist-credit node and its scope.

    This mirrors Picard's current artist-genre fallback:
      * ordinary release: every album artist credited on the release;
      * Various Artists compilation: every artist credited on the track/recording.

    A standalone recording without a release falls back to its recording credit.
    """
    if various_artists:
        # Picard builds its compilation artist fallback from the recording's
        # artist credit. A release-track wrapper may also carry an artist-credit;
        # prefer the nested recording node when present, then fall back to the
        # outer track node if the recording credit is absent.
        if _artist_credit_nodes(recording):
            return recording, "track artists (Various Artists compilation)"
        if _artist_credit_nodes(track_node):
            return track_node, "track artists (Various Artists compilation)"
        return {}, "track artists (Various Artists compilation; no artist credit found)"

    if _artist_credit_nodes(release):
        return release, "album artists (ordinary release)"
    if _artist_credit_nodes(recording):
        return recording, "recording artists (no release artist credit)"
    return {}, "album artists (no artist credit found)"


def _merge_artist_counters(entity: Any, *, use_folksonomy: bool) -> Counter:
    """Sum genre counters for all distinct credited artists.

    This is intentionally a sum, matching Picard's own Counter aggregation.
    A genre shared by two credited artists receives the sum of their counts.
    """
    total = Counter()
    for artist in _unique_artist_nodes(entity):
        total.update(_entity_counter(artist, use_folksonomy=use_folksonomy))
    return total


def describe_artist_source(
    track_node: Any,
    release_node: Any = None,
    *,
    various_artists: bool = False,
) -> tuple[str, list[str]]:
    """Return the artist fallback scope and unique credited artist names."""
    recording = _unwrap_recording(track_node)
    release = release_node if isinstance(release_node, dict) else {}
    entity, scope = _artist_source_entity(track_node, recording, release, various_artists)
    names = [str(artist.get("name") or artist.get("sort-name") or "(unnamed artist)")
             for artist in _unique_artist_nodes(entity)]
    return scope, names


def extract_source_counters(
    track_node: Any,
    release_node: Any = None,
    *,
    use_folksonomy: bool = False,
    various_artists: bool = False,
) -> dict[str, Counter]:
    """Return independent recording/release/release-group/artist counters.

    ``track_node`` may be either a recording entity or a release-track node
    containing a nested ``recording`` object. ``release_node`` is the raw
    release response. The artist fallback mirrors Picard: use album artist
    credits for ordinary releases, and track artist credits on Various Artists
    compilations.

    This function intentionally reads only raw JSON fields. It never consults
    Picard's ``track.genres`` / ``album.genres`` counters, which Picard may have
    already mutated by merging the hierarchy in place.
    """
    recording = _unwrap_recording(track_node)
    release = release_node if isinstance(release_node, dict) else {}
    release_group = release.get("release-group")

    artist_entity, _artist_scope = _artist_source_entity(
        track_node, recording, release, various_artists
    )

    return {
        "recording": _entity_counter(recording, use_folksonomy=use_folksonomy),
        "release": _entity_counter(release, use_folksonomy=use_folksonomy),
        "release_group": _entity_counter(release_group, use_folksonomy=use_folksonomy),
        "artist": _merge_artist_counters(artist_entity, use_folksonomy=use_folksonomy),
    }
