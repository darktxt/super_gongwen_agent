# super-gongwen-agent

一个面向中文公文写作场景的多轮写作 Agent。项目内置技能系统、材料读取工具、工作区状态管理、可观测性日志和终稿装配能力，所有决策均由llm做出，无固定流程引导。

## 项目特点

- 面向公文写作流程建模，支持提纲、正文、局部改写、润色、定稿等动作。
- 内置技能系统，区分主写作 skill 与修订 skill。
- 支持读取仓库根目录 `materials/` 下的 `.txt`、`.md`、`.json`、`.docx`、`.pdf` 材料。
- 使用工作区状态持久化会话，支持多轮追问、补充材料和版本沉淀。
- 终稿默认导出为 `.docx` 文档，便于直接在 Word/WPS 中继续修改和流转。
- 记录调试文件、运行事件与输出结果，便于定位模型行为与提示词效果。

## 仓库结构

```text
.
├─ api_gateway/          # LLM 客户端封装
├─ editorial_brain/      # 提示编排、动作协议、解析与质量门禁
├─ materials/            # 写作材料目录，content_access.py 默认从这里读取
├─ observability/        # 运行日志、事件与调试输出
├─ result_assembler/     # CLI 结果视图装配
├─ session_storage/      # 会话目录、事件流、产物保存
├─ skill_system/         # skill 加载、目录、执行与约束
├─ tool_runtime/         # 材料检索、读取、保存等工具
├─ utils/                # 序列化、时钟、session id
├─ workspace/            # 工作区状态、快照、patch 应用
├─ app.py                # 应用编排主入口
├─ config.py             # 配置读取
├─ main.py               # CLI 入口
└─ requirements.txt      # 运行依赖
```

## 架构说明

如果你想先了解项目整体设计，而不是直接从代码入口开始阅读，可以先看：

- [arch.md](./arch.md)：完整的架构设计说明，包含运行主链路、Editorial Brain 动作协议、workspace 状态模型、materials 读取边界、skill/tool 扩展点与当前实现取舍。

建议阅读顺序：

1. 先看本 README，了解项目定位和使用方式。
2. 再看 [arch.md](./arch.md)，建立整体架构认知。
3. 最后从 `main.py`、`app.py` 和各子模块进入具体实现。

## 环境要求

- Python 3.11+

安装依赖：

```bash
pip install -r requirements.txt
```

## 配置

项目通过环境变量读取模型配置，不内置任何默认凭据。启动时会自动尝试读取本地 `.env` 文件。

可参考根目录 `.env.example`：

```bash
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
OPENAI_TIMEOUT=300
OPENAI_TEMPERATURE=
OPENAI_AGENTS_ENABLE_TRACING=1
OPENAI_AGENTS_OUTPUT_MODE=auto
SUPER_GONGWEN_RUNTIME=agents_sdk
SUPER_GONGWEN_HOME=
```

说明：

- 优先级上，系统已存在的环境变量高于 `.env` 中的同名配置。
- 默认会优先读取 `--base-dir` 指向目录下的 `.env`，其次读取仓库根目录 `.env`。
- `OPENAI_API_KEY`：必填，OpenAI 兼容接口的密钥。
- `OPENAI_BASE_URL`：可选，自定义兼容网关时填写；直连官方接口可留空。
- `OPENAI_MODEL`：必填，运行 `run_turn` 时使用的模型名。
- `OPENAI_AGENTS_ENABLE_TRACING`：可选，是否启用 Agents SDK tracing，默认开启。
- `OPENAI_AGENTS_OUTPUT_MODE`：可选，支持 `auto`、`structured`、`text`。默认 `auto`。
- `SUPER_GONGWEN_RUNTIME`：可选，运行时后端，支持 `agents_sdk` 与 `legacy`，默认 `agents_sdk`。
- `SUPER_GONGWEN_HOME`：可选，指定运行态数据目录；默认写入当前目录下的 `.super_gongwen/`。

## 运行时说明

当前仓库默认使用 OpenAI Agents SDK 作为运行时编排内核，但仍保留现有领域层：

- `workspace/` 继续作为公文写作状态的事实源。
- `tool_runtime/` 继续负责受控材料读取，读取边界仍限制在 `materials/`。
- `session_storage/`、`result_assembler/`、终稿导出与 GUI/CLI 入口继续沿用现有工程层。

运行时切换策略：

- `SUPER_GONGWEN_RUNTIME=agents_sdk`：启用新的 Agents SDK 运行时。
- `SUPER_GONGWEN_RUNTIME=legacy`：回退到原有 `chat.completions + 自定义解析` 运行时。

Agents SDK 输出模式：

- `OPENAI_AGENTS_OUTPUT_MODE=auto`：默认策略；若配置了 `OPENAI_BASE_URL`，自动切到 `text` 模式，否则默认走 `structured`。
- `OPENAI_AGENTS_OUTPUT_MODE=structured`：优先使用 SDK 结构化输出，适合官方直连或稳定支持 schema 的供应商。
- `OPENAI_AGENTS_OUTPUT_MODE=text`：由 Agents SDK 负责运行时编排，但最终文本统一交给项目内 JSON 解析器处理，适合 `<think> + json`、代码块 JSON、混合文本等兼容网关场景。

兼容性说明：

