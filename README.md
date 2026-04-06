# super-gongwen-agent

一个面向中文公文写作场景的多轮写作 Agent。项目内置技能系统、材料读取工具、工作区状态管理、可观测性日志和终稿装配能力，所有决策均由llm做出，无固定流程引导。

## 项目特点

- 面向公文写作流程建模，支持提纲、正文、局部改写、润色、定稿等动作。
- 内置技能系统，区分主写作 skill 与修订 skill。
- 支持读取仓库根目录 `materials/` 下的 `.txt`、`.md`、`.json`、`.docx`、`.pdf` 材料。
- 使用工作区状态持久化会话，支持多轮追问、补充材料和版本沉淀。
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

项目通过环境变量读取模型配置，不内置任何默认凭据。

可参考根目录 `.env.example`：

```bash
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
OPENAI_TIMEOUT=300
OPENAI_TEMPERATURE=
SUPER_GONGWEN_HOME=
```

说明：

- `OPENAI_API_KEY`：必填，OpenAI 兼容接口的密钥。
- `OPENAI_BASE_URL`：可选，自定义兼容网关时填写；直连官方接口可留空。
- `OPENAI_MODEL`：必填，运行 `run_turn` 时使用的模型名。
- `SUPER_GONGWEN_HOME`：可选，指定运行态数据目录；默认写入当前目录下的 `.super_gongwen/`。

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
- `outputs/final_output.md`：终稿输出
- `versions/`：中间版本产物
- `tool_results/`：工具调用结果

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
