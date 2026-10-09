# avp — 不露脸双语科普视频管线

把一句选题做成：**带出处的研究资料 → 双语脚本（可交叉审稿）→ Q 版人物插画 + 动态图解 → 多角色 AI 配音 → 英文横屏长片（YouTube）+ 中文竖屏分集短片（抖音/视频号/小红书）→ 发布文案 + 自动质检**。

为「中国古代经济思想史」栏目设计，但所有栏目相关的东西都在一个 `series.toml`（栏目圣经）里，换一份就是另一个栏目。

## 流程

```
 选题 brief
   │  research   联网检索 → research.md（史实、原文、争议、数据，附来源）
   │  outline    分幕节拍表 → outline.md（钩子、三幕、图解点、笑点）
   │  script     双语主脚本 → script.json + script.md（每条史实带脚注 [cN]）
   ├──── 审稿关卡：把 script.md 交给其他模型核查 → avp revise 改稿 → avp approve
   │  cast       人物设定图（栏目级，跨集复用，保证形象一致）
   │  art        插画（附人物设定图做参考，16:9 母版）
   │  motion     图生视频：把标了 motion 的插画做成约 5 秒纸偶动画（可选，providers.video；失败或欠费时自动退回静帧，不会卡住整集）
   │  voice      每句台词 × 每种语言一个 WAV（旁白 + 角色不同声音）
   │  compose    每个输出：推拉镜头/竖屏重构图、动态图解、烧录字幕、转场、配乐闪避、响度标准化
   │  publish    标题、简介、YouTube 章节、参考资料、话题标签
   └  qc         时长/音轨/响度/削波检查 + 抽帧总览图
```

每一步的产物都是可编辑的文件；重跑时只重新生成变化了的部分（按内容指纹判断）。

## 安装

需要 Python 3.10+、ffmpeg、Chromium（渲染图解用）。macOS / Linux / Windows 都可以。

```bash
cd video-pipeline
pip install -e '.[anthropic,gemini,dev]'   # 只装你用得到的：anthropic = Claude，gemini = Gemini
playwright install chromium        # 或设置 AVP_CHROMIUM=/path/to/chrome
export GEMINI_API_KEY=...          # 用哪家就配哪家的 key，见 .env.example；本地模型不需要
```

## 用法

```bash
avp init  channels/econ                         # 用栏目模板建一个栏目，然后编辑 series.toml
avp new   channels/econ "管仲与盐铁专营：齐国如何不加税而富国（官山海）" --slug guanzhong
avp run   channels/econ/episodes/01-guanzhong   # 跑到审稿关卡自动停下

# 审稿：把 script.md 整份交给其他模型（文档顶部附有核查提示词），把意见汇总到 notes.md
avp revise  channels/econ/episodes/01-guanzhong @notes.md   # 按意见改稿（旧版本存进 revisions/）
avp approve channels/econ/episodes/01-guanzhong
avp run     channels/econ/episodes/01-guanzhong              # 生产全部成片

avp status channels/econ
avp check  channels/econ/episodes/01-guanzhong   # 手改或别的模型改过 script.json 后：校验并重新生成 script.md
avp run    channels/econ/episodes/01-guanzhong --skip-review --draft --provider mock   # 零成本、低清预览结构和节奏
```

常用选项：

| 选项 | 作用 |
|---|---|
| `--provider mock` | 全部离线（占位图、合成音），先验证流程和节奏，不花 API 费用 |
| `--only art,voice` / `--from compose` / `--to script` | 只跑部分阶段 |
| `--scenes s03,s07 --force` | 只重做某几个镜头 |
| `--outputs youtube_en` | 只渲染某个输出 |
| `--skip-review` | 不审稿先出草稿片（看效果用） |
| `--draft` | 540p 低清快速预览（正式清晰度单独缓存，不冲突） |
| `render.animate` | `marked`（默认，只给写了 motion 的镜头做动画，省钱）或 `all`（每张插画都动） |
| `--provider anthropic` | 研究和写稿用 Claude（画图、配音仍按 series.toml 设置） |

## 换模型 / 完全本地运行

管线本身就是一个普通的 Python 命令行程序，跑在你自己的电脑上（ffmpeg + Chromium 本地渲染）。模型调用全部通过可替换的「提供方」接入，**每个环节可以单独换**，不绑定任何一家厂商，也不依赖云端：

