#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from java_metadata import extract_java_version
from nodejs_metadata import extract_nodejs_version
from python_metadata import extract_python_version
from rust_metadata import extract_rust_version


Extractor = Callable[[str], str]


def extract_generic_version(source_dir: str) -> str:
    src = Path(source_dir)
    version_file = src / "VERSION"
    if version_file.exists():
        version = version_file.read_text(encoding="utf-8", errors="ignore").strip()
        if version:
            return version
    return ""


def extract_go_version(source_dir: str) -> str:
    return extract_generic_version(source_dir)


def extract_c_version(source_dir: str) -> str:
    return extract_generic_version(source_dir)


def extract_cpp_version(source_dir: str) -> str:
    return extract_generic_version(source_dir)


def extract_ruby_version(source_dir: str) -> str:
    return extract_generic_version(source_dir)


EXTRACTORS: dict[str, Extractor] = {
    "python": extract_python_version,
    "rust": extract_rust_version,
    "nodejs": extract_nodejs_version,
    "java": extract_java_version,
    "go": extract_go_version,
    "c": extract_c_version,
    "cpp": extract_cpp_version,
    "ruby": extract_ruby_version,
}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: extract_version.py <lang> <source-dir>", file=sys.stderr)
        sys.exit(2)

    lang = sys.argv[1].strip().lower()
    source_dir = sys.argv[2]
    extractor = EXTRACTORS.get(lang)
    if extractor is None:
        print(f"unsupported language: {lang}", file=sys.stderr)
        sys.exit(2)

    print(extractor(source_dir))
