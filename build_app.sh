#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if [[ "${CONDA_DEFAULT_ENV:-}" != "screenshot-translator" || -z "${CONDA_PREFIX:-}" ]]; then
  printf '错误：请先激活现有 screenshot-translator Conda 环境。\n' >&2
  exit 1
fi
if ! python -c 'import PyInstaller, PySide6, Xlib' >/dev/null 2>&1; then
  printf '错误：缺少构建依赖，请安装 requirements-build.txt。\n' >&2
  exit 1
fi
if [[ ! -f "$CONDA_PREFIX/lib/libxcb-cursor.so.0" ]]; then
  printf '错误：当前环境缺少 libxcb-cursor.so.0。\n' >&2
  exit 1
fi
for openssl_library in libssl.so.3 libcrypto.so.3; do
  if [[ ! -f "$CONDA_PREFIX/lib/$openssl_library" ]]; then
    printf '错误：当前环境缺少 %s。\n' "$openssl_library" >&2
    exit 1
  fi
done

unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH

python -m PyInstaller --noconfirm --clean screenshot-translator.spec
install -m 0644 LICENSE dist/screenshot-translator/LICENSE
install -m 0644 THIRD_PARTY_NOTICES.md dist/screenshot-translator/THIRD_PARTY_NOTICES.md

archive="dist/screenshot-translator-linux-x86_64.tar.gz"
archive_epoch="${SOURCE_DATE_EPOCH:-0}"
tar \
  --sort=name \
  --mtime="@$archive_epoch" \
  --owner=0 \
  --group=0 \
  --numeric-owner \
  -C dist \
  -cf - screenshot-translator | gzip -n > "$archive"

printf '构建完成：%s\n' "$project_dir/$archive"
