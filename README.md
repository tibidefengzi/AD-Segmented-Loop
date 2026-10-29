# AD-Segmented-Loop

ComfyUI 分段循环插件：在时间线上管理图片、视频与音频，逐段生成、预览、审核和续接。

当前版本：**v0.4.9**。开源许可：**GPL-3.0-only**。

## 功能

- 图片参考管理与音视频多轨时间线，支持裁剪、独立分段、素材拖放与智能切分。
- 按段读取提示词及参考素材，保留外部模型、条件与采样节点的连接方式。
- 分段循环、停止续接、本段抽卡、预览审核与版本保留。
- 按编号选择视频合并，支持分段视频循环播放预览。
- 原视频、原音频及分段参考输出，素材占用显示与可重建缓存清理。

节点分类：**AD-Segmented-Loop**；主节点为 **AD-素材准备、AD-分段生成、AD-预览审批**。

## 安装

### 方法一：下载

从 [Releases](https://github.com/tibidefengzi/AD-Segmented-Loop/releases) 下载插件 ZIP，解压后将 `AD-Segmented-Loop` 文件夹放入 `ComfyUI/custom_nodes`。

### 方法二：Git

在 `ComfyUI/custom_nodes` 目录执行：

```bash
git clone https://github.com/tibidefengzi/AD-Segmented-Loop.git
```

随后使用运行 ComfyUI 的 Python 安装依赖：

```bash
python -m pip install -r custom_nodes/AD-Segmented-Loop/requirements.txt
```

Windows 便携版请在 `ComfyUI_windows_portable` 目录执行：

```bat
python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\AD-Segmented-Loop\requirements.txt
```

重启 ComfyUI 并重新打开页面。已安装且满足要求的依赖可复用，不需要单独替换现有 torch/torchaudio。

## 使用

1. 添加三个主节点，将素材准备的分段清单连接至分段生成，将当前段上下文连接至预览审批。
2. 导入素材、设置分段与参考范围；模型、条件编码和采样流程继续在外部连接。
3. 将需要查看的媒体接入预览审批，使用“开始”或“分段循环”执行。
4. 需要重抽时先停止运行，选中目标段预览后点击“本段抽卡”；确认结果后按当前审核流程继续。
5. 按住 Ctrl 选择分段视频，使用合并按钮按编号拼接。

黄色抽卡通常只作预览备选。顺序任务主动停止后重抽停止所在段时，成功结果默认选用但不会自动批准；点击“同意批准”后才进入下一段。重抽其它段、普通空闲或完成后抽卡仍保持备选规则。

## 环境与数据

- Python >= 3.10；ComfyUI 0.43.0 已实测，更早版本尚未完成兼容验证。
- 本项目围绕 MiniMax H3 分段流程设计，不包含模型或第三方二进制。
- 导入素材：`ComfyUI/input/AD-Segmented-Loop-input/{图片,音频,视频}`。
- 生成归档：`ComfyUI/output/AD-Segmented-Loop-output/{图片,音频,视频}`。
- 运行进度与缓存：`ComfyUI/output/AD-Segmented-Loop-data`。
- 当前版本完成 CPU 功能及浏览器验证；这不代表 H3 GPU 画质或长片接缝质量已验收。

## 许可

源码按 [GPL-3.0-only](LICENSE) 开源。第三方依赖遵循各自许可，见 [第三方依赖说明](第三方依赖说明.txt)。
