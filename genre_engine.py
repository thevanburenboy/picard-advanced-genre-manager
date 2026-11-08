"""Source-aware genre selection engine, independent of MusicBrainz Picard."""

from __future__ import annotations

from collections import Counter
import unicodedata
from typing import Callable, Mapping

SOURCE_ORDER = ("recording", "release", "release_group", "artist")
BASE_SOURCES = ("recording", "release", "release_group")
SOURCE_ALIASES = {
    "recording": "recording", "track": "recording", "release": "release",
    "release_group": "release_group", "release-group": "release_group",
    "releasegroup": "release_group", "artist": "artist", "all": "all", "*": "all",
}
VALID_MODES = {"cascade", "strict", "combined"}
VALID_ARTIST_POLICIES = {"fallback", "fill"}
VALID_SOURCE_MODES = {"disabled", "community", "community_and_my"}


def normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def parse_exclusion_rules(text: str) -> dict[str, set[str]]:
    rules: dict[str, set[str]] = {source: set() for source in (*SOURCE_ORDER, "all")}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        scope_text, genre_text = line.split(":", 1)
        genre = normalize_name(genre_text)
        if genre:
            for raw_scope in scope_text.split(","):
                source = SOURCE_ALIASES.get(raw_scope.strip().casefold())
                if source:
                    rules[source].add(genre)
    return rules


def _canonical_counter(counter: Mapping[str, int] | Counter | None) -> tuple[Counter, dict[str, str]]:
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
        if key and count > 0:
            labels.setdefault(key, name)
            normalized[key] += count
    return normalized, labels


def _excluded(source: str, genre_key: str, rules: Mapping[str, set[str]]) -> bool:
    return genre_key in rules.get("all", set()) or genre_key in rules.get(source, set())


def _filter_names(
    source: str,
    counter: Mapping[str, int] | Counter | None,
    rules: Mapping[str, set[str]],
    core_filter: Callable[[str], bool] | None = None,
) -> tuple[Counter, dict[str, str]]:
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
                pass
        kept[key] = count
        kept_labels[key] = name
    return kept, kept_labels


