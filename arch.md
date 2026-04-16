# 架构设计说明

## 1. 项目定位

`super-gongwen-agent` 是一个面向中文公文写作场景的多轮写作 Agent。  
它不是“固定流程脚本 + 模板填空”，而是把写作过程拆成一组可观察、可持久化、可扩展的运行时能力：

- 使用 LLM 作为“编辑中枢”做决策；
- 使用 `workspace` 保存多轮写作状态；
- 使用 `tool_runtime` 在受控边界内读取材料；
- 使用 `skill_system` 注入不同公文类型与润色策略；
- 使用 `session_storage` 与 `observability` 保存全过程产物与调试信息。

当前代码以单进程 CLI 方式运行，入口简单，但内部已经具备比较完整的运行时分层。

## 2. 设计目标

本项目当前架构主要围绕以下目标展开：

1. 支持多轮写作，而不是一次性生成。
2. 让材料检索、提纲、草稿、润色、定稿都能被状态化保存。
3. 让 LLM 决策可被约束，而不是任意输出自由文本。
4. 让 skill 和工具可以继续扩展，而不破坏主链路。
5. 让每轮运行都有事件、调试文件和中间版本，便于定位问题。

## 3. 顶层结构

```text
main.py                 CLI 入口
app.py                  应用编排中心（应用装配与运行时协调）
config.py               环境配置加载
runtime_factory.py      运行时装配工厂

agents_runtime/         OpenAI Agents SDK 运行时适配层
editorial_brain/        上下文拼装、动作协议、LLM 调用、质量门禁
workspace/              工作区状态模型、快照、patch 应用、持久化
tool_runtime/           材料读取与工具执行框架
skill_system/           skill 定义加载、选择校验、执行
session_storage/        会话目录、事件流、版本与最终产物保存
observability/          结构化日志、事件写入、调试输出、指标
result_assembler/       将运行结果组装为 CLI 展示文本
materials/              用户提供的写作材料目录
utils/                  时间、序列化、session id 等基础工具
```

其中最核心的编排节点仍是 [app.py](./app.py)。  
但在新的分层下，它不再独占全部 LLM 运行时细节，而是主要负责：

- 应用装配与运行时选择；
- 单轮执行主链路协调；
- 事件记录、错误归一与结果落盘。

与之对应：

- `runtime_factory.py` 负责装配默认 Agents SDK 运行时；
- `agents_runtime/` 负责 OpenAI Agents SDK 的初始化、session 接入、结构化输出与 tracing 适配。

## 4. 核心分层

### 4.1 CLI 与应用装配层

- [main.py](./main.py) 负责解析命令行参数。
- `create_app()` 负责把配置、LLM、skill、工具、workspace、质量门禁等对象装配成应用实例。
- `bootstrap()` 用于初始化会话目录和工作区。
- `run_turn()` 用于执行一次“用户输入 -> 多轮内部推理 -> 输出结果”的完整回合。

这一层的目标是保持入口平整，避免业务逻辑分散在 CLI 中。

### 4.2 Editorial Brain 层

`editorial_brain/` 负责把“当前工作区状态”转换为 LLM 可消费的上下文，并把模型输出解析为受约束的动作协议。

关键模块：

- [editorial_brain/context_compiler.py](./editorial_brain/context_compiler.py)
  - 将 `WorkspaceSnapshot` 编译为 system prompt、user prompt 和结构化上下文块。
- [editorial_brain/contracts_core.py](./editorial_brain/contracts_core.py)
  - 定义允许的动作、输出结构、字段归一逻辑以及 `BrainStepResult`。
- [editorial_brain/output_parser.py](./editorial_brain/output_parser.py)
  - 负责解析模型输出，并兼容 `<think> + json`、代码块 JSON 等变体。
- [editorial_brain/runtime_contracts.py](./editorial_brain/runtime_contracts.py)
  - 定义运行时请求、响应与错误契约，供应用层与运行时适配层共享。
- [editorial_brain/quality_gate_v2.py](./editorial_brain/quality_gate_v2.py)
  - 在 `done` 前做最终质量校验，防止明显不完整的结果直接定稿。

这个分层的核心思想是：  
LLM 不是直接产出最终公文，而是先产出“下一步要做什么”的结构化决策。

当前动作协议由 [editorial_brain/contracts_core.py](./editorial_brain/contracts_core.py) 统一定义和校验。  
从架构角度看，这些 action 可以分成两类：

- 控制类 action：`load_skill`、`read_materials`、`ask_user`
- 写作类 action：`build_outline`、`write_draft`、`write_section`、`revise_draft`、`polish_language`、`finalize`

其中几个关键写作 action 的职责如下：

- `build_outline`
  - 生成提纲文本或结构化章节列表，回写到 `OutlineArtifact`，用于把“写什么”先稳定下来。
- `write_draft`
  - 生成整篇草稿正文，通常在已有题材、约束和提纲后产出一版完整基稿，回写到 `DraftArtifact.full_text`。
- `write_section`
  - 只生成或替换某个章节的内容，必须带 `section_id` 和 `section_text`。
  - 这个动作适合局部补写、按章节迭代或针对某一段重点强化，不必每次重写整稿。
