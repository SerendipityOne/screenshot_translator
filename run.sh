#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
packaged_app="$project_dir/dist/screenshot-translator/screenshot-translator"

if [[ -x "$packaged_app" ]]; then
  unset CONDA_EXE CONDA_PREFIX PYTHONPATH PYTHONHOME LD_LIBRARY_PATH
  exec "$packaged_app" "$@"
fi

# 已激活的开发环境可直接运行源码，不要求执行 conda init。
if python -c 'import PySide6, Xlib' >/dev/null 2>&1; then
  runtime_prefix="$(python -c 'import sys; print(sys.prefix)')"
  unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH
  export LD_LIBRARY_PATH="$runtime_prefix/lib"
  exec python "$project_dir/app.py" "$@"
fi

conda_command=""
for candidate in \
  "${CONDA_EXE:-}" \
  "$(command -v conda || true)" \
  "$HOME/miniconda3/bin/conda" \
  "$HOME/anaconda3/bin/conda"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    conda_command="$candidate"
    break
  fi
done

if [[ -z "$conda_command" ]]; then
  printf '错误：未找到独立应用、可用 Python 环境或 Conda。请先构建应用。\n' >&2
  exit 1
fi

conda_base="$("$conda_command" info --base 2>/dev/null || true)"
conda_script="$conda_base/etc/profile.d/conda.sh"
if [[ -z "$conda_base" || ! -f "$conda_script" ]]; then
  printf '错误：无法定位 conda.sh，请检查 conda 安装。\n' >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$conda_script"
if ! conda activate screenshot-translator; then
  printf '错误：未找到 Conda 环境 screenshot-translator。\n' >&2
  exit 1
fi

# ROS 的 Python 和动态库路径会污染 PySide6/X11；这里只清理当前进程环境。
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib"

exec python "$project_dir/app.py" "$@"
