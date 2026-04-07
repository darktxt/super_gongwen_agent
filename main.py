from __future__ import annotations

import argparse
import sys

from app import create_app
from config import load_config
from result_assembler.assembler import ResultAssembler
from utils.session_ids import generate_session_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap or run super-gongwen-agent.")
    parser.add_argument("--session-id", dest="session_id", help="Optional session id to initialize.")
    parser.add_argument(
        "--base-dir",
        dest="base_dir",
        help="Base directory used to resolve .super_gongwen when SUPER_GONGWEN_HOME is not set.",
    )
    parser.add_argument(
        "--user-input",
        dest="user_input",
        help="Run one drafting turn with the given user input.",
    )
    parser.add_argument(
        "--max-rounds",
        dest="max_rounds",
        type=int,
        default=16,
        help="Maximum internal LLM rounds allowed for a single turn.",
    )
    return parser


def _read_follow_up_input(status: str) -> str | None:
    if status == "completed":
        print("")
        print("已进入终稿后交互模式。")
        print("请输入修改意见或重写要求。单独输入 /end 提交，输入 /exit 结束。")
    else:
        print("")
        print("已进入补充信息交互模式。")
        print("请输入补充材料或说明。单独输入 /end 提交，输入 /exit 结束。")

    lines: list[str] = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            print("")
            return None

        normalized = line.strip()
        if normalized == "/exit":
            return None
        if normalized == "/end":
            if lines:
                return "\n".join(lines).strip()
            print("请至少输入一行内容，或输入 /exit 结束。")
            continue
        lines.append(line)


def _should_enter_interactive_loop() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _emit_progress(message: str) -> None:
    print(message, flush=True)


def _emit_round_progress(assembler: ResultAssembler, turn_result: object) -> None:
    rendered = assembler.render_round_progress(turn_result)
    if rendered.strip():
        print("")
        print(rendered, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    assembler = ResultAssembler()

    app = create_app(
        config=load_config(base_dir=args.base_dir),
        progress_reporter=_emit_progress if args.user_input else None,
        round_reporter=(lambda turn_result: _emit_round_progress(assembler, turn_result))
        if args.user_input
        else None,
    )
    if args.user_input:
        session_id = args.session_id or generate_session_id()
        turn_result = app.run_turn(
            session_id=session_id,
            user_input=args.user_input,
            max_rounds=args.max_rounds,
        )

        while True:
            view_model = assembler.assemble(turn_result)
            print(assembler.render_text(view_model))

            if turn_result.status not in {"completed", "needs_user_input"}:
                return 1

            if not _should_enter_interactive_loop():
                return 0

            follow_up_input = _read_follow_up_input(turn_result.status)
            if not follow_up_input:
                return 0

            turn_result = app.run_turn(
                session_id=session_id,
                user_input=follow_up_input,
                max_rounds=args.max_rounds,
            )

    result = app.bootstrap(session_id=args.session_id)
    print("会话已初始化。")
    if result.session_id:
        print(f"会话ID：{result.session_id}")
        print("可使用该会话ID继续发起或续写公文。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
