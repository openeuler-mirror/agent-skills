#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        try:
            from pip._vendor import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            tomllib = None  # type: ignore[assignment]


def load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_python_version(source_dir: str) -> str:
    src = Path(source_dir)

    pyproject = src / "pyproject.toml"
    data = load_toml(pyproject)
    if data:
        project = data.get("project")
        if isinstance(project, dict):
            version = project.get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
        tool = data.get("tool")
        if isinstance(tool, dict):
            poetry = tool.get("poetry")
            if isinstance(poetry, dict):
                version = poetry.get("version")
                if isinstance(version, str) and version.strip():
                    return version.strip()

    version_file = src / "VERSION"
    if version_file.exists():
        version = version_file.read_text(encoding="utf-8", errors="ignore").strip()
        if version:
            return version

    setup_py = src / "setup.py"
    if setup_py.exists():
        content = setup_py.read_text(encoding="utf-8", errors="ignore")
        patterns = [
            r'version\s*=\s*["\']([^"\']+)["\']',
            r'__version__\s*=\s*["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return match.group(1).strip()

    return ""


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "version":
        print("usage: python_metadata.py version <source-dir>", file=sys.stderr)
        sys.exit(2)
    print(extract_python_version(sys.argv[2]))
