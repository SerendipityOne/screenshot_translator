"""兼容启动入口：业务实现位于 screenshot_translator package。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from screenshot_translator.config import (
    APP_NAME,
    API_TIMEOUT_SECONDS,
    AUTOSTART_FILENAME,
    DEFAULT_OCR_HOTKEY,
    DEFAULT_SELECTION_TRANSLATE_HOTKEY,
    DEFAULT_TRANSLATE_HOTKEY,
    MAX_API_RESPONSE_BYTES,
    MAX_SOURCE_CHARACTERS,
    OCR_LANGUAGES,
    OCR_LANGUAGE_ARGUMENT,
    OCR_TIMEOUT_SECONDS,
    LOGGER,
    ApiConfig,
    AppConfig,
    ConfigError,
    ConfigStore,
    HotkeySpec,
    parse_hotkey,
    write_private_atomic,
)
from screenshot_translator.controller import (
    ProcessingThread,
    ScreenshotTranslatorApp,
    SelectionTranslationThread,
)
from screenshot_translator.desktop import (
    AutostartManager as _AutostartManager,
    quote_desktop_exec_argument,
)
from screenshot_translator.hotkeys import GlobalHotkeys
from screenshot_translator.services import (
    OcrError,
    TranslationError,
    build_translation_payload,
    check_tesseract_languages,
    image_to_png_bytes,
    parse_chat_completion,
    request_translation,
    run_ocr,
)
from screenshot_translator.widgets import (
    ResultWindow,
    ScaledImageLabel,
    ScreenshotOverlay,
    SelectionTranslationPopup,
    SettingsDialog,
)


class AutostartManager(_AutostartManager):
    """保持旧的 app.AutostartManager 工厂路径并固定源码入口为 app.py。"""

    @classmethod
    def from_standard_location(cls) -> "AutostartManager":
        config_root = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.GenericConfigLocation
            )
        )
        return super().from_standard_location(
            Path(__file__).resolve(), config_root=config_root
        )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setOrganizationName("Local")
    qt_app.setQuitOnLastWindowClosed(False)
    controller = ScreenshotTranslatorApp(
        qt_app,
        source_entrypoint=Path(__file__).resolve(),
    )
    if not controller.start():
        return 1
    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
