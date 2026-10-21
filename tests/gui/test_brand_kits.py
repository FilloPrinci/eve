"""Brand kit dialog: end card previews for each output preset."""

from pytestqt.qtbot import QtBot

from easy_video_editor.appsettings import AppSettings
from easy_video_editor.core.brand import BrandKit
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.ui.settings_panel import BrandKitDialog


def test_brand_kit_dialog_previews_the_end_card(qtbot: QtBot) -> None:
    settings = AppSettings(
        brand_kits=[BrandKit(id="kit", name="Kit", game_title="V-CORE", cta_text="Play now")]
    )
    dialog = BrandKitDialog(settings, toolchain=Toolchain.discover)
    qtbot.addWidget(dialog)
    images = dialog.preview.images
    assert set(images) == {"youtube", "shorts", "instagram_feed"}

    def rendered() -> bool:
        return all(label.pixmap() is not None and not label.pixmap().isNull()
                   for label in images.values())  # fmt: skip

    qtbot.waitUntil(rendered, timeout=20_000)
    assert not dialog.preview.busy.isVisible()
    assert dialog.preview.status.text() == ""


def test_brand_kit_preview_reports_errors(qtbot: QtBot) -> None:
    def missing() -> Toolchain:
        raise RuntimeError("ffmpeg not found")

    settings = AppSettings(brand_kits=[BrandKit(id="kit", name="Kit")])
    dialog = BrandKitDialog(settings, toolchain=missing)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "ffmpeg not found" in dialog.preview.status.text(), timeout=5000)
