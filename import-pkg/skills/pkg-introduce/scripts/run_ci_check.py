#!/usr/bin/env python3
"""
CI 门禁：在容器内执行 repoclosure（运行时依赖）+ dnf builddep（编译期依赖）。
将归档仓 dist/ 作为 ci-local 源，验证新构建包的依赖闭合。

用法：
  python3 run_ci_check.py \
    --pkgs python3-foo python3-bar \
    --container oe-build-env \
    --repo-local /root/.claude/skills/rpm-repo-github \
    --reports-dir ./reports

exit codes:
  0  全部通过
  1  检查失败
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


CI_TMP = "/tmp/_ci_dist"


def _docker_exec(container: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, "bash", "-c", cmd],
        capture_output=True, text=True,
    )


def copy_dist_to_container(container: str, dist_dir: Path) -> None:
    subprocess.run(["docker", "exec", container, "rm", "-rf", CI_TMP], capture_output=True)
    subprocess.run(["docker", "cp", str(dist_dir), f"{container}:{CI_TMP}"], check=True)
    _docker_exec(container, "rm -rf /var/cache/dnf/ci-local*")


def run_repoclosure(container: str, pkgs: list[str]) -> tuple[bool, str]:
    # 检测可用 repo 列表
    probe = _docker_exec(container, "dnf repolist --enabled 2>/dev/null | awk 'NR>1{print $1}'")
    available = set(probe.stdout.split())

    wanted = ["OS", "everything", "update", "EPOL", "EPOL-update", "repo-aitest"]
    enable_args = f"--repofrompath ci-local,{CI_TMP} --disablerepo='*' --enablerepo=ci-local"
    for repo in wanted:
        if repo in available:
            enable_args += f" --enablerepo={repo}"

    pkg_args = " ".join(f"--pkg {p}" for p in pkgs)
    cmd = f"dnf install -y --quiet dnf-utils 2>/dev/null; repoclosure {enable_args} --newest {pkg_args} 2>&1"
    result = _docker_exec(container, cmd)

    if result.returncode != 0:
        return False, result.stdout + result.stderr
    return True, ""


def run_builddep(container: str, pkg: str, dist_dir: Path) -> tuple[bool, str]:
    # 重新复制 dist 到容器（repoclosure 可能已清理）
    subprocess.run(["docker", "exec", container, "rm", "-rf", CI_TMP], capture_output=True)
    subprocess.run(["docker", "cp", str(dist_dir), f"{container}:{CI_TMP}"], check=True)

    spec_in_container = f"/root/rpmbuild/SPECS/{pkg}.spec"
    cmd = (
        f"dnf builddep --assumeno "
        f"--repofrompath ci-local,{CI_TMP} --enablerepo ci-local "
        f"{spec_in_container} 2>&1"
    )
    result = _docker_exec(container, cmd)

    combined = result.stdout + result.stderr
    # dnf builddep --assumeno 在依赖可满足时以非零码退出（因为 assumeno 拒绝安装）
    # 只有输出含 "Error:" + "could not be found"/"No match" 才是真正的依赖缺失
    failed = "Error:" in combined and ("could not be found" in combined or "No match" in combined)
    return (not failed), (combined if failed else "")


def main() -> int:
    parser = argparse.ArgumentParser(description="CI 门禁：repoclosure + dnf builddep")
    parser.add_argument("--pkgs", nargs="+", required=True, help="待检查的包名列表")
    parser.add_argument("--container", required=True, help="容器名")
    parser.add_argument("--repo-local", required=True, help="归档仓本地路径")
    parser.add_argument("--reports-dir", default="./reports", help="报告目录")
    args = parser.parse_args()

    dist_dir = Path(args.repo_local) / "dist"
    reports_dir = Path(args.reports_dir)
    errors: list[str] = []

    print(f"[CI] 复制 dist/ 到容器 {args.container}...")
    copy_dist_to_container(args.container, dist_dir)

    # 检查1：repoclosure（所有包一起验证运行时依赖）
    print(f"[CI] 运行 repoclosure（{len(args.pkgs)} 个包）...")
    ok, msg = run_repoclosure(args.container, args.pkgs)
    if ok:
        print("[CI] ✓ 运行时依赖检查通过")
    else:
        print(f"[CI] ✗ 运行时依赖检查失败", file=sys.stderr)
        errors.append(f"repoclosure 失败:\n{msg.strip()}")

    # 检查2：dnf builddep（逐包验证编译期依赖）
    for pkg in args.pkgs:
        print(f"[CI] 运行 dnf builddep（{pkg}）...")
        ok, msg = run_builddep(args.container, pkg, dist_dir)
        if ok:
            print(f"[CI] ✓ {pkg} 编译期依赖检查通过")
        else:
            print(f"[CI] ✗ {pkg} 编译期依赖检查失败", file=sys.stderr)
            errors.append(f"{pkg} BuildRequires 不满足:\n{msg.strip()}")

    # 清理容器内临时目录
    subprocess.run(["docker", "exec", args.container, "rm", "-rf", CI_TMP], capture_output=True)

    # 写结果文件
    result = {"status": "pass" if not errors else "fail", "errors": errors}
    (reports_dir / "ci_check_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if errors:
        print(f"\n[CI] 门禁未通过，共 {len(errors)} 项失败", file=sys.stderr)
        return 1

    print("[CI] 门禁全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
