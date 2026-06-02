#!/usr/bin/env python3
"""import-package-step 状态机。

读取 session 状态，输出下一步 action，并在 action 完成后更新状态。

用法：
  # 读状态，输出 action
  python3 step_supervisor.py --session-dir /path/to/session

  # action 完成后更新状态
  python3 step_supervisor.py --session-dir /path/to/session \
      --update-action build_dep --update-target dj-static \
      --build-result success --ci-status pass

  # 标记 dep 为 reused（evaluate 完成后）
  python3 step_supervisor.py --session-dir /path/to/session \
      --update-action evaluate --update-target static3 \
      --gate-decision reuse_official

输出 JSON：
  {"action": "build_dep", "target": "dj-static", "delay": 60, "loop": 6}
  {"action": "done", "target": "sites-faciles", "delay": null, "loop": 10}
  {"action": "fail", "target": "dep build_failed: [...]", "delay": null, "loop": 3}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# build_rpm_result.json 的合法终态
# precheck_done  — 预检通过但构建未完成（agent 中断），视为"待构建"
# interrupted    — agent 异常退出，视为"待构建"
VALID_BUILD_STATUSES = {
    "success", "dep_needed", "failed", "ci_failed", "precheck_done", "interrupted"
}

# dep_registry 中表示"已就绪"的状态（等价于 build_done）
DEP_READY_STATUSES = {"build_done", "reused"}

# 编译慢的语言，用较长延迟
SLOW_LANGS = {"rust", "go", "c", "cpp"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_lang(sd: Path, pkgname: str) -> str:
    gate_f = sd / f"pkgs/{pkgname}/gate_result_{pkgname}.json"
    if gate_f.exists():
        return read_json(gate_f).get("result", {}).get("lang", "")
    return ""


def build_delay(lang: str) -> int:
    return 270 if lang in SLOW_LANGS else 60


def determine_action(sd: Path, wf: dict, reg: dict) -> tuple[str, str, int | None]:
    """返回 (action, target, delay_seconds)。delay=None 表示停止循环。"""
    PKGNAME = wf["pkgname"]

    # 读主包 build_rpm_result
    main_result_path = sd / f"pkgs/{PKGNAME}/build_rpm_result.json"
    main_result = read_json(main_result_path) if main_result_path.exists() else None
    main_status = main_result.get("status") if main_result else None
    if main_status and main_status not in VALID_BUILD_STATUSES:
        main_status = None
        main_result = None

    # 优先级 1：有 dep 待 evaluate
    pending_eval = [k for k, v in reg.items() if v["status"] == "pending_evaluate"]
    if pending_eval:
        return ("evaluate", pending_eval[0], 60)

    # 优先级 2：有 dep 待构建
    pending_build = [k for k, v in reg.items() if v["status"] == "evaluate_done"]
    if pending_build:
        dep = pending_build[0]
        lang = get_lang(sd, dep)
        return ("build_dep", dep, build_delay(lang))

    # 优先级 3：有 dep 构建失败
    failed_deps = [k for k, v in reg.items() if v["status"] == "build_failed"]
    if failed_deps:
        return ("fail", f"dep build_failed: {failed_deps}", None)

    # 优先级 4：所有 dep 完成（或无 dep），处理主包
    all_deps_ready = all(v["status"] in DEP_READY_STATUSES for v in reg.values())
    if all_deps_ready or not reg:
        if main_status in (None, "dep_needed", "precheck_done", "interrupted"):
            lang = get_lang(sd, PKGNAME)
            return ("build_main", PKGNAME, build_delay(lang))

        if main_status in ("failed", "ci_failed"):
            return ("fail", f"main build {main_status}", None)

        if main_status == "success" and (main_result or {}).get("ci_status") == "pass":
            # 优先级 5：待 critique
            critique_file = sd / f"pkgs/{PKGNAME}/critique_round1_{PKGNAME}.json"
            if not critique_file.exists():
                return ("critique", PKGNAME, 60)

            verdict = read_json(critique_file).get("verdict", "")
            retry = wf.get("critique_retry", 0)

            if verdict == "PASS":
                # 优先级 7：待 feedback
                feedback_file = sd / f"pkgs/{PKGNAME}/feedback_{PKGNAME}.json"
                if not feedback_file.exists():
                    return ("feedback", PKGNAME, 60)
                return ("done", PKGNAME, None)

            if verdict == "FIX_REQUIRED":
                if retry < 3:
                    # 优先级 6：重建
                    lang = get_lang(sd, PKGNAME)
                    return ("rebuild", PKGNAME, build_delay(lang))
                else:
                    # 超过重试次数，接受当前状态
                    feedback_file = sd / f"pkgs/{PKGNAME}/feedback_{PKGNAME}.json"
                    if not feedback_file.exists():
                        return ("feedback", PKGNAME, 60)
                    return ("done", PKGNAME, None)

            if verdict == "ABORT":
                return ("fail", "critique ABORT", None)

            # verdict 为空，重新 critique
            return ("critique", PKGNAME, 60)

        return ("fail", f"unexpected main_status: {main_status}", None)

    return ("fail", "unexpected dep_registry state", None)


def update_after_evaluate(sd: Path, reg: dict, reg_path: Path, target: str, gate_decision: str) -> None:
    """evaluate 完成后更新 dep_registry。"""
    if "reuse" in gate_decision:
        reg[target]["status"] = "reused"
    elif gate_decision in ("introduce_new", "upgrade_user_repo"):
        reg[target]["status"] = "evaluate_done"
    else:
        reg[target]["status"] = "build_failed"
        reg[target]["error"] = gate_decision
    write_json(reg_path, reg)


def update_after_build(
    sd: Path, wf: dict, wf_path: Path, reg: dict, reg_path: Path,
    target: str, build_status: str, ci_status: str, is_dep: bool
) -> None:
    """build_dep / build_main 完成后更新状态。"""
    if build_status == "success" and ci_status == "pass":
        if is_dep:
            reg[target]["status"] = "build_done"
            write_json(reg_path, reg)
        wf.setdefault("built_pkgs", [])
        if target not in wf["built_pkgs"]:
            wf["built_pkgs"].append(target)

    elif build_status == "dep_needed":
        # 新 dep 已写入 dep_registry，重新读取
        reg_new = read_json(reg_path)
        reg.clear()
        reg.update(reg_new)

    elif build_status in ("precheck_done", "interrupted") or build_status not in VALID_BUILD_STATUSES:
        # 构建未完成，保持 evaluate_done，下次重建
        print(f"[warn] {target} build_rpm_result.status={build_status!r}, will retry", file=sys.stderr)

    else:
        # failed / ci_failed
        if is_dep:
            reg[target]["status"] = "build_failed"
            reg[target]["error"] = build_status
            write_json(reg_path, reg)


def main() -> int:
    parser = argparse.ArgumentParser(description="import-package-step 状态机")
    parser.add_argument("--session-dir", required=True)

    # 更新模式参数
    parser.add_argument("--update-action", choices=["evaluate", "build_dep", "build_main", "rebuild", "critique_retry"])
    parser.add_argument("--update-target", default="")
    parser.add_argument("--gate-decision", default="")   # evaluate 完成后
    parser.add_argument("--build-result", default="")    # build 完成后
    parser.add_argument("--ci-status", default="")       # build 完成后

    args = parser.parse_args()
    sd = Path(args.session_dir)

    wf_files = list(sd.glob("workflow_*.json"))
    if not wf_files:
        print(json.dumps({"error": "no workflow file found"}))
        return 1
    wf_path = wf_files[0]
    wf = read_json(wf_path)
    PKGNAME = wf["pkgname"]

    reg_path = sd / "dep_registry.json"
    reg = read_json(reg_path) if reg_path.exists() else {}

    # ── 更新模式 ──────────────────────────────────────────────────────────────
    if args.update_action:
        if args.update_action == "evaluate":
            update_after_evaluate(sd, reg, reg_path, args.update_target, args.gate_decision)

        elif args.update_action in ("build_dep", "build_main"):
            is_dep = args.update_action == "build_dep"
            update_after_build(
                sd, wf, wf_path, reg, reg_path,
                args.update_target, args.build_result, args.ci_status, is_dep
            )

        elif args.update_action == "critique_retry":
            # 删除旧 critique 文件，递增重试计数
            critique_file = sd / f"pkgs/{PKGNAME}/critique_round1_{PKGNAME}.json"
            if critique_file.exists():
                critique_file.unlink()
            wf["critique_retry"] = wf.get("critique_retry", 0) + 1

        wf["loop_count"] = wf.get("loop_count", 0) + 1
        write_json(wf_path, wf)
        print(json.dumps({"updated": True}))
        return 0

    # ── 读状态模式：输出下一步 action ─────────────────────────────────────────
    # 检查 dep 的非标准 status，打印警告
    for dep_name, dep_info in reg.items():
        if dep_info["status"] != "evaluate_done":
            continue
        dep_result_path = sd / f"pkgs/{dep_name}/build_rpm_result.json"
        if dep_result_path.exists():
            dep_status = read_json(dep_result_path).get("status")
            if dep_status and dep_status not in VALID_BUILD_STATUSES:
                print(f"[warn] dep {dep_name} non-standard status={dep_status!r}, will rebuild", file=sys.stderr)

    action, target, delay = determine_action(sd, wf, reg)
    loop = wf.get("loop_count", 0) + 1

    result = {"action": action, "target": target, "delay": delay, "loop": loop, "pkgname": PKGNAME}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
