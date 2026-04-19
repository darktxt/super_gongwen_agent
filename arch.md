# 架构设计说明

## 1. 当前目标

当前版本的目标非常克制：

- 保留 `workspace` 结构
- 保留 `materials/` 安全边界
- 用 LiteLLM + OpenAI Agents SDK 搭一个最小可工作的公文写作运行时

除此之外，旧系统里的大部分自研运行时层都已经删除。

## 2. 核心原则

### 2.1 `workspace` 是事实源

`workspace.json` 继续保存：

- 提纲
- 草稿
- 材料检索痕迹
- 待追问问题
- 自审与风险摘要

运行时不再引入另一套事实源。

### 2.2 agent 负责业务裁决

程序只负责：

- 读取和保存 `workspace`
- 暴露受控工具
- 调用 Agents SDK
- 消费结构化结果
- 写出最终产物

业务判断由 coordinator 完成。

### 2.3 materials 是硬边界

模型只能通过工具访问 `materials/`。
不能直接自由读取仓库路径。

### 2.4 以 SDK 原生能力替代手写胶水

当前运行时只使用以下官方能力：

- `output_type`
- `function_tool`
- `RunContextWrapper`
- `agent.as_tool()`

不再保留：

- 手写动作协议解析链
- 文本 JSON 修复主路径
- Python 事后补跑 specialist
- 多层编排 / 观测包装层

## 3. 当前结构

```text
main.py
app.py
runtime_core.py
config.py
workspace/
session_storage/
materials/
```

其中：

- `runtime_core.py` 是唯一的运行时核心
- `app.py` 是极薄的应用壳层
- `workspace/` 保持原有结构

## 4. runtime_core

`runtime_core.py` 负责四类内容：

1. 结构化输出模型
2. 受控材料工具
3. review specialist tool-agent
4. LiteLLM Agents Runtime 调用

### 4.1 coordinator

coordinator 是唯一最终业务裁决者，输出 `CoordinatorResult`，包含：

- `action`
- `decision_rationale`
- `completion_mode`
- `assumptions`
- `major_risks`
- `response_text`
- `outline_sections`
- `draft_text`
- `final_text`
- `question_pack`
- `review_summary`

### 4.2 materials tools

当前工具包括：

- `list_materials`
- `search_materials`
- `read_material`
- `grep_materials`

它们通过 `RunContextWrapper` 获取：

- `session_id`
- `working_root`
- `materials_root`
- `workspace`

工具结果会回写成最小 tool event，供 `workspace` 更新材料状态。

### 4.3 review specialist

`ReviewSpecialist` 是独立 agent：

- 输入：草稿、目标、材料摘要、当前风险
- 输出：`ReviewResult`

它通过 `agent.as_tool()` 暴露为 `review_draft`，由 coordinator 按需调用。

### 4.4 LiteLLM runtime

运行时使用：

- `LitellmModel`
- `Runner.run_sync`
- `RunConfig`

当前只支持 LiteLLM，不再保留旧的多运行时工厂。

## 5. app 层

`app.py` 只做 5 件事：

1. bootstrap 会话目录
2. 读取 `workspace`
3. 调用 runtime
4. 把结果写回 `workspace`
5. 在 `finalize` 时保存 `final.md`

它不再：

- 编译复杂上下文快照
- 执行多轮自定义 orchestration
- 维护庞大事件流
- 在应用层追加质量裁决

## 6. 会话目录

当前最小会话目录如下：

```text
.super_gongwen/
  sessions/
    <session_id>/
      workspace.json
      debug/
        latest_run.json
      outputs/
        final.md
```

## 7. 一轮执行链路

```text
用户输入
  -> app.run_turn()
  -> 读取 workspace
  -> coordinator 运行
      -> function_tool 取材
      -> review_draft 审稿
  -> 输出结构化结果
  -> 写回 workspace
  -> 如有 finalize，写出 final.md
```

## 8. 当前已删除的旧系统能力

本次重构明确删除了：

- `agents_runtime/`
- GUI
- `observability/`
- `result_assembler/`
- `orchestration/`
- `runtime_factory.py`
- DOCX 导出链

这样做的目的不是“偷懒”，而是让代码真正围绕唯一值得保留的资产构建：`workspace`。
