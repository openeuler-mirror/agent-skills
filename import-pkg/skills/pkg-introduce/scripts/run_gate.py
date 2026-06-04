#!/usr/bin/env python3
"""Phase 2：引入门禁（需要容器）

前提：run_check.py 已成功跑完（overall_status=done），
      check_result_<pkgname>.json 中有确认的 lang/version。

步骤：setup_env → existing_check

输出 gate_result_<pkgname>.json，格式：
  overall_status: "done" | "failed"
  steps.<step>.status: "done" | "failed"
  result.decision: "reuse_official" | "reuse_user_repo" | "introduce_new" |
                   "upgrade_user_repo" | "block_official_older"

exit codes:
  0  overall_status=done   — decision 已确认，可以进 build-rpm 或直接结束
  1  overall_status=failed — 硬失败
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PKG_SCRIPTS = Path(__file__).resolve().parent
_SETUP_CONTAINER_SCRIPT = Path(__file__).resolve().parent / "setup_container.py"
sys.path.insert(0, str(_PKG_SCRIPTS))

from run_pkg_introduce_flow import (  # noqa: E402
    FlowError,
    run_existing_check,
    run_command,
)

GATE_STEPS = ["setup_env", "existing_check"]


# ── Helpers ────────────────────────────────────────────────────────────────

def _save(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _already_done(step_data: dict[str, Any]) -> bool:
    return step_data.get("status") in ("done", "skipped")


# ── Step runners ───────────────────────────────────────────────────────────

def _run_setup_env(report: dict, source_dir: Path, lang: str, container: str, mode: str) -> None:
    if mode == "dependency":
        proc = run_command(["docker", "inspect", container])
        if proc.returncode == 0:
            report["steps"]["setup_env"] = {
                "status": "done", "container": container, "note": "dependency mode: reused"
            }
        else:
            report["steps"]["setup_env"] = {
                "status": "failed", "reason": f"dependency mode requires existing container {container}"
            }
            raise FlowError(f"container {container} not found for dependency mode")
        return

    # 容器已存在且运行中，直接复用，不重新创建
    proc = run_command(["docker", "inspect", "--format", "{{.State.Running}}", container])
    if proc.returncode == 0 and proc.stdout.strip() == "true":
        report["steps"]["setup_env"] = {"status": "done", "container": container, "note": "reused existing container"}
        return

    proc = run_command([
        sys.executable, str(_SETUP_CONTAINER_SCRIPT),
        "--source-dir", str(source_dir),
        "--install-base",
        "--lang", lang,
        "--name", container,
    ])
    if proc.returncode != 0:
        reason = proc.stderr.strip() or proc.stdout.strip() or "setup_container failed"
        report["steps"]["setup_env"] = {"status": "failed", "reason": reason}
        raise FlowError(reason)
    report["steps"]["setup_env"] = {"status": "done", "container": container}


def _run_existing_check(report: dict, pkgname: str, version: str, lang: str,
                        reports_dir: Path, container: str, constraint: str = "") -> str:
    try:
        result = run_existing_check(pkgname, version, lang, reports_dir, container, constraint=constraint)
        decision = result.get("decision", "")
        report["steps"]["existing_check"] = {
            "status": "done",
            "decision": decision,
            "reason": result.get("reason", ""),
        }
        return decision
    except FlowError as exc:
        report["steps"]["existing_check"] = {"status": "failed", "reason": exc.reason}
        raise


# ── Main ───────────────────────────────────────────────────────────────────

def run_gate(args: argparse.Namespace) -> int:
    reports_dir = Path(args.reports_dir)
    check_report_path = reports_dir / f"check_result_{args.pkg}.json"
    gate_report_path = reports_dir / f"gate_result_{args.pkg}.json"

    # Load check result to get lang/version/source_dir
    if not check_report_path.exists():
        print(f"[ERROR] check_result_{args.pkg}.json not found; run run_check.py first", file=sys.stderr)
        return 1
    check = json.loads(check_report_path.read_text(encoding="utf-8"))
    if check.get("overall_status") != "done":
        print(f"[ERROR] check phase not done (status={check.get('overall_status')}); "
              "resolve needs_ai steps in check_result first", file=sys.stderr)
        return 1

    cr = check.get("result") or {}
    lang = args.lang or cr.get("lang", "")
    version = args.version or cr.get("version", "")
    source_dir = Path(args.source_dir or cr.get("source_dir", f"./sources/{args.pkg}"))

    if not lang or not version:
        print(f"[ERROR] lang or version missing (lang={lang!r}, version={version!r}); "
              "check check_result.result fields", file=sys.stderr)
        return 1

    # Load or init gate report
    if gate_report_path.exists():
        report = json.loads(gate_report_path.read_text(encoding="utf-8"))
    else:
        report = {
            "pkgname": args.pkg,
            "lang": lang,
            "version": version,
            "overall_status": "pending",
            "steps": {step: {"status": "pending"} for step in GATE_STEPS},
            "result": None,
        }

    steps = report["steps"]

    # Derive container name from session.json if not provided
    container = args.container
    if not container:
        session_path = Path("./session.json")
        if session_path.exists():
            container = json.loads(session_path.read_text(encoding="utf-8")).get("container", "oe-build-env")
        else:
            container = "oe-build-env"

    try:
        # ── setup_env ─────────────────────────────────────────────────────
        if not _already_done(steps["setup_env"]):
            _run_setup_env(report, source_dir, lang, container, args.mode)
            _save(report, gate_report_path)

        # ── existing_check ────────────────────────────────────────────────
        if not _already_done(steps["existing_check"]):
            decision = _run_existing_check(report, args.pkg, version, lang, reports_dir, container, args.constraint)
            _save(report, gate_report_path)

    except FlowError:
        report["overall_status"] = "failed"
        _save(report, gate_report_path)
        print(json.dumps({"status": "failed", "report": str(gate_report_path)}, ensure_ascii=False))
        return 1

    all_done = all(s.get("status") in ("done", "skipped") for s in steps.values())
    report["overall_status"] = "done" if all_done else "failed"
    report["result"] = {
        "lang": lang,
        "version": version,
        "source_dir": str(source_dir),
        "decision": steps.get("existing_check", {}).get("decision", ""),
        "container": container,
    }
    _save(report, gate_report_path)

    print(json.dumps({"status": report["overall_status"], "report": str(gate_report_path)},
                     ensure_ascii=False))
    return 0 if report["overall_status"] == "done" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="pkg-introduce Phase 2: 引入门禁（需要容器）")
    parser.add_argument("--pkg", required=True)
    parser.add_argument("--url", required=True, dest="upstream_url")
    parser.add_argument("--lang", default="", help="Language (read from check_result if omitted)")
    parser.add_argument("--version", default="", help="Version (read from check_result if omitted)")
    parser.add_argument("--constraint", default="", help="版本约束（如 >=1.0,<2.0），传给 existing_check 做 reuse 判断")
    parser.add_argument("--source-dir", default="", dest="source_dir",
                        help="Source dir (read from check_result if omitted)")
    parser.add_argument("--mode", default="top-level", choices=["top-level", "dependency"])
    parser.add_argument("--container", default="", help="Container name (reads session.json if omitted)")
    parser.add_argument("--reports-dir", default="./reports", dest="reports_dir")
    parser.add_argument("--pkg-dir", default=None, dest="pkg_dir")
    args = parser.parse_args()
    if args.pkg_dir:
        args.reports_dir = args.pkg_dir
    return run_gate(args)


if __name__ == "__main__":
    sys.exit(main())