- `revise_draft`
  - 对现有整稿做结构或内容层面的再加工，产出一版新的全文修订稿。
- `polish_language`
  - 在整体内容基本稳定后，进一步做语言层面的润色、压缩空话、增强表达力度。
- `finalize`
  - 产出最终交付文本，并在进入最终保存前接受 `QualityGate` 校验。

这套协议有两个重要作用：

1. 让模型输出从“任意自然语言”收敛为“有限动作 + 受约束载荷”。
2. 让 `app.py` 可以根据 action 明确决定是调工具、补状态、局部写作，还是直接进入定稿流程。

### 4.3 Workspace 状态层

`workspace/` 是项目的长期状态中心。  
它把公文写作过程拆成多个显式 artifact，而不是只保留一段历史对话。

关键模块：

- [workspace/models.py](./workspace/models.py)
  - 定义 `WorkspaceState` 及其子状态。
- [workspace/patcher.py](./workspace/patcher.py)
  - 负责把用户输入、工具结果、skill 结果、模型 patch 合并回工作区。
- [workspace/store.py](./workspace/store.py)
  - 负责 `workspace.json` 的读写与快照生成。

当前工作区主要包含以下几类状态：

- 指令类：`DirectiveLedger`
- 写作种子：`SeedArtifact`
- 激活技能：`ActiveSkillsState`
- 材料目录与检索记录：`MaterialCatalog`、`RetrievedMaterialsState`
- 证据板：`EvidenceBoard`
- 提纲：`OutlineArtifact`
- 草稿：`DraftArtifact`
- 自审结果：`SelfReview`
- 修订历史：`RevisionHistoryEntry`
- 调试态：最近一轮运行摘要、错误、上下文文件索引等

这样做的好处是：

- 状态显式，可调试；
- 多轮补充材料时不会丢上下文；
- 后续可以更容易替换 prompt、tool、skill，而不是推翻整个状态模型。

### 4.4 Tool Runtime 层

`tool_runtime/` 为 LLM 提供受控工具能力，目前重点服务于材料检索与内容读取。

关键模块：

- [tool_runtime/registry.py](./tool_runtime/registry.py)
  - 注册默认工具：`search`、`list`、`read`、`grep`、`diff`、`save`、`add_info`。
- [tool_runtime/executor.py](./tool_runtime/executor.py)
  - 批量执行工具调用，必要时把大结果落地为引用。
- [tool_runtime/content_access.py](./tool_runtime/content_access.py)
  - 真正负责材料目录解析、文件遍历、文本读取、PDF/DOCX 提取。

当前工具层的设计重点不是“联网搜索”，而是“可靠读取本地材料库”。

### 4.5 Skill System 层

`skill_system/` 用于描述不同公文类型与修订策略，避免把所有提示词都塞进主 prompt。

关键模块：

- [skill_system/catalog.py](./skill_system/catalog.py)
  - 加载并索引 skill JSON。
- [skill_system/guard.py](./skill_system/guard.py)
  - 校验模型挑选的 skill 是否合法。
- [skill_system/tool.py](./skill_system/tool.py)
  - 执行 skill，把 skill 约束与写作要求写回工作区。

当前 skill 分两类：

- `primary`：主写作类型，如部署讲话、实施方案、工作报告；
- `revision`：修订策略，如压缩空话、补数据案例、强化措施表达。

skill 的作用不是直接生成全文，而是改变后续写作约束和上下文。

### 4.6 持久化与可观测性层

关键模块：

- [session_storage/history.py](./session_storage/history.py)
  - 初始化会话目录，写入事件、版本、中间产物、最终输出。
- [session_storage/paths.py](./session_storage/paths.py)
  - 统一生成会话路径。
- [observability/events.py](./observability/events.py)
  - 事件清洗与调试 JSON 输出。
- [observability/logger.py](./observability/logger.py)
  - 结构化日志。
- [observability/metrics.py](./observability/metrics.py)
  - 进程内简单指标计数。

默认会话目录结构大致如下：

```text
.super_gongwen/
└── sessions/
    └── <session_id>/
        ├── workspace.json
        ├── events.jsonl
        ├── outputs/
        │   └── final_output.md
        ├── versions/
        ├── tool_results/
        └── debug/
```

## 5. 端到端运行链路

一次 `run_turn()` 的主链路可概括为：

1. 读取或创建当前会话的 `WorkspaceState`。
2. 把本轮用户输入写入工作区。
3. 从工作区生成快照，并由 `ContextCompiler` 编译成模型上下文。
4. 调用 Agents SDK 运行时请求模型。
5. 将模型输出解析为 `BrainStepResult`。
6. 若动作为 `load_skill`，则通过 `SkillTool` 加载 skill。
7. 若动作为材料读取相关操作，则通过 `ToolExecutor` 调用工具。
8. 将技能结果、工具结果或模型 patch 回写到工作区。
9. 若动作为 `done`，则先通过 `QualityGate`，通过后保存最终输出。
10. 全程记录事件、调试文件、版本文件，并持久化 `workspace.json`。

