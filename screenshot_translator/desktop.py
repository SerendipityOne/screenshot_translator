from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .config import (
    APP_NAME,
    AUTOSTART_FILENAME,
    ConfigError,
    write_private_atomic,
)


LOGGER = logging.getLogger(APP_NAME)
SYSTEMD_SERVICE_NAME = "screenshot-translator.service"
SYSTEMD_SERVICE_FILENAME = SYSTEMD_SERVICE_NAME
INSTALL_ROOT_NAME = "screenshot-translator"
INSTALL_APP_DIRNAME = "app"
AUTOSTART_DELAY_SECONDS = 8


def quote_desktop_exec_argument(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ConfigError("自启动命令路径无效。")
    escaped = value.replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        escaped = escaped.replace(character, f"\\{character}")
    return f'"{escaped}"'


def quote_systemd_exec_argument(value: str) -> str:
    """按 systemd ExecStart 规则转义路径，不经过 shell。"""
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ConfigError("systemd 启动路径无效。")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    # % 是 systemd specifier 前缀，普通路径中的百分号必须写成 %%。
    escaped = escaped.replace("%", "%%")
    return f'"{escaped}"'


class AutostartManager:
    def __init__(
        self,
        entry_path: Path,
        launch_command: tuple[str, ...],
        *,
        service_path: Path,
        source_bundle: Path | None,
        install_dir: Path | None,
        can_enable: bool,
        systemctl_command: tuple[str, ...] = ("systemctl", "--user"),
    ) -> None:
        self.entry_path = entry_path
        self.service_path = service_path
        self.launch_command = launch_command
        self.source_bundle = source_bundle
        self.install_dir = install_dir
        self.can_enable = can_enable
        self.systemctl_command = systemctl_command

    @classmethod
    def from_standard_location(
        cls,
        source_entrypoint: Path | None = None,
        config_root: Path | None = None,
        data_root: Path | None = None,
        systemctl_command: tuple[str, ...] = ("systemctl", "--user"),
    ) -> "AutostartManager":
        if config_root is None:
            config_root = cls._standard_path(
                QStandardPaths.StandardLocation.GenericConfigLocation,
                "配置目录",
            )
        if data_root is None:
            data_root = cls._standard_path(
                QStandardPaths.StandardLocation.GenericDataLocation,
                "数据目录",
            )

        if getattr(sys, "frozen", False):
            executable = Path(sys.executable).resolve()
            source_bundle = executable.parent
            install_dir = data_root / INSTALL_ROOT_NAME / INSTALL_APP_DIRNAME
            launch_command = (str(executable),)
            can_enable = True
        else:
            entrypoint = source_entrypoint or Path(sys.argv[0]).resolve()
            source_bundle = None
            install_dir = None
            launch_command = (
                str(Path(sys.executable).resolve()),
                str(entrypoint.resolve()),
            )
            can_enable = False

        return cls(
            config_root / "autostart" / AUTOSTART_FILENAME,
            launch_command,
            service_path=config_root / "systemd" / "user" / SYSTEMD_SERVICE_FILENAME,
            source_bundle=source_bundle,
            install_dir=install_dir,
            can_enable=can_enable,
            systemctl_command=systemctl_command,
        )

    @staticmethod
    def _standard_path(location: QStandardPaths.StandardLocation, label: str) -> Path:
        value = QStandardPaths.writableLocation(location)
        if not value:
            raise ConfigError(f"无法定位用户{label}。")
        return Path(value)

    @property
    def installed_executable(self) -> Path | None:
        if self.install_dir is None or not self.launch_command:
            return None
        return self.install_dir / Path(self.launch_command[0]).name

    def is_enabled(self) -> bool:
        try:
            result = self._run_systemctl(
                "is-enabled", SYSTEMD_SERVICE_NAME, check=False
            )
        except ConfigError as exc:
            LOGGER.warning("读取 systemd 自启动状态失败 error=%s", exc)
            return False
        return result.returncode == 0

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            if not self.can_enable:
                raise ConfigError(
                    "源码开发模式不能启用 systemd 自启动，请运行独立应用后启用。"
                )
            self._enable_service()
        else:
            self._disable_service()

    def _enable_service(self) -> None:
        old_enabled = self.is_enabled()
        old_unit = self._snapshot(self.service_path)
        old_legacy_entry = self._snapshot(self.entry_path)
        install_changed = False
        backup: Path | None = None
        try:
            install_changed, backup = self._install_bundle()
            write_private_atomic(self.service_path, self._systemd_unit())
            self._run_systemctl("daemon-reload")
            self._run_systemctl("enable", SYSTEMD_SERVICE_NAME)
            self._remove_legacy_entry()
        except Exception as exc:
            rollback_errors: list[str] = []
            try:
                self._restore_snapshot(self.service_path, old_unit)
                self._run_systemctl("daemon-reload", check=False)
                if old_enabled:
                    self._run_systemctl("enable", SYSTEMD_SERVICE_NAME, check=False)
                else:
                    self._run_systemctl("disable", SYSTEMD_SERVICE_NAME, check=False)
                self._restore_snapshot(self.entry_path, old_legacy_entry)
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
            try:
                if install_changed:
                    self._rollback_install(backup)
            except Exception as rollback_exc:
                rollback_errors.append(f"应用副本：{rollback_exc}")
            detail = f"无法启用 systemd 自启动：{exc}"
            if rollback_errors:
                detail += f"；回滚失败：{'；'.join(rollback_errors)}"
            raise ConfigError(detail) from exc
        else:
            self._discard_backup(backup)
            LOGGER.info(
                "systemd 用户自启动已启用 service=%s path=%s",
                self.service_path,
                self.installed_executable,
            )

    def _disable_service(self) -> None:
        was_enabled = self.is_enabled()
        old_legacy_entry = self._snapshot(self.entry_path)
        try:
            if was_enabled:
                self._run_systemctl("disable", SYSTEMD_SERVICE_NAME)
            self._remove_legacy_entry()
        except Exception as exc:
            if was_enabled:
                try:
                    self._run_systemctl("enable", SYSTEMD_SERVICE_NAME, check=False)
                except Exception as rollback_exc:
                    raise ConfigError(
                        f"无法关闭 systemd 自启动：{exc}；恢复服务失败：{rollback_exc}"
                    ) from exc
            try:
                self._restore_snapshot(self.entry_path, old_legacy_entry)
            except Exception as rollback_exc:
                raise ConfigError(
                    f"无法关闭 systemd 自启动：{exc}；恢复旧配置失败：{rollback_exc}"
                ) from exc
            raise ConfigError(f"无法关闭 systemd 自启动：{exc}") from exc
        LOGGER.info("systemd 用户自启动已关闭 service=%s", SYSTEMD_SERVICE_NAME)

    def _install_bundle(self) -> tuple[bool, Path | None]:
        if self.source_bundle is None or self.install_dir is None:
            raise ConfigError("当前运行模式没有可安装的独立应用。")
        source_bundle = self.source_bundle.resolve()
        install_dir = self.install_dir
        installed_executable = self.installed_executable
        if (
            install_dir.exists()
            and source_bundle == install_dir.resolve()
            and installed_executable is not None
            and os.access(installed_executable, os.X_OK)
        ):
            return False, None
        source_executable = source_bundle / Path(self.launch_command[0]).name
        if not source_bundle.is_dir() or not source_executable.is_file():
            raise ConfigError(f"无法定位当前独立应用目录：{source_bundle}")

        parent = install_dir.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, 0o700)
        staging = Path(tempfile.mkdtemp(prefix=".app-", dir=parent))
        staging.rmdir()
        backup: Path | None = None
        try:
            shutil.copytree(source_bundle, staging, symlinks=True)
            staged_executable = staging / source_executable.name
            if not staged_executable.is_file() or not os.access(
                staged_executable, os.X_OK
            ):
                raise ConfigError("复制后的独立应用不可执行。")
            if install_dir.exists() or install_dir.is_symlink():
                backup = Path(tempfile.mkdtemp(prefix=".backup-", dir=parent))
                backup.rmdir()
                os.replace(install_dir, backup)
            os.replace(staging, install_dir)
            os.chmod(install_dir, 0o700)
            return True, backup
        except Exception:
            self._remove_path(staging)
            if backup is not None:
                self._remove_path(install_dir)
                os.replace(backup, install_dir)
            raise

    def _rollback_install(self, backup: Path | None) -> None:
        if self.install_dir is None:
            return
        if backup is None:
            # 没有旧版本时，回滚只移除本次生成的固定副本。
            self._remove_path(self.install_dir)
            return
        self._remove_path(self.install_dir)
        os.replace(backup, self.install_dir)

    def _discard_backup(self, backup: Path | None) -> None:
        if backup is not None:
            try:
                self._remove_path(backup)
            except OSError as exc:
                LOGGER.warning("旧独立应用备份清理失败 path=%s error=%s", backup, exc)

    def _systemd_unit(self) -> str:
        executable = self.installed_executable
        if executable is None or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ConfigError("无法定位固定安装的独立应用。")
        command = quote_systemd_exec_argument(str(executable))
        return (
            "[Unit]\n"
            "Description=Screenshot Translator\n"
            "After=graphical-session.target\n"
            "PartOf=graphical-session.target\n"
            "StartLimitIntervalSec=120\n"
            "StartLimitBurst=5\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStartPre=/usr/bin/sleep {AUTOSTART_DELAY_SECONDS}\n"
            f"ExecStart={command} --service\n"
            "Restart=on-failure\n"
            "RestartSec=5s\n"
            "SyslogIdentifier=screenshot-translator\n"
            "StandardOutput=journal\n"
            "StandardError=journal\n"
            "\n"
            "[Install]\n"
            "WantedBy=graphical-session.target\n"
        )

    def _run_systemctl(
        self,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [*self.systemctl_command, *arguments],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ConfigError("未找到 systemctl，无法管理用户服务。") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConfigError("systemctl 操作超时。") from exc
        except OSError as exc:
            raise ConfigError(f"无法执行 systemctl：{exc}") from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ConfigError(
                f"systemctl 操作失败：{detail or f'退出码 {result.returncode}'}"
            )
        return result

    @staticmethod
    def _snapshot(path: Path) -> tuple[str, int] | None:
        if not path.exists():
            return None
        if not path.is_file():
            raise ConfigError(f"路径不是普通文件：{path}")
        return path.read_text(encoding="utf-8"), path.stat().st_mode & 0o777

    @staticmethod
    def _restore_snapshot(path: Path, snapshot: tuple[str, int] | None) -> None:
        if snapshot is None:
            path.unlink(missing_ok=True)
            return
        write_private_atomic(path, snapshot[0])
        os.chmod(path, snapshot[1])

    def _remove_legacy_entry(self) -> None:
        self.entry_path.unlink(missing_ok=True)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
