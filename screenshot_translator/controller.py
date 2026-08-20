from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCursor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
    QMenu,
    QStyle,
    QSystemTrayIcon,
    QWidget,
)

from .config import APP_NAME, ApiConfig, AppConfig, ConfigError, ConfigStore
from .desktop import AutostartManager
from .hotkeys import GlobalHotkeys
from .services import (
    OcrError,
    TranslationError,
    check_tesseract_languages,
    image_to_png_bytes,
    request_translation,
    run_ocr,
)
from .widgets import ResultWindow, ScreenshotOverlay, SettingsDialog


LOGGER = logging.getLogger(APP_NAME)


class ProcessingThread(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, mode: str, png_bytes: bytes, config: ApiConfig | None):
        super().__init__()
        self.mode = mode
        self.png_bytes = png_bytes
        self.config = config

    def run(self) -> None:
        try:
            LOGGER.info(
                "开始后台处理 mode=%s bytes=%d", self.mode, len(self.png_bytes)
            )
            ocr_text = run_ocr(self.png_bytes)
            if not ocr_text:
                raise OcrError("OCR 未识别到文字。")
            if self.isInterruptionRequested():
                return
            if self.mode == "translate":
                if self.config is None:
                    raise ConfigError("翻译任务缺少 API 配置。")
                result = request_translation(self.config, ocr_text)
            else:
                result = ocr_text
            self.succeeded.emit(result)
        except (ConfigError, OcrError, TranslationError) as exc:
            LOGGER.warning("处理失败 mode=%s error=%s", self.mode, exc)
            self.failed.emit(str(exc))
        except Exception as exc:
            LOGGER.exception("处理发生未预期异常 mode=%s", self.mode)
            self.failed.emit(f"处理失败：{exc}")


