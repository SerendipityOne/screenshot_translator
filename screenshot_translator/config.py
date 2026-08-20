from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QKeySequence
from Xlib import X


APP_NAME = "ScreenshotTranslator"
OCR_LANGUAGES = ("chi_sim", "chi_tra", "eng")
OCR_LANGUAGE_ARGUMENT = "+".join(OCR_LANGUAGES)
OCR_TIMEOUT_SECONDS = 30
API_TIMEOUT_SECONDS = 60
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_CHARACTERS = 100_000
DEFAULT_TRANSLATE_HOTKEY = "Ctrl+Alt+Q"
DEFAULT_OCR_HOTKEY = "Ctrl+Alt+W"
DEFAULT_SELECTION_TRANSLATE_HOTKEY = "Ctrl+Alt+E"
AUTOSTART_FILENAME = "screenshot-translator.desktop"

LOGGER = logging.getLogger(APP_NAME)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ApiConfig:
    api_url: str = ""
    model: str = ""
    api_key: str = ""

    def validated(self) -> "ApiConfig":
        config = ApiConfig(
            api_url=self.api_url.strip(),
            model=self.model.strip(),
            api_key=self.api_key.strip(),
        )
        parsed = urlsplit(config.api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError("API URL 必须是完整的 HTTP(S) 地址。")
        if parsed.username or parsed.password:
            raise ConfigError("API URL 中不能包含用户名或密码。")
        if not parsed.path or parsed.path == "/":
            raise ConfigError("请填写包含接口路径的完整 API URL。")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ConfigError("远程 API 必须使用 HTTPS；HTTP 仅允许本机地址。")
        if not config.model:
            raise ConfigError("模型名称不能为空。")
        if not config.api_key:
            raise ConfigError("API Key 不能为空。")
        return config


@dataclass(frozen=True)
class HotkeySpec:
    portable_text: str
    keysym_name: str
    modifiers: int


def parse_hotkey(value: str, label: str) -> HotkeySpec:
    sequence = QKeySequence(value.strip(), QKeySequence.SequenceFormat.PortableText)
    portable_text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1 or not portable_text:
        raise ConfigError(f"{label}必须是单段快捷键。")

    combination = sequence[0]
    modifier_value = int(combination.keyboardModifiers().value)
    control = int(Qt.KeyboardModifier.ControlModifier.value)
    alt = int(Qt.KeyboardModifier.AltModifier.value)
    shift = int(Qt.KeyboardModifier.ShiftModifier.value)
    meta = int(Qt.KeyboardModifier.MetaModifier.value)
    allowed_modifiers = control | alt | shift | meta
    if modifier_value & ~allowed_modifiers:
        raise ConfigError(f"{label}包含不支持的修饰键。")
    if not modifier_value & (control | alt | meta):
        raise ConfigError(f"{label}至少需要 Ctrl、Alt 或 Super。")

    key = int(combination.key())
    if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
        keysym_name = chr(key)
    elif int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
        keysym_name = chr(key)
    elif int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F12):
        keysym_name = f"F{key - int(Qt.Key.Key_F1) + 1}"
    else:
        raise ConfigError(f"{label}主键只支持字母、数字或 F1-F12。")

    x_modifiers = 0
    if modifier_value & control:
        x_modifiers |= X.ControlMask
    if modifier_value & alt:
        x_modifiers |= X.Mod1Mask
    if modifier_value & shift:
        x_modifiers |= X.ShiftMask
    if modifier_value & meta:
        x_modifiers |= X.Mod4Mask
    return HotkeySpec(portable_text, keysym_name, x_modifiers)


@dataclass(frozen=True)
class AppConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    translate_hotkey: str = DEFAULT_TRANSLATE_HOTKEY
    ocr_hotkey: str = DEFAULT_OCR_HOTKEY
    selection_translate_hotkey: str = DEFAULT_SELECTION_TRANSLATE_HOTKEY

    def validated(self, require_api: bool = False) -> "AppConfig":
        api = ApiConfig(
            api_url=self.api.api_url.strip(),
            model=self.api.model.strip(),
            api_key=self.api.api_key.strip(),
        )
        api_values = (api.api_url, api.model, api.api_key)
        if require_api or any(api_values):
            if not all(api_values):
                raise ConfigError(
                    "翻译接口的 API URL、模型和 API Key 必须全部填写。"
                )
            api = api.validated()

        translate = parse_hotkey(self.translate_hotkey, "截图翻译快捷键")
        ocr = parse_hotkey(self.ocr_hotkey, "截图 OCR 快捷键")
        selection_translate = parse_hotkey(
            self.selection_translate_hotkey, "划词翻译快捷键"
        )
        portable_hotkeys = {
            translate.portable_text,
            ocr.portable_text,
            selection_translate.portable_text,
        }
        if len(portable_hotkeys) != 3:
            raise ConfigError("截图翻译、截图 OCR 和划词翻译不能使用相同快捷键。")
        return AppConfig(
            api,
            translate.portable_text,
            ocr.portable_text,
            selection_translate.portable_text,
        )


def write_private_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def from_standard_location(cls) -> "ConfigStore":
        config_dir = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppConfigLocation
            )
        )
        return cls(config_dir / "config.json")

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError("配置根节点不是对象")
            # 旧版本只保存 API 字段；缺少快捷键时必须继续使用原默认值。
            config = AppConfig(
                api=ApiConfig(
                    api_url=str(data.get("api_url", "")),
                    model=str(data.get("model", "")),
                    api_key=str(data.get("api_key", "")),
                ),
                translate_hotkey=str(
                    data.get("translate_hotkey", DEFAULT_TRANSLATE_HOTKEY)
                ),
                ocr_hotkey=str(data.get("ocr_hotkey", DEFAULT_OCR_HOTKEY)),
                selection_translate_hotkey=str(
                    data.get(
                        "selection_translate_hotkey",
                        DEFAULT_SELECTION_TRANSLATE_HOTKEY,
                    )
                ),
            ).validated()
            os.chmod(self.path, 0o600)
            return config
        except (OSError, TypeError, ValueError) as exc:
            raise ConfigError(f"无法读取设置：{exc}") from exc

    def save(self, config: AppConfig) -> None:
        config = config.validated()
        payload = asdict(config.api)
        payload.update(
            {
                "translate_hotkey": config.translate_hotkey,
                "ocr_hotkey": config.ocr_hotkey,
                "selection_translate_hotkey": config.selection_translate_hotkey,
            }
        )
        try:
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            write_private_atomic(self.path, content)
            LOGGER.info("设置已保存 path=%s", self.path)
        except OSError as exc:
            raise ConfigError(f"无法保存设置：{exc}") from exc
