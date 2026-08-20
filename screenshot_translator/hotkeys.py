from __future__ import annotations

from PySide6.QtCore import QSocketNotifier, QObject, Signal
from Xlib import X, XK, display

from .config import (
    DEFAULT_OCR_HOTKEY,
    DEFAULT_TRANSLATE_HOTKEY,
    ConfigError,
    parse_hotkey,
)


class GlobalHotkeys(QObject):
    translate_requested = Signal()
    ocr_requested = Signal()

    def __init__(
        self,
        translate_hotkey: str = DEFAULT_TRANSLATE_HOTKEY,
        ocr_hotkey: str = DEFAULT_OCR_HOTKEY,
    ) -> None:
        super().__init__()
        self._display = display.Display()
        self._root = self._display.screen().root
        self._registrations: set[tuple[int, int]] = set()
        self._event_modes: dict[tuple[int, int], str] = {}
        self._num_lock_mask = self._find_num_lock_mask()
        try:
            self.reconfigure(translate_hotkey, ocr_hotkey)
        except Exception:
            self.close()
            raise
        self._notifier = QSocketNotifier(
            self._display.fileno(), QSocketNotifier.Type.Read, self
        )
        self._notifier.activated.connect(self._drain_events)

    def _find_num_lock_mask(self) -> int:
        num_lock_keycode = self._display.keysym_to_keycode(XK.XK_Num_Lock)
        for index, keycodes in enumerate(self._display.get_modifier_mapping()):
            if num_lock_keycode in keycodes:
                return 1 << index
        return 0

    def _build_registration_state(
        self, translate_hotkey: str, ocr_hotkey: str
    ) -> tuple[set[tuple[int, int]], dict[tuple[int, int], str]]:
        translate = parse_hotkey(translate_hotkey, "截图翻译快捷键")
        ocr = parse_hotkey(ocr_hotkey, "截图 OCR 快捷键")
        if translate.portable_text == ocr.portable_text:
            raise ConfigError("截图翻译和截图 OCR 不能使用相同快捷键。")

        ignored_variants = {
            0,
            X.LockMask,
            self._num_lock_mask,
            X.LockMask | self._num_lock_mask,
        }
        registrations: set[tuple[int, int]] = set()
        event_modes: dict[tuple[int, int], str] = {}
        for mode, hotkey in (("translate", translate), ("ocr", ocr)):
            keysym = XK.string_to_keysym(hotkey.keysym_name)
            keycode = self._display.keysym_to_keycode(keysym)
            if not keycode:
                raise ConfigError(
                    f"当前键盘布局无法使用快捷键 {hotkey.portable_text}。"
                )
            event_modes[(keycode, hotkey.modifiers)] = mode
            for ignored in ignored_variants:
                registrations.add((keycode, hotkey.modifiers | ignored))
        return registrations, event_modes

    def reconfigure(self, translate_hotkey: str, ocr_hotkey: str) -> None:
        registrations, event_modes = self._build_registration_state(
            translate_hotkey, ocr_hotkey
        )
        # 先保留旧注册并尝试新增项；只有全部成功后才释放废弃项。
        additions = registrations - self._registrations
        grab_errors: list[object] = []

        def collect_error(error: object, _request: object) -> None:
            grab_errors.append(error)

        for keycode, modifiers in additions:
            self._root.grab_key(
                keycode,
                modifiers,
                False,
                X.GrabModeAsync,
                X.GrabModeAsync,
                onerror=collect_error,
            )
        self._display.sync()
        if grab_errors:
            for keycode, modifiers in additions:
                self._root.ungrab_key(keycode, modifiers)
            self._display.sync()
            raise RuntimeError("无法注册全局快捷键，可能已被其他程序占用。")

        for keycode, modifiers in self._registrations - registrations:
            self._root.ungrab_key(keycode, modifiers)
        self._display.sync()
        self._registrations = registrations
        self._event_modes = event_modes

    def _drain_events(self, *_args: object) -> None:
        while self._display.pending_events():
            event = self._display.next_event()
            if event.type != X.KeyPress:
                continue
            normalized_state = event.state & ~(X.LockMask | self._num_lock_mask)
            mode = self._event_modes.get((event.detail, normalized_state))
            if mode == "translate":
                self.translate_requested.emit()
            elif mode == "ocr":
                self.ocr_requested.emit()

    def close(self) -> None:
        notifier = getattr(self, "_notifier", None)
        if notifier is not None:
            notifier.setEnabled(False)
        root = getattr(self, "_root", None)
        x_display = getattr(self, "_display", None)
        if root is not None and x_display is not None:
            for keycode, modifiers in self._registrations:
                root.ungrab_key(keycode, modifiers)
            try:
                x_display.sync()
            finally:
                x_display.close()
        self._registrations.clear()
