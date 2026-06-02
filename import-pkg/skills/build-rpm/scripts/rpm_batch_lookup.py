#!/usr/bin/env python3
"""Shared one-shot RPM availability lookup via a single container-side DNF sack load."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

_INTERNAL_KEYS = {"queries", "prefer_devel", "enabled_repos"}

# 宿主机缓存文件路径，优先用 SESSION_TMP_DIR 环境变量，否则用 /tmp
_CACHE_DIR = Path(os.environ.get("SESSION_TMP_DIR", "/tmp")) / "rpm_lookup_cache"
_CACHE_FILE = _CACHE_DIR / "batch_lookup_cache.json"

def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_cache(cache: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _task_key(task: Dict[str, Any], enabled_repos: Optional[List[str]]) -> str:
    payload = {k: v for k, v in task.items() if k not in _INTERNAL_KEYS}
    payload["_repos"] = sorted(enabled_repos) if enabled_repos else []
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()

_CONTAINER_SCRIPT = r'''
import json
import sys
import warnings

warnings.filterwarnings("ignore")

try:
    import dnf
except ImportError:
    print(json.dumps({"error": "dnf not available"}))
    sys.exit(0)

INTERNAL_KEYS = {"queries", "prefer_devel", "enabled_repos"}


def unique_packages(packages):
    seen = set()
    result = []
    for pkg in packages:
        name = getattr(pkg, "name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(pkg)
    return result


def first_package(sack, name):
    packages = unique_packages(sack.query().filter(name=name))
    return packages[0] if packages else None


def resolve_devel_candidate(sack, pkg):
    name = getattr(pkg, "name", "")
    if not name:
        return None
    if name.endswith("-devel"):
        return pkg
    return first_package(sack, f"{name}-devel")


def pick_package(sack, packages, prefer_devel):
    packages = unique_packages(packages)
    if not packages:
        return None
    if prefer_devel:
        for pkg in packages:
            name = getattr(pkg, "name", "")
            if name.endswith("-devel"):
                return pkg
        for pkg in packages:
            candidate = resolve_devel_candidate(sack, pkg)
            if candidate is not None:
                return candidate
    return packages[0]


def query_packages(sack, query):
    kind = query["kind"]
    value = query["value"]
    if kind == "provides":
        return sack.query().filter(provides=value)
    if kind == "name":
        return sack.query().filter(name=value)
    if kind == "name_glob":
        return sack.query().filter(name__glob=value)
    if kind == "file":
        return sack.query().filter(file=value)
    if kind == "file_glob":
        return sack.query().filter(file__glob=value)
    raise ValueError(f"unsupported query kind: {kind}")


def sanitize_task(task):
    result = {k: v for k, v in task.items() if k not in INTERNAL_KEYS}
    result["rpm"] = None
    result["version"] = None
    result["release"] = None
    result["level"] = ""
    return result


tasks = json.load(sys.stdin)
enabled_repos = tasks[0].get("enabled_repos") if tasks else None
base = dnf.Base()
base.conf.cachedir = "/var/cache/dnf"
base.conf.cacheonly = True
base.read_all_repos()
if enabled_repos is not None:
    allowed = set(enabled_repos)
    for repo in base.repos.iter_enabled():
        repo.disable()
    for repo in base.repos.all():
        if repo.id in allowed:
            repo.enable()
base.fill_sack(load_system_repo=False, load_available_repos=True)
sack = base.sack

results = []
for task in tasks:
    result = sanitize_task(task)
    for query in task.get("queries", []):
        packages = query_packages(sack, query)
        pkg = pick_package(sack, packages, query.get("prefer_devel", task.get("prefer_devel", False)))
        if pkg is not None:
            result["rpm"] = getattr(pkg, "name", None)
            result["version"] = getattr(pkg, "version", None)
            result["release"] = getattr(pkg, "release", None)
            result["level"] = query.get("level", query["kind"])
            break
    results.append(result)

print(json.dumps(results))
'''


class BatchLookupError(RuntimeError):
    """Raised when the shared RPM batch lookup cannot complete."""


def provides_query(value: str, level: str) -> Dict[str, Any]:
    return {"kind": "provides", "value": value, "level": level}


def name_query(value: str, level: str = "name", prefer_devel: bool = False) -> Dict[str, Any]:
    return {"kind": "name", "value": value, "level": level, "prefer_devel": prefer_devel}


def name_glob_query(value: str, level: str = "name-glob", prefer_devel: bool = False) -> Dict[str, Any]:
    return {"kind": "name_glob", "value": value, "level": level, "prefer_devel": prefer_devel}


def file_query(value: str, level: str = "file", prefer_devel: bool = False) -> Dict[str, Any]:
    return {"kind": "file", "value": value, "level": level, "prefer_devel": prefer_devel}


def file_glob_query(value: str, level: str = "file-glob", prefer_devel: bool = False) -> Dict[str, Any]:
    return {"kind": "file_glob", "value": value, "level": level, "prefer_devel": prefer_devel}


def fallback_results(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            **{k: v for k, v in task.items() if k not in _INTERNAL_KEYS},
            "rpm": None,
            "version": None,
            "release": None,
            "level": "",
        }
        for task in tasks
    ]


def run_batch_lookup(
    container: str,
    tasks: List[Dict[str, Any]],
    timeout: int = 120,
    enabled_repos: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not tasks:
        return []

    # 宿主机缓存：命中的直接返回，未命中的发往容器查询
    cache = _load_cache()
    results: List[Any] = [None] * len(tasks)
    miss_indices: List[int] = []
    miss_tasks: List[Dict[str, Any]] = []

    for i, task in enumerate(tasks):
        key = _task_key(task, enabled_repos)
        if key in cache:
            results[i] = cache[key]
        else:
            miss_indices.append(i)
            miss_tasks.append(task)

    if miss_tasks:
        payload_tasks = []
        for task in miss_tasks:
            payload_task = dict(task)
            if enabled_repos is not None:
                payload_task["enabled_repos"] = enabled_repos
            payload_tasks.append(payload_task)

        proc = subprocess.run(
            ["docker", "exec", "-i", container, "python3", "-c", _CONTAINER_SCRIPT],
            input=json.dumps(payload_tasks),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()[:400]
            raise BatchLookupError(detail or f"docker exec failed with code {proc.returncode}")

        raw = proc.stdout.strip()
        if not raw:
            raise BatchLookupError((proc.stderr or "no output").strip()[:400])

        try:
            fresh = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BatchLookupError(f"JSON parse error: {exc}") from exc

        # 写回缓存并填充结果
        for idx, result in zip(miss_indices, fresh):
            key = _task_key(tasks[idx], enabled_repos)
            cache[key] = result
            results[idx] = result
        _save_cache(cache)

    return results  # type: ignore[return-value]
