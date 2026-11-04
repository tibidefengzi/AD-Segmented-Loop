# 第三方出处与许可声明（THIRD-PARTY NOTICES）

本包（ComfyUI-H3-Latent-Relay）以 **MIT** 发布（见 [`LICENSE`](LICENSE)）。
下列第三方作品在**设计阶段被阅读、参考或对照**，按「机制 / 契约 / 论文」三类分别说明。
**当前版本（≥ 0.6.0）不包含任何 GPL / AGPL / LGPL 代码**（历史版本见 §一·B）。
⚠️ 但本包的**运行时宿主 ComfyUI 是 GPL-3.0**，且本包在进程内 import 其模块 —— 见 **§一·C**，
那是本文件里唯一一条"不是复制、但是 copyleft 依赖"的关系，引用本包前请一并阅读。

---

## 一、机制与契约参考（无代码逐字复制）

| 第三方 | 许可 | 被参考的内容 | 我们的实现 | 性质 |
|---|---|---|---|---|
| **AIMixer / `ComfyUI_MiniMaxH3_Director`** | Apache-2.0（✅ **2026-09-20 已一手核对**，见 §四） | ① 拷贝桥：上一段 AV latent 硬拷贝 + 噪声掩码 + 音频尾拷贝 + NestedTensor 双流打包；② 段头低频残差对齐（`_lowfreq_appearance_pull` / `match_export_opening_grade`）；③ 批量化盒式模糊（把 guide 的模糊提到循环外、逐帧模糊合成一次 `avg_pool2d`） | `relay_core.build_continue_latent` / `lowfreq_pull` / `_box_blur_hwc`；节点 `H3RelayCopyBridge`、`H3RelayPost.lowfreq_pull` | **读源码后按机制重写**：公式层面对齐（差分式低频残差传递本身是**唯一写法**），但控制流、参数面、权重曲线、边界与降级处理均为本仓库独立设计（本包另加：逐帧线性权重斜坡、画布不一致即不动刀、掩码按上游原生契约走） |
| **`comfyui-minimax-h3-audio-T8`**（`T8mars/…`） | ⚠️ **GPL-3.0-or-later**（2026-09-20 一手核对更正；**此前本文件误记为 MIT**） | 掩码语义：走 ComfyUI **原生 H3 契约**（`MiniMaxH3.scale_latent_inpaint` / `mask_row_values`），与具体采样器无关；全 0 硬锁 = 钉住区零重绘 | `relay_core.prefix_*_weights` + `build_continue_latent` 的 `noise_mask` 构造 | 该契约来自**上游 ComfyUI 宿主源码**（**GPL-3.0**，运行时 import，不复制）——见 **§一·C**；T8 **仅作为"用法佐证"**（证明这个掩码语义就是原生契约），**未复制其任何代码**。⚠️ 但它自身是 copyleft：后续若想照着它的实现改，先读 §一·B 的教训 |
| **`ComfyUI-Apt_Preset`** | **许可未知**（包已于 2026-09-13 从开发机移除，无从复核；已移除 ⇒ 现无从取得其许可） | 仅**沿用其 latent 字典键名**做兼容读取：`apt_h3_export_tail_latent` / `apt_h3_export_tail_audio_latent` / `apt_h3_export_context_frames` | `relay_core.KEY_EXPORT_*`（常量名与取值） | **数据键名，非代码**：无 import、无调用、无复制；仅当上游 latent 里带了这些键才复用其值，否则走本包自己的切片路径 |

> ⚠️ **「机制不受版权保护，表达受保护」**：上表两行均为**机制层**参考。
> 若后续有维护者发现任何段落与第三方源码构成**表达层**重合，请按 §四 流程处理
> （本仓库主张 MIT、且**不得**混入 copyleft 代码）。
> 👉 **§一·B 就是这样一次发现，以及它的处理结果——请一并阅读。**

### B. ⚠️ 已消除的历史重合：`ComfyUI-H3-Motion-Context`（GPL-3.0）

**这一条不是"参考"，是"曾经重合、现已重写"。单独成节，是为了让它可被独立检索到。**

