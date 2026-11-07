"""Source-aware genre selection engine, independent of MusicBrainz Picard.

This module deliberately has no Qt or Picard imports, making the selection
rules safe to test before connecting them to Picard's metadata pipeline.
"""

from __future__ import annotations

from collections import Counter
import unicodedata
from typing import Callable, Mapping

SOURCE_ORDER = ("recording", "release", "release_group", "artist")
BASE_SOURCES = ("recording", "release", "release_group")
SOURCE_ALIASES = {
    "recording": "recording",
    "track": "recording",
    "release": "release",
    "release_group": "release_group",
    "release-group": "release_group",
    "releasegroup": "release_group",
    "artist": "artist",
    "all": "all",
    "*": "all",
}
VALID_MODES = {"cascade", "strict", "combined"}
VALID_ARTIST_POLICIES = {"fallback", "fill"}


def normalize_name(value: str) -> str:
    """Normalize genre names for case-insensitive equality and deduplication."""
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def parse_exclusion_rules(text: str) -> dict[str, set[str]]:
    """Parse source-scoped exact-name exclusions.

    Examples::

        artist: Christmas Music
        release,release_group: Soundtrack
        all: Spoken Word

    Scope names are case-insensitive. Multiple source scopes can be comma
    separated. Matching is Unicode-normalized, whitespace-trimmed and
    case-insensitive. Blank lines and lines starting with # are ignored.
    """
    rules: dict[str, set[str]] = {source: set() for source in (*SOURCE_ORDER, "all")}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        scope_text, genre_text = line.split(":", 1)
        genre = normalize_name(genre_text)
        if not genre:
            continue
        for raw_scope in scope_text.split(","):
            source = SOURCE_ALIASES.get(raw_scope.strip().casefold())
            if source:
                rules[source].add(genre)
    return rules


def _canonical_counter(counter: Mapping[str, int] | Counter | None) -> tuple[Counter, dict[str, str]]:
    """Return normalized keys and their first original display name."""
    normalized = Counter()
    labels: dict[str, str] = {}
    if not counter:
        return normalized, labels
    for raw_name, raw_count in counter.items():
        name = str(raw_name).strip()
        key = normalize_name(name)
        try:
            count = int(raw_count)
        except (TypeError, ValueError, OverflowError):
            continue
        if not key or count <= 0:
            continue
        labels.setdefault(key, name)
        normalized[key] += count
    return normalized, labels


def _excluded(source: str, genre_key: str, rules: Mapping[str, set[str]]) -> bool:
    return genre_key in rules.get("all", set()) or genre_key in rules.get(source, set())


