from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .config import (
    APP_NAME,
    AUTOSTART_FILENAME,
    ConfigError,
    write_private_atomic,
)


LOGGER = logging.getLogger(APP_NAME)


def quote_desktop_exec_argument(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ConfigError("自启动命令路径无效。")
    escaped = value.replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        escaped = escaped.replace(character, f"\\{character}")
    return f'"{escaped}"'


class AutostartManager:
    def __init__(
        self,
        entry_path: Path,
        launch_command: tuple[str, ...],
    ) -> None:
        self.entry_path = entry_path
        self.launch_command = launch_command

    @classmethod
    def from_standard_location(
        cls,
        source_entrypoint: Path | None = None,
        config_root: Path | None = None,
    ) -> "AutostartManager":
        if config_root is None:
            config_root = Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.GenericConfigLocation
                )
            )
        if getattr(sys, "frozen", False):
            launch_command = (str(Path(sys.executable).resolve()),)
        else:
            entrypoint = source_entrypoint or Path(sys.argv[0]).resolve()
            launch_command = (
                str(Path(sys.executable).resolve()),
                str(entrypoint.resolve()),
            )
        return cls(
            config_root / "autostart" / AUTOSTART_FILENAME,
            launch_command,
        )

    def is_enabled(self) -> bool:
        return self.entry_path.is_file()

    def set_enabled(self, enabled: bool) -> None:
        try:
            if not enabled:
                self.entry_path.unlink(missing_ok=True)
                LOGGER.info("登录自启动已关闭 path=%s", self.entry_path)
                return
            content = self._desktop_entry()
            write_private_atomic(self.entry_path, content)
            LOGGER.info("登录自启动已开启 path=%s", self.entry_path)
        except OSError as exc:
            raise ConfigError(f"无法更新登录自启动：{exc}") from exc

    def _desktop_entry(self) -> str:
        if not self.launch_command or not os.access(self.launch_command[0], os.X_OK):
            raise ConfigError("无法执行当前程序，不能启用登录自启动。")
        command = " ".join(
            quote_desktop_exec_argument(argument)
            for argument in self.launch_command
        )
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Version=1.0\n"
            "Name=Screenshot Translator\n"
            "Comment=截图翻译与 OCR\n"
            f"Exec={command}\n"
            "Terminal=false\n"
            "OnlyShowIn=GNOME;\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
