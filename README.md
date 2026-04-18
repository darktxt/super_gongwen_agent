# super-gongwen-agent

一个面向中文公文写作的多轮 Agent 工程。当前运行时已经收敛为单一路径：

- 用 OpenAI Agents SDK 负责 agent 编排
- 用 LiteLLM 统一接入底层模型
- 用 `workspace/` 保存写作状态与中间产物

项目目标不是“给一个 prompt 直接吐整稿”，而是让补材、提纲、起草、修订、定稿都成为可追踪、可回放、可继续迭代的工程链路。

## 当前架构

- `app.py`：应用编排入口，负责单轮回合执行、状态回写与终稿导出
- `agents_runtime/`：Agents SDK 运行时、上下文编译、动作协议、材料工具与结果落盘
- `workspace/`：工作区事实源，保存提纲、草稿、证据、修订历史、质量快照
- `session_storage/`、`observability/`：会话目录、事件流、调试文件与产物保存

更完整的设计说明见 [arch.md](./arch.md)。

## 环境要求

- Python 3.11+

安装依赖：

```bash
pip install -r requirements.txt
```

## 配置

项目通过环境变量读取模型配置，推荐使用根目录 `.env`。

```bash
LITELLM_MODEL=
LITELLM_API_KEY=
LITELLM_BASE_URL=
LITELLM_TIMEOUT=300
LITELLM_TEMPERATURE=
OPENAI_AGENTS_ENABLE_TRACING=1
OPENAI_AGENTS_OUTPUT_MODE=auto
SUPER_GONGWEN_HOME=
```

说明：

- `LITELLM_MODEL`、 `LITELLM_API_KEY`、`LITELLM_BASE_URL` 按所选 provider 需要填写
- `OPENAI_AGENTS_OUTPUT_MODE` 目前仅作兼容读取；运行时已统一收敛到文本 JSON 协议
- `SUPER_GONGWEN_HOME` 用于指定运行态目录，默认是当前目录下的 `.super_gongwen/`

## 运行方式

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

启动本地 GUI：

```bash
python gui_main.py
```

## materials 目录

运行时默认只允许读取仓库根目录 `materials/` 内的材料，支持：

- `.txt`
- `.md`
- `.json`
- `.docx`
- `.pdf`

相对路径会被解释为 `materials/` 下的路径，越界访问会被阻止。这是当前工具层最重要的安全边界。

## 运行链路

一次 `run_turn()` 的核心流程是：

1. 读取 `workspace.json` 并写入本轮用户输入
2. `ContextCompiler` 把工作区快照编译成主控 agent 上下文
3. 主控 agent 通过受控工具补材料，并输出一个 `BrainStepResult`
4. 应用层按 action 回写提纲、草稿、修订或追问
5. 当主控输出 `finalize` 时直接导出终稿

这里有两个明确原则：

- 工作流判断尽量交给 agent，本地层只保留事实、记录、适配
- 应用层不再使用独立质量门禁，也不再追加一轮应用层 LLM 评审

## 输出与产物

默认会在 `.super_gongwen/sessions/<session_id>/` 下生成：

- `workspace.json`：工作区事实源
- `events.jsonl`：运行事件流
- `debug/`：上下文、请求、响应与步骤调试文件
- `versions/`：中间版本产物
- `tool_results/`：工具结果落盘
- `outputs/final.md`
- `outputs/final.docx`

终稿是否成熟由主控 agent 结合上下文、自审与历史快照自行判断；应用层只负责执行导出，不再追加门禁裁决。

## Agents SDK 输出模式

- 当前主控与 specialist 已统一使用文本 JSON 协议
- 运行时会解析 `<think> + json`、代码块 JSON、半结构化文本，并在必要时触发一次 JSON 修复回合
- 若 LiteLLM provider 把业务动作误发成 tool call，运行时会自动切到无工具恢复模式重跑

## 已知边界

- 当前工具面只聚焦本地 `materials/`，不以内置联网检索为主路径
- `app.py` 仍然偏大，但主链路已经清晰收敛
- 文稿质量更多依赖主控 agent 的语义判断与 `self_review` 沉淀，而不是本地启发式规则

## 参考

- 架构说明：[arch.md](./arch.md)
- 变更规格：[openspec/changes/adopt-litellm-quality-first-runtime/](./openspec/changes/adopt-litellm-quality-first-runtime/)
