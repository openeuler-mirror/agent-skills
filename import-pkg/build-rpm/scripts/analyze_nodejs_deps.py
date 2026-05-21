#!/usr/bin/env python3
"""
Node.js 包 RPM 依赖分析脚本

依赖来源：
  1. package.json — name / engines.node（版本约束）
  2. binding.gyp  — libraries 字段中的 -l<lib>（原生扩展）

RPM 查询策略（共享一次性批量查询）：
  Level 1: `pkgconfig(foo)`
  Level 2: `libfoo.so*` / `*-devel` 回退

固定 BuildRequires：nodejs-devel + npm（+ 原生扩展查到的系统库）

用法：
  python3 analyze_nodejs_deps.py <source_dir>
  python3 analyze_nodejs_deps.py <source_dir> --check-rpm --container oe-build-env
  python3 analyze_nodejs_deps.py <source_dir> --check-rpm --container oe-build-env -o result.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from rpm_batch_lookup import (
    BatchLookupError,
    fallback_results,
    file_glob_query,
    name_query,
    provides_query,
    run_batch_lookup,
)


GLIBC_BUILTINS = {"pthread", "m", "dl", "c", "rt", "gcc_s", "stdc++", "resolv", "util"}


# ── 1. 源码解析 ───────────────────────────────────────────────────────────────

def parse_package_json(source_dir: str) -> Dict:
    """解析 package.json，提取 name 和 engines.node"""
    pkg_json = Path(source_dir) / "package.json"
    if not pkg_json.exists():
        return {"found": False, "name": "", "node_version": "", "has_native": False}

    try:
        data = json.loads(pkg_json.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return {"found": True, "name": "", "node_version": "", "has_native": False}

    name = data.get("name", "")
    node_version = ""
    engines = data.get("engines", {})
    if isinstance(engines, dict):
        node_version = engines.get("node", "")

    # 检测是否有原生扩展
    has_native = (Path(source_dir) / "binding.gyp").exists()

    return {
        "found": True,
        "name": name,
        "node_version": node_version,
        "has_native": has_native,
    }


def parse_binding_gyp(source_dir: str) -> Dict:
    """
    解析 binding.gyp，提取 libraries 字段中的 -l<lib>。
    binding.gyp 是 JSON 超集（允许注释），用正则提取 libraries 数组。
    """
    gyp = Path(source_dir) / "binding.gyp"
    if not gyp.exists():
        return {"found": False, "link_libs": []}

    content = gyp.read_text(errors="ignore")
    link_libs: Set[str] = set()

    # 提取所有 "libraries": [...] 块
    for m in re.finditer(r'"libraries"\s*:\s*\[([^\]]*)\]', content, re.DOTALL):
        block = m.group(1)
        for lib in re.findall(r'-l(\w+)', block):
            if lib not in GLIBC_BUILTINS:
                link_libs.add(lib)

    return {"found": True, "link_libs": sorted(link_libs)}


# ── 2. 两级 RPM 查询 ──────────────────────────────────────────────────────────

def build_lookup_tasks(link_libs: List[str]) -> List[Dict]:
    tasks: List[Dict] = []
    for lib in link_libs:
        lib_lower = lib.lower()
        tasks.append({
            "dep": lib,
            "type": "link",
            "prefer_devel": True,
            "queries": [
                provides_query(f"pkgconfig({lib_lower})", "pkgconfig()"),
                file_glob_query(f"*/lib{lib_lower}.so*", "libso", prefer_devel=True),
                name_query(f"{lib_lower}-devel", "name", prefer_devel=True),
                name_query(f"lib{lib_lower}-devel", "name", prefer_devel=True),
            ],
        })
    return tasks


def check_rpm_availability(container: str, link_libs: List[str]) -> Dict:
    tasks = build_lookup_tasks(link_libs)
    print(f"\n[INFO] 在容器 {container} 内查询 RPM 可用性（单次批量查询）...")

    try:
        results = run_batch_lookup(container, tasks, timeout=120)
    except (BatchLookupError, OSError, json.JSONDecodeError) as e:
        print(f"[WARN] 批量 RPM 查询失败（{e}），跳过依赖检查")
        results = fallback_results(tasks)

    available, missing = [], []
    for item in results:
        lib, rpm, level = item["dep"], item.get("rpm"), item.get("level", "")
        if rpm:
            print(f"  ✓ [link] {lib:<38} → {rpm}  ({level})")
            available.append({"dep": lib, "type": "link", "rpm": rpm, "level": level})
        else:
            print(f"  ✗ [link] {lib:<38} → 未找到")
            missing.append({"dep": lib, "type": "link"})
    return {"available": available, "missing": missing}


# ── 3. BuildRequires 生成 & 报告 ──────────────────────────────────────────────

def build_rpm_requires(pkg_info: Dict, rpm_check: Optional[Dict]) -> List[str]:
    result = ["nodejs-devel", "npm"]
    node_ver = pkg_info.get("node_version", "")
    if node_ver:
        # 提取最低版本号，如 ">=16.0.0" → "16"
        m = re.search(r"(\d+)", node_ver)
        if m:
            result[0] = f"nodejs >= {m.group(1)}"

    if pkg_info.get("has_native"):
        result.append("gcc")
        result.append("gcc-c++")
        result.append("python3")   # node-gyp 依赖

    if rpm_check:
        seen = set(result)
        for item in rpm_check.get("available", []):
            rpm = item["rpm"]
            if rpm not in seen:
                result.append(rpm)
                seen.add(rpm)
    return result


def print_report(pkg_info: Dict, gyp_info: Dict, rpm_check: Optional[Dict]):
    sep = "=" * 60
    print(f"\n{sep}")
    print("Node.js 包 RPM 依赖分析报告")
    print(sep)
    print(f"  包名      : {pkg_info.get('name', '(未知)')}")
    if pkg_info.get("node_version"):
        print(f"  Node 版本 : {pkg_info['node_version']}")
    print(f"  原生扩展  : {'是' if pkg_info.get('has_native') else '否'}")

    if gyp_info.get("link_libs"):
        print(f"\n[binding.gyp -l 链接库]  {len(gyp_info['link_libs'])} 个")
        for lib in gyp_info["link_libs"]:
            print(f"  - {lib}")

    if rpm_check:
        avail = rpm_check["available"]
        miss  = rpm_check["missing"]
        print(f"\n[RPM 可用性]  已有 {len(avail)} / 缺失 {len(miss)}")
        for item in avail:
            print(f"  ✓ {item['dep']:<30} → {item['rpm']}  [{item['level']}]")
        for item in miss:
            print(f"  ✗ {item['dep']}")

    br = build_rpm_requires(pkg_info, rpm_check)
    print(f"\n[BuildRequires 建议]")
    for r in br:
        print(f"  BuildRequires: {r}")
    print(sep)


# ── 4. 主入口 ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Node.js 包 RPM 依赖分析")
    parser.add_argument("source_dir", help="Node.js 项目源码目录")
    parser.add_argument("--check-rpm", action="store_true", help="在容器内查询 RPM 可用性")
    parser.add_argument("--container", default="oe-build-env", help="OpenEuler 容器名")
    parser.add_argument("-o", "--output", default="", help="结果输出到 JSON 文件")
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source_dir)
    if not os.path.isdir(source_dir):
        print(f"[ERROR] 目录不存在: {source_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] 分析目录: {source_dir}")
    pkg_info = parse_package_json(source_dir)
    gyp_info = parse_binding_gyp(source_dir)

    if not pkg_info["found"]:
        print("[WARN] 未找到 package.json，可能不是 Node.js 项目", file=sys.stderr)

    rpm_check = None
    if args.check_rpm:
        link_libs = gyp_info.get("link_libs", [])
        if not link_libs:
            print("[INFO] 未检测到原生扩展依赖，跳过 RPM 查询")
        else:
            print(f"\n[INFO] 在容器 {args.container} 内查询 RPM 可用性...")
            rpm_check = check_rpm_availability(args.container, link_libs)

    print_report(pkg_info, gyp_info, rpm_check)

    if args.output:
        result = {
            "name": pkg_info.get("name", ""),
            "node_version": pkg_info.get("node_version", ""),
            "has_native": pkg_info.get("has_native", False),
            "link_libs": gyp_info.get("link_libs", []),
            "rpm_check": rpm_check,
            "build_requires": build_rpm_requires(pkg_info, rpm_check),
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] 结果已保存: {args.output}")

    if rpm_check and rpm_check["missing"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
