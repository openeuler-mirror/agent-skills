#!/usr/bin/env python3
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path


def _strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _find_first_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _strip_namespace(element.tag) == name and element.text and element.text.strip():
            return element.text.strip()
    return ""


def extract_java_version(source_dir: str) -> str:
    src = Path(source_dir)

    pom_xml = src / "pom.xml"
    if pom_xml.exists():
        try:
            root = ET.fromstring(pom_xml.read_text(encoding="utf-8", errors="ignore"))
            version = _find_first_text(root, "version")
            if version and not version.startswith("${"):
                return version
        except Exception:
            pass

    gradle_properties = src / "gradle.properties"
    if gradle_properties.exists():
        content = gradle_properties.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"(?m)^\s*version\s*=\s*(.+?)\s*$", content)
        if match:
            return match.group(1).strip()

    for filename in ("build.gradle", "build.gradle.kts"):
        build_file = src / filename
        if not build_file.exists():
            continue
        content = build_file.read_text(encoding="utf-8", errors="ignore")
        patterns = [
            r"(?m)^\s*version\s*=\s*['\"]([^'\"]+)['\"]",
            r"(?m)^\s*version\s+['\"]([^'\"]+)['\"]",
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return match.group(1).strip()

    return ""


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "version":
        print("usage: java_metadata.py version <source-dir>", file=sys.stderr)
        sys.exit(2)
    print(extract_java_version(sys.argv[2]))
