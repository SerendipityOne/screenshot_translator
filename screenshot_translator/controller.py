from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QClipboard, QCursor, QPixmap
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
from .widgets import (
    ResultWindow,
    ScreenshotOverlay,
    SelectionTranslationPopup,
    SettingsDialog,
)


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


class SelectionTranslationThread(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, source_text: str, config: ApiConfig):
        super().__init__()
        self.source_text = source_text
        self.config = config

    def run(self) -> None:
        try:
            LOGGER.info("开始后台划词翻译 chars=%d", len(self.source_text))
            if self.isInterruptionRequested():
                return
            self.succeeded.emit(request_translation(self.config, self.source_text))
        except (ConfigError, TranslationError) as exc:
            LOGGER.warning("划词翻译失败 error=%s", exc)
            self.failed.emit(str(exc))
        except Exception as exc:
            LOGGER.exception("划词翻译发生未预期异常")
            self.failed.emit(f"处理失败：{exc}")


class ScreenshotTranslatorApp(QObject):
    def __init__(
        self,
        qt_app: QApplication,
        source_entrypoint: Path | None = None,
        service_mode: bool = False,
    ):
        super().__init__()
        self.qt_app = qt_app
        self.service_mode = service_mode
        self.config_store = ConfigStore.from_standard_location()
        self.autostart_manager = AutostartManager.from_standard_location(
            source_entrypoint
        )
        self.settings = AppConfig()
        self.result_window = ResultWindow()
        self.selection_popup = SelectionTranslationPopup()
        self.tray: QSystemTrayIcon | None = None
        self.translate_action: QAction | None = None
        self.ocr_action: QAction | None = None
        self.hotkeys: GlobalHotkeys | None = None
        self.overlay: ScreenshotOverlay | None = None
        self.worker: QThread | None = None
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
            message = (
                f"{exc}；本次启动将使用默认设置，"
                "可在托盘菜单中重新保存。"
            )
            if self.service_mode:
                LOGGER.error("设置读取失败：%s", message)
                return False
            else:
                QMessageBox.warning(None, "设置读取失败", message)
            self.settings = AppConfig()
        try:
            check_tesseract_languages()
            self.hotkeys = GlobalHotkeys(
                self.settings.translate_hotkey,
                self.settings.ocr_hotkey,
                self.settings.selection_translate_hotkey,
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
        self.hotkeys.selection_translate_requested.connect(
            self.request_selection_translation
        )
        self.qt_app.aboutToQuit.connect(self.shutdown)
        LOGGER.info(
            "程序已启动 hotkeys=%s,%s,%s",
            self.settings.translate_hotkey,
            self.settings.ocr_hotkey,
            self.settings.selection_translate_hotkey,
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
            autostart_supported=self.autostart_manager.can_enable,
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
        config_saved = False
        hotkeys_applied = False
        autostart_applied = False
        try:
            # 先原子保存配置；后续运行时步骤失败时可以恢复旧配置，避免
            # 配置文件已经更新但快捷键或 systemd 服务仍是旧状态。
            self.config_store.save(config)
            config_saved = True
            if self.hotkeys is not None:
                self.hotkeys.reconfigure(
                    config.translate_hotkey,
                    config.ocr_hotkey,
                    config.selection_translate_hotkey,
                )
                hotkeys_applied = True
            if (
                autostart_enabled != old_autostart
                or (autostart_enabled and self.autostart_manager.can_enable)
            ):
                self.autostart_manager.set_enabled(autostart_enabled)
                autostart_applied = True
        except Exception as exc:
            LOGGER.warning("设置应用失败，准备恢复旧设置 error=%s", exc)
            rollback_errors: list[str] = []
            if autostart_applied:
                try:
                    self.autostart_manager.set_enabled(old_autostart)
                except Exception as rollback_exc:
                    rollback_errors.append(f"自启动：{rollback_exc}")
            if hotkeys_applied and self.hotkeys is not None:
                try:
                    self.hotkeys.reconfigure(
                        old_config.translate_hotkey,
                        old_config.ocr_hotkey,
                        old_config.selection_translate_hotkey,
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(f"快捷键：{rollback_exc}")
            if config_saved:
                try:
                    self.config_store.save(old_config)
                except Exception as rollback_exc:
                    rollback_errors.append(f"配置：{rollback_exc}")
            if rollback_errors:
                LOGGER.exception("设置保存失败且回滚不完整")
                raise ConfigError(
                    f"{exc}；恢复旧设置失败：{'；'.join(rollback_errors)}"
                ) from exc
            raise

        self.settings = config
        self._update_action_labels()
        LOGGER.info(
            "通用设置已应用 hotkeys=%s,%s,%s autostart=%s",
            config.translate_hotkey,
            config.ocr_hotkey,
            config.selection_translate_hotkey,
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

    def request_selection_translation(self) -> None:
        if self.is_busy:
            self._notify("任务进行中", "请等待当前任务完成。")
            return

        anchor = QCursor.pos()
        clipboard = QApplication.clipboard()
        if clipboard is None or not clipboard.supportsSelection():
            self.selection_popup.show_error_at(
                anchor, "当前 X11 会话不支持 PRIMARY selection。"
            )
            return
        source_text = clipboard.text(QClipboard.Mode.Selection).strip()
        if not source_text:
            self.selection_popup.show_error_at(
                anchor, "未读取到选中文本，请先用鼠标选中文字。"
            )
            return

        self.is_busy = True
        try:
            config = self.settings.api.validated()
        except ConfigError:
            config = self.open_settings(initial_tab=1, require_api=True)
            if config is None:
                self.is_busy = False
                return

        self.selection_popup.show_loading(anchor)
        self.worker = SelectionTranslationThread(source_text, config)
        self.worker.succeeded.connect(self.selection_popup.show_result)
        self.worker.failed.connect(self.selection_popup.show_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

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
        self.selection_popup.hide()
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

    def _critical(self, title: str, message: str) -> None:
        LOGGER.error("%s：%s", title, message)
        if not self.service_mode:
            QMessageBox.critical(None, title, message)