| 环节 | 可选提供方 |
|---|---|
| `research` 研究 | `anthropic`（Claude + 联网搜索，附来源）· `gemini`（Google 搜索溯源）· `openai`（任意兼容接口；无联网时会在资料顶部标注"需人工核实"）· 或者你自己写 `research.md` |
| `llm` 大纲/脚本/改稿 | `anthropic`（Claude，官方 SDK，结构化输出）· `gemini` · `openai` = 任何 OpenAI 兼容接口：DeepSeek、通义千问、Kimi、GLM、OpenRouter、本地 **Ollama / LM Studio / vLLM / llama.cpp** |
| `image` 插画 | `gemini`（支持人物参考图，形象最稳）· `openai`（gpt-image 等）· `sdwebui`（本地 **Stable Diffusion WebUI / Forge**）|
| `video` 插画动起来 | `wan`（万相图生视频，百炼）· 留空 = 只用静帧推拉镜头 |
| `tts` 配音 | `gemini` · `openai`（OpenAI TTS 或本地 **Kokoro-FastAPI** 等）· `command`（任意本地程序：**Piper、CosyVoice、F5-TTS、sherpa-onnx**…）|
| 全部 | `mock`：离线占位，零成本测试流程 |

在 `series.toml` 的 `[providers]` 里切换，对应的地址、模型名写在 `[openai.llm]` / `[openai.image]` / `[openai.tts]` / `[sdwebui]` / `[command_tts]`，模板里有各家的填写示例。例如完全离线：

```toml
[providers]
llm = "openai"      # 本地 Ollama 跑 Qwen
image = "sdwebui"   # 本地 SD WebUI
tts = "command"     # 本地 Piper / CosyVoice

[openai.llm]
base_url = "http://localhost:11434/v1"
model = "qwen2.5:32b"
```

所有中间产物都是普通文件（Markdown、JSON、PNG、WAV、MP4），任何模型或人都可以接手其中一步：比如用别的工具画图后直接放进 `assets/<镜头>/image.png`，管线会照常合成。

> 渲染耗时参考：4 核 CPU、540p 下，2 分钟成片约 100 秒；1080p 的 10 分钟双语成片预计需要 30–60 分钟，适合放着跑。

## 产物

```
episodes/01-guanzhong/
  research.md  outline.md  script.json  script.md  revisions/
  assets/s05/image.png  assets/s05/motion.mp4  assets/s05/en_01.wav  assets/s05/zh_01.wav
  out/youtube_en.mp4  youtube_en.srt  youtube_en.publish.md  youtube_en.sheet.jpg
  out/douyin_zh_part1.mp4 … part3.mp4  (+ .srt / .publish.md / .sheet.jpg)
  out/cover_en.jpg  cover_zh.jpg  qc.md
```

## 脚本格式（script.json）

一份主脚本驱动所有输出：画面共用，每句台词同时有 `zh` 和 `en`（是两份各自地道的稿子，不是互译）。

- `scenes[].kind`：`illustration`（AI 插画 + 推拉镜头；`motion` 写一句“画面里什么在动”，有动画时先播动画再定格）或 `diagram`（动态图解）
- 图解模板：`chapter` 幕标题 · `quote` 古文原句 + 译文 · `flow` 流程/资金流 · `compare` 正反方对比 · `timeline` 时间线 · `stat` 关键数字
- `scenes[].part`：中文竖屏版按这个拆成几集短片（在每幕结尾的悬念处切）
- `lines[].speaker`：`narrator` 或角色 id；角色台词在字幕里自动加「管仲：」前缀
- `claims`：每条史实的出处、原文和可信度（established / debated / traditional）

## 设计取舍

- **为什么要审稿关卡**：YouTube 2025 年起加强了对「低质量、模板化 AI 内容」的限制。不露脸本身不是问题，原创研究、观点和编辑投入才是。所以脚本必须经人审过才进入生产，简介里列出参考资料，并注明 AI 辅助制作。
- **图解为什么自己渲染**：图解是 HTML 模板 + 纯函数 `renderAt(t)`，用无头 Chromium 逐帧截图后送进 ffmpeg，结果确定可复现（与 HyperFrames 思路相同，模板少、稳定、易于 LLM 填参数）。以后需要更自由的动效，可以换成 HyperFrames 或 Manim 渲染器。
- **竖屏怎么做**：插画只画一次 16:9 母版，竖屏版取中间方形区域放在模糊背景上（提示词要求主体居中），图解则原生按竖屏排版。

## 测试

```bash
pytest            # 单元测试 + 一个用 mock 提供方的端到端渲染测试（需要 ffmpeg 和 Chromium）
```