def _filter_source(
    source: str,
    counter: Mapping[str, int] | Counter | None,
    rules: Mapping[str, set[str]],
    min_usage: int,
    core_filter: Callable[[str], bool] | None = None,
) -> tuple[Counter, dict[str, str]]:
    """Apply exclusions, Picard-style name filters, and relative vote threshold.

    ``core_filter`` is an optional predicate that returns True when a genre
    should be skipped (Picard's TagGenreFilter.skip can be adapted to this).
    """
    counts, labels = _canonical_counter(counter)
    kept = Counter()
    kept_labels: dict[str, str] = {}
    for key, count in counts.items():
        name = labels[key]
        if _excluded(source, key, rules):
            continue
        if core_filter is not None:
            try:
                if core_filter(name):
                    continue
            except Exception:
                # A secondary Picard filter must not make selection fail.
                pass
        kept[key] = count
        kept_labels[key] = name

    if not kept:
        return kept, kept_labels

    threshold = max(0, min(100, int(min_usage)))
    if threshold:
        top_count = max(kept.values())
        for key, count in list(kept.items()):
            # Matches Picard TagGenreFilter.filter: integer floor percentage.
            if (100 * count // top_count) < threshold:
                del kept[key]
                kept_labels.pop(key, None)
    return kept, kept_labels


def _ranked_keys(counter: Counter) -> list[str]:
    # Picard sorts by descending count, then genre name.
    return sorted(counter, key=lambda key: (-counter[key], key))


def select_genres(
    source_counters: Mapping[str, Mapping[str, int] | Counter],
    *,
    mode: str = "cascade",
    enabled: Mapping[str, bool] | None = None,
    max_genres: int = 5,
    min_usage: int = 0,
    exclusion_rules: str = "",
    core_filter: Callable[[str], bool] | None = None,
    output_order: str = "votes",
    artist_policy: str = "fallback",
) -> tuple[list[str], dict[str, list[str]]]:
    """Select genres from independent MusicBrainz sources.

    ``artist_policy`` controls how artist genres participate after the three
    lower hierarchy levels have been filtered:
      * ``fallback``: use artist genres only if no eligible lower-level genres
        remain after exclusions, Picard's name filter and minimum usage.
      * ``fill``: use artist genres to fill unused slots after lower-level
        genres. Artist votes are ranked within the artist source, not compared
        numerically with votes from recording/release/release-group sources.

    In strict mode, the strict single-source contract takes precedence: only
    the first enabled source with eligible genres is used, so artist genres are
    reached only when no enabled lower source is populated.

    Modes:
      * ``cascade``: recording -> release -> release group, then artist
        according to ``artist_policy``. Threshold is independent per source.
      * ``strict``: use only the first enabled source with eligible genres.
      * ``combined``: merge recording/release/release-group counts, apply the
        threshold to the merged set, then use artist according to
        ``artist_policy`` if slots remain (or if no eligible base genres exist).

    ``output_order`` may be ``votes`` or ``alphabetical``. In cascade and
    combined-fill modes, source hierarchy is preserved and votes rank genres
    within each source/population. In ordinary combined mode, merged votes are
    ranked globally. Ties are alphabetical.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported selection mode: {mode!r}")
    if artist_policy not in VALID_ARTIST_POLICIES:
        artist_policy = "fallback"

    limit = max(0, min(1000, int(max_genres)))
    if limit == 0:
        return [], {source: [] for source in SOURCE_ORDER}

    active = {source: True for source in SOURCE_ORDER}
    if enabled is not None:
        active.update({source: bool(enabled.get(source, True)) for source in SOURCE_ORDER})

    rules = parse_exclusion_rules(exclusion_rules)
    eligible_counters: dict[str, Counter] = {}
    labels_by_source: dict[str, dict[str, str]] = {}
    eligible_names: dict[str, list[str]] = {}

    # Combined applies its threshold only after combining the three base
    # sources. Artist remains independently thresholded, because its votes
    # belong to a different entity/population.
    for source in SOURCE_ORDER:
        threshold = 0 if mode == "combined" and source in BASE_SOURCES else min_usage
        if not active[source]:
            eligible_counters[source] = Counter()
            labels_by_source[source] = {}
            eligible_names[source] = []
            continue
        filtered, labels = _filter_source(
            source,
            source_counters.get(source, Counter()),
            rules,
            threshold,
            core_filter,
        )
        eligible_counters[source] = filtered
        labels_by_source[source] = labels
        eligible_names[source] = [labels[key] for key in _ranked_keys(filtered)]

    if output_order not in {"votes", "alphabetical"}:
        output_order = "votes"

    selected: dict[str, str] = {}
    selected_sources: dict[str, str] = {}
    selected_counts: dict[str, int] = {}

    def add_from_source(source: str, available_slots: int) -> None:
        if available_slots <= 0 or not active[source]:
            return
        for key in _ranked_keys(eligible_counters[source]):
            if key in selected:
                continue
            selected[key] = labels_by_source[source][key]
            selected_sources[key] = source
            selected_counts[key] = eligible_counters[source][key]
            if len(selected) >= limit:
                return

    if mode == "cascade":
        for source in BASE_SOURCES:
            add_from_source(source, limit - len(selected))
            if len(selected) >= limit:
                break
        if active["artist"] and len(selected) < limit:
            if artist_policy == "fill" or not selected:
                add_from_source("artist", limit - len(selected))

    elif mode == "strict":
        for source in SOURCE_ORDER:
            if not active[source]:
                continue
            ranked = _ranked_keys(eligible_counters[source])
            if ranked:
                chosen = ranked[:limit]
                selected = {key: labels_by_source[source][key] for key in chosen}
                selected_sources = {key: source for key in chosen}
                selected_counts = {key: eligible_counters[source][key] for key in chosen}
                break

    else:  # combined
        combined = Counter()
        combined_labels: dict[str, str] = {}
        for source in BASE_SOURCES:
            if not active[source]:
                continue
            for key, count in eligible_counters[source].items():
                combined[key] += count
                combined_labels.setdefault(key, labels_by_source[source][key])

        if combined and min_usage:
            top_count = max(combined.values())
            combined = Counter({
                key: count for key, count in combined.items()
                if (100 * count // top_count) >= max(0, min(100, int(min_usage)))
            })

        ranked_base = _ranked_keys(combined)
        for key in ranked_base[:limit]:
            selected[key] = combined_labels[key]
            selected_sources[key] = "combined"
            selected_counts[key] = combined[key]

        if active["artist"] and len(selected) < limit:
            if artist_policy == "fill" or not selected:
                add_from_source("artist", limit - len(selected))

    if output_order == "alphabetical":
        ordered_keys = sorted(selected, key=lambda key: normalize_name(selected[key]))
    elif mode == "cascade":
        source_rank = {source: index for index, source in enumerate(SOURCE_ORDER)}
        ordered_keys = sorted(
            selected,
            key=lambda key: (
                source_rank.get(selected_sources.get(key, "artist"), len(SOURCE_ORDER)),
                -selected_counts.get(key, 0),
                normalize_name(selected[key]),
            ),
        )
    elif mode == "combined" and any(source == "artist" for source in selected_sources.values()):
        # Lower-level combined votes first; artist fill follows, ranked within
        # the artist population rather than compared against merged base votes.
        ordered_keys = sorted(
            selected,
            key=lambda key: (
                1 if selected_sources.get(key) == "artist" else 0,
                -selected_counts.get(key, 0),
                normalize_name(selected[key]),
            ),
        )
    else:
        ordered_keys = sorted(
            selected,
            key=lambda key: (-selected_counts.get(key, 0), normalize_name(selected[key])),
        )

    result = [selected[key] for key in ordered_keys]
    return result, eligible_names
