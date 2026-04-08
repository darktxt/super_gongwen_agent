from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from app import TurnRunResult, create_app
from config import AppConfig, load_config
from result_assembler.assembler import ResultAssembler
from session_storage.paths import build_session_paths
from workspace.store import WorkspaceStore

from .env_config import apply_gui_config_to_environment
from .settings_dialog import SettingsDialog


class AppWindow:
    POLL_INTERVAL_MS = 120

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("super-gongwen-agent GUI")
        self.root.geometry("1280x760")
        self.root.minsize(1080, 680)

        self.result_assembler = ResultAssembler()
        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_session_id = ""
        self.current_turn_result: TurnRunResult | None = None
        self.is_running = False

        self.config: AppConfig = load_config(base_dir=".")
        self.app = self._build_app(self.config)
        self.workspace_store = WorkspaceStore(app_home=self.app.app_home_path())

        self.status_text = tk.StringVar(value="空闲")
        self.current_session_text = tk.StringVar(value="当前会话：未选择")
        self.round_text = tk.StringVar(value="轮次：-")
        self.action_text = tk.StringVar(value="动作：-")
        self.result_status_text = tk.StringVar(value="结果状态：-")
        self.error_text = tk.StringVar(value="错误摘要：-")
        self.question_text = tk.StringVar(value="当前无待补充问题")

        self._build_styles()
        self._build_layout()
        self._refresh_sessions(select_session_id="")
        self.root.after(self.POLL_INTERVAL_MS, self._process_event_queue)

    def _build_app(self, config: AppConfig):
        return create_app(
            config=config,
            progress_reporter=self._handle_progress_message,
            round_reporter=self._handle_round_result,
        )

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#f5f6f7")
        style.configure("Toolbar.TFrame", background="#eef2f4")
        style.configure("Section.TLabelframe", padding=10)
        style.configure("Status.TLabel", foreground="#304050")

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(12, 10), style="Toolbar.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(4, weight=1)

        self.settings_button = ttk.Button(toolbar, text="设置", command=self._open_settings)
        self.settings_button.grid(row=0, column=0, padx=(0, 8))
        self.new_session_button = ttk.Button(toolbar, text="新建会话", command=self._create_session)
        self.new_session_button.grid(row=0, column=1, padx=(0, 8))
        self.open_session_button = ttk.Button(toolbar, text="打开会话", command=self._open_selected_session)
        self.open_session_button.grid(row=0, column=2, padx=(0, 16))
        ttk.Label(toolbar, textvariable=self.current_session_text).grid(
            row=0,
            column=3,
            sticky="w",
        )
        ttk.Label(toolbar, textvariable=self.status_text, style="Status.TLabel").grid(
            row=0,
            column=4,
            sticky="e",
        )

        content = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)

        left_frame = ttk.Labelframe(content, text="会话", style="Section.TLabelframe")
        middle_frame = ttk.Labelframe(content, text="输入与过程", style="Section.TLabelframe")
        right_frame = ttk.Labelframe(content, text="当前结果", style="Section.TLabelframe")

        content.add(left_frame, weight=1)
        content.add(middle_frame, weight=2)
        content.add(right_frame, weight=2)

        self._build_session_panel(left_frame)
        self._build_interaction_panel(middle_frame)
        self._build_result_panel(right_frame)
        self._build_status_bar()

    def _build_session_panel(self, parent: ttk.Labelframe) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(header, text="刷新", command=self._refresh_sessions).grid(row=0, column=0, sticky="w")

        self.session_tree = ttk.Treeview(
            parent,
            columns=("updated_at", "status"),
            show="tree headings",
            selectmode="browse",
            height=18,
        )
        self.session_tree.heading("#0", text="会话 ID")
        self.session_tree.heading("updated_at", text="更新时间")
        self.session_tree.heading("status", text="状态")
        self.session_tree.column("#0", width=220, stretch=True)
        self.session_tree.column("updated_at", width=140, anchor="w")
        self.session_tree.column("status", width=80, anchor="center")
        self.session_tree.grid(row=1, column=0, sticky="nsew")
        self.session_tree.bind("<Double-1>", lambda _event: self._open_selected_session())

    def _build_interaction_panel(self, parent: ttk.Labelframe) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        ttk.Label(parent, text="输入内容").grid(row=0, column=0, sticky="w")
        self.input_text = ScrolledText(parent, height=10, wrap=tk.WORD)
        self.input_text.grid(row=1, column=0, sticky="nsew", pady=(6, 10))

        button_row = ttk.Frame(parent)
        button_row.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.submit_button = ttk.Button(button_row, text="开始生成", command=self._submit_turn)
        self.submit_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_row, text="清空输入", command=self._clear_input).grid(row=0, column=1)

        self.question_label = ttk.Label(
            parent,
            textvariable=self.question_text,
            justify=tk.LEFT,
            foreground="#8a4f00",
        )
        self.question_label.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(parent, text="过程消息").grid(row=4, column=0, sticky="w")
        self.message_text = ScrolledText(parent, height=16, wrap=tk.WORD, state="disabled")
        self.message_text.grid(row=5, column=0, sticky="nsew", pady=(6, 0))
        parent.rowconfigure(5, weight=1)

    def _build_result_panel(self, parent: ttk.Labelframe) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        ttk.Label(parent, text="结果文本").grid(row=0, column=0, sticky="w")
        self.result_text = ScrolledText(parent, wrap=tk.WORD, state="disabled")
        self.result_text.grid(row=1, column=0, sticky="nsew", pady=(6, 10))

        self.output_path_label = ttk.Label(parent, text="输出路径：-", justify=tk.LEFT)
        self.output_path_label.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        action_row = ttk.Frame(parent)
        action_row.grid(row=3, column=0, sticky="e")
        ttk.Button(action_row, text="复制文本", command=self._copy_result_text).grid(row=0, column=0)

    def _build_status_bar(self) -> None:
        status_bar = ttk.Frame(self.root, padding=(12, 0, 12, 10))
        status_bar.grid(row=2, column=0, sticky="ew")
        for column in range(4):
            status_bar.columnconfigure(column, weight=1)

        ttk.Label(status_bar, textvariable=self.round_text).grid(row=0, column=0, sticky="w")
        ttk.Label(status_bar, textvariable=self.action_text).grid(row=0, column=1, sticky="w")
        ttk.Label(status_bar, textvariable=self.result_status_text).grid(row=0, column=2, sticky="w")
        ttk.Label(status_bar, textvariable=self.error_text).grid(row=0, column=3, sticky="w")

    def _open_settings(self) -> None:
        if self.is_running:
            messagebox.showinfo("提示", "处理中暂不可修改设置", parent=self.root)
            return
        SettingsDialog(self.root, on_apply=self._apply_settings)

    def _apply_settings(self, values: dict[str, str]) -> None:
        apply_gui_config_to_environment(values)
        self.config = load_config(base_dir=".")
        self.app = self._build_app(self.config)
        self.workspace_store = WorkspaceStore(app_home=self.app.app_home_path())
        self._append_message("已重新加载配置。")
        self._refresh_sessions(select_session_id=self.current_session_id)

    def _create_session(self) -> None:
        if self.is_running:
            return
        result = self.app.bootstrap()
        self.current_session_id = str(result.session_id or "")
        self.current_session_text.set(f"当前会话：{self.current_session_id}")
        self.status_text.set("空闲")
        self._append_message(f"已创建新会话：{self.current_session_id}")
        self._refresh_sessions(select_session_id=self.current_session_id)
        self._load_session_snapshot(self.current_session_id)

    def _open_selected_session(self) -> None:
        selected = self.session_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一个会话", parent=self.root)
            return
        session_id = str(selected[0])
        self.current_session_id = session_id
        self.current_session_text.set(f"当前会话：{session_id}")
        self.status_text.set("空闲")
        self._append_message(f"已切换到会话：{session_id}")
        self._load_session_snapshot(session_id)

    def _refresh_sessions(self, select_session_id: str | None = None) -> None:
        self.session_tree.delete(*self.session_tree.get_children())
        sessions_root = self.app.app_home_path() / "sessions"
        if not sessions_root.exists():
            return

        session_rows: list[tuple[str, str, str]] = []
        for session_dir in sorted(
            [item for item in sessions_root.iterdir() if item.is_dir()],
            key=lambda item: item.name,
            reverse=True,
        ):
            session_id = session_dir.name
            updated_at = "-"
            status = "空白"
            try:
                workspace = self.workspace_store.load(session_id=session_id)
                updated_at = str(workspace.session_meta.get("updated_at", "") or "-")
                status = self._derive_workspace_status(workspace)
            except Exception:
                status = "异常"
            session_rows.append((session_id, updated_at, status))

        for session_id, updated_at, status in session_rows:
            self.session_tree.insert("", "end", iid=session_id, text=session_id, values=(updated_at, status))

        target_session_id = select_session_id or self.current_session_id
        if target_session_id and self.session_tree.exists(target_session_id):
            self.session_tree.selection_set(target_session_id)
            self.session_tree.focus(target_session_id)

    def _derive_workspace_status(self, workspace: Any) -> str:
        if list(getattr(workspace, "pending_questions", []) or []):
            return "待补充"
        draft_artifact = getattr(workspace, "draft_artifact", None)
        outline_artifact = getattr(workspace, "outline_artifact", None)
        if str(getattr(draft_artifact, "status", "") or "") == "finalized":
            return "已完成"
        if str(getattr(draft_artifact, "full_text", "") or "").strip():
            return "进行中"
        if str(getattr(outline_artifact, "outline_text", "") or "").strip():
            return "有提纲"
        return "空白"

    def _clear_input(self) -> None:
        self.input_text.delete("1.0", tk.END)

    def _submit_turn(self) -> None:
        if self.is_running:
            return

        user_input = self.input_text.get("1.0", tk.END).strip()
        if not user_input:
            messagebox.showinfo("提示", "请输入本轮需求或补充内容", parent=self.root)
            return

        if not self.current_session_id:
            result = self.app.bootstrap()
            self.current_session_id = str(result.session_id or "")
            self.current_session_text.set(f"当前会话：{self.current_session_id}")
            self._refresh_sessions(select_session_id=self.current_session_id)

        self.is_running = True
        self._set_running_controls(enabled=False)
        self.status_text.set("处理中")
        self.error_text.set("错误摘要：-")
        self.question_text.set("当前无待补充问题")
        self._append_message("已发起本轮处理。")

        thread = threading.Thread(
            target=self._run_turn_in_background,
            args=(self.current_session_id, user_input),
            daemon=True,
        )
        thread.start()

    def _run_turn_in_background(self, session_id: str, user_input: str) -> None:
        try:
            turn_result = self.app.run_turn(session_id=session_id, user_input=user_input, max_rounds=16)
            self.event_queue.put(("turn_result", turn_result))
        except Exception as exc:
            self.event_queue.put(("thread_error", str(exc)))

    def _handle_progress_message(self, message: str) -> None:
        self.event_queue.put(("progress", str(message or "").strip()))

    def _handle_round_result(self, turn_result: TurnRunResult) -> None:
        rendered = self.result_assembler.render_round_progress(turn_result).strip()
        if rendered:
            self.event_queue.put(("round_progress", rendered))

    def _process_event_queue(self) -> None:
        while True:
            try:
                event_type, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "progress":
                if payload:
                    self._append_message(str(payload))
                continue

            if event_type == "round_progress":
                self._append_message(str(payload))
                continue

            if event_type == "thread_error":
                self.is_running = False
                self._set_running_controls(enabled=True)
                self.status_text.set("失败")
                self.error_text.set(f"错误摘要：{payload}")
                self._append_message(f"执行线程异常：{payload}")
                continue

            if event_type == "turn_result":
                self._handle_turn_result(payload)

        self.root.after(self.POLL_INTERVAL_MS, self._process_event_queue)

    def _handle_turn_result(self, turn_result: TurnRunResult) -> None:
        self.is_running = False
        self._set_running_controls(enabled=True)
        self.current_turn_result = turn_result
        self.current_session_id = turn_result.session_id
        self.current_session_text.set(f"当前会话：{turn_result.session_id}")
        self.round_text.set(f"轮次：{turn_result.rounds_used}")
        self.result_status_text.set(f"结果状态：{turn_result.status}")
        action_taken = str(getattr(getattr(turn_result, "step", None), "action_taken", "") or "-")
        self.action_text.set(f"动作：{action_taken}")
        error_summary = str(getattr(turn_result, "error_message", "") or "-")
        self.error_text.set(f"错误摘要：{error_summary}")

        if turn_result.status == "needs_user_input":
            self.status_text.set("待补充")
            self.submit_button.configure(text="提交补充")
        elif turn_result.status == "completed":
            self.status_text.set("已完成")
            self.submit_button.configure(text="开始生成")
        elif turn_result.status in {"failed", "max_rounds_exceeded"}:
            self.status_text.set("失败")
            self.submit_button.configure(text="开始生成")
        else:
            self.status_text.set("空闲")
            self.submit_button.configure(text="开始生成")

        self._render_questions(turn_result)
        self._render_result_text(turn_result)
        self._append_message(self.result_assembler.render_text(self.result_assembler.assemble(turn_result)))
        self._refresh_sessions(select_session_id=turn_result.session_id)

    def _render_questions(self, turn_result: TurnRunResult) -> None:
        question_pack = list(getattr(turn_result, "question_pack", []) or [])
        workspace = getattr(turn_result, "workspace", None)
        pending_questions = list(getattr(workspace, "pending_questions", []) or [])
        questions = question_pack or pending_questions
        if not questions:
            self.question_text.set("当前无待补充问题")
            return

        lines = []
        for index, question in enumerate(questions, start=1):
            text = str(question.get("question", "") or "").strip()
            if text:
                lines.append(f"{index}. {text}")
        self.question_text.set("待补充问题：\n" + "\n".join(lines) if lines else "当前无待补充问题")

    def _render_result_text(self, turn_result: TurnRunResult | None) -> None:
        output_text = ""
        output_path = "-"

        if turn_result is not None:
            output_text = str(getattr(turn_result, "final_text", "") or "").strip()
            output_path = str(getattr(turn_result, "final_output_path", "") or "").strip() or "-"
            workspace = getattr(turn_result, "workspace", None)
        else:
            workspace = None

        if not output_text and workspace is not None:
            output_text = str(getattr(getattr(workspace, "draft_artifact", None), "full_text", "") or "").strip()
        if not output_text and workspace is not None:
            output_text = str(
                getattr(getattr(workspace, "outline_artifact", None), "outline_text", "") or ""
            ).strip()

        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", output_text or "当前暂无结果")
        self.result_text.configure(state="disabled")
        self.output_path_label.configure(text=f"输出路径：{output_path}")

    def _append_message(self, message: str) -> None:
        normalized = str(message or "").strip()
        if not normalized:
            return
        self.message_text.configure(state="normal")
        if self.message_text.index("end-1c") != "1.0":
            self.message_text.insert(tk.END, "\n\n")
        self.message_text.insert(tk.END, normalized)
        self.message_text.see(tk.END)
        self.message_text.configure(state="disabled")

    def _load_session_snapshot(self, session_id: str) -> None:
        try:
            workspace = self.workspace_store.load(session_id=session_id)
        except Exception as exc:
            self.result_status_text.set("结果状态：读取失败")
            self.error_text.set(f"错误摘要：{exc}")
            return

        self.current_turn_result = None
        self.round_text.set("轮次：-")
        self.action_text.set("动作：-")
        self.result_status_text.set(f"结果状态：{self._derive_workspace_status(workspace)}")
        self.error_text.set("错误摘要：-")
        self.question_text.set("当前无待补充问题")

        pending_questions = list(getattr(workspace, "pending_questions", []) or [])
        if pending_questions:
            lines = []
            for index, question in enumerate(pending_questions, start=1):
                text = str(question.get("question", "") or "").strip()
                if text:
                    lines.append(f"{index}. {text}")
            if lines:
                self.question_text.set("待补充问题：\n" + "\n".join(lines))
                self.submit_button.configure(text="提交补充")
        else:
            self.submit_button.configure(text="开始生成")

        output_path = build_session_paths(session_id=session_id, app_home=self.app.app_home_path()).final_output_path
        simulated_turn = TurnRunResult(
            session_id=session_id,
            status="completed" if output_path.exists() else "loaded",
            rounds_used=0,
            final_output_path=str(output_path) if output_path.exists() else "",
            workspace=workspace,
        )
        self._render_result_text(simulated_turn)

    def _copy_result_text(self) -> None:
        content = self.result_text.get("1.0", tk.END).strip()
        if not content or content == "当前暂无结果":
            messagebox.showinfo("提示", "当前没有可复制的结果", parent=self.root)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        messagebox.showinfo("提示", "结果文本已复制", parent=self.root)

    def _set_running_controls(self, *, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (
            self.settings_button,
            self.new_session_button,
            self.open_session_button,
            self.submit_button,
        ):
            widget.configure(state=state)
        self.session_tree.configure(selectmode="browse" if enabled else "none")
        self.input_text.configure(state="normal" if enabled else "disabled")


def launch_gui() -> None:
    root = tk.Tk()
    AppWindow(root)
    root.mainloop()