| 项 | 内容 |
|---|---|
| 对方 | `ComfyUI-H3-Motion-Context` ｜ **GPL-3.0** ｜ `Copyright (C) 2026 NikoDemon80` ｜ `github.com/NikoDemon80/ComfyUI-H3-Motion-Context` |
| 重合位置 | **三簇**：<br>① `relay_core.apply_relay` 的**锚位合成段**（合并既有 keyframes + 丢弃落在钉住区内的锚）<br>② 网格辅助 `relay_core.step_offsets` / `steps_for_frames`<br>③ `relay_core.audio_tail_from_latent` 的音频尾段切法（外溢量与尾部切片） |
| 重合形态 | **表达层** —— 变量名（`kept`/`dropped`/`prior`/`head_end`、`acc`/`covered`、`overhang`）、控制流顺序、注释的论证结构一致。<br>其**机制**（钉住区去重、跨度前缀和、按外溢量夹取尾段）在逻辑上近乎必然、不受版权保护；受保护的是**表达** |
| 存在于 | **v0.2.1（首次开源 `59ccd36`，2026-09-11）～ v0.5.0** 的公开提交历史 |
| 处置 | **2026-09-19 全部重写**（三簇）。行为**逐位等价**，由 [`tools/verify_rewrite_equivalence.py`](tools/verify_rewrite_equivalence.py) 做差分验证：旧实现从 `git show <rev>` 捞取（不手工转录），137 个用例逐位比对 + **变异体自证**（故意做坏的实现必须被抓到） |
| 历史 | **未改写 git 历史**。重写只影响当前版本；`59ccd36` 起的公开历史里这三簇仍在，任何人可检出复核 |

**⚠️ 核验方法（留给后续维护者，别重复踩）**

第一轮核查用的是「**两边函数/类名交集为 0 ⇒ 同源只有一段**」——**这个判据是无效的**。
函数名可以完全不同而**函数体逐字节相同**（本包 `steps_for_frames` 与对方 `_steps_for_frames` 即如此），
所以第一轮漏掉了第二、三簇。正确做法是**逐函数比对函数体**，并且**先做归因测试**：
这些重合行是不是来自上游 ComfyUI 核心？

- 经归因**排除**（两边都源自官方，不算同源）：`pixel_frames`（官方
  `comfy_extras/nodes_minimax_h3.py` 里就是同一行 `sum(FRAME_PER_TOKEN[k % 5] for k in range(...))`）、
  `FRAME_PER_TOKEN[k % 5]`、`acc +=`、`total_t`、`resolved_frame_index`、
  `cond_t = cursor + FRAME_RESCALE * kf[...]`。
- 经归因**保留为候选**：`covered`、`overhang`、`out.append(acc)`、`steps_for_frames` —— 官方核心**均无**。
- 扫描工具：[`tools/scan_expression_overlap.py`](tools/scan_expression_overlap.py)
  （`python tools/scan_expression_overlap.py <对方仓库路径>`）。
  ⚠️ 它**只报数不定性**，且过滤器**不要按 `return`/`if` 前缀排除**——那会把短函数整段滤掉，
  造成"看起来干净"的假象。

**为什么不 force-push 抹掉历史**：本仓已公开且已有第三方 fork / PR，重写历史抹不掉已存在的副本，
反而会让「事实是否被隐瞒」本身成为问题。**如实记录 + 当前版本已消除**，是这里选择的处理方式。

**未决事项（本仓库无法单方判定，不替下游下结论）**：

- **派生方向未定**：对方公开史早于本包（其 0.2.0 可追至 2026-08-09），但本包可能经
  **`ComfyUI-Apt_Preset`** 中转 —— `relay_core.KEY_EXPORT_FRAMES = "apt_h3_export_context_frames"`
  是 Apt 的键。Apt_Preset 整包已于 2026-09-13 从本机移除，**其 LICENSE 已无从复核**。
- 因此**不能排除**上述三簇最终可追溯至 GPL 系谱。本包已按最保守口径处理（重写 + 披露），
  但**尚未取得对方作者的书面确认**。若你是下游使用者且需要确定性，请自行评估或联系对方作者。
