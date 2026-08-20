# Screenshot Translator

Ubuntu X11 下的 PyQt6 托盘工具：区域截图后执行 OCR，或继续通过 OpenAI Chat Completions 兼容接口翻译为简体中文。

```text
┌──────────── 截图翻译 / 截图 OCR ────────────┐
│                                            │
│  截图（等比缩放）    │  译文或 OCR 文本      │
│                      │  只读、可选择复制     │
│                      │                       │
└────────────────────────────────────────────┘
```

## 功能

- `Ctrl+Alt+Q`：截取鼠标所在显示器的区域，OCR 后翻译为简体中文。
- `Ctrl+Alt+W`：截取鼠标所在显示器的区域，只执行 OCR。
- OCR 使用本机 Tesseract 的 `chi_sim+chi_tra+eng` 语言数据。
- 截图原图始终保留在本机；翻译时只发送 OCR 文本。

## 环境要求

- Ubuntu 24.04、GNOME X11；不支持 Wayland。
- Tesseract 5，以及 `chi_sim`、`chi_tra`、`eng` 语言数据。
- 已启用 GNOME AppIndicator 扩展：

```bash
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

## 安装

使用独立 Miniconda 环境：

```bash
cbase
conda create -n screenshot-translator python=3.12 pip xcb-util-cursor -y \
  --override-channels \
  -c https://repo.anaconda.com/pkgs/main \
  -c https://repo.anaconda.com/pkgs/r
conda activate screenshot-translator
python -m pip install -r requirements.txt
```

`xcb-util-cursor` 是 PyQt6 xcb 平台插件在 X11 下所需的 Conda 库。若本机 Conda 配置把 defaults 指向不可用镜像，上述 `--override-channels` 只对本次创建命令生效，不会修改配置。不要执行 `conda init`，本项目不会修改 Miniconda 的自动激活设置。

## 运行

```bash
./run.sh
```

`run.sh` 会清理当前进程继承的 ROS Python/动态库路径，并设置 Conda Qt 库路径。程序启动后只显示托盘图标。托盘菜单提供截图翻译、截图 OCR、翻译 API 设置和退出。

首次使用截图翻译时需填写：

- 完整的 Chat Completions 兼容 API URL，例如 `https://example.com/v1/chat/completions`。
- 模型名称。
- API Key。

API Key 以明文保存在当前用户的 Qt 应用配置目录，配置文件权限固定为 `0600`。程序拒绝远程 HTTP 接口，但允许 `localhost`、`127.0.0.1` 和 `::1` 的本机 HTTP 接口。

## 测试

```bash
cbase
conda activate screenshot-translator
env -u PYTHONPATH -u PYTHONHOME \
  LD_LIBRARY_PATH="$CONDA_PREFIX/lib" \
  QT_QPA_PLATFORM=offscreen python -m unittest -v
```

自动测试覆盖配置校验与权限、Mock API 请求契约、Tesseract 语言检查和一次真实英文 OCR 冒烟测试。全局快捷键、托盘和双显示器截图需要在 X11 桌面中人工验证。

## 当前边界

- 不支持 Wayland、Windows、跨显示器框选或开机启动。
- 不保存截图或 OCR 历史，不自动复制结果。
- 固定使用 `choices[0].message.content` 响应格式，不依赖特定厂商 SDK。
