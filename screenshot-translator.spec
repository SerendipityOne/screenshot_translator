from pathlib import Path
import os


project_dir = Path.cwd()
conda_prefix = Path(os.environ.get("CONDA_PREFIX", ""))
xcb_cursor = conda_prefix / "lib" / "libxcb-cursor.so.0"
openssl_libraries = [
    conda_prefix / "lib" / "libssl.so.3",
    conda_prefix / "lib" / "libcrypto.so.3",
]
missing_libraries = [
    path
    for path in [xcb_cursor, *openssl_libraries]
    if not path.is_file()
]
if missing_libraries:
    raise SystemExit(
        "当前环境缺少打包动态库："
        + ", ".join(str(path) for path in missing_libraries)
    )

a = Analysis(
    [str(project_dir / "app.py")],
    pathex=[str(project_dir)],
    # xcb 插件的相对 RPATH 指向 Qt/lib，放在这里可脱离 Conda 直接解析。
    binaries=[
        (str(xcb_cursor), "PySide6/Qt/lib"),
        *[(str(path), ".") for path in openssl_libraries],
    ],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="screenshot-translator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="screenshot-translator",
)