class ScreenshotTranslatorApp(QObject):
    def __init__(
        self,
        qt_app: QApplication,
        source_entrypoint: Path | None = None,
    ):
        super().__init__()
        self.qt_app = qt_app
        self.config_store = ConfigStore.from_standard_location()
        self.autostart_manager = AutostartManager.from_standard_location(
            source_entrypoint
        )
        self.settings = AppConfig()
        self.result_window = ResultWindow()
        self.tray: QSystemTrayIcon | None = None
        self.translate_action: QAction | None = None
        self.ocr_action: QAction | None = None
        self.hotkeys: GlobalHotkeys | None = None
        self.overlay: ScreenshotOverlay | None = None
        self.worker: ProcessingThread | None = None
        self.is_busy = False
        self.was_result_visible = False
        self.should_quit_after_work = False

    def start(self) -> bool:
        if QApplication.platformName() != "xcb":
            self._critical("平台不支持", "本程序首版仅支持 X11（Qt xcb 平台）。")
            return False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._critical(
                "系统托盘不可用",
                "请先启用 GNOME 的 ubuntu-appindicators@ubuntu.com 扩展。",
            )
            return False
        try:
            self.settings = self.config_store.load()
        except ConfigError as exc:
            QMessageBox.warning(
                None,
                "设置读取失败",
                f"{exc}\n\n本次启动将使用默认设置，"
                "可在托盘菜单中重新保存。",
            )
            self.settings = AppConfig()
        try:
            check_tesseract_languages()
            self.hotkeys = GlobalHotkeys(
                self.settings.translate_hotkey, self.settings.ocr_hotkey
            )
        except Exception as exc:
            LOGGER.exception("启动前置检查失败")
            self._critical("启动失败", str(exc))
            return False

        icon = self.qt_app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        menu = QMenu()
        self.translate_action = QAction(menu)
        self.ocr_action = QAction(menu)
        self._update_action_labels()
        settings_action = QAction("设置…", menu)
        quit_action = QAction("退出", menu)
        self.translate_action.triggered.connect(
            lambda: self.request_capture("translate")
        )
        self.ocr_action.triggered.connect(lambda: self.request_capture("ocr"))
        settings_action.triggered.connect(lambda: self.open_settings())
        quit_action.triggered.connect(self.request_quit)
        menu.addAction(self.translate_action)
        menu.addAction(self.ocr_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("截图翻译与 OCR")
        self.tray.setContextMenu(menu)
        self.tray.show()

        self.hotkeys.translate_requested.connect(
            lambda: self.request_capture("translate")
        )
        self.hotkeys.ocr_requested.connect(lambda: self.request_capture("ocr"))
        self.qt_app.aboutToQuit.connect(self.shutdown)
        LOGGER.info(
            "程序已启动 hotkeys=%s,%s",
            self.settings.translate_hotkey,
            self.settings.ocr_hotkey,
        )
        return True

    def open_settings(
        self, initial_tab: int = 0, require_api: bool = False
    ) -> ApiConfig | None:
        dialog = SettingsDialog(
            self.settings,
            self.autostart_manager.is_enabled(),
            self._apply_settings,
            require_api=require_api,
            initial_tab=initial_tab,
            parent=self.result_window,
        )
        if (
            dialog.exec() == QDialog.DialogCode.Accepted
            and dialog.saved_config is not None
        ):
            return dialog.saved_config.api
        return None

    def _apply_settings(self, config: AppConfig, autostart_enabled: bool) -> None:
        old_config = self.settings
        old_autostart = self.autostart_manager.is_enabled()
        # 配置文件最后原子落盘；前面的运行时状态失败时均恢复旧值。
        try:
            if self.hotkeys is not None:
                self.hotkeys.reconfigure(
                    config.translate_hotkey, config.ocr_hotkey
                )
            self.autostart_manager.set_enabled(autostart_enabled)
            self.config_store.save(config)
        except Exception as exc:
            LOGGER.warning("设置应用失败，准备恢复旧设置 error=%s", exc)
            rollback_errors: list[str] = []
            try:
                self.autostart_manager.set_enabled(old_autostart)
            except Exception as rollback_exc:
                rollback_errors.append(f"自启动：{rollback_exc}")
            try:
                if self.hotkeys is not None:
                    self.hotkeys.reconfigure(
                        old_config.translate_hotkey, old_config.ocr_hotkey
                    )
            except Exception as rollback_exc:
                rollback_errors.append(f"快捷键：{rollback_exc}")
            if rollback_errors:
                LOGGER.exception("设置保存失败且回滚不完整")
                raise ConfigError(
                    f"{exc}；恢复旧设置失败：{'；'.join(rollback_errors)}"
                ) from exc
            raise

        self.settings = config
        self._update_action_labels()
        LOGGER.info(
            "通用设置已应用 hotkeys=%s,%s autostart=%s",
            config.translate_hotkey,
            config.ocr_hotkey,
            autostart_enabled,
        )

    def _update_action_labels(self) -> None:
        if self.translate_action is not None:
            self.translate_action.setText(
                f"截图翻译（{self.settings.translate_hotkey}）"
            )
        if self.ocr_action is not None:
            self.ocr_action.setText(f"截图 OCR（{self.settings.ocr_hotkey}）")

    def request_capture(self, mode: str) -> None:
        if self.is_busy:
            self._notify("任务进行中", "请等待当前截图任务完成。")
            return
        config: ApiConfig | None = None
        if mode == "translate":
            try:
                config = self.settings.api.validated()
            except ConfigError:
                config = self.open_settings(initial_tab=1, require_api=True)
                if config is None:
                    return

        self.is_busy = True
        self.was_result_visible = self.result_window.isVisible()
        self.result_window.hide()
        LOGGER.info("准备截图 mode=%s", mode)
        QTimer.singleShot(150, lambda: self._show_overlay(mode, config))

    def _show_overlay(self, mode: str, config: ApiConfig | None) -> None:
        screen = QApplication.screenAt(QCursor.pos())
        if screen is None:
            self._capture_failed("无法确定鼠标所在显示器。")
            return
        screenshot = screen.grabWindow(0)
        if screenshot.isNull():
            self._capture_failed("无法读取屏幕内容，请确认当前会话为 X11。")
            return
        self.overlay = ScreenshotOverlay(screenshot, screen.geometry())
        self.overlay.selected.connect(
            lambda pixmap: self._capture_selected(mode, config, pixmap)
        )
        self.overlay.canceled.connect(self._capture_canceled)
        self.overlay.show_overlay()

    def _capture_selected(
        self, mode: str, config: ApiConfig | None, pixmap: QPixmap
    ) -> None:
        if self.overlay is not None:
            self.overlay.deleteLater()
            self.overlay = None
        try:
            png_bytes = image_to_png_bytes(pixmap)
        except OcrError as exc:
            self._capture_failed(str(exc))
            return

        self.result_window.show_capture(mode, pixmap)
        self.worker = ProcessingThread(mode, png_bytes, config)
        self.worker.succeeded.connect(self.result_window.show_result)
        self.worker.failed.connect(self.result_window.show_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _capture_canceled(self) -> None:
        if self.overlay is not None:
            self.overlay.deleteLater()
            self.overlay = None
        self.is_busy = False
        if self.was_result_visible:
            self.result_window.show()
        LOGGER.info("截图已取消")

    def _capture_failed(self, message: str) -> None:
        self.is_busy = False
        if self.was_result_visible:
            self.result_window.show()
        QMessageBox.warning(None, "截图失败", message)

    def _worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        self.is_busy = False
        if worker is not None:
            worker.deleteLater()
        LOGGER.info("后台任务结束")
        if self.should_quit_after_work:
            QTimer.singleShot(0, self.qt_app.quit)

    def request_quit(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.should_quit_after_work = True
            self.worker.requestInterruption()
            self._notify("等待任务结束", "当前任务结束后程序将退出。")
            return
        self.qt_app.quit()

    def shutdown(self) -> None:
        if self.hotkeys is not None:
            self.hotkeys.close()
            self.hotkeys = None
        if self.tray is not None:
            self.tray.hide()
        LOGGER.info("程序已退出")

    def _notify(self, title: str, message: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(
                title, message, QSystemTrayIcon.MessageIcon.Information, 3000
            )

    @staticmethod
    def _critical(title: str, message: str) -> None:
        QMessageBox.critical(None, title, message)
