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
        self.root.geometry("1380x860")
        self.root.minsize(1180, 720)

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
        self.result_status_text = tk.StringVar(value="状态：-")
        self.error_text = tk.StringVar(value="错误：-")
        self.outline_title_text = tk.StringVar(value="当前提纲")
        self.body_title_text = tk.StringVar(value="当前正文")

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
        style.configure("Toolbar.TFrame", background="#eef2f4")
        style.configure("Section.TLabelframe", padding=10)
        style.configure("Status.TLabel", foreground="#304050")
        style.configure("Muted.TLabel", foreground="#5d6975")

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
        ttk.Label(toolbar, textvariable=self.current_session_text).grid(row=0, column=3, sticky="w")
        ttk.Label(toolbar, textvariable=self.status_text, style="Status.TLabel").grid(
            row=0,
            column=4,
            sticky="e",
        )

        content = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)

        left_frame = ttk.Labelframe(content, text="会话", style="Section.TLabelframe")
        middle_frame = ttk.Labelframe(content, text="交互与过程理解", style="Section.TLabelframe")
        right_frame = ttk.Labelframe(content, text="本轮产物与当前正文", style="Section.TLabelframe")

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
            height=20,
        )
        self.session_tree.heading("#0", text="会话 ID")
        self.session_tree.heading("updated_at", text="更新时间")
        self.session_tree.heading("status", text="状态")
        self.session_tree.column("#0", width=220, stretch=True)
        self.session_tree.column("updated_at", width=150, anchor="w")
        self.session_tree.column("status", width=80, anchor="center")
        self.session_tree.grid(row=1, column=0, sticky="nsew")
        self.session_tree.bind("<Double-1>", lambda _event: self._open_selected_session())

    def _build_interaction_panel(self, parent: ttk.Labelframe) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        input_frame = ttk.Labelframe(parent, text="输入", style="Section.TLabelframe")
        input_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        input_frame.columnconfigure(0, weight=1)

        self.input_text = ScrolledText(input_frame, height=6, wrap=tk.WORD)
        self.input_text.grid(row=0, column=0, sticky="ew")

        button_row = ttk.Frame(input_frame)
        button_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.submit_button = ttk.Button(button_row, text="开始生成", command=self._submit_turn)
        self.submit_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_row, text="清空输入", command=self._clear_input).grid(row=0, column=1)

        self.question_frame = ttk.Labelframe(parent, text="待补充问题", style="Section.TLabelframe")
        self.question_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.question_frame.columnconfigure(0, weight=1)
        self.question_text = ScrolledText(self.question_frame, height=6, wrap=tk.WORD, state="disabled")
        self.question_text.grid(row=0, column=0, sticky="nsew")

        self.review_frame = ttk.Labelframe(parent, text="本轮自评", style="Section.TLabelframe")
        self.review_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        self.review_frame.columnconfigure(0, weight=1)
        self.review_text = ScrolledText(self.review_frame, height=9, wrap=tk.WORD, state="disabled")
        self.review_text.grid(row=0, column=0, sticky="nsew")

        self.process_frame = ttk.Labelframe(parent, text="本轮过程", style="Section.TLabelframe")
        self.process_frame.grid(row=3, column=0, sticky="nsew")
        self.process_frame.columnconfigure(0, weight=1)
        self.process_text = ScrolledText(self.process_frame, height=11, wrap=tk.WORD, state="disabled")
        self.process_text.grid(row=0, column=0, sticky="nsew")

    def _build_result_panel(self, parent: ttk.Labelframe) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        content = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        content.grid(row=0, column=0, sticky="nsew")

        outline_frame = ttk.Labelframe(content, text="提纲区", style="Section.TLabelframe")
        body_frame = ttk.Labelframe(content, text="正文区", style="Section.TLabelframe")

        outline_frame.columnconfigure(0, weight=1)
        outline_frame.rowconfigure(1, weight=1)
        body_frame.columnconfigure(0, weight=1)
        body_frame.rowconfigure(1, weight=1)

        ttk.Label(outline_frame, textvariable=self.outline_title_text, style="Muted.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Label(body_frame, textvariable=self.body_title_text, style="Muted.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 6),
        )

        self.outline_text = ScrolledText(outline_frame, wrap=tk.WORD, state="disabled")
        self.outline_text.grid(row=1, column=0, sticky="nsew")

        self.body_text = ScrolledText(body_frame, wrap=tk.WORD, state="disabled")
        self.body_text.grid(row=1, column=0, sticky="nsew")

        body_footer = ttk.Frame(body_frame)
        body_footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        body_footer.columnconfigure(0, weight=1)

        self.output_path_label = ttk.Label(body_footer, text="输出路径：-", style="Muted.TLabel")
        self.output_path_label.grid(row=0, column=0, sticky="w")
        ttk.Button(body_footer, text="复制正文", command=self._copy_result_text).grid(row=0, column=1)

        content.add(outline_frame, weight=1)
        content.add(body_frame, weight=2)

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
        self._refresh_sessions(select_session_id=self.current_session_id)

    def _create_session(self) -> None:
        if self.is_running:
            return
        result = self.app.bootstrap()
        self.current_session_id = str(result.session_id or "")
        self.current_session_text.set(f"当前会话：{self.current_session_id}")
        self.status_text.set("空闲")
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
        self.result_status_text.set("状态：处理中")
        self.error_text.set("错误：-")

        thread = threading.Thread(
            target=self._run_turn_in_background,
            args=(self.current_session_id, user_input),
            daemon=True,
        )
        thread.start()

    def _run_turn_in_background(self, session_id: str, user_input: str) -> None:
        try:
            turn_result = self.app.run_turn(
                session_id=session_id,
                user_input=user_input,
                max_rounds=16,
            )
            self.event_queue.put(("turn_result", turn_result))
        except Exception as exc:
            self.event_queue.put(("thread_error", str(exc)))

    def _handle_progress_message(self, message: str) -> None:
        self.event_queue.put(("progress", str(message or "").strip()))

    def _handle_round_result(self, turn_result: TurnRunResult) -> None:
        self.event_queue.put(("round_result", turn_result))

    def _process_event_queue(self) -> None:
        while True:
            try:
                event_type, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "progress":
                self._handle_progress_event(str(payload or ""))
                continue

            if event_type == "round_result":
                self._apply_turn_snapshot(payload, final=False)
                continue

            if event_type == "thread_error":
                self.is_running = False
                self._set_running_controls(enabled=True)
                self.status_text.set("失败")
                self.result_status_text.set("状态：失败")
                self.error_text.set(f"错误：{payload}")
                continue

            if event_type == "turn_result":
                self._handle_turn_result(payload)

        self.root.after(self.POLL_INTERVAL_MS, self._process_event_queue)

    def _handle_progress_event(self, message: str) -> None:
        normalized = str(message or "").strip()
        if not normalized:
            return
        self.status_text.set("处理中")
        self.result_status_text.set("状态：处理中")

    def _handle_turn_result(self, turn_result: TurnRunResult) -> None:
        self.is_running = False
        self._set_running_controls(enabled=True)
        self.current_turn_result = turn_result
        self._apply_turn_snapshot(turn_result, final=True)
        self._refresh_sessions(select_session_id=turn_result.session_id)

    def _apply_turn_snapshot(self, turn_result: TurnRunResult, *, final: bool) -> None:
        self.current_turn_result = turn_result
        self.current_session_id = turn_result.session_id
        self.current_session_text.set(f"当前会话：{turn_result.session_id}")

        round_context = self.result_assembler._build_round_context(turn_result)
        self._update_status_bar(turn_result, round_context, final=final)
        self._render_questions(turn_result)
        self._render_review(turn_result, round_context)
        self._render_process(turn_result, round_context)
        self._render_right_panel(turn_result, round_context)

        if final:
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
        else:
            self.status_text.set("处理中")

    def _update_status_bar(self, turn_result: TurnRunResult, round_context: Any, *, final: bool) -> None:
        self.round_text.set(f"轮次：第 {int(getattr(turn_result, 'rounds_used', 0) or 0)} 轮")
        action_taken = str(getattr(round_context, "action_taken", "") or "").strip() or "-"
        action_label = str(getattr(round_context, "action_label", "") or "").strip()
        if action_label and action_label != action_taken:
            self.action_text.set(f"动作：{action_taken}（{action_label}）")
        else:
            self.action_text.set(f"动作：{action_taken}")

        status_value = str(getattr(turn_result, "status", "") or "")
        self.result_status_text.set(f"状态：{self._display_status(status_value, final=final)}")
        error_message = str(getattr(turn_result, "error_message", "") or "").strip() or "-"
        self.error_text.set(f"错误：{error_message}")

    def _display_status(self, status_value: str, *, final: bool) -> str:
        if not final and status_value == "continued":
            return "处理中"
        return {
            "completed": "已完成",
            "needs_user_input": "待补充",
            "failed": "失败",
            "max_rounds_exceeded": "达到轮次上限",
            "loaded": "已加载",
            "continued": "处理中",
        }.get(status_value, status_value or "-")

    def _render_questions(self, turn_result: TurnRunResult | None) -> None:
        lines: list[str] = []
        if turn_result is not None:
            question_pack = list(getattr(turn_result, "question_pack", []) or [])
            workspace = getattr(turn_result, "workspace", None)
            pending_questions = list(getattr(workspace, "pending_questions", []) or [])
            questions = question_pack or pending_questions
            for index, question in enumerate(questions, start=1):
                text = str(question.get("question", "") or "").strip()
                if text:
                    lines.append(f"{index}. {text}")
        self._set_text_content(self.question_text, "\n".join(lines) or "当前无待补充问题。")

    def _render_review(self, turn_result: TurnRunResult | None, round_context: Any | None) -> None:
        lines: list[str] = []
        review = getattr(round_context, "review", None) if round_context is not None else None
        if review is None and turn_result is not None:
            workspace = getattr(turn_result, "workspace", None)
            review = getattr(workspace, "self_review", None)

        if review is not None:
            content_summary = str(getattr(review, "content_status_summary", "") or "").strip()
            language_summary = str(getattr(review, "language_status_summary", "") or "").strip()
            dominant_issue = str(getattr(review, "dominant_issue", "") or "").strip()
            open_gaps = [
                str(item).strip()
                for item in list(getattr(review, "open_gaps", []) or [])
                if str(item).strip()
            ]
            notes = [
                str(item).strip()
                for item in list(getattr(review, "notes", []) or [])
                if str(item).strip()
            ]

            if content_summary:
                lines.append("内容评价：")
                lines.append(content_summary)
            if language_summary:
                if lines:
                    lines.append("")
                lines.append("语言评价：")
                lines.append(language_summary)
            if dominant_issue:
                if lines:
                    lines.append("")
                lines.append("当前主要问题：")
                lines.append(dominant_issue)
            if open_gaps:
                if lines:
                    lines.append("")
                lines.append("待补缺口：")
                lines.extend(f"{index}. {item}" for index, item in enumerate(open_gaps, start=1))
            if notes:
                if lines:
                    lines.append("")
                lines.append("补充说明：")
                lines.extend(f"{index}. {item}" for index, item in enumerate(notes, start=1))

        self._set_text_content(self.review_text, "\n".join(lines) or "当前暂无本轮自评信息。")

    def _render_process(self, turn_result: TurnRunResult | None, round_context: Any | None) -> None:
        lines: list[str] = []
        if turn_result is None:
            self._set_text_content(self.process_text, "当前暂无过程信息。")
            return

        if round_context is not None:
            action_taken = str(getattr(round_context, "action_taken", "") or "").strip()
            action_label = str(getattr(round_context, "action_label", "") or "").strip()
            primary_skill = str(getattr(round_context, "primary_skill_display", "") or "").strip()
            revision_skills = [
                str(item).strip()
                for item in list(getattr(round_context, "revision_skill_displays", []) or [])
                if str(item).strip()
            ]
            material_actions = [
                str(item).strip()
                for item in list(getattr(round_context, "material_actions", []) or [])
                if str(item).strip()
            ]
            material_names = [
                str(item).strip()
                for item in list(getattr(round_context, "material_names", []) or [])
                if str(item).strip()
            ]
            next_step_hint = str(getattr(round_context, "next_step_hint", "") or "").strip()

            if action_taken:
                lines.append("当前动作：")
                lines.append(f"{action_taken}（{action_label or action_taken}）")
            if primary_skill:
                if lines:
                    lines.append("")
                lines.append("主写作 skill：")
                lines.append(primary_skill)
            if revision_skills:
                if lines:
                    lines.append("")
                lines.append("修订 skill：")
                lines.extend(f"{index}. {item}" for index, item in enumerate(revision_skills, start=1))
            if material_actions:
                if lines:
                    lines.append("")
                lines.append("本轮读材：")
                lines.extend(material_actions)
            if material_names:
                if lines:
                    lines.append("")
                lines.append("涉及材料：")
                lines.extend(f"{index}. {item}" for index, item in enumerate(material_names, start=1))
            if next_step_hint:
                if lines:
                    lines.append("")
                lines.append("下一步建议：")
                lines.append(next_step_hint)

        self._set_text_content(self.process_text, "\n".join(lines) or "当前暂无过程信息。")

    def _render_right_panel(self, turn_result: TurnRunResult | None, round_context: Any | None) -> None:
        outline_title, outline_text = self._extract_outline_panel(turn_result, round_context)
        body_title, body_text, output_path = self._extract_body_panel(turn_result)

        self.outline_title_text.set(outline_title)
        self.body_title_text.set(body_title)
        self._set_text_content(self.outline_text, outline_text or "当前暂无提纲。")
        self._set_text_content(self.body_text, body_text or "当前暂无正文。")
        self.output_path_label.configure(text=f"输出路径：{output_path}")

    def _extract_outline_panel(self, turn_result: TurnRunResult | None, round_context: Any | None) -> tuple[str, str]:
        workspace = getattr(turn_result, "workspace", None) if turn_result is not None else None
        artifact_title = str(getattr(round_context, "artifact_title", "") or "").strip() if round_context else ""
        artifact_text = str(getattr(round_context, "artifact_text", "") or "").strip() if round_context else ""

        if "提纲" in artifact_title and artifact_text:
            return artifact_title, artifact_text

        outline_artifact = getattr(workspace, "outline_artifact", None) if workspace is not None else None
        sections = list(getattr(outline_artifact, "sections", []) or []) if outline_artifact is not None else []
        lines: list[str] = []
        for index, section in enumerate(sections, start=1):
            heading = str(getattr(section, "heading", "") or "").strip()
            if heading:
                lines.append(f"{index}. {heading}")
        if lines:
            return "当前提纲", "\n".join(lines)

        outline_text = str(getattr(outline_artifact, "outline_text", "") or "").strip() if outline_artifact else ""
        if outline_text:
            return "当前提纲", outline_text
        return "当前提纲", ""

    def _extract_body_panel(self, turn_result: TurnRunResult | None) -> tuple[str, str, str]:
        if turn_result is None:
            return "当前正文", "", "-"

        output_path = str(getattr(turn_result, "final_output_path", "") or "").strip() or "-"
        final_text = str(getattr(turn_result, "final_text", "") or "").strip()
        if final_text:
            return "当前正文（终稿）", final_text, output_path

        step = getattr(turn_result, "step", None)
        action_taken = str(getattr(step, "action_taken", "") or "").strip()
        payload = getattr(step, "action_payload", None)
        if action_taken == "write_section":
            section_id = str(getattr(payload, "section_id", "") or "").strip()
            section_text = str(getattr(payload, "section_text", "") or "").strip()
            if section_text:
                return f"当前正文（本轮章节：{section_id or '未命名章节'}）", section_text, output_path

        workspace = getattr(turn_result, "workspace", None)
        draft_artifact = getattr(workspace, "draft_artifact", None) if workspace is not None else None
        draft_text = str(getattr(draft_artifact, "full_text", "") or "").strip() if draft_artifact else ""
        if draft_text:
            return "当前正文", draft_text, output_path
        return "当前正文", "", output_path

    def _load_session_snapshot(self, session_id: str) -> None:
        try:
            workspace = self.workspace_store.load(session_id=session_id)
        except Exception as exc:
            self.result_status_text.set("状态：读取失败")
            self.error_text.set(f"错误：{exc}")
            return

        self.current_turn_result = None
        self.round_text.set(
            f"轮次：第 {int(getattr(getattr(workspace, 'debug_state', None), 'last_round_no', 0) or 0)} 轮"
        )
        self.action_text.set(
            f"动作：{str(getattr(getattr(workspace, 'debug_state', None), 'last_action', '') or '-').strip() or '-'}"
        )
        self.result_status_text.set(f"状态：{self._derive_workspace_status(workspace)}")
        self.error_text.set("错误：-")

        output_path = build_session_paths(
            session_id=session_id,
            app_home=self.app.app_home_path(),
        ).final_output_path
        simulated_turn = TurnRunResult(
            session_id=session_id,
            status="completed" if output_path.exists() else "loaded",
            rounds_used=int(getattr(getattr(workspace, "debug_state", None), "last_round_no", 0) or 0),
            final_output_path=str(output_path) if output_path.exists() else "",
            workspace=workspace,
        )

        self._render_questions(simulated_turn)
        self._render_review(simulated_turn, round_context=None)
        self._render_process(simulated_turn, round_context=None)
        self._render_right_panel(simulated_turn, round_context=None)

        if list(getattr(workspace, "pending_questions", []) or []):
            self.status_text.set("待补充")
            self.submit_button.configure(text="提交补充")
        else:
            self.status_text.set("空闲")
            self.submit_button.configure(text="开始生成")

    def _copy_result_text(self) -> None:
        content = self.body_text.get("1.0", tk.END).strip()
        if not content or content == "当前暂无正文。":
            content = self.outline_text.get("1.0", tk.END).strip()
        if not content or content in {"当前暂无提纲。", "当前暂无正文。"}:
            messagebox.showinfo("提示", "当前没有可复制的内容", parent=self.root)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        messagebox.showinfo("提示", "内容已复制", parent=self.root)

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

    def _set_text_content(self, widget: ScrolledText, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", str(content or "").strip())
        widget.configure(state="disabled")


def launch_gui() -> None:
    root = tk.Tk()
    AppWindow(root)
    root.mainloop()
