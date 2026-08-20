from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import ApiConfig, AppConfig, ConfigError


class ScaledImageLabel(QLabel):
    def __init__(self) -> None:
        super().__init__("等待截图")
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background: #202124; color: #d0d0d0;")

    def set_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self._source.isNull() or self.width() < 1 or self.height() < 1:
            return
        self.setPixmap(
            self._source.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class ResultWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("截图翻译 / OCR")
        self.resize(1200, 720)

        self.image_label = ScaledImageLabel()
        self.text_output = QPlainTextEdit()
        self.text_output.setReadOnly(True)
        self.text_output.setPlaceholderText("识别结果将在这里显示")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.image_label)
        splitter.addWidget(self.text_output)
        splitter.setSizes([600, 600])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

    def show_capture(self, mode: str, pixmap: QPixmap) -> None:
        title = "截图翻译" if mode == "translate" else "截图 OCR"
        self.setWindowTitle(title)
        self.image_label.set_source(pixmap)
        self.text_output.setPlainText("正在识别…")
        self.show()
        self.raise_()
        self.activateWindow()

    def show_result(self, text: str) -> None:
        self.text_output.setPlainText(text)

    def show_error(self, message: str) -> None:
        self.text_output.setPlainText(f"处理失败\n\n{message}")

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        autostart_enabled: bool,
        apply_settings: Callable[[AppConfig, bool], None],
        require_api: bool = False,
        initial_tab: int = 0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.apply_settings = apply_settings
        self.require_api = require_api
        self.saved_config: AppConfig | None = None
        self.setWindowTitle("设置")
        self.setMinimumSize(620, 360)

        self.autostart = QCheckBox("登录 GNOME 后自动启动（仅 X11）")
        self.autostart.setChecked(autostart_enabled)
        self.translate_hotkey = QKeySequenceEdit()
        self.translate_hotkey.setMaximumSequenceLength(1)
        self.translate_hotkey.setKeySequence(QKeySequence(config.translate_hotkey))
        self.ocr_hotkey = QKeySequenceEdit()
        self.ocr_hotkey.setMaximumSequenceLength(1)
        self.ocr_hotkey.setKeySequence(QKeySequence(config.ocr_hotkey))

        general_notice = QLabel(
            "快捷键必须包含 Ctrl、Alt 或 Super；Shift 可附加，"
            "主键支持字母、数字和 F1-F12。"
        )
        general_notice.setWordWrap(True)
        general_form = QFormLayout()
        general_form.addRow("截图翻译快捷键：", self.translate_hotkey)
        general_form.addRow("截图 OCR 快捷键：", self.ocr_hotkey)
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_layout.addWidget(self.autostart)
        general_layout.addLayout(general_form)
        general_layout.addWidget(general_notice)
        general_layout.addStretch()

        self.api_url = QLineEdit(config.api.api_url)
        self.api_url.setPlaceholderText("https://example.com/v1/chat/completions")
        self.model = QLineEdit(config.api.model)
        self.model.setPlaceholderText("模型名称")
        self.api_key = QLineEdit(config.api.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("API Key")

        api_notice = QLabel(
            "API Key 将以 0600 权限明文保存在当前用户配置目录；"
            "截图原图不会上传，只有 OCR 文本会发送到此接口。"
        )
        api_notice.setWordWrap(True)

        api_form = QFormLayout()
        api_form.addRow("完整 API URL：", self.api_url)
        api_form.addRow("模型：", self.model)
        api_form.addRow("API Key：", self.api_key)
        api_tab = QWidget()
        api_layout = QVBoxLayout(api_tab)
        api_layout.addLayout(api_form)
        api_layout.addWidget(api_notice)
        api_layout.addStretch()

        self.tabs = QTabWidget()
        self.tabs.addTab(general_tab, "通用设置")
        self.tabs.addTab(api_tab, "翻译接口设置")
        self.tabs.setCurrentIndex(1 if initial_tab == 1 else 0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            config = AppConfig(
                api=ApiConfig(
                    api_url=self.api_url.text(),
                    model=self.model.text(),
                    api_key=self.api_key.text(),
                ),
                translate_hotkey=self.translate_hotkey.keySequence().toString(
                    QKeySequence.SequenceFormat.PortableText
                ),
                ocr_hotkey=self.ocr_hotkey.keySequence().toString(
                    QKeySequence.SequenceFormat.PortableText
                ),
            ).validated(require_api=self.require_api)
            self.apply_settings(config, self.autostart.isChecked())
        except (ConfigError, RuntimeError) as exc:
            QMessageBox.warning(self, "配置无效", str(exc))
            return
        self.saved_config = config
        self.accept()


class ScreenshotOverlay(QWidget):
    selected = Signal(QPixmap)
    canceled = Signal()

    def __init__(self, screenshot: QPixmap, screen_geometry: QRect):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._screenshot = screenshot
        self._origin: QPoint | None = None
        self._selection = QRect()
        self.setGeometry(screen_geometry)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def show_overlay(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self._screenshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 105))

        if not self._selection.isEmpty():
            cropped = self._crop(self._selection)
            painter.drawPixmap(self._selection, cropped)
            painter.setPen(QPen(QColor("#4da3ff"), 2))
            painter.drawRect(self._selection)

        help_rect = QRect(20, 20, min(480, self.width() - 40), 44)
        painter.fillRect(help_rect, QColor(0, 0, 0, 180))
        painter.setPen(QColor("white"))
        painter.drawText(
            help_rect.adjusted(12, 0, -12, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "拖动鼠标选择区域；按 Esc 或右键取消",
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._selection = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._origin is not None:
            self._selection = QRect(
                self._origin, event.position().toPoint()
            ).normalized().intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        selection = self._selection
        self._origin = None
        if selection.width() < 5 or selection.height() < 5:
            self._selection = QRect()
            self.update()
            return
        cropped = self._crop(selection)
        self.selected.emit(cropped)
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)

    def _crop(self, logical_rect: QRect) -> QPixmap:
        scale_x = self._screenshot.width() / max(1, self.width())
        scale_y = self._screenshot.height() / max(1, self.height())
        pixel_rect = QRect(
            round(logical_rect.x() * scale_x),
            round(logical_rect.y() * scale_y),
            round(logical_rect.width() * scale_x),
            round(logical_rect.height() * scale_y),
        ).intersected(self._screenshot.rect())
        cropped = self._screenshot.copy(pixel_rect)
        cropped.setDevicePixelRatio(self._screenshot.devicePixelRatio())
        return cropped

    def _cancel(self) -> None:
        self.canceled.emit()
        self.close()
