"""Compatibility helpers for Picard 3 profile-aware configuration sections."""

from __future__ import annotations

from typing import Any, Mapping


CONFIG_SCHEMA_VERSION = 2
CONFIG_SCHEMA_KEY = "_genre_hierarchy_config_schema"
SOURCE_NAMES = ("recording", "release", "release_group", "artist")


def read_config_value(section: Any, key: str, default: Any) -> Any:
    try:
        value = section[key]
    except (KeyError, IndexError, TypeError, AttributeError):
        return default
    return default if value is None else value


def initialize_plugin_config(section: Any, defaults: Mapping[str, Any]) -> bool:
    """Register plugin defaults and migrate the 0.6 boolean source controls.

    Schema 0 is the broken early settings page and gets one safe-default reset.
    Schema 1 is the working 0.6.0 configuration; its source booleans migrate to
    the new tri-state modes without losing any other user settings.
    """
    for key, default in defaults.items():
        section.register_option(key, default)
    section.register_option(CONFIG_SCHEMA_KEY, 0)

    try:
        current_version = int(read_config_value(section, CONFIG_SCHEMA_KEY, 0))
    except (TypeError, ValueError, OverflowError):
        current_version = 0

    if current_version < 1:
        for key, default in defaults.items():
            section[key] = default
        section[CONFIG_SCHEMA_KEY] = CONFIG_SCHEMA_VERSION
        return True

    if current_version < 2:
        for source in SOURCE_NAMES:
            old_value = read_config_value(section, f"source_{source}", True)
            section[f"source_mode_{source}"] = "community" if bool(old_value) else "disabled"
        section[CONFIG_SCHEMA_KEY] = CONFIG_SCHEMA_VERSION

    return False


def picard_uses_folksonomy(global_settings: Any) -> bool:
    return bool(read_config_value(global_settings, "folksonomy_tags", False))