- 另有两处**行为/设计层面**的对标（非代码搬运），一并列出以示完整：
  `H3RelayChain` + `web/relay_kit_chain.js`（行为对标其 Chain，代码独立实现，见 `CHANGES.md`）；
  `layout_contract.py`（"读上游源码 + 不一致即拒绝运行"的**思路**相同，检查对象与实现不同）。

### C. ⚠️ 运行时宿主：ComfyUI 是 **GPL-3.0**（本文件此前误记为 Apache-2.0）

**这一条不是"参考"，是"运行时依赖"——而且宿主是强 copyleft。单独成节，因为它决定了引用本包时你要评估什么。**

| 项 | 内容 |
|---|---|
| 事实 | ComfyUI（`comfyanonymous/ComfyUI`）以 **GPL-3.0** 发布。已一手核对本机 `ComfyUI/LICENSE`：首行 `GNU GENERAL PUBLIC LICENSE Version 3`，且全文**没有**任何针对自定义节点 / 插件的例外条款（grep `custom node` / `plugin` / `exception` 只命中 GPL 通用条款） |
| 本包与它的关系 | 运行时 **import** `folder_paths` / `node_helpers` / `comfy.nested_tensor`；`layout_contract.py` 读取上游 `comfy_extras/nodes_minimax_h3.py` 的 `FRAME_PER_TOKEN` 做契约核对。**不复制**任何 ComfyUI 源码文件，仓库内无 GPL 文本 |
| 我方立场（**主张，不是结论**） | 本包是独立程序，仅通过 ComfyUI 公开的 Python API 与公开数据结构交互，不以 ComfyUI 源码为派生基础；这也是 ComfyUI 自定义节点生态的通行做法（大量节点包以 MIT / Apache 发布，Comfy Registry 亦接受） |
| ⚠️ 未决 | GPL-3.0 对"进程内 import 的插件"是否构成派生作品，**既无司法判例，也无 ComfyUI 官方书面确认**。插件耦合度比"跨进程 arm's length 通信"更紧，严格解释下存在被主张的空间。需要确定性的场景（企业合规审查 / 再分发）请自行取得法律意见 |
| 更正记录 | 2026-09-19 之前本文件把宿主写成 **Apache-2.0 —— 那是错的**，现已更正；README「📄 许可与出处」同步 |

### C-1. 🔍 2026-10-16 新增「视频起段」（`H3RelaySeedFromVideo`）的同源审计

**这一节按 §一·B 的教训做**（那次重合证明「函数名交集为 0」**不是**同源判据）——
故本次直接做**逐行文本比对**，不看函数名、不看结构：

| 项 | 内容 |
|---|---|
| 该节点做什么 | 把一段**已有视频**编码成第 0 段的 AV latent 边车，之后各段照常续接。仅此一项，无新算法 |
| 调用的宿主公开 API | `vae.encode()`（视频/音频 VAE）、`comfy.utils.common_upscale()`、`comfy.audio.resample()`、`comfy.nested_tensor.NestedTensor`。与核心节点 `VAEEncode` / `VAEEncodeAudio` / `LTXVConcatAVLatent` 用的是同一批 API —— 属 §一·C 的「运行时 import，非复制」范畴 |
| 对照对象 | 宿主 `comfy_extras/nodes_minimax_h3.py`、`nodes_audio.py`、`comfy/sd.py`、`comfy/nested_tensor.py`，以及 **`comfyui-obvpm-timeline`**（GPL-3.0）与 **`ComfyUI-MiniMaxH3-TimelineDirector`**（GPL-3.0）的全部 `*.py` |
| 判据 | 取该节点**非注释、≥15 字符**的每一行（共 128 行），在对照集合里逐字查找 |
| 结果 | **命中 10 条，全部为不可版权的协议样板**：`def INPUT_TYPES(cls):`、`import comfy.audio` / `import comfy.nested_tensor` / `import comfy.utils`、`raise ValueError(`、`if audio is None:`、`"video_vae": ("VAE", {` 等输入声明行。**无任何一处表达层重合** |
| 说明 | 「从一段已有视频出发往外接」这个**机制/想法**在 `obvpm` 与 TimelineDirector 里都存在（本包 2026-10-16 阅读其公开说明与源码后知悉）；按「机制不受版权保护、表达受保护」的口径，本包**只取想法**，实现为独立编写。⚠️ 后续若有人想「照着它的实现改」，先读 §一·B |

