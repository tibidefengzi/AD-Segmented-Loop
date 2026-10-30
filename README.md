# AD-Segmented-Loop

ComfyUI 分段循环插件：在时间线上管理图片、视频与音频，逐段生成、预览、审核和续接。

当前版本：**v0.4.23**。开源许可：**GPL-3.0-only**。

## 最新更新（v0.4.23）

- **纯图片手动分段数**：首次导入图片按图片数量初始化；点击“应用分段”或手动调整后，以手动段数为准。之后增删图片不再改变段数。
- “应用分段”保留已有各段长度，只在末尾补足或删除差值段；新增段取“每段长度”。参考图作用范围不变，显式删空保持为空，旧工作流继续兼容。
- **提示词分段识别修复**：无素材时，只读取与素材准备实际相连的分段生成节点的正向提示词；输入后立即同步，避免多套节点互相影响。
- **空保存路径**：自动按类型归档到 `ComfyUI/output/AD-Segmented-Loop-output/{图片,音频,视频}`；填写路径后使用填写位置。下载和选择目录的初始位置同步。
- 保留任意选段运行与原进度续接、白线跟随、三节点进度条、Windows原生中文大图标文件窗口；新建节点默认对齐32，旧工作流明确保存的数值不强制改写。

更新前先保存工作流并结束当前队列，安全关闭ComfyUI后更新；重启并 **Ctrl+F5** 刷新。不要同时启用多份插件目录。

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
- 保存路径留空：按类型保存到 `ComfyUI/output/AD-Segmented-Loop-output/{图片,音频,视频}`；填写后使用指定位置。
- 生成目录入口：`ComfyUI/output/AD-Segmented-Loop-output`，历史归档保留。
- 运行进度与缓存：`ComfyUI/output/AD-Segmented-Loop-data`。
- v0.4.23 既有验收记录：352 项后台回归、13 组前端检查、12 份 JS 语法检查及 7 项隔离真实 HTTP 检查通过；未运行 H3 GPU 采样或长片画质验收。
- 原生文件选择/另存为功能仅支持 Windows。

## 许可

源码按 [GPL-3.0-only](LICENSE) 开源。第三方依赖遵循各自许可，见 [第三方依赖说明](第三方依赖说明.txt)。
