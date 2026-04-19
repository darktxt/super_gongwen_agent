# super-gongwen-agent

一个面向中文公文写作的最小多轮 Agent 运行时。

当前版本只保留三件核心资产：

- `workspace/`：写作过程事实源
- `materials/`：本地材料安全边界
- LiteLLM + OpenAI Agents SDK：最小运行时内核

不再保留旧的 GUI、DOCX 导出、复杂观测层、手写 JSON 修复链和多层运行时胶水。

## 当前结构

- `runtime_core.py`：coordinator、review specialist、结构化输出、`function_tool`、`agent.as_tool()`
- `app.py`：最小应用壳层，负责 `workspace` 读写、调用 runtime、保存结果
- `main.py`：最小 CLI
- `workspace/`：保留原有工作区结构
- `session_storage/`：最小会话目录与输出落盘
- `materials/`：模型可读取的本地材料边界

## 运行要求

- Python 3.11+
- LiteLLM 可用模型配置

安装依赖：

```bash
pip install -r requirements.txt
```

## 配置

优先读取根目录或 `--base-dir` 下的 `.env`：

```bash
LITELLM_MODEL=
LITELLM_API_KEY=
LITELLM_BASE_URL=
LITELLM_TEMPERATURE=
OPENAI_AGENTS_ENABLE_TRACING=1
SUPER_GONGWEN_HOME=
```

说明：

- 当前只支持 LiteLLM 路径。
- `SUPER_GONGWEN_HOME` 不配置时，默认使用 `<base-dir>/.super_gongwen/`。

## 用法

初始化会话：

```bash
python main.py --base-dir .
```

直接发起一轮写作：

```bash
python main.py --base-dir . --user-input "请根据 materials 中的材料起草一篇部署讲话稿"
```

继续同一会话：

```bash
python main.py --base-dir . --session-id <会话ID> --user-input "补充强调责任闭环和时间节点"
```

## 当前运行时

运行时主链只有一条：

1. 读取 `workspace.json`
2. 写入本轮用户输入
3. coordinator 通过 Agents SDK 运行
4. 按需调用本地 `function_tool`
5. 按需调用 `review_draft` tool-agent
6. 直接返回结构化结果
7. 应用层写回 `workspace` 并在需要时导出 `final.md`

## Tools

当前仅提供 4 个受控材料工具：

- `list_materials`
- `search_materials`
- `read_material`
- `grep_materials`

这些工具只能访问 `materials/` 内的：

- `.txt`
- `.md`
- `.json`
- `.docx`
- `.pdf`

越界路径会被拒绝。

## Review

review 不再由 Python 事后补跑，而是：

- 使用独立 `ReviewSpecialist`
- 通过 `agent.as_tool()` 暴露给 coordinator
- 返回结构化审阅结论
- 直接影响 coordinator 的最终动作

## 输出

默认会在 `.super_gongwen/sessions/<session_id>/` 下生成：

- `workspace.json`
- `debug/latest_run.json`
- `outputs/final.md`（仅在 `finalize` 时生成）

## 验证

当前最小测试集：

```bash
python -m unittest tests.test_minimal_runtime
```

更多设计说明见 [arch.md](./arch.md)。
