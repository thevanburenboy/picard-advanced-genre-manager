"""Compatibility helpers for Picard plugin configuration sections.

Picard 3.x exposes profile-aware configuration sections that support mapping
access but may not implement dict.get().  All plugin setting reads therefore
use indexed access through this module.
"""

from __future__ import annotations

from typing import Any, Mapping


CONFIG_SCHEMA_VERSION = 1
CONFIG_SCHEMA_KEY = "_genre_hierarchy_config_schema"


def read_config_value(section: Any, key: str, default: Any) -> Any:
    """Read a registered setting through Picard's mapping interface."""
    try:
        value = section[key]
    except (KeyError, IndexError, TypeError, AttributeError):
        return default
    return default if value is None else value


def initialize_plugin_config(section: Any, defaults: Mapping[str, Any]) -> bool:
    """Register defaults and repair settings saved by the broken 0.4.0 UI.

    0.4.0's options page failed during load() on Picard's ProfileConfigSection.
    If the user then clicked "Make It Happen!", Qt's untouched widget defaults
    (0 genres, all sources disabled) could be written back.  The first run of
    this corrected release resets the plugin settings once, then records a
    schema marker so subsequent launches preserve user choices.

    Returns True when a one-time reset was performed.
    """
    for key, default in defaults.items():
        section.register_option(key, default)
    section.register_option(CONFIG_SCHEMA_KEY, 0)

    current_version = read_config_value(section, CONFIG_SCHEMA_KEY, 0)
    try:
        current_version = int(current_version)
    except (TypeError, ValueError, OverflowError):
        current_version = 0

    if current_version < CONFIG_SCHEMA_VERSION:
        for key, default in defaults.items():
            section[key] = default
        section[CONFIG_SCHEMA_KEY] = CONFIG_SCHEMA_VERSION
        return True

    return False


def picard_uses_folksonomy(global_settings: Any) -> bool:
    """Return Picard's own global 'folksonomy tags as genre' setting.

    This deliberately keeps the plugin from storing a duplicate vocabulary
    switch that could disagree with Picard's Metadata → Genres option.
    """
    return bool(read_config_value(global_settings, "folksonomy_tags", False))
