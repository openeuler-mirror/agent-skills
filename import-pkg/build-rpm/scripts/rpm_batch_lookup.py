#!/usr/bin/env python3
"""Shared one-shot RPM availability lookup via a single container-side DNF sack load."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

_INTERNAL_KEYS = {"queries", "prefer_devel", "enabled_repos"}

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

    payload_tasks = []
    for task in tasks:
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
        results = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BatchLookupError(f"invalid JSON output: {exc}") from exc

    if isinstance(results, dict) and results.get("error"):
        raise BatchLookupError(str(results["error"]))
    if not isinstance(results, list):
        raise BatchLookupError(f"unexpected lookup payload: {type(results).__name__}")
    return results
