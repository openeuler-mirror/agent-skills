#!/usr/bin/env python3
"""
获取 EBS-openEuler-Mainline 最新 aarch64 Docker 镜像

功能：
  1. 爬取 dailybuild 站点，找到最新构建目录
  2. 下载 openEuler-docker.aarch64.tar.xz
  3. 加载到本地 Docker（可选）

用法：
  python fetch_latest_image.py [--download] [--load] [--output-dir /path]
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

BASE_URL = "http://121.36.84.172/dailybuild"
BRANCH = "EBS-openEuler-Mainline"
ARCH = "x86_64"
IMAGE_FILENAME = f"openEuler-docker.{ARCH}.tar.xz"


def fetch_html(url: str) -> BeautifulSoup:
    """获取页面并解析"""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"[ERROR] 请求失败 {url}: {e}", file=sys.stderr)
        sys.exit(1)


def list_build_dirs(branch_url: str) -> List[Dict]:
    """
    列出所有按日期命名的构建目录，返回按日期排序的列表。
    目录格式：openeuler-YYYY-MM-DD-HH-MM-SS/
    """
    soup = fetch_html(branch_url)
    pattern = re.compile(r"^openeuler-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})/$")
    builds = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = pattern.match(href)
        if m:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d-%H-%M-%S")
            builds.append({"name": href.rstrip("/"), "datetime": dt, "href": href})

    builds.sort(key=lambda x: x["datetime"], reverse=True)
    return builds


def get_image_url(branch_url: str, build_name: str) -> str:
    """拼接镜像下载 URL"""
    return f"{branch_url}/{build_name}/docker_img/{ARCH}/{IMAGE_FILENAME}"


def verify_checksum(file_path: Path, sha256sum_url: str) -> bool:
    """下载 sha256sum 文件并校验"""
    try:
        resp = requests.get(sha256sum_url, timeout=10)
        resp.raise_for_status()
        # 格式：<hash>  <filename>
        expected_hash = resp.text.strip().split()[0]
    except Exception as e:
        print(f"[WARN] 无法获取校验文件: {e}")
        return True  # 跳过校验

    print(f"[INFO] 校验 SHA256: {expected_hash[:16]}...")
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest()

    if actual == expected_hash:
        print("[INFO] 校验通过")
        return True
    else:
        print(f"[ERROR] 校验失败: 期望 {expected_hash}, 实际 {actual}")
        return False


def download_image(url: str, output_dir: Path) -> Path:
    """流式下载镜像，显示进度"""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / IMAGE_FILENAME

    if dest.exists():
        print(f"[INFO] 文件已存在，跳过下载: {dest}")
        return dest

    print(f"[INFO] 开始下载: {url}")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                print(f"\r[INFO] 下载进度: {pct:.1f}% ({downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB)", end="")

    print()
    print(f"[INFO] 下载完成: {dest}")
    return dest


def load_docker_image(tar_path: Path) -> str:
    """将 tar.xz 加载到 Docker，返回镜像名"""
    print(f"[INFO] 加载 Docker 镜像: {tar_path}")
    try:
        result = subprocess.run(
            ["docker", "load", "-i", str(tar_path)],
            capture_output=True, text=True, check=True
        )
        # 输出格式：Loaded image: openeuler:latest 或 Loaded image ID: sha256:...
        output = result.stdout.strip()
        print(f"[INFO] {output}")
        m = re.search(r"Loaded image:\s*(\S+)", output)
        return m.group(1) if m else output
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] docker load 失败: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("[ERROR] Docker 未安装或不在 PATH 中", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="获取 EBS-openEuler-Mainline 最新 aarch64 Docker 镜像")
    parser.add_argument("--download", action="store_true", help="下载镜像文件")
    parser.add_argument("--load", action="store_true", help="加载镜像到 Docker（自动启用 --download）")
    parser.add_argument("--output-dir", default="./images", help="镜像保存目录（默认 ./images）")
    parser.add_argument("--list", action="store_true", help="仅列出可用构建，不下载")
    args = parser.parse_args()

    if args.load:
        args.download = True

    branch_url = f"{BASE_URL}/{BRANCH}"
    print(f"[INFO] 扫描构建目录: {branch_url}")

    builds = list_build_dirs(branch_url)
    if not builds:
        print("[ERROR] 未找到任何构建目录", file=sys.stderr)
        sys.exit(1)

    if args.list:
        print(f"\n找到 {len(builds)} 个构建目录（最新在前）:")
        for b in builds[:10]:
            print(f"  {b['name']}  ({b['datetime'].strftime('%Y-%m-%d %H:%M:%S')})")
        return

    latest = builds[0]
    image_url = get_image_url(branch_url, latest["name"])
    sha256_url = image_url + ".sha256sum"

    print(f"[INFO] 最新构建: {latest['name']} ({latest['datetime'].strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"[INFO] 镜像 URL: {image_url}")

    if not args.download:
        # 仅输出 URL，供外部使用
        print(f"\nIMAGE_URL={image_url}")
        return

    output_dir = Path(args.output_dir)
    tar_path = download_image(image_url, output_dir)
    verify_checksum(tar_path, sha256_url)

    if args.load:
        image_name = load_docker_image(tar_path)
        print(f"\nDOCKER_IMAGE={image_name}")
    else:
        print(f"\nTAR_PATH={tar_path}")


if __name__ == "__main__":
    main()