def _apply_threshold(counter: Counter, labels: dict[str, str], min_usage: int) -> tuple[Counter, dict[str, str]]:
    kept = counter.copy()
    kept_labels = dict(labels)
    threshold = max(0, min(100, int(min_usage)))
    if kept and threshold:
        top = max(kept.values())
        for key, count in list(kept.items()):
            if (100 * count // top) < threshold:
                del kept[key]
                kept_labels.pop(key, None)
    return kept, kept_labels


def _ranked_keys(counter: Counter) -> list[str]:
    return sorted(counter, key=lambda key: (-counter[key], key))


def _prepare_source(
    source: str,
    community_counter: Mapping[str, int] | Counter | None,
    personal_counter: Mapping[str, int] | Counter | None,
    rules: Mapping[str, set[str]],
    min_usage: int,
    core_filter: Callable[[str], bool] | None,
    *,
    personal_enabled: bool,
) -> tuple[Counter, dict[str, str], set[str], Counter, dict[str, str]]:
    """Prepare one source. Personal names bypass min usage but not exclusions/filter."""
    community, community_labels = _filter_names(source, community_counter, rules, core_filter)
    eligible_community, eligible_labels = _apply_threshold(community, community_labels, min_usage)
    personal, personal_labels = _filter_names(
        source, personal_counter if personal_enabled else Counter(), rules, core_filter
    )
    personal_keys = set(personal)
    # Keep a consistent display label, preferring the community spelling.
    labels = dict(eligible_labels)
    for key, label in personal_labels.items():
        labels.setdefault(key, label)
    return eligible_community, eligible_labels, personal_keys, community, labels


def _source_selection(
    eligible_community: Counter,
    eligible_labels: dict[str, str],
    personal_keys: set[str],
    all_community: Counter,
    all_labels: dict[str, str],
    limit: int,
    output_order: str,
) -> tuple[list[str], dict[str, int]]:
    """Select protected personal names first, then fill with eligible community names.

    Personal status protects a genre from both minimum-usage filtering and the
    maximum cutoff. When personal genres exceed the limit, the highest public
    vote-ranked personal genres win. Final output is still ranked by public votes.
    """
    if limit <= 0:
        return [], {}
    ranked_personal = sorted(
        personal_keys,
        key=lambda key: (-all_community.get(key, 0), normalize_name(all_labels.get(key, key))),
    )
    chosen = set(ranked_personal[:limit])
    for key in _ranked_keys(eligible_community):
        if len(chosen) >= limit:
            break
        chosen.add(key)

    counts = {key: all_community.get(key, 0) for key in chosen}
    if output_order == "alphabetical":
        ordered = sorted(chosen, key=lambda key: normalize_name(all_labels.get(key, key)))
    else:
        ordered = sorted(
            chosen,
            key=lambda key: (-all_community.get(key, 0), normalize_name(all_labels.get(key, key))),
        )
    return ordered, counts


def select_genres(
    source_counters: Mapping[str, Mapping[str, int] | Counter],
    *,
    personal_counters: Mapping[str, Mapping[str, int] | Counter] | None = None,
    personal_enabled: Mapping[str, bool] | None = None,
    mode: str = "cascade",
    enabled: Mapping[str, bool] | None = None,
    max_genres: int = 5,
    min_usage: int = 0,
    exclusion_rules: str = "",
    core_filter: Callable[[str], bool] | None = None,
    output_order: str = "votes",
    artist_policy: str = "fallback",
) -> tuple[list[str], dict[str, list[str]]]:
    """Select source-aware community genres with optional personal-genre protection.

    Personal genres are not a separate hierarchy level. Each entity source can
    independently include community-only or community-plus-personal data. When
    enabled, personal names bypass minimum usage and are protected from the
    maximum cutoff; they are still ordered by public vote counts. Public counts
    already include the user's upvote, so personal counts are never added to
    community counters.
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
    my_enabled = {source: False for source in SOURCE_ORDER}
    if personal_enabled is not None:
        my_enabled.update({source: bool(personal_enabled.get(source, False)) for source in SOURCE_ORDER})

    personal_counters = personal_counters or {}
    rules = parse_exclusion_rules(exclusion_rules)
    prepared = {}
    eligible_names: dict[str, list[str]] = {}
    for source in SOURCE_ORDER:
        if not active[source]:
            prepared[source] = (Counter(), {}, set(), Counter(), {})
            eligible_names[source] = []
            continue
        threshold = 0 if mode == "combined" and source in BASE_SOURCES else min_usage
        prepared[source] = _prepare_source(
            source,
            source_counters.get(source, Counter()),
            personal_counters.get(source, Counter()),
            rules,
            threshold,
            core_filter,
            personal_enabled=my_enabled[source],
        )
        eligible, labels, my_keys, _all_community, all_labels = prepared[source]
        names_by_key = dict(labels)
        names_by_key.update({key: value for key, value in all_labels.items() if key not in names_by_key})
        eligible_names[source] = sorted(
            {names_by_key.get(key, key) for key in (set(eligible) | my_keys)},
            key=lambda name: normalize_name(name),
        )

    if output_order not in {"votes", "alphabetical"}:
        output_order = "votes"

    selected: dict[str, str] = {}
    selected_sources: dict[str, str] = {}
    selected_counts: dict[str, int] = {}

    def add_from_source(source: str, slots: int) -> None:
        if slots <= 0 or not active[source]:
            return
        eligible, eligible_labels, my_keys, all_community, all_labels = prepared[source]
        ordered, _counts = _source_selection(
            eligible, eligible_labels, my_keys, all_community, all_labels, slots, "votes"
        )
        for key in ordered:
            if key in selected:
                continue
            selected[key] = all_labels.get(key, eligible_labels.get(key, key))
            selected_sources[key] = source
            selected_counts[key] = all_community.get(key, 0)
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
            eligible, eligible_labels, my_keys, all_community, all_labels = prepared[source]
            chosen, _counts = _source_selection(
                eligible, eligible_labels, my_keys, all_community, all_labels, limit, output_order
            )
            if chosen:
                for key in chosen:
                    selected[key] = all_labels.get(key, eligible_labels.get(key, key))
                    selected_sources[key] = source
                    selected_counts[key] = all_community.get(key, 0)
                break

    else:  # combined
        # Preserve the complete public vote totals separately from the
        # threshold-filtered community candidates. Personal genres bypass the
        # threshold, but their final order and competition for protected slots
        # must still use the *unfiltered* combined public totals.
        combined_public = Counter()
        combined_labels: dict[str, str] = {}
        personal_keys: set[str] = set()
        for source in BASE_SOURCES:
            if not active[source]:
                continue
            _eligible, _eligible_labels, my_keys, all_community, all_labels = prepared[source]
            for key, count in all_community.items():
                combined_public[key] += count
                combined_labels.setdefault(key, all_labels.get(key, key))
            if my_enabled[source]:
                # Personal submissions are a set across the enabled entity
                # scopes: the same genre marked on multiple entities remains
                # one selected genre, not multiple personal votes.
                personal_keys.update(my_keys)
                for key in my_keys:
                    combined_labels.setdefault(key, all_labels.get(key, key))

        eligible_combined, eligible_combined_labels = _apply_threshold(
            combined_public, combined_labels, min_usage
        )
        labels_for_selection = dict(combined_labels)
        labels_for_selection.update(eligible_combined_labels)
        ranked_base, _counts = _source_selection(
            eligible_combined,
            eligible_combined_labels,
            personal_keys,
            combined_public,
            labels_for_selection,
            limit,
            "votes",
        )
        for key in ranked_base:
            selected[key] = combined_labels.get(key, key)
            selected_sources[key] = "combined"
            selected_counts[key] = combined_public.get(key, 0)

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

    return [selected[key] for key in ordered_keys], eligible_names
