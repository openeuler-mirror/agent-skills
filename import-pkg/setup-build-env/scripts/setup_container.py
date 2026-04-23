#!/usr/bin/env python3
"""
启动 OpenEuler aarch64 构建容器

功能：
  1. 每次调用强制删除旧容器并重新创建（保证环境干净）
  2. 挂载源码目录到容器内 /build/source
  3. 在容器内安装基础构建工具
  4. 提供 exec_in_container() 供其他脚本调用

用法：
  python3 setup_container.py --source-dir ./sources/fzf
  python3 setup_container.py --source-dir ./sources/fzf --name my-build-env
  python3 setup_container.py --stop   # 停止并删除容器
"""

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_IMAGE = "openeuler-mainline:latest"
DEFAULT_NAME  = "oe-build-env"
CONTAINER_SOURCE_DIR = "/build/source"

# Mainline 镜像是 openEuler 25.09，用官方源 + dailybuild update 叠加
# 官方源提供完整基础包，dailybuild update 提供最新增量包
REPO_TEMPLATE = """\
[OS]
name=openEuler-{ver}-OS
baseurl=https://repo.openeuler.org/openEuler-{ver}/OS/$basearch/
enabled=1
gpgcheck=0

[everything]
name=openEuler-{ver}-everything
baseurl=https://repo.openeuler.org/openEuler-{ver}/everything/$basearch/
enabled=1
gpgcheck=0

[EPOL]
name=openEuler-{ver}-EPOL
baseurl=https://repo.openeuler.org/openEuler-{ver}/EPOL/main/$basearch/
enabled=1
gpgcheck=0
{update_section}"""

# dailybuild URL 宿主机缓存文件及其有效期（秒）
_DAILYBUILD_CACHE_FILE = "/tmp/oe_dailybuild_url.cache"
_DAILYBUILD_CACHE_TTL  = 3600   # 1 小时

# 容器内基础工具（所有语言都需要）
BASE_PACKAGES = [
    "gcc", "gcc-c++", "make", "rpm-build", "dnf-plugins-core",
    "git", "wget", "tar", "which", "findutils",
]

# 各语言专项工具链
LANG_PACKAGES = {
    "go":     ["golang"],
    "python": ["python3", "python3-pip", "python3-devel"],
    "java":   ["java-latest-openjdk-devel", "maven"],
    "rust":   ["rust", "cargo"],
    "nodejs": ["nodejs", "npm"],
    # c/c++ 已由 BASE_PACKAGES 覆盖，无需额外包
    "c":      [],
    "cpp":    [],
}


def container_exists(name: str) -> bool:
    result = subprocess.run(["docker", "inspect", name], capture_output=True)
    return result.returncode == 0


def _get_latest_dailybuild() -> str:
    """获取最新 dailybuild 目录名，优先读宿主机缓存（1 小时 TTL）。"""
    import time, os
    cache = _DAILYBUILD_CACHE_FILE
    if os.path.exists(cache):
        age = time.time() - os.path.getmtime(cache)
        if age < _DAILYBUILD_CACHE_TTL:
            cached = open(cache).read().strip()
            if cached:
                print(f"[INFO] dailybuild cache hit: {cached} (age {int(age)}s)")
                return cached

    # 缓存失效或不存在，重新请求
    r = subprocess.run(
        ["curl", "-s", "--max-time", "10",
         "http://121.36.84.172/dailybuild/EBS-openEuler-Mainline/"],
        capture_output=True, text=True
    )
    import re
    matches = re.findall(r'openeuler-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}', r.stdout)
    latest = sorted(matches)[-1] if matches else ""
    if latest:
        try:
            open(cache, "w").write(latest)
        except OSError:
            pass
        print(f"[INFO] dailybuild fetched: {latest}")
    else:
        print("[WARN] 无法获取 dailybuild 目录，跳过 update 源", file=sys.stderr)
    return latest


def fix_repo(name: str) -> bool:
    """替换容器内错误的 repo，使用官方源 + dailybuild update"""
    print("[INFO] 修复 repo 配置...")

    # 读取容器内系统版本
    r = subprocess.run(
        ["docker", "exec", name, "bash", "-c",
         "grep VERSION_ID /etc/os-release | cut -d= -f2 | tr -d '\"'"],
        capture_output=True, text=True
    )
    ver = r.stdout.strip() or "25.09"
    print(f"[INFO] 容器系统版本: openEuler {ver}")

    # 从宿主机获取最新 dailybuild 目录（带缓存，避免每次请求服务器）
    latest_build = _get_latest_dailybuild()
    update_section = ""
    if latest_build:
        update_url = (f"http://121.36.84.172/dailybuild/EBS-openEuler-Mainline"
                      f"/{latest_build}/update/$basearch/")
        update_section = (f"\n[dailybuild-update]\nname=openEuler-Mainline-dailybuild-update\n"
                          f"baseurl={update_url}\nenabled=1\ngpgcheck=0\n")
        print(f"[INFO] dailybuild update: {latest_build}")

    repo = REPO_TEMPLATE.format(ver=ver, update_section=update_section)

    # 通过 docker cp 写入，避免 shell 转义问题
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".repo", delete=False) as f:
        f.write(repo)
        tmp = f.name
    subprocess.run(["docker", "cp", tmp, f"{name}:/etc/yum.repos.d/openEuler.repo"], check=True)
    os.unlink(tmp)

    rc = exec_in_container(name, "dnf clean all -q", workdir="/")
    if rc != 0:
        print("[ERROR] dnf clean 失败", file=sys.stderr)
        return False
    print("[INFO] repo 已更新")
    return True


