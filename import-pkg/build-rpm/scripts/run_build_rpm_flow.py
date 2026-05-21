#!/usr/bin/env python3
"""Authoritative build-rpm flow orchestrator.

First version goals:
- run pre_check_deps and fold recursive dependency processing into a single build result
- expose a machine-readable build_rpm_result_<pkg>.json for pkg-introduce
- keep recursion as an internal detail of build-rpm
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
PRE_CHECK_SCRIPT = SCRIPTS_DIR / "pre_check_deps.py"
PREPARE_BUILD_INPUTS_SCRIPT = SCRIPTS_DIR / "prepare_build_inputs.py"


class FlowError(RuntimeError):
    def __init__(self, reason: str, failure_type: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.failure_type = failure_type


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def result_path(pkgname: str, reports_dir: Path) -> Path:
    return reports_dir / f"build_rpm_result_{pkgname}.json"


def run_precheck(pkgname: str, lang: str, source_dir: str, container: str, reports_dir: Path) -> tuple[int, Path, str, str]:
    precheck_json = reports_dir / f"pre_check_{pkgname}.json"
    proc = run_command([
        sys.executable,
        str(PRE_CHECK_SCRIPT),
        pkgname,
        lang,
        source_dir,
        "--container",
        container,
        "-o",
        str(precheck_json),
    ])
    return proc.returncode, precheck_json, proc.stdout.strip(), proc.stderr.strip()


def prepare_build_inputs(pkgname: str, version: str, source_dir: str, spec_path: Path, container: str) -> subprocess.CompletedProcess:
    return run_command([
        sys.executable,
        str(PREPARE_BUILD_INPUTS_SCRIPT),
        "--pkg",
        pkgname,
        "--version",
        version,
        "--source-dir",
        source_dir,
        "--spec",
        str(spec_path),
        "--container",
        container,
    ])


def run_dnf_builddep(pkgname: str, container: str) -> subprocess.CompletedProcess:
    return run_command([
        "docker",
        "exec",
        container,
        "bash",
        "-c",
        f"dnf builddep -y ~/rpmbuild/SPECS/{pkgname}.spec 2>&1",
    ])


def run_rpmbuild(pkgname: str, container: str) -> subprocess.CompletedProcess:
    return run_command([
        "docker",
        "exec",
        container,
        "bash",
        "-c",
        f"rpmbuild -ba ~/rpmbuild/SPECS/{pkgname}.spec 2>&1",
    ])


# rpmlint error codes that are infrastructure-level and cannot be fixed in spec.
# These are filtered out before deciding rpmlint_passed.
RPMLINT_INFRA_WHITELIST = {
    "no-signature",          # GPG signing is done by OBS/koji, not local rpmbuild
    "incorrect-fsf-address", # Upstream LICENSE file content, not a spec issue
}


def run_rpmlint(pkgname: str, container: str) -> dict[str, Any]:
    """Run rpmlint on all built RPMs and return structured result.

    Returns a dict with:
      errors        — list of {code, message} for actionable E: items
      warnings      — list of {code, message} for W: items
      whitelisted   — list of {code, message} for filtered infra errors
      error_count   — count of actionable errors (excluding whitelisted)
      passed        — True if error_count == 0
      raw           — full rpmlint output
    """
    proc = run_command([
        "docker", "exec", container, "bash", "-c",
        f"rpmlint ~/rpmbuild/RPMS/noarch/{pkgname}*.rpm "
        f"~/rpmbuild/RPMS/x86_64/{pkgname}*.rpm "
        f"~/rpmbuild/RPMS/aarch64/{pkgname}*.rpm 2>/dev/null || true",
    ])
    raw = proc.stdout.strip()

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    whitelisted: list[dict[str, str]] = []

    for line in raw.splitlines():
        # rpmlint output format: "<pkg>: E: <code> <message>" or "E: <code> <message>"
        m = re.match(r"^(?:[^:]+:\s+)?([EW]):\s+(\S+)(.*)", line)
        if not m:
            continue
        level, code, rest = m.group(1), m.group(2), m.group(3).strip()
        entry = {"code": code, "message": rest}
        if level == "E":
            if code in RPMLINT_INFRA_WHITELIST:
                whitelisted.append(entry)
            else:
                errors.append(entry)
        else:
            warnings.append(entry)

    return {
        "errors": errors,
        "warnings": warnings,
        "whitelisted": whitelisted,
        "error_count": len(errors),
        "passed": len(errors) == 0,
        "raw": raw,
    }


def read_spec_name(spec_path: Path) -> str:
    for line in spec_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("Name:"):
            return line.split(":", 1)[1].strip()
    return ""


def run_install(pkgname: str, container: str, rpm_name: str = "") -> subprocess.CompletedProcess:
    rpm_prefix = rpm_name or pkgname
    return run_command([
        "docker",
        "exec",
        container,
        "bash",
        "-c",
        f"rpm -ivh ~/rpmbuild/RPMS/aarch64/{rpm_prefix}-*.rpm 2>&1 || rpm -ivh ~/rpmbuild/RPMS/noarch/{rpm_prefix}-*.rpm 2>&1",
    ])


def is_idempotent_install_conflict(output: str) -> bool:
    text = output or ""
    return (
        "is already installed" in text
        and "conflicts with file from package" in text
    )


def build_result_payload(
    *,
    pkgname: str,
    lang: str,
    version: str,
    requested_version: str,
    depth: int,
    status: str,
    action: str,
    reason: str,
    precheck_summary: dict[str, Any],
    dependency_resolution: dict[str, Any],
    artifacts: dict[str, str],
    failure_type: str = "",
    failure_reason: str = "",
    build: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "pkgname": pkgname,
        "lang": lang,
        "version": version,
        "requested_version": requested_version,
        "depth": depth,
        "status": status,
        "action": action,
        "reason": reason,
        "build": build or {},
        "dependency_resolution": dependency_resolution,
        "precheck": precheck_summary,
        "artifacts": artifacts,
        "failure": {
            "failure_type": failure_type,
            "failure_reason": failure_reason,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="权威 build-rpm 主流程 orchestrator")
    parser.add_argument("pkgname")
    parser.add_argument("lang")
    parser.add_argument("upstream_url")
    parser.add_argument("version")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--container", default="oe-build-env")
    parser.add_argument("--source-dir", default="")
    parser.add_argument("--spec", default="", help="Existing spec file path")
    parser.add_argument("--build-state-dir", default="./build_state")
    parser.add_argument("--reports-dir", default="./reports")
    parser.add_argument("-o", "--output", default="")
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    build_state_dir = Path(args.build_state_dir)
    output_path = Path(args.output) if args.output else result_path(args.pkgname, reports_dir)
    source_dir = args.source_dir or f"./sources/{args.pkgname}"

    artifacts: dict[str, str] = {}

    try:
        precheck_rc, precheck_json, precheck_stdout, precheck_stderr = run_precheck(
            args.pkgname,
            args.lang,
            source_dir,
            args.container,
            reports_dir,
        )
        artifacts["precheck_json"] = str(precheck_json)
        if precheck_rc not in (0, 2):
            payload = build_result_payload(
                pkgname=args.pkgname,
                lang=args.lang,
                version=args.version,
                requested_version=args.version,
                depth=args.depth,
                status="blocked",
                action="blocked",
                reason="pre_check_deps failed",
                precheck_summary={},
                dependency_resolution={},
                artifacts=artifacts,
                failure_type="retryable_dependency_resolution_failure",
                failure_reason=precheck_stderr or precheck_stdout or "pre_check_deps failed",
            )
            write_json(output_path, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1

        precheck = read_json(precheck_json)
        precheck_summary = {
            "resolved_count": len(precheck.get("resolved") or []),
            "pending_count": len(precheck.get("pending") or []),
            "blocked_count": len(precheck.get("blocked") or []),
        }

        blocked = list(precheck.get("blocked") or [])
        pending = list(precheck.get("pending") or [])
        if blocked:
            payload = build_result_payload(
                pkgname=args.pkgname,
                lang=args.lang,
                version=args.version,
                requested_version=args.version,
                depth=args.depth,
                status="blocked",
                action="blocked",
                reason="precheck blocked dependencies",
                precheck_summary=precheck_summary,
                dependency_resolution={
                    "precheck_status": "blocked",
                    "recursion_status": "blocked",
                },
                artifacts=artifacts,
                failure_type="retryable_dependency_resolution_failure",
                failure_reason="precheck blocked dependencies",
            )
            write_json(output_path, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1

        recursion_payload: dict[str, Any] = {
            "precheck_status": "resolved_only" if not pending else "pending_found",
            "recursion_status": "not_needed" if not pending else "pending",
            "resolved_count": precheck_summary["resolved_count"],
            "pending_count": precheck_summary["pending_count"],
            "blocked_count": precheck_summary["blocked_count"],
        }

        if pending:
            # Dependency recursion is owned by build-rpm skill, not this script.
            # Return rc=2 with structured payload so skill can run /pkg-introduce per dep.
            payload = build_result_payload(
                pkgname=args.pkgname,
                lang=args.lang,
                version=args.version,
                requested_version=args.version,
                depth=args.depth,
                status="pending_deps",
                action="pending_deps",
                reason=f"{len(pending)} pending dependencies require recursive introduction",
                precheck_summary=precheck_summary,
                dependency_resolution={
                    "precheck_status": "pending_found",
                    "recursion_status": "deferred_to_skill",
                    "resolved_count": precheck_summary["resolved_count"],
                    "pending_count": precheck_summary["pending_count"],
                    "blocked_count": precheck_summary["blocked_count"],
                    "pending_deps": [d.get("name") or d.get("dep", "") for d in pending],
                },
                artifacts=artifacts,
            )
            write_json(output_path, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2

        build_state = {
            "spec_generated": False,
            "rpmlint_passed": False,
            "rpmlint": {},
            "rpmbuild_passed": False,
            "install_requested": args.install,
            "install_passed": False,
        }

        if args.spec:
            spec_path = Path(args.spec)
            rpm_name = read_spec_name(spec_path)
            artifacts["spec_path"] = str(spec_path)
            prepare_proc = prepare_build_inputs(args.pkgname, args.version, source_dir, spec_path, args.container)
            artifacts["prepared_inputs"] = str(spec_path)
            if prepare_proc.returncode != 0:
                payload = build_result_payload(
                    pkgname=args.pkgname,
                    lang=args.lang,
                    version=args.version,
                    requested_version=args.version,
                    depth=args.depth,
                    status="blocked",
                    action="blocked",
                    reason="prepare build inputs failed",
                    precheck_summary=precheck_summary,
                    dependency_resolution=recursion_payload,
                    artifacts=artifacts,
                    failure_type="non_retryable_build_failure",
                    failure_reason=prepare_proc.stderr.strip() or prepare_proc.stdout.strip() or "prepare build inputs failed",
                    build=build_state,
                )
                write_json(output_path, payload)
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 1
            build_state["spec_generated"] = True

            builddep_proc = run_dnf_builddep(args.pkgname, args.container)
            if builddep_proc.returncode != 0:
                payload = build_result_payload(
                    pkgname=args.pkgname,
                    lang=args.lang,
                    version=args.version,
                    requested_version=args.version,
                    depth=args.depth,
                    status="blocked",
                    action="blocked",
                    reason="dnf builddep failed",
                    precheck_summary=precheck_summary,
                    dependency_resolution=recursion_payload,
                    artifacts=artifacts,
                    failure_type="non_retryable_build_failure",
                    failure_reason=builddep_proc.stderr.strip() or builddep_proc.stdout.strip() or "dnf builddep failed",
                    build=build_state,
                )
                write_json(output_path, payload)
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 1

            rpmbuild_proc = run_rpmbuild(args.pkgname, args.container)
            if rpmbuild_proc.returncode != 0:
                payload = build_result_payload(
                    pkgname=args.pkgname,
                    lang=args.lang,
                    version=args.version,
                    requested_version=args.version,
                    depth=args.depth,
                    status="blocked",
                    action="blocked",
                    reason="rpmbuild failed",
                    precheck_summary=precheck_summary,
                    dependency_resolution=recursion_payload,
                    artifacts=artifacts,
                    failure_type="non_retryable_build_failure",
                    failure_reason=rpmbuild_proc.stderr.strip() or rpmbuild_proc.stdout.strip() or "rpmbuild failed",
                    build=build_state,
                )
                write_json(output_path, payload)
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 1
            build_state["rpmbuild_passed"] = True

            rpmlint_result = run_rpmlint(args.pkgname, args.container)
            build_state["rpmlint"] = rpmlint_result
            build_state["rpmlint_passed"] = rpmlint_result["passed"]
            if not rpmlint_result["passed"]:
                print(f"[WARN] rpmlint 发现 {rpmlint_result['error_count']} 个可修复错误:")
                for e in rpmlint_result["errors"]:
                    print(f"  E: {e['code']} {e['message']}")
            if rpmlint_result["whitelisted"]:
                print(f"[INFO] rpmlint 白名单过滤 {len(rpmlint_result['whitelisted'])} 个基础设施级错误（不阻断）:")
                for e in rpmlint_result["whitelisted"]:
                    print(f"  (filtered) E: {e['code']} {e['message']}")

            if args.install:
                install_proc = run_install(args.pkgname, args.container, rpm_name=rpm_name)
                install_output = (install_proc.stderr.strip() or "") + ("\n" if install_proc.stderr.strip() and install_proc.stdout.strip() else "") + (install_proc.stdout.strip() or "")
                if install_proc.returncode != 0 and not is_idempotent_install_conflict(install_output):
                    payload = build_result_payload(
                        pkgname=args.pkgname,
                        lang=args.lang,
                        version=args.version,
                        requested_version=args.version,
                        depth=args.depth,
                        status="blocked",
                        action="blocked",
                        reason="rpm install failed",
                        precheck_summary=precheck_summary,
                        dependency_resolution=recursion_payload,
                        artifacts=artifacts,
                        failure_type="non_retryable_build_failure",
                        failure_reason=install_output or "rpm install failed",
                        build=build_state,
                    )
                    write_json(output_path, payload)
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return 1
                build_state["install_passed"] = True

        payload = build_result_payload(
            pkgname=args.pkgname,
            lang=args.lang,
            version=args.version,
            requested_version=args.version,
            depth=args.depth,
            status="resolved",
            action="built_new",
            reason="build-rpm flow completed successfully" if args.spec else "precheck and dependency recursion completed (rpmbuild loop not yet folded into this orchestrator)",
            precheck_summary=precheck_summary,
            dependency_resolution=recursion_payload,
            artifacts=artifacts,
            build=build_state,
        )
        write_json(output_path, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except FlowError as exc:
        payload = build_result_payload(
            pkgname=args.pkgname,
            lang=args.lang,
            version=args.version,
            requested_version=args.version,
            depth=args.depth,
            status="blocked",
            action="blocked",
            reason=exc.reason,
            precheck_summary={},
            dependency_resolution={},
            artifacts=artifacts,
            failure_type=exc.failure_type,
            failure_reason=exc.reason,
        )
        write_json(output_path, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
