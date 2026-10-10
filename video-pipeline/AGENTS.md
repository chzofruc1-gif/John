# AGENTS.md — 给其他 agent / 模型的接入说明

这份文件写给要驱动这条管线的 agent（Claude Code、Codex、Cursor、Cline、自研脚本……）。人看 README.md，agent 看这里。

## 一句话

`avp` 把一句选题做成双语视频：研究 → 大纲 → 双语脚本 →（审稿关卡）→ 人物设定图 → 插画 → 图生视频 → 配音 → 合成 → 发布文案 → 质检。每一步的产物都是普通文件，任何 agent 都可以接手其中一步。

## 三种接入方式（功能完全一样）

| 方式 | 用法 | 适合 |
|---|---|---|
| **MCP 服务** | `avp mcp`（stdio） | Claude Code / Claude Desktop / Cursor 等支持 MCP 的客户端 |
| **命令行 + JSON** | `avp --json <命令> ...`，结果打印到 stdout，日志在 stderr | 任何能跑 shell 的 agent |
| **Python API** | `from avp import api` | 自己写编排脚本 |

三者都建立在 `avp/api.py` 上，返回的都是同一份 JSON 结构。

### 注册 MCP

```bash
claude mcp add avp -- avp mcp                       # Claude Code（在 video-pipeline 目录里执行）
```

Claude Desktop / Cursor 见 `integrations/mcp.json`。工具列表：`stages` `status` `init_series` `new_episode` `plan` `run` `get_script` `put_script` `script_schema` `check` `revise` `approve` `adopt` `list_outputs` `doctor`。

## 标准流程

```
new_episode(series_dir, brief)                     → episode_dir
run(episode_dir, to_stage="script")                → 研究、大纲、双语脚本（付费：LLM）
get_script / put_script / check                    → 审稿、事实核查、改稿（可以由别的模型做）
approve(episode_dir)                               → 只在人或核查流程确认后调用
plan(episode_dir)                                  → 预演：要生成什么、需要多少次付费调用
run(episode_dir, allow_paid=true)                  → 生产：画图、动画、配音、合成、质检
list_outputs(episode_dir)                          → 成片路径、字幕、发布文案、质检结论
```

`run` 的返回值 `status`：
- `done`：完成。
- `paused`：停在审稿关卡，脚本还没 approve。
- `failed`：出错，`message` 写着原因。
- `refused`：付费调用数超过上限，没有开始。
- `planned`：只做了预演。

## 规矩（务必遵守）

1. **花钱前先问人。** 付费的阶段有 research / outline / script / cast / art / motion / voice，用的是 LLM、画图、视频和配音模型；compose / publish / qc 在本地跑，不花钱。
   - 先 `plan`，把 `paid_calls` 和各阶段数量给用户看，用户同意后再 `run(allow_paid=true)`。
   - 用 CLI 时可以加 `--max-paid N` 给付费调用数设上限。
   - MCP 的 `run` 默认拒绝任何付费调用。
2. **不要替人 approve。** 审稿关卡的作用是让人或其他模型先核查史实。
   - `approve` 只在用户明确同意后调用。
   - `put_script` 写入改动过的脚本时，会自动取消批准状态。
3. **只改需要改的。** 管线按内容指纹做增量：没变的镜头不会重新生成。
   - 想重做某几个镜头，用 `scenes=["s04","s08"]` 加 `force=true`。不要删整个目录。
4. **动画失败不会卡住整集。** 某个镜头生成失败或账户余额不足时，这个镜头自动退回静帧，`counts.skipped` 会记下来。

## 交接点：哪些文件可以由别的 agent 直接产出

| 文件 | 说明 | 格式 |
|---|---|---|
| `episodes/<NN-slug>/brief.md` | 选题说明 | 自由文本 |
| `research.md` | 研究资料（带出处） | Markdown；存在就不会重新研究 |
| `outline.md` | 分幕节拍表 | Markdown |
| `script.json` | **核心**：双语主脚本 | 见 `schemas/script.schema.json` 或 `avp schema`；写完调 `check` |
| `assets/<镜头>/image.png` | 插画（16:9 母版） | PNG；自己画的放进来即可 |
| `assets/<镜头>/motion.mp4` | 插画的动画版 | MP4，约 5 秒，第一帧 = 插画 |
| `assets/<镜头>/<zh|en>_NN.wav` | 每句台词的配音 | WAV |
| `series.toml` | 栏目圣经：风格、人物、声音、输出规格、提供方 | TOML |

手放进来的素材要**认领**：调一次 `adopt(episode_dir)`（命令行：`avp adopt <集目录>`）。认领时不调用任何模型，只记录指纹，之后的运行会保留这些素材，不会重新生成。缺的那些素材照常由管线生成。

### script.json 要点

- `scenes[].kind`：
  - `illustration`：AI 插画，需要 `visual_prompt`，可选 `motion`（一句话描述画面里什么在动）。
  - `diagram`：动态图解，模板有 chapter / quote / flow / compare / timeline / stat。
- `scenes[].lines[]`：`speaker` 填 `narrator` 或 cast id，`text` 同时写 `zh` 和 `en`，两种语言各自地道，不是互译。
- `scenes[].part`：中文竖屏版按这个字段拆成几集短片。
- `claims[]`：每条史实都要写 `source`、`quote`（原文）和 `confidence`（established / debated / traditional）。在台词里用 `[cN]` 引用。

## 换模型 / 加自己的模型（插件）

在 `series.toml` 的 `[providers]` 里，每个环节都可以填一个 Python 类：

```toml
[providers]
image = "my_models.flux_local:FluxImage"     # 模块:类名，模块要能被 import（在 PYTHONPATH 里）

[plugins.flux]                                # 插件自己的参数，从 config.plugins["flux"] 读
url = "http://127.0.0.1:8188"
```

```python
class FluxImage:
    def __init__(self, config, kind):          # config = SeriesConfig，kind = "image"
        self.name, self.model = "flux", "flux-dev"
        self.opts = config.plugins.get("flux", {})
    def generate(self, prompt, aspect, out_path, references): ...   # 写一个 PNG 到 out_path
```

各环节的方法签名见 `avp/providers/base.py`：

| 环节 | 方法 |
|---|---|
| research / llm | `research(prompt)`、`text(task, prompt)`、`json(task, prompt, schema, hints)` |
| image | `generate(prompt, aspect, out_path, references)` |
| tts | `synthesize(text, voice, style, language, out_path)` |
| video | `animate(image, prompt, out_path)` |

内置的提供方：
- anthropic、gemini、openai（任何 OpenAI 兼容接口，含本地 Ollama / LM Studio）。
- qwen、cosyvoice、wan（阿里云百炼）。
- sdwebui（本地 Stable Diffusion）。
- command（任意本地 TTS 程序）。
- mock（离线、免费，用来测流程）。

## 自检

```bash
avp doctor channels/econ-history     # ffmpeg、Chromium、中文字体、各提供方需要的 key
avp --json status channels/econ-history
pytest                               # 全部离线，不花钱
```
