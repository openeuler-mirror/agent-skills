#!/usr/bin/env python3
"""
RPM 编译前依赖预检脚本

在 rpmbuild 循环前调用，分析运行时依赖在 OpenEuler 源、官方归档仓库、用户 RPM 仓库中的可用性，
输出需要递归引入的包列表（格式：<pkgname> <upstream_url>），供调用方继续处理。

用法：
  python3 pre_check_deps.py <pkgname> <lang> <source_dir> [--container oe-build-env]

退出码：
  0 — 所有依赖均已满足或可复用
  2 — 存在需要递归引入/升级的依赖（stdout 输出 <name> <url> 列表）
  1 — 存在阻断项或脚本执行出错
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent.parent.parent / "pkg-introduce" / "scripts"
CHECK_EXISTING_SCRIPT = SCRIPT_DIR / "check_existing_package.py"

# ── 语言 → 分析脚本映射 ───────────────────────────────────────────────────────

ANALYZERS = {
    "python": {"script": "analyze_python_deps.py", "extra_args": []},
    "go":     {"script": "analyze_go_deps.py",     "extra_args": []},
    "rust":   {"script": "analyze_rust_deps.py",   "extra_args": []},
    "c":      {"script": "analyze_c_deps.py",      "extra_args": []},
    "cpp":    {"script": "analyze_cpp_deps.py",    "extra_args": []},
    "nodejs": {"script": "analyze_nodejs_deps.py", "extra_args": []},
    "java":   {"script": "analyze_java_deps.py",   "extra_args": []},
    "ruby":   {"script": "analyze_ruby_deps.py",   "extra_args": []},
}


def load_existing_checker() -> Any:
    script_dir = str(SCRIPT_DIR)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("check_existing_package", CHECK_EXISTING_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本: {CHECK_EXISTING_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXISTING_CHECKER = load_existing_checker()


# ── PyPI 上游地址查询 ──────────────────────────────────────────────────────────

def _github_search_repo(pkg_name: str) -> str:
    """通过 GitHub Search API 查找包名对应的仓库，返回 html_url 或空串。"""
    # 尝试常见 org 直接命中（避免 Search API 速率限制）
    normalized = pkg_name.replace("-", "_")
    for candidate in [pkg_name, normalized]:
        for org in ["", "BeanieODM", "roman-right"]:
            path = f"{org}/{candidate}" if org else candidate
            try:
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{path}",
                    headers={"User-Agent": "pre_check_deps/1.0", "Accept": "application/vnd.github+json"},
                )
                data = json.loads(urllib.request.urlopen(req, timeout=8).read())
                if data.get("html_url"):
                    return data["html_url"]
            except Exception:
                pass
    # fallback: GitHub Search API
    try:
        query = urllib.parse.quote(f"{pkg_name} language:python")
        req = urllib.request.Request(
            f"https://api.github.com/search/repositories?q={query}&per_page=3",
            headers={"User-Agent": "pre_check_deps/1.0", "Accept": "application/vnd.github+json"},
        )
        results = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for item in results.get("items", []):
            name_lower = item.get("name", "").lower().replace("-", "_")
            pkg_lower = pkg_name.lower().replace("-", "_")
            if name_lower == pkg_lower and item.get("html_url"):
                return item["html_url"]
    except Exception:
        pass
    return ""


def get_pypi_upstream(pypi_name: str) -> str:
    """从 PyPI JSON API 提取项目上游地址（GitHub/GitLab 优先）。
    若 PyPI metadata 无 URL，额外尝试 GitHub 搜索。
    """
    try:
        req = urllib.request.Request(
            f"https://pypi.org/pypi/{pypi_name}/json",
            headers={"User-Agent": "pre_check_deps/1.0"},
        )
        info = json.loads(urllib.request.urlopen(req, timeout=10).read())["info"]
        candidates = []
        if info.get("home_page"):
            candidates.append(info["home_page"])
        for url in (info.get("project_urls") or {}).values():
            if url:
                candidates.append(url)
        for url in candidates:
            if url.startswith("http") and "pypi.org" not in url:
                return url
    except Exception:
        pass
    # PyPI metadata 无有效 URL，尝试 GitHub 搜索
    github_url = _github_search_repo(pypi_name)
    if github_url:
        return github_url
    return f"https://pypi.org/project/{pypi_name}"


# ── 通用辅助 ──────────────────────────────────────────────────────────────────

def resolve_python_executable() -> str:
    """优先使用 python3.11，不存在时回退到当前 python3。"""
    candidates = [
        "/usr/bin/python3.11",
        "/usr/local/bin/python3.11",
        shutil.which("python3.11"),
        sys.executable,
        shutil.which("python3"),
    ]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if candidate.startswith("/") and not Path(candidate).exists():
            continue
        return candidate
    return "python3"


def make_output_path(pkgname: str, requested: str) -> str:
    return requested or f"/tmp/dep_check_{pkgname}.json"


def make_analysis_path(output_path: str, pkgname: str) -> Path:
    out_path = Path(output_path)
    suffix = out_path.suffix or ".json"
    stem = out_path.name[:-len(suffix)] if out_path.name.endswith(suffix) else out_path.name
    return out_path.with_name(f"{stem}_analysis{suffix}") if stem else Path(f"/tmp/dep_check_{pkgname}_analysis.json")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_source_match(dep: dict[str, Any], source_item: dict[str, Any] | None) -> dict[str, Any]:
    requirement = dep.get("requirement", "")
    if not source_item:
        return {
            "status": "missing",
            "rpm": None,
            "version": None,
            "release": None,
            "satisfies_requirement": False,
            "reason": "OpenEuler 源中未找到可用包",
        }

    requirement_info = EXISTING_CHECKER.parse_requirement(requirement)
    version = source_item.get("version")
    if requirement_info["status"] == "parsed":
        satisfies = EXISTING_CHECKER.evaluate_requirement(version, requirement_info)
        if satisfies:
            reason = f"OpenEuler 源中已有满足约束 {requirement} 的包"
            status = "satisfied"
        else:
            reason = f"OpenEuler 源中已有包，但版本 {version or '未知'} 不满足约束 {requirement}"
            status = "older"
    elif requirement_info["status"] == "unknown":
        satisfies = False
        reason = f"OpenEuler 源中已有包，但版本约束 {requirement} 无法可靠解析，保守继续"
        status = "unknown_requirement"
    else:
        satisfies = True
        reason = "OpenEuler 源中已有可用包"
        status = "satisfied"

    return {
        "status": status,
        "rpm": source_item.get("rpm"),
        "version": version,
        "release": source_item.get("release"),
        "satisfies_requirement": bool(satisfies),
        "reason": reason,
    }


def build_source_index(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        keys = {
            (item.get("dep", ""), item.get("requirement", "")),
            (item.get("dep", ""), ""),
            (item.get("name", ""), item.get("requirement", "")),
            (item.get("name", ""), ""),
        }
        for key in keys:
            if key[0]:
                index.setdefault(key, item)
    return index


def lookup_source_item(dep: dict[str, Any], source_index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    keys = [
        (dep.get("dep", ""), dep.get("requirement", "")),
        (dep.get("dep", ""), ""),
        (dep.get("name", ""), dep.get("requirement", "")),
        (dep.get("name", ""), ""),
    ]
    for key in keys:
        if key[0] and key in source_index:
            return source_index[key]
    return None


def merge_official_source_older_result(
    dep: dict[str, Any],
    source_check: dict[str, Any],
    existing_check: dict[str, Any],
) -> dict[str, Any]:
    requested = dict(existing_check.get("requested") or {})
    requested_version = (requested.get("version") or "").strip()
    requirement = (requested.get("requirement") or dep.get("requirement") or "").strip()

    official = dict(existing_check.get("official") or {})
    user_repo = dict(existing_check.get("user_repo") or {})

    highest = {
        "path": "<openeuler-source>",
        "match_type": "source_repo",
        "name": source_check.get("rpm") or dep.get("name") or dep.get("dep") or "",
        "version": source_check.get("version"),
        "release": source_check.get("release"),
        "arch": None,
    }

    matched_paths = list(official.get("matched_paths") or [])
    if "<openeuler-source>" not in matched_paths:
        matched_paths.append("<openeuler-source>")

    candidates = list(official.get("candidates") or [])
    candidates.append(highest)

    official.update(
        {
            "exists": True,
            "matched_paths": matched_paths,
            "candidates": candidates,
            "highest": highest,
            "satisfies_requested_version": False if requested_version else None,
            "satisfies_requirement": False,
            "meets_need": False,
            "comparison_unknown": False,
        }
    )

    decision = EXISTING_CHECKER.choose_decision(official, user_repo, requested_version, requirement)
    patched = dict(existing_check)
    patched["official"] = official
    patched["exists_in_official"] = True
    patched["decision"] = decision
    patched["reason"] = EXISTING_CHECKER.build_reason(
        decision,
        official,
        user_repo,
        requested_version,
        requirement,
    )
    return patched


def resolve_upstream_url(name: str, lang: str) -> str:
    """尝试为任意语言的依赖包解析上游 URL。
    策略：语言特定注册表 → GitHub 搜索 → 空串（由调用方决定如何处理）。
    """
    if not name:
        return ""
    if lang == "go":
        # Go module path 本身就是 URL，直接推导，无需查注册表
        # 例：github.com/gin-gonic/gin → https://github.com/gin-gonic/gin
        if name.startswith("github.com/") or name.startswith("gitlab.com/") or name.startswith("golang.org/"):
            return "https://" + name
        # 其他 module path（如 k8s.io/xxx）走 GitHub 搜索兜底
        return _github_search_repo(name.split("/")[-1])
    if lang == "python":
        return get_pypi_upstream(name)
    if lang == "rust":
        try:
            req = urllib.request.Request(
                f"https://crates.io/api/v1/crates/{name}",
                headers={"User-Agent": "pre_check_deps/1.0"},
            )
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            repo = data.get("crate", {}).get("repository") or data.get("crate", {}).get("homepage")
            if repo:
                return repo
        except Exception:
            pass
    if lang == "nodejs":
        try:
            req = urllib.request.Request(
                f"https://registry.npmjs.org/{name}/latest",
                headers={"User-Agent": "pre_check_deps/1.0"},
            )
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            repo = data.get("repository", {})
            if isinstance(repo, dict):
                url = repo.get("url", "")
            else:
                url = str(repo)
            # 规范化 git+https://github.com/... 或 github:foo/bar
            url = url.replace("git+", "").replace("git://", "https://")
            if url.startswith("github:"):
                url = "https://github.com/" + url[7:]
            url = url.rstrip("/").removesuffix(".git")
            if url and "pypi.org" not in url:
                return url
        except Exception:
            pass
    # 通用兜底：GitHub 搜索
    return _github_search_repo(name)


def normalize_dependency_item(item: dict[str, Any], lang: str, category: str) -> dict[str, Any]:
    name = item.get("name") or item.get("dep") or ""
    upstream_url = item.get("upstream_url", "")
    if not upstream_url and name:
        upstream_url = resolve_upstream_url(name, lang)
    return {
        "name": name,
        "dep": item.get("dep") or name,
        "spec": item.get("spec") or item.get("dep") or name,
        "type": item.get("type") or lang,
        "category": category,
        "requirement": item.get("requirement", ""),
        "rpm_requirement": item.get("rpm_requirement") or item.get("rpm_name") or item.get("dep") or name,
        "upstream_url": upstream_url,
    }


def dependency_items_from_result(lang: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    if lang == "python":
        for item in result.get("dependency_items", []):
            items.append(normalize_dependency_item(item, lang, "runtime"))
        for item in result.get("build_sys_dependency_items", []):
            items.append(normalize_dependency_item(item, lang, "build_system"))
        return items

    if lang == "cpp":
        for item in result.get("dependency_items", []):
            items.append(normalize_dependency_item(item, lang, "runtime"))
        return items

    rpm_check = result.get("rpm_check") or {}
    for item in rpm_check.get("missing", []):
        items.append(normalize_dependency_item(item, lang, "runtime"))
    return items


def build_available_index_for_result(lang: str, result: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    available_items: list[dict[str, Any]] = []
    rpm_check = result.get("rpm_check") or {}
    available_items.extend(rpm_check.get("available", []))
    if lang == "python":
        build_sys_check = result.get("build_sys_rpm_check") or {}
        available_items.extend(build_sys_check.get("available", []))
    return build_source_index(available_items)


def classify_dependency(dep: dict[str, Any], lang: str, source_index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    source_item = lookup_source_item(dep, source_index)
    source_check = summarize_source_match(dep, source_item)

    if source_check["satisfies_requirement"]:
        return {
            **dep,
            "source_check": source_check,
            "existing_check": None,
            "decision": "reuse_source",
            "action": "resolved",
            "reason": source_check["reason"],
        }

    existing_check = EXISTING_CHECKER.check_existing_package(
        dep["name"],
        requirement=dep.get("requirement", ""),
        lang=lang,
    )
    if source_check["status"] == "older" and not existing_check.get("official", {}).get("exists"):
        existing_check = merge_official_source_older_result(dep, source_check, existing_check)
    decision = existing_check["decision"]
    if decision in {"reuse_official", "reuse_user_repo"}:
        action = "resolved"
    elif decision == "block_official_older":
        action = "blocked"
    else:
        action = "recurse"

    return {
        **dep,
        "source_check": source_check,
        "existing_check": existing_check,
        "decision": decision,
        "action": action,
        "reason": existing_check["reason"],
    }


def build_summary(pkgname: str, lang: str, source_dir: str, analysis_file: str, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pkgname": pkgname,
        "lang": lang,
        "source_dir": source_dir,
        "analysis_file": analysis_file,
        "dependency_decisions": decisions,
        "resolved": [item for item in decisions if item["action"] == "resolved"],
        "pending": [item for item in decisions if item["action"] == "recurse"],
        "blocked": [item for item in decisions if item["action"] == "blocked"],
    }


def print_pending_to_stdout(pending: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()
    for item in pending:
        key = (item["name"], item.get("upstream_url", ""))
        if key in seen:
            continue
        seen.add(key)
        print(f"{item['name']} {item.get('upstream_url', '')}".rstrip())


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RPM 编译前依赖预检")
    parser.add_argument("pkgname", help="包名")
    parser.add_argument("lang", help="语言：python/go/rust/c/cpp/nodejs/java/ruby")
    parser.add_argument("source_dir", help="源码目录（绝对路径）")
    parser.add_argument("--container", default="oe-build-env", help="容器名")
    parser.add_argument("-o", "--output", default="", help="JSON 结果输出路径")
    args = parser.parse_args()

    lang = args.lang.lower()
    if lang not in ANALYZERS:
        print(f"[WARN] 不支持的语言 {lang}，跳过预检", file=sys.stderr)
        sys.exit(0)

    cfg = ANALYZERS[lang]
    script = SCRIPT_DIR / cfg["script"]
    if not script.exists():
        print(f"[WARN] 分析脚本不存在: {script}，跳过预检", file=sys.stderr)
        sys.exit(0)

    out_file = make_output_path(args.pkgname, args.output)
    analysis_path = make_analysis_path(out_file, args.pkgname)

    cmd = [resolve_python_executable(), str(script), args.source_dir, "--check-rpm", "-o", str(analysis_path)]
    if lang == "python":
        cmd += ["--pkg", args.pkgname, "--container", args.container]
    else:
        cmd += ["--container", args.container]

    print(f"[pre_check] 运行: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=False)
    if proc.returncode not in (0, 2):
        print(f"[ERROR] 依赖分析脚本执行失败，退出码: {proc.returncode}", file=sys.stderr)
        sys.exit(1)

    try:
        result = load_json(analysis_path)
    except Exception as e:
        print(f"[ERROR] 无法读取分析结果: {e}", file=sys.stderr)
        sys.exit(1)

    dependency_items = dependency_items_from_result(lang, result)
    source_index = build_available_index_for_result(lang, result)
    decisions = [classify_dependency(dep, lang, source_index) for dep in dependency_items]
    summary = build_summary(args.pkgname, lang, args.source_dir, str(analysis_path), decisions)

    output_path = Path(out_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    blocked = summary["blocked"]
    pending = summary["pending"]
    resolved = summary["resolved"]

    print(f"[pre_check] 已解决 {len(resolved)} 个依赖，待递归 {len(pending)} 个，阻断 {len(blocked)} 个", file=sys.stderr)

    if blocked:
        for item in blocked:
            print(f"[BLOCK] {item['name']}: {item['reason']}", file=sys.stderr)
        sys.exit(1)

    if not pending:
        print("[pre_check] 所有依赖均已满足或可复用", file=sys.stderr)
        sys.exit(0)

    print(f"[pre_check] 发现 {len(pending)} 个需递归处理的依赖：", file=sys.stderr)
    for item in pending:
        print(f"  - {item['name']}  {item.get('upstream_url', '')}  [{item['decision']}]", file=sys.stderr)
    print_pending_to_stdout(pending)
    sys.exit(2)


if __name__ == "__main__":
    main()
