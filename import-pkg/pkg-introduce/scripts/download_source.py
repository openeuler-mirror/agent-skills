#!/usr/bin/env python3
"""
从 PR 信息中提取上游地址并下载源码

流程：
  1. 读取 extract_pr_info.py 生成的 pr_N_info.json
  2. 从 YAML diff 中解析 upstream 字段
  3. 根据 URL 类型选择下载方式：
     - GitHub/GitLab/AtomGit → git clone --depth=1
     - .tar.gz/.tar.xz/.zip  → wget + 解压

用法：
  python3 download_source.py --pr-json pr_1_info.json --output-dir ./sources
  python3 download_source.py --owner shuyingbanbao --repo community --pr 1 --output-dir ./sources
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ── 1. 从 PR JSON 提取 upstream URL ──────────────────────────────────────────

def extract_upstream_url(pr_json_path: str) -> Optional[str]:
    """从 pr_N_info.json 的 diff 内容中解析 upstream 字段"""
    with open(pr_json_path, encoding="utf-8") as f:
        data = json.load(f)

    files = data.get("files") or []
    for file_info in files:
        filename = file_info.get("filename", "")
        # 只处理 .yaml/.yml 文件
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue

        patch = file_info.get("patch", "")
        diff_text = patch.get("diff", "") if isinstance(patch, dict) else patch

        url = _parse_upstream_from_diff(diff_text)
        if url:
            print(f"[INFO] 在文件 {filename} 中找到 upstream: {url}")
            return url

    return None


def _parse_upstream_from_diff(diff_text: str) -> Optional[str]:
    """从 diff 文本中提取 upstream: <url> 行"""
    for line in diff_text.splitlines():
        # diff 新增行以 + 开头
        content = line.lstrip("+").strip()
        m = re.match(r"upstream\s*:\s*(\S+)", content, re.IGNORECASE)
        if m:
            url = m.group(1).rstrip("/")
            return url
    return None


# ── 2. 判断 URL 类型并下载 ────────────────────────────────────────────────────

def detect_url_type(url: str) -> str:
    """
    判断 URL 类型：
      git_repo  — GitHub/GitLab/Gitee/AtomGit 仓库地址
      tarball   — .tar.gz / .tar.xz / .tar.bz2 / .zip 压缩包
      unknown
    """
    lower = url.lower()
    if any(lower.endswith(ext) for ext in (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip")):
        return "tarball"
    git_hosts = ("github.com", "gitlab.com", "gitee.com", "atomgit.com", "gitcode.com")
    if any(host in lower for host in git_hosts):
        return "git_repo"
    # 其他 http/https 地址，尝试当 git 仓库处理
    if lower.startswith("http") or lower.startswith("git@"):
        return "git_repo"
    return "unknown"


def download_git_repo(url: str, output_dir: Path) -> Path:
    """git clone --depth=1，返回克隆后的目录路径"""
    # 从 URL 推断目录名，去掉 .git 后缀
    repo_name = url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    dest = output_dir / repo_name

    if dest.exists():
        print(f"[INFO] 目录已存在，跳过克隆: {dest}")
        return dest

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] git clone --depth=1 {url}")
    result = subprocess.run(
        ["git", "clone", "--depth=1", url, str(dest)],
        capture_output=False,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"[ERROR] git clone 失败，退出码: {result.returncode}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] 克隆完成: {dest}")
    return dest


def download_tarball(url: str, output_dir: Path) -> Path:
    """下载压缩包并解压，返回解压后的目录路径"""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = url.split("/")[-1].split("?")[0]
    dest_file = output_dir / filename

    if not dest_file.exists():
        print(f"[INFO] 下载: {url}")
        result = subprocess.run(
            ["wget", "-q", "--show-progress", "-O", str(dest_file), url],
            timeout=300,
        )
        if result.returncode != 0:
            print(f"[ERROR] 下载失败", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"[INFO] 文件已存在，跳过下载: {dest_file}")

    # 解压
    print(f"[INFO] 解压: {dest_file}")
    if filename.endswith(".zip"):
        subprocess.run(["unzip", "-q", str(dest_file), "-d", str(output_dir)], check=True)
    else:
        subprocess.run(["tar", "-xf", str(dest_file), "-C", str(output_dir)], check=True)

    # 找到解压出来的目录（排除压缩包本身）
    entries = [e for e in output_dir.iterdir() if e.is_dir()]
    if len(entries) == 1:
        return entries[0]
    # 多个目录时，取名字最像包名的
    stem = re.sub(r"\.(tar\.\w+|tgz|zip)$", "", filename)
    for e in entries:
        if stem in e.name:
            return e
    return entries[0] if entries else output_dir


def download_source(url: str, output_dir: Path) -> Path:
    """根据 URL 类型自动选择下载方式"""
    url_type = detect_url_type(url)
    print(f"[INFO] URL 类型: {url_type}")

    if url_type == "git_repo":
        return download_git_repo(url, output_dir)
    elif url_type == "tarball":
        return download_tarball(url, output_dir)
    else:
        print(f"[WARN] 未知 URL 类型，尝试 git clone: {url}")
        return download_git_repo(url, output_dir)


# ── 3. 主入口 ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="从 PR 提取上游地址并下载源码")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr-json", help="已有的 pr_N_info.json 文件路径")
    group.add_argument("--upstream-url", help="直接指定上游 URL（跳过 PR 解析）")
    parser.add_argument("--output-dir", default="./sources", help="源码下载目录（默认 ./sources）")
    parser.add_argument("-o", "--output", default="", help="将结果写入 JSON 文件")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    if args.upstream_url:
        url = args.upstream_url
    else:
        if not os.path.exists(args.pr_json):
            print(f"[ERROR] 文件不存在: {args.pr_json}", file=sys.stderr)
            sys.exit(1)
        url = extract_upstream_url(args.pr_json)
        if not url:
            print("[ERROR] 未在 PR 文件中找到 upstream 字段", file=sys.stderr)
            sys.exit(1)

    source_dir = download_source(url, output_dir)
    print(f"\nSOURCE_DIR={source_dir}")

    if args.output:
        result = {"upstream_url": url, "source_dir": str(source_dir)}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 结果已保存: {args.output}")


if __name__ == "__main__":
    main()