### D. ✅ 运行时**可选**依赖：`Comfyui_Minimax_h3_latent_Upscaler`（MIT）

| 项 | 内容 |
|---|---|
| 上游 | `LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler`（本机 `custom_nodes/` 内，git remote 一手核对）；**LICENSE 首行 `MIT License`，`Copyright (c) 2026 LBH-123-AI`** |
| 权重出处 | `https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler` → 放 `ComfyUI/models/latent_upscale_models/` |
| 本包用它的方式 | `H3RelayLatentUpscale` = **适配层**，运行时从 ComfyUI 节点注册表取 `MinimaxH3LatentUpscaler3D` 的类并调用其 `execute()`（**不 import 其包内 `nodes/` 子包** —— 与 ComfyUI 根 `nodes` 同名，迟早撞车）；**零 patch、不改其源码、不复制其模型代码** |
| 模型路径 | **优先直接调用作者的 `scan_models()`** ⇒ 装了上游节点即**共用同一份权重文件与同一目录**，本包不另立目录、不做第二份拷贝 |
| 数学出处 | 分块拼接（两侧 replicate 帧填充 + 线性渐变权重 + 按累计权重归一）的**口径**与作者 `forward()` 内部一致，以免"换个实现换条缝"；本包重写为 CPU 可测函数（`relay_core.upscale_chunk_plan` / `temporal_tile_upscale`），未复制其神经网络结构代码 |
| 未装会怎样 | 本包**照常加载、其余 7 个节点功能不变**；只有使用 `H3RelayLatentUpscale` 时抛 RuntimeError，把仓库地址与 HF 权重地址写进报错里（不静默降级、不假成功） |
| 二次开发注意 | MIT 允许闭源/商用/再分发，**唯一义务 = 保留版权声明与许可文本**；若你把放大模型的实现复制进自己的发行版（本包**没有**），请随件附上作者的 LICENSE |

## 二、论文与公开算法（仅算法出处，无代码）

| 出处 | 用在哪 |
|---|---|
| Reinhard et al., *Color transfer between images*, IEEE CG&A 21(5):34–41, 2001 | `match_prev_stats` 的逐通道一阶+二阶矩匹配 |
| *WCT: Universal Style Transfer via Feature Transforms*, NeurIPS 2017（arXiv:1705.08086） | 同上（矩匹配族） |
| *TTC: Pathwise Test-Time Correction*（arXiv:2602.05871） | 同上（跨段统计纠偏的思路） |
| *SEINE*（arXiv:2310.20700）、*SynCoS*（arXiv:2503.08605）、*GLC-Diffusion*（arXiv:2501.05484）、FlowLong / Unified Long Video Inpainting（arXiv:2511.03272） | `blend` 掩码的窗形权重（重叠区由两个独立估计加权平均 ⇒ 权重取窗函数） |
| *Diffusion Forcing*（arXiv:2407.01392）、*SDEdit*（arXiv:2108.01073） | `ramp` 掩码：把连续掩码值读作「逐 token 噪声等级 / 按强度重绘」 |
| RePaint（arXiv:2201.09865） | 每步 `out = out·m + anchor·(1−m)` 的逐步回锚 |

## 三、copyleft 作品清单（含一次已消除的历史重合）