def start_container(source_dir: str, name: str, image: str) -> bool:
    """强制删除旧容器并重新创建，确保每次环境干净"""
    src = Path(source_dir).resolve()
    if not src.exists():
        print(f"[ERROR] 源码目录不存在: {src}", file=sys.stderr)
        return False

    if container_exists(name):
        print(f"[INFO] 删除旧容器以确保环境干净: {name}")
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    print(f"[INFO] 创建并启动容器: {name}")
    print(f"[INFO] 镜像: {image}")
    print(f"[INFO] 挂载: {src} → {CONTAINER_SOURCE_DIR}")

    r = subprocess.run([
        "docker", "run", "-d",
        "--platform", "linux/amd64",
        "--name", name,
        "-v", f"{src}:{CONTAINER_SOURCE_DIR}",
        image,
        "tail", "-f", "/dev/null",   # 保持容器运行
    ], capture_output=True, text=True)

    if r.returncode != 0:
        print(f"[ERROR] 启动容器失败:\n{r.stderr}", file=sys.stderr)
        return False

    print(f"[INFO] 容器已启动: {r.stdout.strip()[:12]}")
    fix_repo(name)
    return True


def exec_in_container(name: str, cmd: str, workdir: str = CONTAINER_SOURCE_DIR) -> int:
    """在容器内执行命令，实时输出，返回退出码"""
    r = subprocess.run([
        "docker", "exec", "-w", workdir, name,
        "bash", "-c", cmd
    ])
    return r.returncode


def install_base_packages(name: str, lang: str = "") -> bool:
    """安装基础构建工具，以及语言专项工具链"""
    print("[INFO] 安装基础构建工具...")
    pkgs = list(BASE_PACKAGES)

    lang_key = lang.lower().replace("-", "").replace(".", "")
    extra = LANG_PACKAGES.get(lang_key, [])
    if extra:
        print(f"[INFO] 追加语言工具链 ({lang}): {' '.join(extra)}")
        pkgs.extend(extra)
    elif lang and lang_key not in LANG_PACKAGES:
        print(f"[WARN] 未知语言类型 '{lang}'，仅安装基础包", file=sys.stderr)

    commands = [f"dnf install -y --allowerasing {' '.join(pkgs)}"]
    if lang_key == "python":
        commands.append(
            "dnf install -y --allowerasing python3 python3-pip && "
            "dnf install -y --allowerasing --nobest python3-devel"
        )
        commands.append(
            "dnf distro-sync -y --allowerasing python3 python3-devel python3-pip"
        )

    for command in commands:
        rc = exec_in_container(name, command)
        if rc == 0:
            print("[INFO] 基础工具安装完成")
            return True
        print(f"[WARN] 安装命令失败，尝试下一种策略: {command}", file=sys.stderr)

    print("[ERROR] 基础包安装失败", file=sys.stderr)
    return False


def stop_container(name: str):
    """停止并删除容器"""
    if container_exists(name):
        print(f"[INFO] 停止容器: {name}")
        subprocess.run(["docker", "stop", name], capture_output=True)
        subprocess.run(["docker", "rm", name], capture_output=True)
        print(f"[INFO] 容器已删除: {name}")
    else:
        print(f"[INFO] 容器不存在: {name}")


def main():
    parser = argparse.ArgumentParser(description="启动 OpenEuler aarch64 构建容器")
    parser.add_argument("--source-dir", default="", help="源码目录（挂载到容器内）")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"容器名（默认 {DEFAULT_NAME}）")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help=f"镜像名（默认 {DEFAULT_IMAGE}）")
    parser.add_argument("--install-base", action="store_true", help="安装基础构建工具")
    parser.add_argument("--lang", default="", help="语言类型（go/python/java/rust/nodejs/c/cpp），用于安装专项工具链")
    parser.add_argument("--stop", action="store_true", help="停止并删除容器")
    args = parser.parse_args()

    if args.stop:
        stop_container(args.name)
        return

    if not args.source_dir:
        parser.print_help()
        sys.exit(1)

    ok = start_container(args.source_dir, args.name, args.image)
    if not ok:
        sys.exit(1)

    if args.install_base:
        ok = install_base_packages(args.name, args.lang)
        if not ok:
            sys.exit(1)

    print(f"\nCONTAINER_NAME={args.name}")
    print(f"SOURCE_DIR_IN_CONTAINER={CONTAINER_SOURCE_DIR}")


if __name__ == "__main__":
    main()
