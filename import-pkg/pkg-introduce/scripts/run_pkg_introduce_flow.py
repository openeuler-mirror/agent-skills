#!/usr/bin/env python3
"""Stage-based pkg-introduce orchestrator helpers.

This file intentionally exposes subcommands for skill-driven orchestration.
The skill remains the top-level phase controller; this script closes each phase.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

CLAUDE_SKILLS_DIR = Path(__file__).resolve().parents[2]
PKG_SCRIPTS_DIR = Path(__file__).resolve().parent
RESULT_SCRIPT = PKG_SCRIPTS_DIR / "pkg_introduce_result.py"
CHECK_REPO_SCRIPT = PKG_SCRIPTS_DIR / "check_repo.py"
DOWNLOAD_SCRIPT = PKG_SCRIPTS_DIR / "download_source.py"
LICENSE_SCRIPT = PKG_SCRIPTS_DIR / "check_license.py"
EXTRACT_VERSION_SCRIPT = PKG_SCRIPTS_DIR / "extract_version.py"
CHECK_EXISTING_SCRIPT = PKG_SCRIPTS_DIR / "check_existing_package.py"
INIT_SESSION_SCRIPT = PKG_SCRIPTS_DIR / "init_session_state.py"


class FlowError(RuntimeError):
    def __init__(self, reason: str, failure_type: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.failure_type = failure_type


# ---------- Common helpers ----------

def run_command(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_version_text(version: str) -> str:
    return (version or "").strip().removeprefix("v")


def result_path(pkgname: str, reports_dir: Path) -> Path:
    return reports_dir / f"pkg_introduce_result_{pkgname}.json"


def run_result_command(subcommand: str, pkgname: str, reports_dir: Path, extra_args: list[str]) -> subprocess.CompletedProcess:
    command = [sys.executable, str(RESULT_SCRIPT), subcommand, pkgname, "--reports-dir", str(reports_dir), *extra_args]
    return run_command(command)


def update_result(
    pkgname: str,
    reports_dir: Path,
    *,
    action: str | None = None,
    reason: str | None = None,
    status: str | None = None,
    failure_type: str | None = None,
    failure_reason: str | None = None,
    version: str | None = None,
    requested_version: str | None = None,
    decision: str | None = None,
    lang: str | None = None,
    analysis_file: str | None = None,
    archived: bool | None = None,
) -> None:
    extra: list[str] = []
    mapping = {
        "--action": action,
        "--reason": reason,
        "--status": status,
        "--failure-type": failure_type,
        "--failure-reason": failure_reason,
        "--version": version,
        "--requested-version": requested_version,
        "--decision": decision,
        "--lang": lang,
        "--analysis-file": analysis_file,
    }
    for flag, value in mapping.items():
        if value is not None:
            extra += [flag, value]
    if archived is not None:
        extra += ["--archived", "true" if archived else "false"]
    proc = run_result_command("update", pkgname, reports_dir, extra)
    if proc.returncode != 0:
        raise FlowError(f"failed to update result: {proc.stderr.strip() or proc.stdout.strip()}")


# ---------- Phase implementations ----------

def initialize_top_level(build_state_dir: Path, reports_dir: Path, sources_dir: Path) -> dict[str, Any]:
    proc = run_command([
        sys.executable,
        str(INIT_SESSION_SCRIPT),
        "--build-state-dir",
        str(build_state_dir),
        "--reports-dir",
        str(reports_dir),
        "--sources-dir",
        str(sources_dir),
    ])
    if proc.returncode != 0:
        raise FlowError(proc.stderr.strip() or proc.stdout.strip() or "failed to initialize session state")
    return {
        "status": "done",
        "build_state_dir": str(build_state_dir),
        "reports_dir": str(reports_dir),
        "sources_dir": str(sources_dir),
    }


def run_repo_check(pkgname: str, upstream_url: str, reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / f"repo_check_{pkgname}.json"
    proc = run_command([sys.executable, str(CHECK_REPO_SCRIPT), upstream_url, "-o", str(path)])
    if proc.returncode != 0:
        raise FlowError(proc.stderr.strip() or proc.stdout.strip() or "repo check failed", "non_retryable_repo_blocked")
    return {"status": "done", "repo_check": str(path)}


def run_download(pkgname: str, upstream_url: str, expected_version: str, sources_dir: Path, reports_dir: Path) -> dict[str, Any]:
    source_dir = sources_dir / pkgname
    shutil.rmtree(source_dir, ignore_errors=True)
    path = reports_dir / f"download_result_{pkgname}.json"
    command = [
        sys.executable,
        str(DOWNLOAD_SCRIPT),
        "--upstream-url",
        upstream_url,
        "--output-dir",
        str(sources_dir),
        "-o",
        str(path),
    ]
    if expected_version:
        command[4:4] = ["--version", expected_version]
    proc = run_command(command)
    if proc.returncode != 0:
        raise FlowError(proc.stderr.strip() or proc.stdout.strip() or "download failed", "non_retryable_source_missing")
    return {"status": "done", "source_dir": str(source_dir), "download_result": str(path)}


def run_license_check(pkgname: str, source_dir: Path, reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / f"license_check_{pkgname}.json"
    proc = run_command([sys.executable, str(LICENSE_SCRIPT), str(source_dir), "--pkg", pkgname, "-o", str(path)])
    if proc.returncode != 0:
        raise FlowError(proc.stderr.strip() or proc.stdout.strip() or "license check failed", "non_retryable_license_blocked")
    return {"status": "done", "license_check": str(path)}


def detect_lang(source_dir: Path) -> str:
    """Detect primary language by checking characteristic files in priority order.

    Priority: go.mod > Cargo.toml > package.json > pyproject.toml/setup.py >
              pom.xml/build.gradle > *.gemspec/Gemfile > CMakeLists.txt/meson.build/configure.ac
    """
    checks = [
        ("go.mod",                                          "go"),
        ("Cargo.toml",                                      "rust"),
        ("package.json",                                    "nodejs"),
        ("pyproject.toml",                                  "python"),
        ("setup.py",                                        "python"),
        ("pom.xml",                                         "java"),
        ("build.gradle",                                    "java"),
        ("build.gradle.kts",                                "java"),
    ]
    for filename, lang in checks:
        if (source_dir / filename).exists():
            return lang
    # gemspec (glob)
    if list(source_dir.glob("*.gemspec")) or (source_dir / "Gemfile").exists():
        return "ruby"
    # C/C++ build systems
    for f in ("CMakeLists.txt", "meson.build", "configure.ac"):
        if (source_dir / f).exists():
            return "c"
    return "python"  # fallback


def detect_lang_and_version(source_dir: Path, expected_version: str) -> dict[str, Any]:
    lang = detect_lang(source_dir)
    proc = run_command([sys.executable, str(EXTRACT_VERSION_SCRIPT), lang, str(source_dir)])
    version = proc.stdout.strip()
    if proc.returncode != 0:
        raise FlowError(proc.stderr.strip() or proc.stdout.strip() or "failed to detect version")
    if not version:
        # Static extraction exhausted — signal skill to invoke agent fallback
        return {
            "status": "needs_agent",
            "lang": lang,
            "version": "",
            "reason": "static version extraction returned empty; dynamic version or non-standard layout",
            "source_dir": str(source_dir),
            "expected_version": expected_version,
        }
    if expected_version and normalize_version_text(expected_version) != normalize_version_text(version):
        raise FlowError(
            f"expected version {expected_version} does not match detected version {version}",
            "non_retryable_source_missing",
        )
    return {"status": "done", "lang": lang, "version": version}


def run_existing_check(pkgname: str, version: str, lang: str, reports_dir: Path, container: str) -> dict[str, Any]:
    path = reports_dir / f"existing_check_{pkgname}.json"
    proc = run_command([
        sys.executable,
        str(CHECK_EXISTING_SCRIPT),
        pkgname,
        "--version",
        version,
        "--lang",
        lang,
        "--container",
        container,
        "-o",
        str(path),
    ])
    if proc.returncode != 0:
        raise FlowError(proc.stderr.strip() or proc.stdout.strip() or "existing check failed")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"status": "done", "existing_check": str(path), "decision": data.get("decision", ""), "reason": data.get("reason", "")}


def finalize_result(args: argparse.Namespace) -> dict[str, Any]:
    reports_dir = Path(args.reports_dir)
    update_result(
        args.pkg,
        reports_dir,
        action=args.action,
        reason=args.reason,
        status=args.status,
        failure_type=args.failure_type,
        failure_reason=args.failure_reason,
        version=args.version,
        requested_version=args.requested_version,
        decision=args.decision,
        lang=args.lang,
        analysis_file=args.analysis_file,
        archived=args.archived,
    )
    return {"status": "done", "result_file": str(result_path(args.pkg, reports_dir))}


# ---------- CLI ----------

def print_payload(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage-based pkg-introduce orchestrator helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--pkg", required=True)
    init_parser.add_argument("--mode", choices=["top-level", "dependency"], required=True)
    init_parser.add_argument("--build-state-dir", default="./build_state")
    init_parser.add_argument("--reports-dir", default="./reports")
    init_parser.add_argument("--sources-dir", default="./sources")

    repo_parser = subparsers.add_parser("repo-check")
    repo_parser.add_argument("--pkg", required=True)
    repo_parser.add_argument("--upstream-url", required=True)
    repo_parser.add_argument("--reports-dir", default="./reports")

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--pkg", required=True)
    download_parser.add_argument("--upstream-url", required=True)
    download_parser.add_argument("--version", default="")
    download_parser.add_argument("--sources-dir", default="./sources")
    download_parser.add_argument("--reports-dir", default="./reports")

    license_parser = subparsers.add_parser("license-check")
    license_parser.add_argument("--pkg", required=True)
    license_parser.add_argument("--source-dir", required=True)
    license_parser.add_argument("--reports-dir", default="./reports")

    detect_parser = subparsers.add_parser("detect")
    detect_parser.add_argument("--pkg", required=True)
    detect_parser.add_argument("--source-dir", required=True)
    detect_parser.add_argument("--expected-version", default="")
    detect_parser.add_argument("--reports-dir", default="./reports")

    existing_parser = subparsers.add_parser("existing-check")
    existing_parser.add_argument("--pkg", required=True)
    existing_parser.add_argument("--lang", required=True)
    existing_parser.add_argument("--version", required=True)
    existing_parser.add_argument("--container", default="oe-build-env")
    existing_parser.add_argument("--reports-dir", default="./reports")

    finalize_parser = subparsers.add_parser("finalize-result")
    finalize_parser.add_argument("--pkg", required=True)
    finalize_parser.add_argument("--reports-dir", default="./reports")
    finalize_parser.add_argument("--action", default=None)
    finalize_parser.add_argument("--reason", default=None)
    finalize_parser.add_argument("--status", default=None)
    finalize_parser.add_argument("--failure-type", dest="failure_type", default=None)
    finalize_parser.add_argument("--failure-reason", dest="failure_reason", default=None)
    finalize_parser.add_argument("--version", default=None)
    finalize_parser.add_argument("--requested-version", dest="requested_version", default=None)
    finalize_parser.add_argument("--decision", default=None)
    finalize_parser.add_argument("--lang", default=None)
    finalize_parser.add_argument("--analysis-file", dest="analysis_file", default=None)
    finalize_parser.add_argument("--archived", choices=["true", "false"], default=None)

    args = parser.parse_args()

    try:
        if args.command == "init":
            payload = initialize_top_level(Path(args.build_state_dir), Path(args.reports_dir), Path(args.sources_dir))
            return print_payload(payload)
        if args.command == "repo-check":
            payload = run_repo_check(args.pkg, args.upstream_url, Path(args.reports_dir))
            return print_payload(payload)
        if args.command == "download":
            payload = run_download(args.pkg, args.upstream_url, args.version, Path(args.sources_dir), Path(args.reports_dir))
            return print_payload(payload)
        if args.command == "license-check":
            payload = run_license_check(args.pkg, Path(args.source_dir), Path(args.reports_dir))
            return print_payload(payload)
        if args.command == "detect":
            payload = detect_lang_and_version(Path(args.source_dir), args.expected_version)
            return print_payload(payload)
        if args.command == "existing-check":
            payload = run_existing_check(args.pkg, args.version, args.lang, Path(args.reports_dir), args.container)
            return print_payload(payload)
        if args.command == "finalize-result":
            archived = None if args.archived is None else args.archived == "true"
            args.archived = archived
            payload = finalize_result(args)
            return print_payload(payload)
        raise FlowError(f"unknown command: {args.command}")
    except FlowError as exc:
        print(json.dumps({"status": "failed", "reason": exc.reason, "failure_type": exc.failure_type}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
