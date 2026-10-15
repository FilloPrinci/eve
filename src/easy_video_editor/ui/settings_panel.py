"""Project settings (SPEC §9), brand kits and application settings."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor import appsettings
from easy_video_editor.ai.agent import ANTHROPIC_PRICES
from easy_video_editor.ai.runner import AiProviderKind
from easy_video_editor.ai.secrets import get_api_key, set_api_key
from easy_video_editor.appsettings import AppSettings
from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.commands import Command, SetEndCard, SetMusic, UpdateSettings
from easy_video_editor.core.errors import EveError
from easy_video_editor.core.ids import new_id
from easy_video_editor.core.model import MusicCue, TextLook, TransitionKind
from easy_video_editor.ui.controller import ProjectController
from easy_video_editor.ui.inspector import seconds_box

US = 1_000_000
MOODS = ["adrenaline", "cinematic", "epic", "playful", "chill"]


class ProjectSettingsPanel(QScrollArea):
    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._loading = False
        self.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)

        # Output
        output = QGroupBox(self.tr("Output"))
        self.presets_box = QVBoxLayout(output)
        self.preset_checks: dict[str, QCheckBox] = {}
        layout.addWidget(output)

        # Edit
        edit = QGroupBox(self.tr("Edit"))
        form = QFormLayout(edit)
        self.target = seconds_box(maximum=600.0, decimals=1, step=5.0)
        self.mood = QComboBox()
        self.mood.setEditable(True)
        self.mood.addItems(MOODS)
        self.transition = QComboBox()
        self.transitions = list(TransitionKind)
        self.transition.addItems([k.value.replace("_", " ") for k in self.transitions])
        self.text_look = QComboBox()
        self.looks = list(TextLook)
        self.text_look.addItems([self.tr("A — classic"), self.tr("B — band"), self.tr("C — bold")])
        self.tolerance = seconds_box(maximum=30.0, decimals=1)
        self.shot_min = seconds_box(maximum=30.0, decimals=1)
        self.shot_max = seconds_box(maximum=60.0, decimals=1)
        self.loudness = QDoubleSpinBox()
        self.loudness.setRange(-40.0, -5.0)
        self.loudness.setSuffix(" LUFS")
        self.game_audio = QDoubleSpinBox()
        self.game_audio.setRange(-60.0, 12.0)
        self.game_audio.setSuffix(" dB")
        self.brand = QComboBox()
        manage = QPushButton(self.tr("Brand kits…"))
        manage.clicked.connect(self.manage_brand_kits)
        brand_row = QHBoxLayout()
        brand_row.addWidget(self.brand, 1)
        brand_row.addWidget(manage)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText(self.tr("Optional instructions for the AI"))
        for name, widget in [
            (self.tr("Target duration"), self.target),
            (self.tr("Mood / pace"), self.mood),
            (self.tr("Default transition"), self.transition),
            (self.tr("Text look"), self.text_look),
            (self.tr("Highlight tolerance"), self.tolerance),
            (self.tr("Shortest shot"), self.shot_min),
            (self.tr("Longest shot"), self.shot_max),
            (self.tr("Loudness target"), self.loudness),
            (self.tr("Game audio level"), self.game_audio),
        ]:
            form.addRow(name, widget)
        form.addRow(self.tr("Brand kit"), brand_row)
        form.addRow(self.tr("AI prompt"), self.prompt)
        layout.addWidget(edit)

        # Current timeline
        timeline = QGroupBox(self.tr("Current edit timeline"))
        form = QFormLayout(timeline)
        self.end_card = QCheckBox(self.tr("Show end card"))
        self.end_card_length = seconds_box(maximum=15.0, decimals=1)
        self.cta = QLineEdit()
        self.cta.setPlaceholderText(self.tr("e.g. Wishlist now on Steam"))
        self.music = QLabel(self.tr("No music"))
        self.music.setWordWrap(True)
        add_music = QPushButton(self.tr("Choose music…"))
        add_music.clicked.connect(self.choose_music)
        clear_music = QPushButton(self.tr("No music"))
        clear_music.clicked.connect(
            lambda: self._timeline_command(lambda tl: SetMusic(timeline_id=tl, cues=[]))
        )
        music_row = QHBoxLayout()
        music_row.addWidget(add_music)
        music_row.addWidget(clear_music)
        form.addRow("", self.end_card)
        form.addRow(self.tr("End card length"), self.end_card_length)
        form.addRow(self.tr("Call to action"), self.cta)
        form.addRow(self.tr("Music"), self.music)
        form.addRow("", music_row)
        layout.addWidget(timeline)
        layout.addStretch(1)
        self.setWidget(body)

        def setting(name: str, value: Callable[[], Any]) -> Callable[..., None]:
            def handler(*_: object) -> None:
                if not self._loading:
                    controller.execute(UpdateSettings(changes={name: value()}))

            return handler

        self.target.valueChanged.connect(
            setting("target_duration_us", lambda: round(self.target.value() * US))
        )
        self.mood.currentTextChanged.connect(setting("mood", self.mood.currentText))
        self.transition.currentIndexChanged.connect(
            setting("transition_style", lambda: self.transitions[self.transition.currentIndex()])
        )
        self.text_look.currentIndexChanged.connect(
            setting("text_look", lambda: self.looks[self.text_look.currentIndex()])
        )
        self.tolerance.valueChanged.connect(
            setting("yellow_tolerance_us", lambda: round(self.tolerance.value() * US))
        )
        self.shot_min.valueChanged.connect(
            setting("shot_length_min_us", lambda: round(self.shot_min.value() * US))
        )
        self.shot_max.valueChanged.connect(
            setting("shot_length_max_us", lambda: round(self.shot_max.value() * US))
        )
        self.loudness.valueChanged.connect(setting("loudness_target_lufs", self.loudness.value))
        self.game_audio.valueChanged.connect(setting("game_audio_db", self.game_audio.value))
        self.brand.currentIndexChanged.connect(
            setting("brand_kit_id", lambda: self.brand.currentData())
        )
        self.prompt.editingFinished.connect(setting("prompt", self.prompt.text))
        for signal in (
            self.end_card.toggled,
            self.end_card_length.valueChanged,
            self.cta.editingFinished,
        ):
            signal.connect(self._end_card_changed)

        controller.projectChanged.connect(self.refresh)
        controller.currentTimelineChanged.connect(self.refresh)

    def _timeline_command(self, make: Callable[[str], Command]) -> None:
        timeline = self.controller.current_timeline
        if timeline is None:
            self.controller.error.emit(self.tr("Create an edit timeline first."))
            return
        self.controller.execute(make(timeline.id))

    def _end_card_changed(self, *_: object) -> None:
        timeline = self.controller.current_timeline
        if self._loading or timeline is None:
            return
        card = timeline.end_card.model_copy(
            update={
                "enabled": self.end_card.isChecked(),
                "duration_us": max(round(self.end_card_length.value() * US), 100_000),
                "cta_text": self.cta.text(),
            }
        )
        self.controller.execute(SetEndCard(timeline_id=timeline.id, end_card=card))

    def choose_music(self) -> None:
        start_dir = ""
        kit = self.controller.brand_kit()
        if kit and kit.music_dir:
            start_dir = kit.music_dir
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose music"),
            start_dir,
            self.tr("Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac)"),
        )
        if path:
            cue = MusicCue(
                id=new_id("music"),
                path=str(Path(path).resolve()),
                fade_in_us=US // 2,
                fade_out_us=2 * US,
            )
            self._timeline_command(lambda tl: SetMusic(timeline_id=tl, cues=[cue]))

    def _toggle_preset(self, preset_id: str, enabled: bool) -> None:
        project = self.controller.project
        if self._loading or project is None:
            return
        ids = [i for i in project.settings.enabled_preset_ids if i != preset_id]
        if enabled:
            ids.append(preset_id)
        self.controller.execute(UpdateSettings(changes={"enabled_preset_ids": ids}))

    def manage_brand_kits(self) -> None:
        dialog = BrandKitDialog(self.controller.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.controller.update_settings(dialog.settings)

    def refresh(self) -> None:
        project = self.controller.project
        self.setEnabled(project is not None)
        if project is None:
            return
        settings = project.settings
        self._loading = True
        for check in self.preset_checks.values():
            self.presets_box.removeWidget(check)
            check.deleteLater()
        self.preset_checks.clear()
        for preset in settings.output_presets:
            limit = f", ≤ {preset.max_duration_us / US:g} s" if preset.max_duration_us else ""
            check = QCheckBox(
                f"{preset.name} ({preset.width}×{preset.height}, {preset.aspect}{limit})"
            )
            check.setChecked(preset.id in settings.enabled_preset_ids)
            check.toggled.connect(lambda on, pid=preset.id: self._toggle_preset(pid, on))
            self.presets_box.addWidget(check)
            self.preset_checks[preset.id] = check
        self.target.setValue(settings.target_duration_us / US)
        self.mood.setCurrentText(settings.mood)
        self.transition.setCurrentIndex(self.transitions.index(settings.transition_style))
        self.text_look.setCurrentIndex(self.looks.index(settings.text_look))
        self.tolerance.setValue(settings.yellow_tolerance_us / US)
        self.shot_min.setValue(settings.shot_length_min_us / US)
        self.shot_max.setValue(settings.shot_length_max_us / US)
        self.loudness.setValue(settings.loudness_target_lufs)
        self.game_audio.setValue(settings.game_audio_db)
        self.brand.clear()
        self.brand.addItem(self.tr("None"), None)
        for kit in self.controller.settings.brand_kits:
            self.brand.addItem(kit.name, kit.id)
        self.brand.setCurrentIndex(max(self.brand.findData(settings.brand_kit_id), 0))
        self.prompt.setText(settings.prompt)
        timeline = self.controller.current_timeline
        self.end_card.setEnabled(timeline is not None)
        if timeline is not None:
            self.end_card.setChecked(timeline.end_card.enabled)
            self.end_card_length.setValue(timeline.end_card.duration_us / US)
            self.cta.setText(timeline.end_card.cta_text)
            names = [Path(cue.path).name for cue in timeline.music]
            self.music.setText(", ".join(names) or self.tr("No music"))
        self._loading = False


# --- Brand kits ------------------------------------------------------------------------------


def rasterize_logo(source: Path, kit_id: str) -> Path:
    """Copy a logo into the config folder; SVG files are rasterised to PNG (the renderer reads
    PNG/JPEG only)."""
    folder = appsettings.config_dir() / "brand_kits" / kit_id
    folder.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() != ".svg":
        target = folder / f"logo{source.suffix.lower()}"
        shutil.copyfile(source, target)
        return target
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(source))
    size = renderer.defaultSize()
    if size.isEmpty():
        size = QSize(1024, 1024)
    scale = 1024 / max(size.width(), size.height())
    image = QImage(
        round(size.width() * scale), round(size.height() * scale), QImage.Format.Format_ARGB32
    )
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    target = folder / "logo.png"
    image.save(str(target))
    return target


class ColorButton(QPushButton):
    def __init__(self, color: str) -> None:
        super().__init__()
        self.color = color
        self.clicked.connect(self.pick)
        self._show()

    def _show(self) -> None:
        self.setText(self.color)
        foreground = "#000" if QColor(self.color).lightness() > 128 else "#fff"
        self.setStyleSheet(f"background-color: {self.color}; color: {foreground}")

    def pick(self) -> None:
        color = QColorDialog.getColor(QColor(self.color), self)
        if color.isValid():
            self.color = color.name().upper()
            self._show()


class BrandKitDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Brand kits"))
        self.settings = settings.model_copy(deep=True)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_kit)
        add = QPushButton(self.tr("Add"))
        add.clicked.connect(self.add_kit)
        remove = QPushButton(self.tr("Remove"))
        remove.clicked.connect(self.remove_kit)
        left = QVBoxLayout()
        left.addWidget(self.list)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        left.addLayout(buttons)

        self.name = QLineEdit()
        self.title_ = QLineEdit()
        self.cta = QLineEdit()
        self.logo = QLabel(self.tr("No logo"))
        choose_logo = QPushButton(self.tr("Choose logo…"))
        choose_logo.clicked.connect(self.choose_logo)
        self.primary = ColorButton("#FFD400")
        self.text_color = ColorButton("#FFFFFF")
        self.background = ColorButton("#000000")
        self.font_path = QLineEdit()
        self.font_path.setPlaceholderText(self.tr("Bundled font (Anton)"))
        self.music_dir = QLineEdit()
        form = QFormLayout()
        for name, widget in [
            (self.tr("Name"), self.name),
            (self.tr("Game title"), self.title_),
            (self.tr("Call to action"), self.cta),
            (self.tr("Logo"), self.logo),
            ("", choose_logo),
            (self.tr("Accent colour"), self.primary),
            (self.tr("Text colour"), self.text_color),
            (self.tr("Background"), self.background),
            (self.tr("Font file"), self.font_path),
            (self.tr("Music folder"), self.music_dir),
        ]:
            form.addRow(name, widget)
        self.editor = QWidget()
        self.editor.setLayout(form)
        for widget in (self.name, self.title_, self.cta, self.font_path, self.music_dir):
            widget.editingFinished.connect(self._store)
        for button in (self.primary, self.text_color, self.background):
            button.clicked.connect(self._store)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        top = QHBoxLayout()
        top.addLayout(left, 1)
        top.addWidget(self.editor, 2)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(box)
        self._logo_path: str | None = None
        self._reload_list()

    def _reload_list(self, select: int = 0) -> None:
        self.list.clear()
        for kit in self.settings.brand_kits:
            self.list.addItem(kit.name)
        self.editor.setEnabled(bool(self.settings.brand_kits))
        if self.settings.brand_kits:
            self.list.setCurrentRow(min(select, len(self.settings.brand_kits) - 1))

    def _show_kit(self, row: int) -> None:
        if not 0 <= row < len(self.settings.brand_kits):
            return
        kit = self.settings.brand_kits[row]
        self.name.setText(kit.name)
        self.title_.setText(kit.game_title)
        self.cta.setText(kit.cta_text)
        self._logo_path = kit.logo_path
        self.logo.setText(Path(kit.logo_path).name if kit.logo_path else self.tr("No logo"))
        for button, color in (
            (self.primary, kit.primary_color),
            (self.text_color, kit.text_color),
            (self.background, kit.background_color),
        ):
            button.color = color
            button._show()
        self.font_path.setText(kit.font_path or "")
        self.music_dir.setText(kit.music_dir or "")

    def _store(self, *_: object) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self.settings.brand_kits):
            return
        kit = self.settings.brand_kits[row]
        self.settings.brand_kits[row] = BrandKit(
            id=kit.id,
            name=self.name.text() or kit.name,
            logo_path=self._logo_path,
            game_title=self.title_.text(),
            cta_text=self.cta.text(),
            primary_color=self.primary.color,
            text_color=self.text_color.color,
            background_color=self.background.color,
            font_path=self.font_path.text() or None,
            music_dir=self.music_dir.text() or None,
        )
        self.list.item(row).setText(self.settings.brand_kits[row].name)

    def add_kit(self) -> None:
        self.settings.brand_kits.append(BrandKit(id=new_id("brand"), name=self.tr("New brand kit")))
        self._reload_list(len(self.settings.brand_kits) - 1)

    def remove_kit(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.settings.brand_kits):
            del self.settings.brand_kits[row]
            self._reload_list(row)

    def choose_logo(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self.settings.brand_kits):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Choose logo"), "", self.tr("Images (*.png *.jpg *.jpeg *.svg)")
        )
        if path:
            target = rasterize_logo(Path(path), self.settings.brand_kits[row].id)
            self._logo_path = str(target)
            self.logo.setText(target.name)
            self._store()

    def _accept(self) -> None:
        self._store()
        self.accept()


class AppSettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Preferences"))
        self.settings = settings.model_copy(deep=True)
        self.ffmpeg = QLineEdit(settings.ffmpeg_path or "")
        self.ffprobe = QLineEdit(settings.ffprobe_path or "")
        self.libmpv = QLineEdit(settings.libmpv_path or "")
        for edit in (self.ffmpeg, self.ffprobe, self.libmpv):
            edit.setPlaceholderText(self.tr("Detect automatically"))
        self.proxy = QSpinBox()
        self.proxy.setRange(144, 1080)
        self.proxy.setSingleStep(36)
        self.proxy.setSuffix(" p")
        self.proxy.setValue(settings.proxy_height)
        self.cache = QSpinBox()
        self.cache.setRange(0, 1_000_000)
        self.cache.setSuffix(" MB")
        self.cache.setValue(settings.cache_limit_mb)
        form = QFormLayout()
        form.addRow(self.tr("ffmpeg"), self.ffmpeg)
        form.addRow(self.tr("ffprobe"), self.ffprobe)
        form.addRow(self.tr("libmpv"), self.libmpv)
        form.addRow(self.tr("Proxy height"), self.proxy)
        form.addRow(self.tr("Cache limit"), self.cache)
        note = QLabel(self.tr("Changes to libmpv take effect after restarting EVE."))
        note.setWordWrap(True)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self._ai_group(settings))
        layout.addWidget(box)

    def _ai_group(self, settings: AppSettings) -> QGroupBox:
        ai = settings.ai
        self.ai_provider = QComboBox()
        self.ai_provider.addItem(
            self.tr("Claude Code (your Claude plan, no API key)"), AiProviderKind.CLAUDE_CODE
        )
        self.ai_provider.addItem(self.tr("Anthropic API (API key)"), AiProviderKind.ANTHROPIC)
        self.ai_provider.setCurrentIndex(max(0, self.ai_provider.findData(ai.provider)))
        self.claude_path = QLineEdit(ai.claude_path or "")
        self.claude_path.setPlaceholderText(self.tr("Detect automatically"))
        self.claude_model = QComboBox()
        self.claude_model.setEditable(True)
        self.claude_model.addItem(self.tr("Plan default"), "")
        for alias in ("sonnet", "opus", "haiku"):
            self.claude_model.addItem(alias, alias)
        index = self.claude_model.findData(ai.claude_model)
        if index >= 0:
            self.claude_model.setCurrentIndex(index)
        else:
            self.claude_model.setEditText(ai.claude_model)
        self.anthropic_model = QComboBox()
        self.anthropic_model.setEditable(True)
        self.anthropic_model.addItems(list(ANTHROPIC_PRICES))
        self.anthropic_model.setCurrentText(ai.anthropic_model)
        self.effort = QComboBox()
        self.effort.addItems(["low", "medium", "high"])
        self.effort.setCurrentText(ai.effort)
        self.frame_budget = QSpinBox()
        self.frame_budget.setRange(0, 200)
        self.frame_budget.setValue(ai.frame_budget)
        self.frame_budget.setToolTip(
            self.tr("How many video frames the AI may look at per run (more frames, more usage).")
        )
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.clear_key = QCheckBox(self.tr("Remove the stored key"))
        try:
            stored = bool(get_api_key("anthropic"))
        except EveError:
            stored = False
            self.api_key.setEnabled(False)
            self.api_key.setPlaceholderText(self.tr("No system keyring available"))
        else:
            self.api_key.setPlaceholderText(
                self.tr("A key is stored; type to replace it") if stored else self.tr("Not set")
            )
        self.clear_key.setVisible(stored)
        form = QFormLayout()
        form.addRow(self.tr("Provider"), self.ai_provider)
        form.addRow(self.tr("Claude Code program"), self.claude_path)
        form.addRow(self.tr("Claude Code model"), self.claude_model)
        form.addRow(self.tr("API model"), self.anthropic_model)
        form.addRow(self.tr("API effort"), self.effort)
        form.addRow(self.tr("API key"), self.api_key)
        form.addRow("", self.clear_key)
        form.addRow(self.tr("Frame budget"), self.frame_budget)
        help_text = QLabel(
            self.tr(
                "Claude Code uses your Claude subscription: install it, run “claude” once in a "
                "terminal and log in (see Help → System check). The API key is kept in the system "
                "keyring, never in files."
            )
        )
        help_text.setWordWrap(True)
        group = QGroupBox(self.tr("AI mode"))
        layout = QVBoxLayout(group)
        layout.addLayout(form)
        layout.addWidget(help_text)
        return group

    def _accept(self) -> None:
        self.settings.ffmpeg_path = self.ffmpeg.text().strip() or None
        self.settings.ffprobe_path = self.ffprobe.text().strip() or None
        self.settings.libmpv_path = self.libmpv.text().strip() or None
        self.settings.proxy_height = self.proxy.value()
        self.settings.cache_limit_mb = self.cache.value()
        ai = self.settings.ai
        ai.provider = AiProviderKind(self.ai_provider.currentData())
        ai.claude_path = self.claude_path.text().strip() or None
        model = self.claude_model.currentText().strip()
        ai.claude_model = "" if model == self.claude_model.itemText(0) else model
        ai.anthropic_model = self.anthropic_model.currentText().strip() or ai.anthropic_model
        ai.effort = self.effort.currentText()  # type: ignore[assignment]
        ai.frame_budget = self.frame_budget.value()
        key = self.api_key.text().strip()
        if key or self.clear_key.isChecked():
            try:
                set_api_key("anthropic", key)
            except EveError as error:
                QMessageBox.warning(self, self.tr("EVE"), str(error))
                return
        self.accept()
