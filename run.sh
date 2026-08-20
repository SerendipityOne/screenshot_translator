#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source /home/yzh/miniconda3/etc/profile.d/conda.sh
conda activate screenshot-translator

# ROS 的动态库和 Python 路径会污染 PyQt/X11；这里只清理当前进程环境。
unset PYTHONPATH PYTHONHOME
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib"

exec python "$project_dir/app.py" "$@"
