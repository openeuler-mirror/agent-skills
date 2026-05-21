#!/usr/bin/env python3
"""检查目标包在 OpenEuler 官方源和 AI 源中的复用/升级决策。

官方源与 AI 源均通过构建容器内的 DNF 软件源视角查询；
AI 源仓库地址来自 `archive-rpm-sources/config.json` 的 remote_url。
不再依赖本地克隆目录扫描来判断 user_repo 是否存在。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from rpm_batch_lookup import BatchLookupError, fallback_results, name_query, run_batch_lookup


SKILLS_DIR = Path(__file__).resolve().parents[2]
ARCHIVE_CONFIG = SKILLS_DIR / "archive-rpm-sources" / "config.json"

USER_CONFIG = ARCHIVE_CONFIG
KNOWN_PREFIXES = ("python3_", "python_", "ros_humble_", "lib_")
DEFAULT_CONTAINER = "oe-build-env"
OFFICIAL_REPO_LABEL = "<openeuler-dnf-source>"
USER_REPO_LABEL = "<repo-aitest-dnf-source>"
USER_REPO_ID = "repo-aitest"
SIMPLE_REQUIREMENT_RE = re.compile(
    r"^(?:[^<>=!]*\)?\s*)?(>=|==|<=|>|<)\s*([0-9A-Za-z.+:_~\-]+)$"
)
COMPOUND_REQUIREMENT_SPLIT_RE = re.compile(r"\s*(?:,|\bwith\b|\band\b)\s*", re.IGNORECASE)


def normalize_name_token(value: str) -> str:
    return re.sub(r"[-_.]+", "_", value.lower())


def load_archive_repo_config(config_path: Path) -> dict[str, str]:
    with config_path.open(encoding="utf-8") as f:
        config = json.load(f)
    repo = config.get("repo", {})
    remote_url = repo.get("remote_url")
    branch = repo.get("branch") or "main"
    if not remote_url:
        raise ValueError(f"配置缺少 repo.remote_url: {config_path}")
    return {"remote_url": remote_url, "branch": branch}


def try_query_official_via_container(
    tasks: list[dict[str, Any]],
    container: str,
    enabled_repos: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    return run_batch_lookup(container, tasks, timeout=120, enabled_repos=enabled_repos)


def list_enabled_official_repo_ids(container: str) -> list[str]:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "bash",
            "-lc",
            "dnf repolist --enabled --quiet | tail -n +2",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    repo_ids: list[str] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        repo_id = stripped.split()[0]
        if repo_id == USER_REPO_ID:
            continue
        repo_ids.append(repo_id)
    return repo_ids


def repo_raw_base(remote_url: str, branch: str) -> str:
    clean = remote_url.strip()
    if clean.endswith(".git"):
        clean = clean[:-4]
    if clean.startswith("https://github.com/"):
        return clean.replace("https://github.com/", "https://raw.githubusercontent.com/") + f"/{branch}"
    raise ValueError(f"暂不支持的 repo.remote_url: {remote_url}")


def configure_user_repo_in_container(container: str, remote_url: str, branch: str) -> str:
    raw_base = repo_raw_base(remote_url, branch)
    repo_baseurl = f"{raw_base}/dist"
    repo_content = (
        "[repo-aitest]\n"
        "name=repo-aitest\n"
        f"baseurl={repo_baseurl}\n"
        "enabled=1\n"
        "gpgcheck=0\n"
    )
    command = (
        "set -euo pipefail; "
        "cat > /etc/yum.repos.d/repo-aitest.repo <<'EOF'\n"
        f"{repo_content}"
        "EOF\n"
        "dnf clean metadata --disablerepo='*' --enablerepo='repo-aitest' >/dev/null 2>&1 || true; "
        "dnf makecache --disablerepo='*' --enablerepo='repo-aitest' >/dev/null"
    )
    subprocess.run(
        ["docker", "exec", container, "bash", "-lc", command],
        check=True,
        capture_output=True,
        text=True,
    )
    return repo_baseurl


def try_query_official_via_host(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raise RuntimeError("host dnf lookup is not supported in this environment")


def base_name_stems(pkgname: str) -> set[str]:
    normalized = normalize_name_token(pkgname)
    stems = {normalized}
    changed = True
    while changed:
        changed = False
        for stem in list(stems):
            for prefix in KNOWN_PREFIXES:
                if stem.startswith(prefix):
                    candidate = stem[len(prefix):]
                    if candidate and candidate not in stems:
                        stems.add(candidate)
                        changed = True
            if stem.startswith("lib") and len(stem) > 3:
                candidate = stem[3:]
                if candidate and candidate not in stems:
                    stems.add(candidate)
                    changed = True
    return {stem for stem in stems if stem}


def build_name_candidates(pkgname: str, lang: str = "") -> set[str]:
    stems = base_name_stems(pkgname)
    candidates = set(stems)
    for stem in stems:
        dash_stem = stem.replace("_", "-")
        underscore_stem = dash_stem.replace("-", "_")
        for variant in {stem, dash_stem, underscore_stem}:
            if not variant:
                continue
            candidates.add(variant)
            candidates.add(f"python3_{variant}")
            candidates.add(f"python3-{variant}")
            candidates.add(f"ros_humble_{variant}")
            candidates.add(f"ros-humble-{variant}")
            candidates.add(f"lib{variant}")
            candidates.add(f"lib_{variant}")
            candidates.add(f"lib-{variant}")
            # Header-only C/C++ packages in official repos may only ship a
            # -devel subpackage with no main binary package (e.g. asio-devel).
            # Include -devel variants so we correctly detect them as existing.
            candidates.add(f"{variant}-devel")
            candidates.add(f"{variant}_devel")
    return {item for item in candidates if item}


def parse_rpm_nvra(filename: str) -> Optional[dict[str, str]]:
    if not filename.endswith(".rpm"):
        return None
    base = filename[:-4]
    parts = base.rsplit(".", 1)
    if len(parts) != 2:
        return None
    nvr, arch = parts
    parts = nvr.rsplit("-", 1)
    if len(parts) != 2:
        return None
    nv, release = parts
    parts = nv.rsplit("-", 1)
    if len(parts) != 2:
        return None
    name, version = parts
    return {"name": name, "version": version, "release": release, "arch": arch}


def split_version_tokens(version: str) -> list[int | str]:
    tokens: list[int | str] = []
    for part in re.split(r"[^A-Za-z0-9]+", version):
        if not part:
            continue
        for token in re.findall(r"[A-Za-z]+|\d+", part):
            tokens.append(int(token) if token.isdigit() else token.lower())
    return tokens


def compare_optional_token(left: int | str | None, right: int | str | None) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        if isinstance(right, int):
            return 0 if right == 0 else -1
        return 1
    if right is None:
        if isinstance(left, int):
            return 0 if left == 0 else 1
        return -1
    if isinstance(left, int) and isinstance(right, int):
        return (left > right) - (left < right)
    if isinstance(left, int):
        return 1
    if isinstance(right, int):
        return -1
    return (left > right) - (left < right)


def compare_versions(left: str, right: str) -> int:
    left_tokens = split_version_tokens(left)
    right_tokens = split_version_tokens(right)
    max_len = max(len(left_tokens), len(right_tokens))
    for idx in range(max_len):
        result = compare_optional_token(
            left_tokens[idx] if idx < len(left_tokens) else None,
            right_tokens[idx] if idx < len(right_tokens) else None,
        )
        if result != 0:
            return result
    return 0


def is_match(name: str, candidates: set[str]) -> bool:
    return normalize_name_token(name) in candidates


def strip_archive_suffix(filename: str) -> str:
    for suffix in ARCHIVE_SUFFIXES:
        if filename.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


def parse_archive_name(filename: str) -> Optional[dict[str, str]]:
    base = strip_archive_suffix(filename)
    match = re.match(r"^(.+)-([0-9][0-9A-Za-z.+:_~\-]*)$", base)
    if not match:
        return None
    return {"name": match.group(1), "version": match.group(2)}


def parse_spec_metadata(spec_path: Path) -> Optional[dict[str, str]]:
    try:
        content = spec_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    name_match = re.search(r"^Name:\s*(\S+)", content, re.MULTILINE)
    version_match = re.search(r"^Version:\s*(\S+)", content, re.MULTILINE)
    if not name_match and not version_match:
        return None
    return {
        "name": name_match.group(1) if name_match else spec_path.stem,
        "version": version_match.group(1) if version_match else "",
    }


def candidate_from_rpm(rpm_path: Path) -> Optional[dict[str, Any]]:
    info = parse_rpm_nvra(rpm_path.name)
    if not info:
        return None
    return {
        "path": str(rpm_path),
        "match_type": "rpm",
        "name": info["name"],
        "version": info["version"],
        "release": info["release"],
        "arch": info["arch"],
    }


def pick_highest_candidate(candidates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    versioned = [item for item in candidates if item.get("version")]
    if versioned:
        best = versioned[0]
        for item in versioned[1:]:
            if compare_versions(item["version"], best["version"]) > 0:
                best = item
        return best
    return candidates[0] if candidates else None


def candidate_from_official_result(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    rpm_name = item.get("rpm") or item.get("name")
    if not rpm_name:
        return None
    return {
        "path": OFFICIAL_REPO_LABEL,
        "match_type": "dnf_repo",
        "name": rpm_name,
        "version": item.get("version"),
        "release": item.get("release"),
        "arch": item.get("arch"),
    }


def candidate_from_user_repo_result(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    rpm_name = item.get("rpm") or item.get("name")
    if not rpm_name:
        return None
    return {
        "path": USER_REPO_LABEL,
        "match_type": "dnf_repo",
        "name": rpm_name,
        "version": item.get("version"),
        "release": item.get("release"),
        "arch": item.get("arch"),
    }


def parse_requirement(requirement: str) -> dict[str, Any]:
    raw = (requirement or "").strip()
    if not raw:
        return {"raw": "", "status": "none", "operator": None, "version": None, "clauses": []}

    lowered = raw.lower()
    if " or " in lowered or "!=" in raw or "~=" in raw:
        return {"raw": raw, "status": "unknown", "operator": None, "version": None, "clauses": []}

    clauses: list[dict[str, str]] = []
    for part in COMPOUND_REQUIREMENT_SPLIT_RE.split(raw.strip().strip("()")):
        clause_text = part.strip().strip("()")
        if not clause_text:
            continue
        match = SIMPLE_REQUIREMENT_RE.match(clause_text)
        if not match:
            return {"raw": raw, "status": "unknown", "operator": None, "version": None, "clauses": []}
        clauses.append(
            {
                "raw": clause_text,
                "operator": match.group(1),
                "version": match.group(2),
            }
        )

    if not clauses:
        return {"raw": raw, "status": "unknown", "operator": None, "version": None, "clauses": []}

    first = clauses[0] if len(clauses) == 1 else {"operator": None, "version": None}
    return {
        "raw": raw,
        "status": "parsed",
        "operator": first["operator"],
        "version": first["version"],
        "clauses": clauses,
    }


def evaluate_constraint(version: Optional[str], operator: Optional[str], expected: Optional[str]) -> Optional[bool]:
    if not version or not operator or not expected:
        return None
    cmp = compare_versions(version, expected)
    if operator == ">=":
        return cmp >= 0
    if operator == "==":
        return cmp == 0
    if operator == "<=":
        return cmp <= 0
    if operator == ">":
        return cmp > 0
    if operator == "<":
        return cmp < 0
    return None


def evaluate_requirement(version: Optional[str], requirement_info: dict[str, Any]) -> Optional[bool]:
    if not version:
        return None
    if requirement_info.get("status") != "parsed":
        return None

    clauses = requirement_info.get("clauses") or []
    if not clauses and requirement_info.get("operator") and requirement_info.get("version"):
        clauses = [
            {
                "operator": requirement_info["operator"],
                "version": requirement_info["version"],
            }
        ]
    if not clauses:
        return None

    results = [
        evaluate_constraint(version, clause.get("operator"), clause.get("version"))
        for clause in clauses
    ]
    if any(result is None for result in results):
        return None
    return all(bool(result) for result in results)


def summarize_local_repo(repo_dir: Path, pkgname: str, lang: str, requested_version: str, requirement: str) -> dict[str, Any]:
    candidates = build_name_candidates(pkgname, lang)
    matched_dirs = collect_dir_matches(repo_dir, candidates, pkgname)
    matched_rpms = collect_rpm_matches(repo_dir, candidates)

    version_candidates: list[dict[str, Any]] = []
    effective_dirs: list[Path] = []
    for matched_dir in matched_dirs:
        dir_candidates = list(iter_dir_candidates(matched_dir, candidates))
        if not dir_candidates:
            continue
        effective_dirs.append(matched_dir)
        version_candidates.extend(dir_candidates)
    for rpm_path in matched_rpms:
        candidate = candidate_from_rpm(rpm_path)
        if candidate:
            version_candidates.append(candidate)

    highest = pick_highest_candidate(version_candidates)
    requirement_info = parse_requirement(requirement)

    satisfies_requested_version: Optional[bool]
    if requested_version:
        satisfies_requested_version = evaluate_constraint(
            highest.get("version") if highest else None,
            ">=",
            requested_version,
        )
    else:
        satisfies_requested_version = None

    if requirement_info["status"] == "parsed":
        satisfies_requirement = evaluate_requirement(
            highest.get("version") if highest else None,
            requirement_info,
        )
    elif requirement_info["status"] == "none":
        satisfies_requirement = None
    else:
        satisfies_requirement = None

    unknown_requirement = requirement_info["status"] == "unknown"
    if unknown_requirement:
        meets_need = False
    elif not requested_version and requirement_info["status"] == "none":
        meets_need = bool(effective_dirs or matched_rpms)
    else:
        comparisons = [
            value for value in (satisfies_requested_version, satisfies_requirement) if value is not None
        ]
        meets_need = bool(comparisons) and all(comparisons)

    return {
        "exists": bool(effective_dirs or matched_rpms),
        "matched_paths": [str(path) for path in effective_dirs] + [str(path) for path in matched_rpms],
        "candidates": version_candidates,
        "highest": highest,
        "satisfies_requested_version": satisfies_requested_version,
        "satisfies_requirement": satisfies_requirement,
        "meets_need": meets_need,
        "comparison_unknown": unknown_requirement,
    }


def summarize_dnf_repo(
    pkgname: str,
    lang: str,
    requested_version: str,
    requirement: str,
    repo_label: str,
    candidate_builder,
    container: str,
    enabled_repos: Optional[list[str]] = None,
) -> dict[str, Any]:
    candidates = sorted(build_name_candidates(pkgname, lang))
    tasks = [{"name": candidate, "queries": [name_query(candidate)]} for candidate in candidates]

    lookup_error: Optional[str] = None
    results: list[dict[str, Any]] = []
    try:
        results = try_query_official_via_container(tasks, container, enabled_repos=enabled_repos)
    except (BatchLookupError, OSError, subprocess.SubprocessError) as exc:
        lookup_error = f"container:{exc}"
        try:
            results = try_query_official_via_host(tasks)
            lookup_error = None
        except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as host_exc:
            lookup_error = f"{lookup_error}; host:{host_exc}" if lookup_error else f"host:{host_exc}"
            results = fallback_results(tasks)

    version_candidates = [
        candidate
        for item in results
        if item.get("rpm")
        for candidate in [candidate_builder(item)]
        if candidate
    ]
    highest = pick_highest_candidate(version_candidates)
    requirement_info = parse_requirement(requirement)

    if requested_version:
        satisfies_requested_version = evaluate_constraint(
            highest.get("version") if highest else None,
            ">=",
            requested_version,
        )
    else:
        satisfies_requested_version = None

    if requirement_info["status"] == "parsed":
        satisfies_requirement = evaluate_requirement(
            highest.get("version") if highest else None,
            requirement_info,
        )
    else:
        satisfies_requirement = None

    unknown_requirement = requirement_info["status"] == "unknown"
    if unknown_requirement:
        meets_need = False
    elif not requested_version and requirement_info["status"] == "none":
        meets_need = bool(version_candidates)
    else:
        comparisons = [value for value in (satisfies_requested_version, satisfies_requirement) if value is not None]
        meets_need = bool(comparisons) and all(comparisons)

    result = {
        "exists": bool(version_candidates),
        "matched_paths": [repo_label] if version_candidates else [],
        "candidates": version_candidates,
        "highest": highest,
        "satisfies_requested_version": satisfies_requested_version,
        "satisfies_requirement": satisfies_requirement,
        "meets_need": meets_need,
        "comparison_unknown": unknown_requirement,
    }
    if lookup_error:
        result["lookup_error"] = lookup_error
    return result


def build_reason(
    decision: str,
    official: dict[str, Any],
    user_repo: dict[str, Any],
    requested_version: str,
    requirement: str,
) -> str:
    requested_desc = requirement or requested_version or "无版本约束"
    if decision == "reuse_official":
        version = (official.get("highest") or {}).get("version") or "已存在"
        return f"官方仓库已有满足要求（{requested_desc}）的版本：{version}"
    if decision == "reuse_user_repo":
        version = (user_repo.get("highest") or {}).get("version") or "已存在"
        return f"用户仓库已有满足要求（{requested_desc}）的版本：{version}"
    if decision == "upgrade_user_repo":
        version = (user_repo.get("highest") or {}).get("version") or "未知版本"
        return f"用户仓库已存在同名包，但最高版本 {version} 不满足要求（{requested_desc}）"
    if decision == "block_official_older":
        version = (official.get("highest") or {}).get("version") or "未知版本"
        return f"官方仓库已存在同名包，但最高版本 {version} 不满足要求（{requested_desc}），需人工决策"
    if official.get("comparison_unknown") or user_repo.get("comparison_unknown"):
        return f"存在同名包，但版本约束无法可靠解析（{requested_desc}），保守继续引入流程"
    return f"官方仓库和用户仓库均无满足要求（{requested_desc}）的包"


def choose_decision(
    official: dict[str, Any],
    user_repo: dict[str, Any],
    requested_version: str,
    requirement: str,
) -> str:
    if official["meets_need"]:
        return "reuse_official"
    if user_repo["meets_need"]:
        return "reuse_user_repo"

    version_sensitive = bool(requested_version or requirement)
    if version_sensitive and user_repo["exists"] and not user_repo.get("comparison_unknown"):
        return "upgrade_user_repo"
    if version_sensitive and official["exists"] and not official.get("comparison_unknown"):
        return "block_official_older"
    return "introduce_new"


def ensure_dnf_cache(container: str) -> None:
    """Warm the DNF cache so cacheonly lookups succeed on a fresh container."""
    subprocess.run(
        ["docker", "exec", container, "bash", "-lc",
         "dnf makecache --disablerepo='repo-aitest' -q 2>/dev/null || true"],
        capture_output=True,
    )


def check_existing_package(pkgname: str, version: str = "", requirement: str = "", lang: str = "", container: str = DEFAULT_CONTAINER) -> dict[str, Any]:
    repo_cfg = load_archive_repo_config(USER_CONFIG)
    user_repo_baseurl = configure_user_repo_in_container(
        container,
        repo_cfg["remote_url"],
        repo_cfg["branch"],
    )
    ensure_dnf_cache(container)
    official_repo_ids = list_enabled_official_repo_ids(container)

    official = summarize_dnf_repo(
        pkgname,
        lang,
        version,
        requirement,
        OFFICIAL_REPO_LABEL,
        candidate_from_official_result,
        container,
        enabled_repos=official_repo_ids,
    )
    user_repo = summarize_dnf_repo(
        pkgname,
        lang,
        version,
        requirement,
        USER_REPO_LABEL,
        candidate_from_user_repo_result,
        container,
        enabled_repos=[USER_REPO_ID],
    )
    decision = choose_decision(official, user_repo, version, requirement)

    result = {
        "requested": {
            "pkgname": pkgname,
            "version": version,
            "requirement": requirement,
            "lang": lang,
            "requirement_info": parse_requirement(requirement),
        },
        "checked_repos": {
            "official": OFFICIAL_REPO_LABEL,
            "user_repo": user_repo_baseurl,
            "official_repo_ids": official_repo_ids,
            "user_repo_id": USER_REPO_ID,
        },
        "official": official,
        "user_repo": user_repo,
        "exists_in_official": official["exists"],
        "exists_in_user_repo": user_repo["exists"],
        "decision": decision,
        "reason": build_reason(decision, official, user_repo, version, requirement),
        "should_skip": decision in {"reuse_official", "reuse_user_repo"},
    }
    return result


def print_repo_summary(label: str, repo_info: dict[str, Any]) -> None:
    highest = repo_info.get("highest") or {}
    print(f"{label}:")
    print(f"  exists                  : {repo_info['exists']}")
    print(f"  highest_version         : {highest.get('version') or '<unknown>'}")
    print(f"  satisfies_requested     : {repo_info['satisfies_requested_version']}")
    print(f"  satisfies_requirement   : {repo_info['satisfies_requirement']}")
    print(f"  meets_need              : {repo_info['meets_need']}")
    print(f"  comparison_unknown      : {repo_info['comparison_unknown']}")
    print(f"  matched_paths:")
    if repo_info["matched_paths"]:
        for path in repo_info["matched_paths"]:
            print(f"    - {path}")
    else:
        print("    - <none>")


def print_summary(result: dict[str, Any]) -> None:
    requested = result["requested"]
    print("Existing package decision")
    print("-" * 60)
    print(f"pkgname       : {requested['pkgname']}")
    print(f"version       : {requested['version'] or '<none>'}")
    print(f"requirement   : {requested['requirement'] or '<none>'}")
    print(f"lang          : {requested['lang'] or '<none>'}")
    print(f"decision      : {result['decision']}")
    print(f"should_skip   : {result['should_skip']}")
    print(f"reason        : {result['reason']}")
    print_repo_summary("official", result["official"])
    print_repo_summary("user_repo", result["user_repo"])


def main() -> None:
    parser = argparse.ArgumentParser(description="检查包在官方仓库和用户 RPM 仓库中的版本感知决策")
    parser.add_argument("pkgname", help="待检查的包名")
    parser.add_argument("--version", default="", help="本次待引入的解析后版本")
    parser.add_argument("--requirement", default="", help="依赖版本约束，例如 '>= 2.1' 或 '== 1.0'")
    parser.add_argument("--lang", default="", help="语言类型，用于辅助包名候选判定")
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="用于执行双源检查的容器名")
    parser.add_argument("--json", action="store_true", help="将 JSON 结果输出到 stdout")
    parser.add_argument("-o", "--output", default="", help="将结果写入 JSON 文件")
    args = parser.parse_args()

    try:
        result = check_existing_package(
            args.pkgname,
            version=args.version.strip(),
            requirement=args.requirement.strip(),
            lang=args.lang.strip().lower(),
            container=args.container,
        )
    except Exception as exc:  # pragma: no cover - CLI safeguard
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result)


if __name__ == "__main__":
    main()