| 作品 / 依赖 | 许可 | 状态 |
|---|---|---|
| **ComfyUI（运行时宿主）** | **GPL-3.0** | ⚠️ **运行时 import，非复制**。仓库内不含其代码；但是本包唯一一条 copyleft 关系，且宿主无插件例外条款。**详见 §一·C**（含我方立场与未决事项）。2026-09-19 之前误记为 Apache-2.0，已更正 |
| `h3_drift.py`（改编自 **Contex-Loop**） | GPL-3.0（二次改编 ⇒ 传染性更强） | **❌ 未进入本包**。本包不含其任何代码、注释结构或派生表达；相关思路**未被采用** |
| **`comfyui-minimax-h3-audio-T8`** | **GPL-3.0-or-later** | ⚠️ **仅作用法佐证，未复制其代码**（证明掩码语义就是 ComfyUI 原生契约）。但**它本身是 copyleft**：2026-09-20 一手核对更正了本文件此前"MIT"的误记。想照它的实现改之前，先读 §一·B |
| **`ComfyUI-H3-Motion-Context`** | **GPL-3.0**（NikoDemon80） | ⚠️ **曾重合，已重写** —— `apply_relay` 锚位合成段在 v0.2.1–v0.5.0 公开历史中构成表达层重合，2026-09-19 整体重写。**详见 §一·B**（含范围核验、未决事项、历史未改写的原因） |

## 四、复核状态与待办（发布前必读）

| 项 | 状态 |
|---|---|
| 本包自身许可 | ✅ MIT（`LICENSE`） |
| **宿主 ComfyUI 许可** | ✅ **已一手核对 = GPL-3.0**（本机 `ComfyUI/LICENSE` 首行；无插件例外条款）。2026-09-19 更正了本文件此前误记的 Apache-2.0，披露见 §一·C |
| 依赖许可 | ✅ `torch`（BSD-3-Clause）/ `safetensors`（Apache-2.0）—— 均为宽松许可，与 MIT 兼容 |
| T8 许可 | ⚠️ **2026-09-20 一手核对更正 = GPL-3.0-or-later**（此前误记 MIT）。核对方式：仓库 `T8mars/comfyui-minimax-h3-audio-T8` 的 `LICENSE` 首行即 `SPDX-License-Identifier: GPL-3.0-or-later`（GitHub API 的 `license` 字段因含非标准头而只报 `NOASSERTION`，**要读 LICENSE 原文，不能只看 API 字段**）。处置：本包与其关系**仅为用法佐证、无代码复制**，机制不受版权保护 ⇒ 结论不变，但对外声明必须改正 |
| **Motion-Context 重合** | ✅ **已核验范围并重写**（§一·B）：重合仅 `apply_relay` 一段，2026-09-19 整体重写，差分验证通过。<br>⚠️ **未决**：派生方向未定（可能经已消失的 Apt_Preset 中转）；**尚未取得对方作者书面确认**。<br>**待办**：联系 NikoDemon80 说明情况。**在此完成前，请勿对外主张"本包与 GPL 无关"** |
| **Director 许可** | ✅ **2026-09-20 已一手核对 = Apache-2.0**（GitHub API `repos/AIMixer/ComfyUI_MiniMaxH3_Director/license` → `spdx_id = Apache-2.0`；旁证：第三方仓库的 NOTICE 亦记为 Apache-2.0）。<br>⇒ 随包附 [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) 与 §一 表署名**均正确**，`lowfreq_pull` / `_box_blur_hwc` **无需重写**。<br>（核对前本项曾标为"未复核、若为无许可则须重写"，现已解除。） |
| **一手核对方法（记下来，别再靠记忆）** | `gh api repos/<owner>/<repo>/license --jq .license.spdx_id` —— 快，但**遇到非标准许可文本会返回 `NOASSERTION`**（T8 就是这样被漏掉的）。<br>所以**凡 `NOASSERTION` 或关键结论，必须再读一次 LICENSE 原文**：`gh api repos/<owner>/<repo>/contents/LICENSE --jq .content \| base64 -d \| head`。<br>本机已装的包也可直接 `head -1 <包目录>/LICENSE`。 |

---

## 五、若你要基于本包二次开发

- 保留 `LICENSE` 与本文件；
- 你新增的代码自行决定许可，但**不得**因引入本包而把 GPL 代码带进来；
- 若你从本包反向借鉴了本仓库的设计，欢迎在 README 里注明——这不作强制要求。