- `create_app()`、`bootstrap()`、`run_turn()` 的对外调用语义保持兼容。
- 当前 Agents SDK 运行时会额外把运行时 session 持久化到 `.super_gongwen/agents_runtime/sessions.sqlite3`，但不会替代 `workspace.json`。
- `OPENAI_BASE_URL` 仍可用于接入 OpenAI 兼容网关；实际是否完全兼容取决于目标网关对 Chat Completions 语义的支持程度。
- 对兼容网关，运行时会优先采用 `text` 模式，并在需要时自动执行一次 JSON 修复回合，以处理“只有分析没有 JSON”这类非标准输出。

## 材料目录

`tool_runtime/content_access.py` 会优先从仓库根目录的 `materials/` 读取材料。

支持的格式：

- `.txt`
- `.md`
- `.json`
- `.docx`
- `.pdf`

说明：

- 相对路径默认会被解释为 `materials/` 下的路径。
- `materials/xxx` 这种显式路径也支持。
- 出于安全限制，只允许读取 `materials/` 目录内部文件，不会越界到其他目录。

示例结构：

```text
materials/
├─ 通知原文.txt
├─ 会议纪要.md
├─ 数据口径.json
├─ 领导讲话.docx
└─ 政策汇编.pdf
```

## 快速开始

1. 准备依赖并设置环境变量。
2. 将写作材料放入仓库根目录的 `materials/`。
3. 通过 `main.py` 初始化或发起写作。

初始化会话：

```bash
python main.py --base-dir .
```

直接发起一轮写作：

```bash
python main.py --base-dir . --user-input "请根据 materials 中的材料起草一篇关于春季安全生产检查工作的部署讲话稿"
```

继续同一会话：

```bash
python main.py --base-dir . --session-id <已有会话ID> --user-input "补充强调责任传导和隐患闭环整改"
```

## 运行产物

默认会在 `.super_gongwen/sessions/<session_id>/` 下生成：

- `workspace.json`：当前工作区状态
- `events.jsonl`：运行事件流
- `debug/`：每轮上下文、请求、响应、step 等调试文件
- `outputs/final.docx`：终稿 Word 文档，作为默认交付结果
- `outputs/final.md`：终稿 Markdown 伴随文件，便于内部兼容和二次处理
- `versions/`：中间版本产物
- `tool_results/`：工具调用结果

## 终稿输出说明

终稿在通过质量门禁后，会默认导出为 `docx` 文档。

当前导出会应用一套适合公文写作场景的基础排版规则，包括：

- A4 页面
- 常见公文页边距
- 标题居中
- 正文字号、行距和首行缩进的统一处理

这套样式是一个工程化基线，便于直接打开、审阅和继续修改；如果你所在单位有更细的版式规范，也可以在导出的 Word 文档上继续微调。

## 终端交互说明

CLI 默认只展示和创作有关的内容，例如：

- 当前处于第几轮
- 本轮使用的 action
- 当前主写作 skill 和修订 skill
- LLM 本轮已输出的内容评价、语言评价、主要问题与待补缺口
- 需要补充的写作问题
- 提纲环节的提纲内容
- 正文环节的正文内容
- 读材环节的具体动作和涉及材料名称
- 已生成的终稿文本
- 最终 Word 文档保存位置

调试文件、事件流和其他运行细节仍然会照常写入会话目录，但不会在正常终端交互中展开显示。

默认情况下，结构化运行日志不会输出到终端。如果确实需要在本地排查运行过程，可显式设置：

```bash
SUPER_GONGWEN_CONSOLE_LOG=1
```

这样会重新开启控制台结构化日志输出，但不会影响会话目录中的调试文件和事件落盘。

## GUI 图形界面

项目现已提供一个轻量本地 GUI 入口，作为现有 CLI 的图形化封装，不替代 CLI，也不新增业务能力。

启动方式：

```bash
python gui_main.py
```

GUI 当前支持：

- 在左上角“设置”中编辑 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`
- 新建会话或打开已有会话
- 提交一轮写作需求或补充信息
- 展示当前进度、待补充问题、当前结果与终稿输出路径

GUI 当前不支持：

- 连接测试
- 材料上传或材料管理
- 会话重命名、删除、搜索
- 独立调试面板

GUI 与 CLI 复用同一套配置、会话目录与核心执行链路：

- 配置仍来自项目根目录 `.env`
- 会话仍保存在 `.super_gongwen/sessions/`
- 核心处理仍通过 `create_app()`、`bootstrap()`、`run_turn()` 完成

## 设计说明

- 当前仓库使用平铺目录结构，直接以 `main.py` 作为唯一 CLI 入口。
- `content_access.py` 优先锚定仓库根目录 `materials/`，避免因为启动目录不同而读不到材料。
- PDF 读取优先使用 `pypdf`，提取失败时再回退到内置的轻量解析逻辑。
- `.docx` 优先使用 `python-docx`，未安装时使用降级解析逻辑。
- 如果未配置模型参数，`bootstrap` 可正常运行，但 `run_turn` 会返回未配置模型的错误，这是预期行为。

## 后续建议

- 为不同公文类型沉淀更多主写作 skill。
- 进一步优化tool，目前还很粗糙，至少要重写read，增加web-search。
- 优化cli，目前纯debug。

## 欢迎一起改进

这个项目现在更像一个持续演进中的公文写作 Agent 基线，而不是已经封闭完成的成品。

欢迎大家一起改进项目，包括但不限于：

- 补充新的公文类型 skill 和修订 skill
- 优化 prompt、动作协议与质量门禁
- 完善材料读取、检索与工具体系
- 改进 CLI 交互、文档和可观测性
- 修复问题、清理代码、提升工程质量

如果你在使用中发现问题、想到更好的设计，或者愿意一起把它打磨得更稳更好用，都非常欢迎提交 issue 或 PR。
