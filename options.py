"""Picard 3.x options page for Genre Hierarchy Manager."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
)

from picard.plugin3.api import OptionsPage

from .config_access import read_config_value


DEFAULTS = {
    "selection_mode": "cascade",
    "max_genres": 5,
    "min_genre_usage": 0,
    "source_recording": True,
    "source_release": True,
    "source_release_group": True,
    "source_artist": True,
    "artist_policy": "fallback",
    "output_order": "votes",
    "apply_picard_filter": True,
    "exclusion_rules": "",
    "write_genres_enabled": True,
    "preview_enabled": False,
    "verbose_logging": False,
}


class GenreHierarchyOptionsPage(OptionsPage):
    NAME = "genre_hierarchy_manager"
    TITLE = "Genre Hierarchy Manager"
    PARENT = "plugins"

    def __init__(self):
        super().__init__()
        root = self.layout()
        if root is None:
            root = QVBoxLayout(self)

        intro = QLabel(
            "Choose source priority, filtering and artist fallback behavior. "
            "Genre-tag replacement is OFF by default; audio files are only changed "
            "when you explicitly use Picard's Save action."
        )
        intro.setToolTip(
            "Keep Picard's 'Use genres from MusicBrainz' enabled so genre data is "
            "retrieved. The plugin replaces only the in-memory genre field; it does "
            "not save files automatically."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        strategy_box = QGroupBox("Selection strategy")
        strategy_form = QFormLayout(strategy_box)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(
            "Priority cascade — fill remaining slots from the next source",
            "cascade",
        )
        self.mode_combo.addItem(
            "Strict priority — use only the first populated source",
            "strict",
        )
        self.mode_combo.addItem(
            "Combined — merge recording, release and release-group votes",
            "combined",
        )
        strategy_form.addRow("Mode:", self.mode_combo)

        self.order_combo = QComboBox()
        self.order_combo.addItem("Vote count (highest first)", "votes")
        self.order_combo.addItem("Alphabetical (A–Z)", "alphabetical")
        strategy_form.addRow("Output order:", self.order_combo)
        self.order_help = QLabel(
            "Cascade: source priority, then votes within each source. "
            "Combined: merged votes. Ties: A–Z."
        )
        order_help = self.order_help
        order_help.setWordWrap(True)
        order_help.setToolTip(
            "Vote order sorts by count within each source while preserving source "
            "priority in cascade mode; counts from different entity levels are not "
            "compared directly. Combined mode sorts by merged vote totals. Ties are "
            "alphabetical."
        )
        order_help.setMinimumHeight(40)
        order_help.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        strategy_form.addRow(order_help)

        self.max_genres_spin = QSpinBox()
        self.max_genres_spin.setRange(0, 100)
        self.max_genres_spin.setSpecialValueText("0 (no genres)")
        strategy_form.addRow("Maximum genres:", self.max_genres_spin)

        self.min_usage_spin = QSpinBox()
        self.min_usage_spin.setRange(0, 100)
        self.min_usage_spin.setSuffix("%")
        strategy_form.addRow("Minimum usage:", self.min_usage_spin)

        self.artist_policy_combo = QComboBox()
        self.artist_policy_combo.addItem(
            "Fallback only (when lower levels yield none)",
            "fallback",
        )
        self.artist_policy_combo.addItem(
            "Fill remaining slots",
            "fill",
        )
        self.artist_policy_combo.setToolTip(
            "Fill remaining slots: after recording, release and release-group "
            "genres pass exclusions and minimum usage, artist genres can top up "
            "the list to the maximum. Fallback only: artist genres are used only "
            "when no eligible genre remains from those three lower levels. In "
            "Strict mode, only the first populated source is ever used, so artist "
            "genres are reached only when the lower sources are empty."
        )
        strategy_form.addRow("Artist genres:", self.artist_policy_combo)

        root.addWidget(strategy_box)

        sources_box = QGroupBox("Enabled genre sources — highest to lowest priority")
        sources_layout = QVBoxLayout(sources_box)
        self.source_checks = {
            "recording": QCheckBox("Recording (individual track)"),
            "release": QCheckBox("Release (specific album edition)"),
            "release_group": QCheckBox("Release group (album across editions)"),
            "artist": QCheckBox("Artist (final source; behavior set above)"),
        }
        for key in ("recording", "release", "release_group", "artist"):
            sources_layout.addWidget(self.source_checks[key])
        root.addWidget(sources_box)

        data_box = QGroupBox("MusicBrainz data and filtering")
        data_layout = QVBoxLayout(data_box)
        self.vocabulary_label = QLabel(
            "Genre vocabulary follows Picard's global setting: Options → Metadata → "
            "Genres → Use folksonomy tags as genre."
        )
        self.vocabulary_label.setWordWrap(True)
        self.picard_filter_check = QCheckBox(
            "Also apply Picard's global genre-name filter"
        )
        data_layout.addWidget(self.vocabulary_label)
        data_layout.addWidget(self.picard_filter_check)
        root.addWidget(data_box)

        exclusions_box = QGroupBox("Source-specific genre exclusions")
        exclusions_layout = QVBoxLayout(exclusions_box)
        exclusions_help = QLabel(
            "One exact, case-insensitive rule per line. Hover for syntax and examples."
        )
        exclusions_help.setWordWrap(True)
        exclusions_help.setToolTip(
            "Use 'all' or one or more comma-separated sources: recording, release, "
            "release_group, artist. Examples:\n"
            "artist: Christmas Music\n"
            "release,release_group: Soundtrack\n"
            "all: Spoken Word\n"
            "Matching ignores leading/trailing whitespace. Lines beginning with # "
            "are comments."
        )
        exclusions_layout.addWidget(exclusions_help)
        self.exclusions_edit = QPlainTextEdit()
        self.exclusions_edit.setPlaceholderText(
            "artist: Christmas Music\nrelease,release_group: Soundtrack"
        )
        # Compact help above and below gives this editor more usable height
        # without making the complete options page substantially taller.
        self.exclusions_edit.setMinimumHeight(205)
        self.exclusions_edit.document().setDocumentMargin(0.0)
        self.exclusions_edit.setStyleSheet("QPlainTextEdit { padding: 1px; }")
        self.exclusions_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        exclusions_layout.addWidget(self.exclusions_edit)
        root.addWidget(exclusions_box)

        tagging_box = QGroupBox("Genre tag output")
        tagging_layout = QVBoxLayout(tagging_box)
        self.write_genres_check = QCheckBox(
            "Write selected genres to the genre tag (replace Picard's default genre list)"
        )
        self.write_genres_check.setToolTip(
            "When enabled, the plugin replaces only the in-memory genre tag after a MusicBrainz lookup. "
            "Audio files are not changed until you explicitly save them in Picard."
        )
        tagging_help = QLabel(
            "Replaces only the in-memory genre tag; Picard's normal Save action is still required."
        )
        tagging_help.setWordWrap(True)
        tagging_help.setToolTip(
            "Off by default. When enabled, selected genres replace Picard's generated genre tag. "
            "No audio file is written automatically; review the results and use Picard's normal "
            "Save action. Other tags are left alone. Normal Picard tagging scripts still run afterwards."
        )
        tagging_layout.addWidget(self.write_genres_check)
        tagging_layout.addWidget(tagging_help)
        root.addWidget(tagging_box)

        diagnostics_box = QGroupBox("Preview diagnostics")
        diagnostics_layout = QVBoxLayout(diagnostics_box)
        self.preview_check = QCheckBox("Log genre-selection preview in the debug log")
        self.verbose_check = QCheckBox(
            "Log source counters and all three mode comparisons for each track"
        )
        diagnostics_layout.addWidget(self.preview_check)
        diagnostics_layout.addWidget(self.verbose_check)
        root.addWidget(diagnostics_box)
        root.addStretch(1)

    def load(self):
        cfg = self.api.plugin_config
        self.mode_combo.setCurrentIndex(
            max(0, self.mode_combo.findData(read_config_value(cfg, "selection_mode", DEFAULTS["selection_mode"])))
        )
        self.order_combo.setCurrentIndex(
            max(0, self.order_combo.findData(read_config_value(cfg, "output_order", DEFAULTS["output_order"])))
        )
        self.artist_policy_combo.setCurrentIndex(
            max(0, self.artist_policy_combo.findData(
                read_config_value(cfg, "artist_policy", DEFAULTS["artist_policy"])
            ))
        )
        self.max_genres_spin.setValue(int(read_config_value(cfg, "max_genres", DEFAULTS["max_genres"])))
        self.min_usage_spin.setValue(int(read_config_value(cfg, "min_genre_usage", DEFAULTS["min_genre_usage"])))
        for source, checkbox in self.source_checks.items():
            checkbox.setChecked(bool(read_config_value(cfg, f"source_{source}", DEFAULTS[f"source_{source}"])))
        global_settings = getattr(getattr(self.api, "global_config", None), "setting", None)
        folksonomy_enabled = bool(read_config_value(global_settings, "folksonomy_tags", False))
        self.vocabulary_label.setText(
            "Currently using: %s. This follows Picard's global setting under "
            "Options → Metadata → Genres; change it there to switch vocabulary."
            % ("folksonomy tags" if folksonomy_enabled else "curated MusicBrainz genres")
        )
        self.picard_filter_check.setChecked(
            bool(read_config_value(cfg, "apply_picard_filter", DEFAULTS["apply_picard_filter"]))
        )
        self.exclusions_edit.setPlainText(str(read_config_value(cfg, "exclusion_rules", DEFAULTS["exclusion_rules"])))
        self.write_genres_check.setChecked(
            bool(read_config_value(cfg, "write_genres_enabled", DEFAULTS["write_genres_enabled"]))
        )
        self.preview_check.setChecked(bool(read_config_value(cfg, "preview_enabled", DEFAULTS["preview_enabled"])))
        self.verbose_check.setChecked(bool(read_config_value(cfg, "verbose_logging", DEFAULTS["verbose_logging"])))

    def save(self):
        cfg = self.api.plugin_config
        cfg["selection_mode"] = self.mode_combo.currentData() or "cascade"
        cfg["output_order"] = self.order_combo.currentData() or "votes"
        cfg["artist_policy"] = self.artist_policy_combo.currentData() or "fallback"
        cfg["max_genres"] = self.max_genres_spin.value()
        cfg["min_genre_usage"] = self.min_usage_spin.value()
        for source, checkbox in self.source_checks.items():
            cfg[f"source_{source}"] = checkbox.isChecked()
        cfg["apply_picard_filter"] = self.picard_filter_check.isChecked()
        cfg["exclusion_rules"] = self.exclusions_edit.toPlainText()
        cfg["write_genres_enabled"] = self.write_genres_check.isChecked()
        cfg["preview_enabled"] = self.preview_check.isChecked()
        cfg["verbose_logging"] = self.verbose_check.isChecked()
