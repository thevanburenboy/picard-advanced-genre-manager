"""Extract source-separated community and personal genre data from raw MusicBrainz JSON."""

from __future__ import annotations

from collections import Counter
from typing import Any


def _entity_counter(entity: Any, *, use_folksonomy: bool = False) -> Counter:
    """Read an entity's community ``genres`` or folksonomy ``tags`` counter."""
    result = Counter()
    if not isinstance(entity, dict):
        return result
    field = "tags" if use_folksonomy else "genres"
    values = entity.get(field) or []
    if isinstance(values, dict):
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


def _entity_personal_counter(entity: Any, *, use_folksonomy: bool = False) -> Counter:
    """Read this user's own submissions as one personal mark per genre/entity.

    MusicBrainz exposes ``user-genres`` for curated genres and ``user-tags`` for
    folksonomy mode. A user can only submit a given tag once to an entity, so
    personal counts are deliberately normalized to one per unique name here.
    """
    result = Counter()
    if not isinstance(entity, dict):
        return result
    field = "user-tags" if use_folksonomy else "user-genres"
    values = entity.get(field) or []
    if isinstance(values, dict):
        values = list(values)
    if not isinstance(values, (list, tuple)):
        return result
    seen = set()
    for value in values:
        if isinstance(value, str):
            name = value
        elif isinstance(value, dict):
            name = value.get("name")
        else:
            continue
        if not name:
            continue
        name = str(name).strip()
        key = name.casefold()
        if name and key not in seen:
            result[name] = 1
            seen.add(key)
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
    """Return distinct credited artists in credit order."""
    unique = []
    seen = set()
    for artist in _artist_credit_nodes(entity):
        artist_id = artist.get("id")
        identity = ("id", artist_id) if artist_id else (
            "name", str(artist.get("name", "")).strip().casefold()
        )
        if identity not in seen:
            seen.add(identity)
            unique.append(artist)
    return unique


def _artist_source_entity(track_node: Any, recording: dict, release: dict, various_artists: bool):
    """Select artist credits using Picard's ordinary-release/compilation rules."""
    if various_artists:
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


def _merge_artist_counters(entity: Any, *, use_folksonomy: bool, personal: bool = False) -> Counter:
    total = Counter()
    for artist in _unique_artist_nodes(entity):
        counter = (
            _entity_personal_counter(artist, use_folksonomy=use_folksonomy)
            if personal else _entity_counter(artist, use_folksonomy=use_folksonomy)
        )
        if personal:
            # Artist is an aggregate source. The same personal genre submitted
            # on multiple credited artists is still one protected genre name,
            # not a weighted personal vote count.
            for name in counter:
                total[name] = 1
        else:
            total.update(counter)
    return total


def describe_artist_source(
    track_node: Any,
    release_node: Any = None,
    *,
    various_artists: bool = False,
) -> tuple[str, list[str]]:
    recording = _unwrap_recording(track_node)
    release = release_node if isinstance(release_node, dict) else {}
    entity, scope = _artist_source_entity(track_node, recording, release, various_artists)
    names = [str(artist.get("name") or artist.get("sort-name") or "(unnamed artist)")
             for artist in _unique_artist_nodes(entity)]
    return scope, names


def extract_source_data(
    track_node: Any,
    release_node: Any = None,
    *,
    use_folksonomy: bool = False,
    various_artists: bool = False,
) -> tuple[dict[str, Counter], dict[str, Counter]]:
    """Return separate community and personal counters for all four hierarchy levels.

    Community counters use ``genres`` or ``tags``. Personal counters use
    ``user-genres`` or ``user-tags`` depending on Picard's global vocabulary
    setting. Artist data is aggregated from the same artist-credit scope Picard
    uses: album artists for ordinary releases, track artists for compilations.
    """
    recording = _unwrap_recording(track_node)
    release = release_node if isinstance(release_node, dict) else {}
    release_group = release.get("release-group")
    artist_entity, _artist_scope = _artist_source_entity(
        track_node, recording, release, various_artists
    )

    community = {
        "recording": _entity_counter(recording, use_folksonomy=use_folksonomy),
        "release": _entity_counter(release, use_folksonomy=use_folksonomy),
        "release_group": _entity_counter(release_group, use_folksonomy=use_folksonomy),
        "artist": _merge_artist_counters(
            artist_entity, use_folksonomy=use_folksonomy, personal=False
        ),
    }
    personal = {
        "recording": _entity_personal_counter(recording, use_folksonomy=use_folksonomy),
        "release": _entity_personal_counter(release, use_folksonomy=use_folksonomy),
        "release_group": _entity_personal_counter(release_group, use_folksonomy=use_folksonomy),
        "artist": _merge_artist_counters(
            artist_entity, use_folksonomy=use_folksonomy, personal=True
        ),
    }
    return community, personal


def extract_source_counters(
    track_node: Any,
    release_node: Any = None,
    *,
    use_folksonomy: bool = False,
    various_artists: bool = False,
) -> dict[str, Counter]:
    """Backward-compatible community-only extractor."""
    community, _personal = extract_source_data(
        track_node,
        release_node,
        use_folksonomy=use_folksonomy,
        various_artists=various_artists,
    )
    return community
