from __future__ import annotations
import argparse
from app import create_app
from config import load_config
from session_storage.paths import build_session_paths
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap or run super-gongwen-agent.")
    parser.add_argument("--session-id", dest="session_id", help="Optional session id to initialize.")
    parser.add_argument("--base-dir", dest="base_dir", help="Base directory for .super_gongwen.")
    parser.add_argument("--user-input", dest="user_input", help="Run one drafting turn with the given user input.")
    return parser
def _emit_progress(message: str) -> None:
    print(message, flush=True)
def _render_turn(turn_result: object) -> str:
    lines: list[str] = []
    response_text = getattr(turn_result, "response_text", "")
    final_text = getattr(turn_result, "final_text", "")
    question_pack = getattr(turn_result, "question_pack", [])
    assumptions = getattr(turn_result, "assumptions", [])
    major_risks = getattr(turn_result, "major_risks", [])
    final_output_path = getattr(turn_result, "final_output_path", "")
    if response_text:
        lines.append(response_text)
    if assumptions:
        lines.append("假设：")
        lines.extend(f"- {item}" for item in assumptions)
    if major_risks:
        lines.append("主要风险：")
        lines.extend(f"- {item}" for item in major_risks)
    if question_pack:
        lines.append("需要你补充的信息：")
        lines.extend(f"- {item.get('question', '')}" for item in question_pack if item.get("question"))
    if final_text:
        lines.append("正文：")
        lines.append(final_text)
    if final_output_path:
        lines.append(f"已写出：{final_output_path}")
    return "\n".join(line for line in lines if line).strip()
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = create_app(config=load_config(base_dir=args.base_dir), progress_reporter=_emit_progress if args.user_input else None)
    if args.user_input:
        session_id = args.session_id or app.bootstrap().session_id
        assert session_id is not None
        print(_render_turn(app.run_turn(session_id=session_id, user_input=args.user_input)))
        return 0
    result = app.bootstrap(session_id=args.session_id)
    print("会话已初始化。")
    if result.session_id:
        print(f"会话ID：{result.session_id}")
        print(f"工作区：{build_session_paths(result.session_id, app_home=result.app_home).workspace_path}")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
