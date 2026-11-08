"""Extend Picard's existing lookup includes to fetch personal genres when requested.

Picard's MetadataItem.set_genre_inc_params() chooses either community genres or
user-genres depending on its native "Only use my genres" option. This wrapper
keeps Picard's normal behavior, ensures the community vocabulary remains
available to this plugin, and adds the matching personal vocabulary only when
at least one plugin source is configured as Community + My Genres.
"""

from __future__ import annotations

from typing import Any

from .config_access import picard_uses_folksonomy, read_config_value


_PATCH_MARKER = "_genre_hierarchy_manager_wrapper"
_ORIGINAL_MARKER = "_genre_hierarchy_manager_original"


def _personal_sources_enabled(plugin_config: Any) -> bool:
    for source in ("recording", "release", "release_group", "artist"):
        if read_config_value(plugin_config, f"source_mode_{source}", "community") == "community_and_my":
            return True
    return False


def install_user_genres_request_hook(api) -> bool:
    """Patch Picard's shared include builder. Returns False if API is unavailable."""
    try:
        from picard.item import MetadataItem
    except Exception as exc:
        api.logger.warning(
            "Genre Hierarchy Manager: personal-genre lookup unavailable; "
            "could not import Picard MetadataItem (%s)", exc
        )
        return False

    current = getattr(MetadataItem, "set_genre_inc_params", None)
    if not callable(current):
        api.logger.warning(
            "Genre Hierarchy Manager: this Picard build has no MetadataItem.set_genre_inc_params; "
            "personal-genre lookup is unavailable."
        )
        return False

    if getattr(current, _PATCH_MARKER, False):
        return True

    original = current

    def set_genre_inc_params_with_personal(inc, config=None):
        require_authentication = bool(original(inc, config))
        setting = getattr(config, "setting", None) if config is not None else None
        if setting is None:
            try:
                from picard.config import get_config
                setting = get_config().setting
            except Exception:
                return require_authentication

        if not bool(read_config_value(setting, "use_genres", True)):
            return require_authentication

        folksonomy = picard_uses_folksonomy(setting)
        # Always ensure the community vocabulary selected in Picard is present.
        # This also compensates for Picard's native "Only use my genres" setting,
        # which otherwise requests user-genres *instead of* community genres.
        inc.add("tags" if folksonomy else "genres")

        if _personal_sources_enabled(api.plugin_config):
            inc.add("user-tags" if folksonomy else "user-genres")
            require_authentication = True
        return require_authentication

    setattr(set_genre_inc_params_with_personal, _PATCH_MARKER, True)
    setattr(set_genre_inc_params_with_personal, _ORIGINAL_MARKER, original)
    MetadataItem.set_genre_inc_params = staticmethod(set_genre_inc_params_with_personal)
    api.logger.info(
        "Genre Hierarchy Manager: installed MusicBrainz personal-genre lookup hook; "
        "authenticated user-genres will be requested when a source is set to Community + My Genres."
    )
    return True


def remove_user_genres_request_hook(api=None) -> bool:
    """Restore Picard's original include builder when the plugin is disabled."""
    try:
        from picard.item import MetadataItem
    except Exception:
        return False
    current = getattr(MetadataItem, "set_genre_inc_params", None)
    if not callable(current) or not getattr(current, _PATCH_MARKER, False):
        return False
    original = getattr(current, _ORIGINAL_MARKER, None)
    if not callable(original):
        return False
    MetadataItem.set_genre_inc_params = staticmethod(original)
    if api is not None:
        api.logger.info("Genre Hierarchy Manager: restored Picard's original genre lookup include builder.")
    return True