可以用下面这张简化图理解：

```text
User Input
   |
   v
main.py
   |
   v
SuperGongwenApp.run_turn()
   |
   +--> WorkspaceStore / WorkspacePatcher
   |
   +--> ContextCompiler
   |
   +--> AgentsSdkBrainRunner
   |
   +--> SkillTool / ToolExecutor
   |
   +--> QualityGate
   |
   +--> SessionStorage / Observability
   |
   v
Final Output / Follow-up Question
```

## 6. materials 目录与安全边界

本项目当前把材料读取严格限制在仓库根目录的 `materials/` 下。

相关设计点：

- [tool_runtime/content_access.py](./tool_runtime/content_access.py) 会优先解析项目根目录下的 `materials/`。
- 支持读取 `.txt`、`.md`、`.json`、`.docx`、`.pdf`。
- 对相对路径和显式 `materials/...` 路径都会做归一化。
- 所有读取路径都会执行 `relative_to(materials_root)` 检查，阻止越界访问。

这意味着：

- 模型和工具不能随意读整个仓库；
- 材料输入边界是清晰的；
- README、代码和目录约定保持一致。

这也是当前项目最重要的安全约束之一。

## 7. 运行时契约设计

当前项目已收敛为单一的 Agents SDK 运行时，但仍保留一层轻量运行时契约：

- `LLMRequest`
- `LLMResponse`
- `BrainRunResult`
- `BrainRunError`

这些契约位于 [editorial_brain/runtime_contracts.py](./editorial_brain/runtime_contracts.py)，设计意图有两点：

1. 让 `app.py`、`output_parser.py` 与运行时适配层通过稳定对象交互，而不是直接耦合具体 SDK 返回结构；
2. 在缺配置、解析失败、修复回合等路径上保持统一的错误与调试载荷格式。

当前唯一正式运行时为 [agents_runtime/brain_runner.py](./agents_runtime/brain_runner.py)：

- 通过 OpenAI Agents SDK 驱动单轮决策；
- 优先使用 structured output；
- 在兼容网关场景下支持 `text` 模式、`<think> + json` 解析与单次 JSON 修复回合；
- 通过 SQLite session 记录 SDK 侧会话状态，但不替代 `workspace.json`。

这样做的目标不是放弃原有领域建模，而是把底层运行时编排统一交给更成熟的 SDK，把自研精力集中在公文写作领域能力上。

## 8. 为什么采用“状态驱动”而不是“纯对话驱动”

如果只保留聊天记录，公文场景很容易出现以下问题：

- 用户补充要求后，旧约束被模型遗忘；
- 材料检索结果难以复用；
- 提纲、草稿、自审之间无法形成显式衔接；
- 难以判断模型到底是“没看材料”还是“看了但没用好”。

当前架构通过 `workspace` 把这些中间件状态实体化，解决的是“可持续写作”问题，而不是单轮问答问题。

## 9. 扩展点

当前项目已经预留出较清晰的扩展位置：

### 9.1 新增公文类型

在 [skill_system/skills/primary](./skill_system/skills/primary) 下新增 JSON skill，即可扩展新的主写作类型。

### 9.2 新增修订策略

在 [skill_system/skills/revision](./skill_system/skills/revision) 下新增 JSON skill，即可扩展新的润色或改写能力。

### 9.3 新增工具

在 `tool_runtime/tools/` 新增工具实现后，补充 `ToolRegistry.build_default()` 注册即可接入。

### 9.4 更换模型供应商

如需更换模型供应商，应优先通过 `OPENAI_BASE_URL` 接入兼容网关，或在 `agents_runtime/` 内扩展新的 Agents SDK 模型适配，而不是恢复旧的双轨运行时装配。

### 9.5 更细的编排拆分

如果后续复杂度继续上升，最优先的重构对象会是 [app.py](./app.py)，可进一步拆成：

- turn orchestrator
- runtime event service
- round executor
- finalization service

当前先集中在一个文件中，是为了让单机 CLI 版本易于迭代和调试。

## 10. 当前权衡与已知限制

当前实现是实用优先，仍有一些明显的工程取舍：

- `app.py` 体量较大，编排逻辑集中，后续适合继续拆分。
- 工具体系目前主要围绕本地材料读取，外部知识接入能力较弱。
- CLI 交互已经可用，但仍偏调试风格，不是完整产品界面。
- 质量门禁已经存在，但仍主要依赖 prompt 与规则组合，不是严格的语义验证系统。
- `workspace` 已较完整，但某些 artifact 之间仍可继续加强一致性约束。

这些限制并不影响当前仓库作为一个清晰、可运行、可继续扩展的公文写作 Agent 基线。

## 11. 总结

这个项目的核心不是某一段 prompt，而是以下四件事的组合：

- 受约束的 LLM 决策协议；
- 显式的工作区状态模型；
- 有边界的材料访问能力；
- 可追踪的会话与调试产物。

也正因为如此，它更适合继续演进成一个稳定的公文写作运行时，而不是一次性的脚本集合。
