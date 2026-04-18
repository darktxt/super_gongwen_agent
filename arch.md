# 架构设计说明

## 1. 定位

`super-gongwen-agent` 是一个面向中文公文写作的多轮运行时，而不是一次性生成脚本。它把写作过程拆成一条可观测、可持久化、可复盘的编排链路：

- 主控 agent 负责决策下一步动作
- `workspace` 负责保存事实状态与中间产物
- `agents_runtime` 负责上下文、协议、工具和运行时适配

当前实现已经收敛为：

- 单一 OpenAI Agents SDK 编排路径
- 单一 LiteLLM 模型接入路径
- 单一 `materials/` 本地材料边界

## 2. 核心原则

### 2.1 让 agent 决策，不让本地启发式代替决策

应用层和编排层只保留三类职责：

- 提供事实上下文
- 记录运行状态
- 执行动作结果

不再在本地层做“证据强/弱”“建议下一步 revise_draft”“满足 4 条证据即可定稿”这类伪智能判断。当前链路唯一的业务决策源是主控 agent。

### 2.2 工作区是事实源

`workspace.json` 不是调试副产物，而是整条写作链路的事实源。提纲、草稿、证据、修订历史、质量快照都沉淀在这里，方便多轮续写与复盘。

### 2.3 工具必须有边界

主控 agent 默认只接触受控材料工具：`search`、`list`、`read`、`grep`。工具访问被限制在 `materials/` 目录内，避免把整个仓库暴露给模型。

## 3. 顶层结构

```text
main.py                 CLI 入口
gui_main.py             本地 GUI 入口
app.py                  应用编排中心
config.py               运行配置读取
runtime_factory.py      运行时装配工厂

agents_runtime/         Agents SDK 运行时、上下文、协议、工具
workspace/              工作区状态、快照、补丁与持久化
session_storage/        会话目录、产物保存
observability/          事件、日志、调试输出
result_assembler/       CLI 展示结果装配
materials/              本地材料目录
```

## 4. 核心模块

### 4.1 应用编排层

[app.py](./app.py) 是当前主编排中心，负责：

- 读取和保存工作区
- 调用主控运行时
- 应用 action 到工作区
- 导出终稿和记录事件

`create_app()`、`bootstrap()`、`run_turn()` 仍然是对外稳定入口。

### 4.2 运行时上下文层

[agents_runtime/context.py](./agents_runtime/context.py) 负责把 `WorkspaceSnapshot` 编译成可被主控 agent 消费的上下文。当前重点不是帮 agent 做判断，而是把判断所需的事实交齐，包括：

- 用户线程
- 当前提纲与草稿状态
- 已检索材料与证据板
- 最近自审快照与历史阻断项

其中 `Decision Snapshot` 和 `Writing Brief` 只保留事实快照与写作约束，不再携带本地启发式推荐动作。

### 4.3 动作协议层

[agents_runtime/protocol.py](./agents_runtime/protocol.py) 定义当前最小运行时契约：

- `BrainStepResult`
- `ActionPayload`
- `LLMRequest`
- `LLMResponse`
- `BrainRunResult`
- `BrainRunError`
- `OutputParser`

主控 agent 最终必须输出合法 `BrainStepResult JSON`。当前允许的 action 有：

- `build_outline`
- `write_draft`
- `write_section`
- `revise_draft`
- `polish_language`
- `ask_user`
- `finalize`

### 4.4 Agents SDK 运行时层

[agents_runtime/brain_runner.py](./agents_runtime/brain_runner.py) 负责：

- 初始化 Agents SDK agent
- 注入受控 `function_tool`
- 调用 LiteLLM provider
- 统一走文本 JSON 协议并解析输出
- 在需要时触发 JSON 修复回合
- 在 provider 把业务动作误发成 tool call 时进入无工具恢复模式
- 运行 `outline`、`draft`、`polish` specialist

这里的 specialist 只产出中间结果，不直接替代主控做最终业务动作。

### 4.5 工作区层

`workspace/` 保存多轮写作状态。核心对象包括：

- 指令与约束
- 材料目录与检索记录
- 证据板
- 提纲
- 草稿全文与分节内容
- 自审结果
- 修订历史
- 质量信号快照与定稿阻断项

这让系统能够区分“还没补材料”“已有材料但没写进稿子”“稿子已经成形但还不适合定稿”。

### 4.6 持久化与可观测性层

`session_storage/` 与 `observability/` 负责沉淀运行证据，包括：

- `workspace.json`
- `events.jsonl`
- `debug/` 调试文件
- `versions/` 中间版本
- `tool_results/` 工具结果
- `outputs/final.md` 与 `outputs/final.docx`

## 5. 端到端链路

一次 `run_turn()` 的主链路如下：

1. 读取当前会话的 `workspace.json`
2. 写入本轮用户输入
3. 生成 `WorkspaceSnapshot`
4. `ContextCompiler` 编译上下文
5. 主控 agent 运行，并在同一轮内按需调用 `search/list/read/grep`
6. 解析输出为 `BrainStepResult`
7. 把 action 结果回写工作区
8. 若动作为 `finalize`，直接导出终稿；否则继续下一轮

简图如下：

```text
User Input
   |
   v
run_turn()
   |
   +--> Workspace
   +--> ContextCompiler
   +--> AgentsSdkBrainRunner
   |      |
   |      +--> function_tool(search/list/read/grep)
   |      +--> specialists
   |
   +--> SessionStorage / Observability
   |
   v
Updated Workspace / Final Output
```

## 6. 当前质量控制

当前质量控制不再依赖独立静态门禁模块，也不再采用“主控判断后，应用层再跑一轮评审”的双层结构。现在的做法是：

- 主控 agent 结合上下文、历史稿件、自审与质量快照决定是否进入 `finalize`
- `self_review`、`quality_review_snapshots`、`finalization_blockers` 继续作为工作区里的历史事实沉淀
- 应用层只执行动作结果，不再额外改判

这意味着：

- 不再靠本地 `if/else` 规则推断下一步动作
- 不再在主控输出后追加一轮应用层质量裁决
- 质量相关信息仍会沉淀进工作区，供后续回合继续处理

## 7. 模型与 provider

当前只支持 LiteLLM workflow。接入不同 provider 的方式是切换 LiteLLM 模型名与所需凭据，例如：

- MiniMax
- GLM
- OpenAI

`OPENAI_BASE_URL` 已退出运行时主配置；如果 provider 需要自定义 base URL，应通过 `LITELLM_BASE_URL` 配置。

## 8. 安全边界

当前最重要的工程边界是 `materials/`：

- 相对路径会被归一化到 `materials/`
- 只允许读取 `.txt`、`.md`、`.json`、`.docx`、`.pdf`
- 任何越界访问都会被阻止

因此，这个项目的主路径是“受控读材 + 结构化写作”，不是“给模型任意系统访问权限”。

## 9. 当前约束

- `app.py` 仍承担较多编排职责，后续可以继续拆分
- 当前工具层聚焦本地材料，不以内置联网检索为主路径
- 文稿质量主要依赖主控 agent 的语义判断，不是形式化验证器
- CLI 与 GUI 已可用，但仍偏工程工具而非完整产品

## 10. 总结

这个项目当前真正的骨架只有四件事：

- 一个最小动作协议
- 一个显式工作区
- 一组受控材料工具
- 一条由主控 agent 驱动的编排链路

也正因此，删掉旧的 `editorial_brain` 和本地启发式规则后，整体架构反而更清晰了：应用层负责执行，agent 负责判断，工作区负责记账。
