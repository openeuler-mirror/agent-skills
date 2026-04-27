#!/usr/bin/env python3
"""根据 pkg-introduce 结果文件收尾依赖状态。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SUCCESS_ACTIONS = {"built_new", "upgraded_user_repo", "reused_official", "reused_user_repo"}
INTRODUCED_ACTIONS = {"built_new", "upgraded_user_repo"}
REUSED_ACTIONS = {"reused_official", "reused_user_repo"}
BLOCKED_ACTIONS = {"blocked"}


def result_path(pkgname: str, reports_dir: str) -> Path:
    return Path(reports_dir) / f"pkg_introduce_result_{pkgname}.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def remove_exact_line(path: Path, line: str) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [item for item in lines if item.strip() != line]
    path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")


def append_unique_line(path: Path, line: str) -> None:
    existing = set()
    if path.exists():
        existing = {item.strip() for item in path.read_text(encoding="utf-8").splitlines() if item.strip()}
    if line in existing:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def summarize(dep_pkgname: str, action: str, reason: str, introduced: bool, result_file: Path) -> dict[str, Any]:
    return {
        "dep_pkgname": dep_pkgname,
        "action": action,
        "reason": reason,
        "introduced": introduced,
        "result_file": str(result_file),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="根据 pkg-introduce 结果文件更新 building/introduced 状态")
    parser.add_argument("dep_pkgname", help="依赖包名")
    parser.add_argument("--build-state-dir", default="./build_state", help="状态目录，默认 ./build_state")
    parser.add_argument("--reports-dir", default="./reports", help="结果文件目录，默认 ./reports")
    parser.add_argument("--keep-building", action="store_true", help="不自动从 building.txt 清理该依赖")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON 结果")
    args = parser.parse_args()

    build_state_dir = Path(args.build_state_dir)
    reports_dir = Path(args.reports_dir)
    building_file = build_state_dir / "building.txt"
    introduced_file = build_state_dir / "introduced.txt"
    result_file = result_path(args.dep_pkgname, str(reports_dir))

    if not result_file.exists():
        # 即使结果文件缺失，也要清理 building.txt，防止残留阻塞后续构建
        if not args.keep_building:
            build_state_dir.mkdir(parents=True, exist_ok=True)
            remove_exact_line(building_file, args.dep_pkgname)
        print(f"结果文件不存在: {result_file}", file=sys.stderr)
        return 1

    data = read_json(result_file)
    action = (data.get("action") or "").strip()
    reason = data.get("reason", "")

    if not args.keep_building:
        build_state_dir.mkdir(parents=True, exist_ok=True)
        remove_exact_line(building_file, args.dep_pkgname)

    if not action:
        # action 为空说明 pkg-introduce 异常退出，building.txt 已清理，直接报错
        print(f"action 为空，pkg-introduce 可能异常退出: {result_file}", file=sys.stderr)
        return 1

    if action in INTRODUCED_ACTIONS:
        build_state_dir.mkdir(parents=True, exist_ok=True)
        append_unique_line(introduced_file, args.dep_pkgname)
        payload = summarize(args.dep_pkgname, action, reason, True, result_file)
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"introduced {args.dep_pkgname} {action}")
        return 0

    if action in REUSED_ACTIONS:
        payload = summarize(args.dep_pkgname, action, reason, False, result_file)
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"reused {args.dep_pkgname} {action}")
        return 0

    if action in BLOCKED_ACTIONS:
        payload = summarize(args.dep_pkgname, action, reason, False, result_file)
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"blocked {args.dep_pkgname} {reason}".rstrip())
        return 1

    print(f"未知 action: {action}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
