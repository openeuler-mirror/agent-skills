#!/usr/bin/env python3
"""
RPM 归档脚本：
  - spec + source tarball → <pkg>/ 目录（升级时清理旧 tarball）
  - 编译好的 RPM          → dist/（扁平结构 + repodata，可直接作为 yum 软件源）
  - 升级处理：
      安全升级（patch/minor，无反向依赖）    → 直接替换
      需要 compat（major 或存在反向依赖）    → 按包类型决策：
        C 库 / 非语言运行时二进制            → rpmrebuild 改名，不依赖旧源码
        Python / Java / Ruby / Node         → 报错中止，提示手动处理
      --force-upgrade                        → 跳过 compat 逻辑，直接替换
  - CI 门禁：提交前在容器内跑 repoclosure，失败则回滚

用法：
  python3 publish_rpm.py --pkgs python3-foo
  python3 publish_rpm.py --pkgs python3-foo --force-upgrade
  python3 publish_rpm.py --pkgs python3-foo python3-bar --container oe-build-env
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


# ──────────────────────────────────────────────
# 基础工具
# ──────────────────────────────────────────────

def load_config(config_path: str) -> dict:
    config = Path(config_path)
    if not config.is_absolute():
        script_relative = Path(__file__).resolve().parent.parent / config_path
        if script_relative.exists():
            config = script_relative

    try:
        with open(config) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] 配置文件不存在: {config}", file=sys.stderr)
        sys.exit(1)


def run(cmd: list, cwd: str = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"[RUN] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def normalize_name_token(value: str) -> str:
    return re.sub(r"[-_.]+", "_", value.lower())


def auth_url(remote_url: str, username: str, token: str) -> str:
    if not token:
        return remote_url
    if "://" in remote_url:
        scheme, rest = remote_url.split("://", 1)
        return f"{scheme}://{username}:{token}@{rest}"
    return remote_url


# ──────────────────────────────────────────────
# Git 仓库管理
# ──────────────────────────────────────────────

def init_or_update_repo(local_dir: str, remote_url: str, branch: str):
    path = Path(local_dir)
    if (path / ".git").exists():
        print(f"[INFO] 拉取最新代码")
        run(["git", "pull", "origin", branch], cwd=local_dir, check=False)
        return
    print(f"[INFO] 克隆仓库: {remote_url} → {local_dir}")
    result = subprocess.run(
        ["git", "clone", "--branch", branch, remote_url, local_dir],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[INFO] 克隆失败（空仓库），初始化本地仓库")
        path.mkdir(parents=True, exist_ok=True)
        run(["git", "init"], cwd=local_dir)
        run(["git", "checkout", "-b", branch], cwd=local_dir)
        run(["git", "remote", "add", "origin", remote_url], cwd=local_dir)


def git_commit_and_push(repo_dir: str, branch: str, remote_url: str, msg: str):
    run(["git", "add", "."], cwd=repo_dir)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_dir,
        capture_output=True, text=True
    )
    if not status.stdout.strip():
        print("[INFO] 无变更，跳过提交")
        return
    run(["git", "commit", "-m", msg], cwd=repo_dir)
    result = subprocess.run(
        ["git", "push", remote_url, f"HEAD:{branch}", "--set-upstream"],
        cwd=repo_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[WARN] 普通推送失败，尝试 force push（首次初始化）")
        run(["git", "push", remote_url, f"HEAD:{branch}", "--force"], cwd=repo_dir)
    else:
        print("[INFO] 推送成功")


def git_reset_working_tree(repo_dir: str):
    subprocess.run(["git", "checkout", "--", "."], cwd=repo_dir)
    subprocess.run(["git", "clean", "-fd"], cwd=repo_dir)


# ──────────────────────────────────────────────
# 从容器拷出文件
# ──────────────────────────────────────────────

def copy_pkg_files(
    container: str, pkg_name: str, repo_dir: str
) -> Tuple[List[str], List[Path]]:
    """
    从容器拷出文件：
      - spec + source tarball → <repo_dir>/<pkg_name>/（升级时清理旧 tarball）
      - 编译好的 RPM          → <repo_dir>/dist/
    返回 (已拷出文件相对路径列表, 新增到 dist/ 的 RPM Path 列表)
    通过集合差值精确追踪新增 RPM，不依赖时间戳。
    """
    pkg_dir  = Path(repo_dir) / pkg_name
    dist_dir = Path(repo_dir) / "dist"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    copied: List[str] = []
    dist_before = set(dist_dir.glob("*.rpm"))

    # ── spec 文件 → <pkg>/（同名覆盖）──
    spec_src = f"/root/rpmbuild/SPECS/{pkg_name}.spec"
    if subprocess.run(["docker", "exec", container, "test", "-f", spec_src],
                      capture_output=True).returncode == 0:
        subprocess.run(
            ["docker", "cp", f"{container}:{spec_src}", str(pkg_dir)], check=True
        )
        print(f"[INFO] 拷出 spec: {pkg_name}.spec → {pkg_name}/")
        copied.append(f"{pkg_name}/{pkg_name}.spec")
    else:
        print(f"[WARN] spec 文件不存在: {spec_src}")

    # ── source tarball → <pkg>/（清理旧版本）──
    base_name = pkg_name.replace("python3-", "")
    normalized_base_name = normalize_name_token(base_name)

    # 从 spec 文件中解析 Source0 的实际文件名（支持 %global 宏展开）
    # 这样可以正确处理 Source0 使用不同于包名的 %{pkg_name} 宏的情况
    # 例如：ros-humble-situational-graphs-msgs 的 tarball 是 situational_graphs_msgs-x.y.z.tar.gz
    def resolve_spec_source_prefix(container: str, pkg_name: str) -> Optional[str]:
        spec_path = f"/root/rpmbuild/SPECS/{pkg_name}.spec"
        r = subprocess.run(
            ["docker", "exec", container, "cat", spec_path],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            return None
        spec_text = r.stdout
        # 收集所有 %global 和 %define 宏
        macros: dict = {}
        for m in re.finditer(r'^%(?:global|define)\s+(\w+)\s+(\S+)', spec_text, re.MULTILINE):
            macros[m.group(1)] = m.group(2)
        # 提取 Source0 行
        m = re.search(r'^Source0?\s*:\s*(\S+)', spec_text, re.MULTILINE)
        if not m:
            return None
        src0 = m.group(1)
        # 展开已知宏
        def expand(s: str, macros: dict) -> str:
            for _ in range(5):
                expanded = re.sub(
                    r'%\{(\w+)\}',
                    lambda mo: macros.get(mo.group(1), mo.group(0)),
                    s
                )
                if expanded == s:
                    break
                s = expanded
            return s
        # 将 %{version} 等无法静态解析的宏替换为通配前缀截断
        src0 = expand(src0, macros)
        # 返回 %{version} 之前的静态前缀（作为文件名匹配前缀）
        prefix = re.split(r'%\{|\$', src0)[0]
        return prefix if prefix else None

    spec_source_prefix = resolve_spec_source_prefix(container, pkg_name)
    # spec_source_prefix 示例："situational_graphs_msgs-"，优先用它；
    # 若无法解析则回退到 base_name
    effective_prefix = spec_source_prefix if spec_source_prefix else base_name

    existing_tarballs = (
        set(pkg_dir.glob("*.tar.gz")) | set(pkg_dir.glob("*.tar.bz2"))
        | set(pkg_dir.glob("*.tar.xz")) | set(pkg_dir.glob("*.zip"))
    )
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c",
         "ls /root/rpmbuild/SOURCES/ 2>/dev/null"],
        capture_output=True, text=True
    )
    new_tarballs: set = set()
    for src in result.stdout.strip().splitlines():
        src = src.strip()
        if not src or src.endswith(".whl") or not src.startswith(effective_prefix):
            continue
        dest = pkg_dir / src
        subprocess.run(
            ["docker", "cp", f"{container}:/root/rpmbuild/SOURCES/{src}", str(pkg_dir)],
            check=True
        )
        print(f"[INFO] 拷出 source: {src} → {pkg_name}/")
        copied.append(f"{pkg_name}/{src}")
        new_tarballs.add(dest)

    for old in existing_tarballs - new_tarballs:
        if old.name.startswith(base_name):
            old.unlink()
            print(f"[INFO] 清理旧 tarball: {old.name}")

    # ── 编译好的 RPM → dist/ ──
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c",
         "find /root/rpmbuild/RPMS -name '*.rpm' 2>/dev/null"],
        capture_output=True, text=True
    )
    for rpm_path in result.stdout.strip().splitlines():
        rpm_path = rpm_path.strip()
        if not rpm_path:
            continue
        rpm_name = Path(rpm_path).name
        normalized_rpm_name = normalize_name_token(rpm_name)
        if normalized_base_name not in normalized_rpm_name:
            continue
        subprocess.run(
            ["docker", "cp", f"{container}:{rpm_path}", str(dist_dir)], check=True
        )
        print(f"[INFO] 拷出 RPM: {rpm_name} → dist/")
        copied.append(f"dist/{rpm_name}")

    # 集合差值：精确获取新增 RPM
    dist_after = set(dist_dir.glob("*.rpm"))
    new_dist_rpms = list(dist_after - dist_before)
    return copied, new_dist_rpms


# ──────────────────────────────────────────────
# RPM 文件名解析与版本比较
# ──────────────────────────────────────────────

def parse_rpm_nvra(filename: str) -> Optional[dict]:
    """解析 {name}-{version}-{release}.{arch}.rpm，失败返回 None。"""
    if not filename.endswith(".rpm"):
        return None
    base = filename[:-4]
    parts = base.rsplit(".", 1)
    if len(parts) != 2:
        return None
    nvr, arch = parts
    parts = nvr.rsplit("-", 1)
    if len(parts) != 2:
        return None
    nv, release = parts
    parts = nv.rsplit("-", 1)
    if len(parts) != 2:
        return None
    name, version = parts
    return {"name": name, "version": version, "release": release, "arch": arch}


def get_version_change_type(old_ver: str, new_ver: str) -> str:
    """
    比较版本号，返回 'major' / 'minor' / 'patch' / 'unknown'。
    取前三段纯数字比较，兼容 0.48.0.alpha.20260317 等非标格式。

    注意：major=0 的 pre-1.0 包（如 ruyi 0.48→0.49）按 minor 处理。
    如存在 breaking change，请使用 --force-upgrade 并手动处理 compat。
    """
    def to_ints(v: str) -> List[int]:
        parts = []
        for seg in v.split("."):
            if seg.isdigit():
                parts.append(int(seg))
            else:
                break
        return parts[:3]

    old, new = to_ints(old_ver), to_ints(new_ver)
    if not old or not new:
        return "unknown"
    if new[0] != old[0]:
        return "major"
    if len(new) > 1 and len(old) > 1 and new[1] != old[1]:
        return "minor"
    return "patch"


# ──────────────────────────────────────────────
# 包类型检测
# ──────────────────────────────────────────────

# 语言运行时包：安装路径由模块名决定（不含版本），新旧版本文件必然冲突
# no_compat: 直接报错中止
# try_compat: 尝试 rpmrebuild（路径含版本，有机会共存，失败再报错）
_RUNTIME_INDICATORS = {
    "python": ["python3_sitelib", "python_sitelib", "python3_sitearch",
               "%py3_install", "python3dist("],
    "java":   ["%{_javadir}", "%{_mavenpomdir}", "%mvn_", "mvn_install"],
    "ruby":   ["%{gem_dir}", "gem install", "rubygems", "%gem_install"],
    "nodejs": ["%{nodejs_sitelib}", "npm install", "node_modules"],
    "perl":   ["%{perl_vendorlib}", "%{perl_vendorarch}", "perl(", "Perl_vendorlib"],
    "lua":    ["%{lua_pkgdir}", "lua_version", "%luarocks_install"],
    "php":    ["%{php_extdir}", "%{php_inidir}", "phpize", "%php_zts"],
}

_NAME_PREFIX_MAP = {
    "python3-": "python", "python-": "python",
    "java-": "java", "maven-": "java",
    "rubygem-": "ruby",
    "nodejs-": "nodejs", "npm-": "nodejs",
    "perl-": "perl",
    "lua-": "lua",
    "php-": "php", "php8-": "php",
}

# 安装路径不含版本号，新旧版本必然文件冲突，不支持 compat
_NO_COMPAT_TYPES = {"python", "nodejs", "perl", "lua", "php"}

# 安装路径含版本号（gem 目录、jar 文件名），有机会 compat，尝试 rpmrebuild
_TRY_COMPAT_TYPES = {"java", "ruby"}


def detect_package_type(pkg_name: str, repo_dir: str) -> str:
    """
    返回包类型：'python' / 'java' / 'ruby' / 'nodejs' / 'other'
    优先读 spec 内容判断，其次按包名前缀推断。
    'other' 包含 C 库和 Go/Rust/C 可执行文件，这类包可以尝试 rpmrebuild compat。
    """
    spec = Path(repo_dir) / pkg_name / f"{pkg_name}.spec"
    if spec.exists():
        content = spec.read_text()
        for lang, markers in _RUNTIME_INDICATORS.items():
            if any(m in content for m in markers):
                return lang

    for prefix, lang in _NAME_PREFIX_MAP.items():
        if pkg_name.startswith(prefix):
            return lang

    return "other"


# ──────────────────────────────────────────────
# 反向依赖查询
# ──────────────────────────────────────────────

def find_rpm_dependents(pkg_name: str, dist_dir: Path, container: str) -> List[str]:
    """查询 dist/ 中哪些 RPM 声明了对 pkg_name 的 Requires。"""
    rpms = list(dist_dir.glob("*.rpm"))
    if not rpms:
        return []

    tmp = "/tmp/_dep_check"
    subprocess.run(["docker", "exec", container, "rm", "-rf", tmp], capture_output=True)
    subprocess.run(["docker", "exec", container, "mkdir", "-p", tmp], capture_output=True)
    for rpm in rpms:
        subprocess.run(
            ["docker", "cp", str(rpm), f"{container}:{tmp}/"], capture_output=True
        )

    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c",
         f"for f in {tmp}/*.rpm; do "
         f"  rpm -qp --requires \"$f\" 2>/dev/null | grep -q '^{re.escape(pkg_name)}' "
         f"  && basename \"$f\"; "
         f"done"],
        capture_output=True, text=True
    )
    subprocess.run(["docker", "exec", container, "rm", "-rf", tmp], capture_output=True)
    return [r.strip() for r in result.stdout.strip().splitlines() if r.strip()]


# ──────────────────────────────────────────────
# Compat 包生成（rpmrebuild，不依赖旧源码）
# ──────────────────────────────────────────────

def create_compat_via_rpmrebuild(
    pkg_name: str,
    old_rpm: Path,
    old_info: dict,
    dist_dir: Path,
    container: str,
) -> bool:
    """
    使用 rpmrebuild 从已有 RPM 文件直接改名生成 compat 包，无需旧源码。
    适用于 C 库和可执行文件类型的包。

    compat 包命名：{pkg_name}-{old_major}
    compat 包提供：Provides: {pkg_name} = {old_version}-{old_release}

    注意：此方法对 Python/Java 等包无效，因为文件路径相同会导致安装冲突。
    返回是否成功。
    """
    old_major   = old_info["version"].split(".")[0]
    compat_name = f"{pkg_name}-{old_major}"

    if list(dist_dir.glob(f"{compat_name}-*.rpm")):
        print(f"[COMPAT] {compat_name} 已存在，跳过")
        return True

    print(f"[COMPAT] 生成 compat 包: {compat_name}（基于 {old_rpm.name}）")

    # 安装 rpmrebuild
    inst = subprocess.run(
        ["docker", "exec", container,
         "dnf", "install", "-y", "--quiet", "rpmrebuild"],
        capture_output=True
    )
    if inst.returncode != 0:
        # rpmrebuild 可能不在官方源里，尝试直接检查是否已安装
        check = subprocess.run(
            ["docker", "exec", container, "which", "rpmrebuild"],
            capture_output=True
        )
        if check.returncode != 0:
            print(f"[WARN] rpmrebuild 不可用（安装失败且未找到）", file=sys.stderr)
            return False

    tmp = "/tmp/_compat_build"
    subprocess.run(
        ["docker", "exec", container, "bash", "-c", f"rm -rf {tmp} && mkdir -p {tmp}/output"],
        capture_output=True
    )

    # 将旧 RPM 拷入容器
    container_rpm = f"{tmp}/{old_rpm.name}"
    subprocess.run(
        ["docker", "cp", str(old_rpm), f"{container}:{container_rpm}"], check=True
    )

    # spec 修改脚本：改 Name，加 Provides
    provides  = f"{pkg_name} = {old_info['version']}-{old_info['release']}"
    patch_py  = (
        "import sys, re\n"
        "c = sys.stdin.read()\n"
        # 替换 Name 字段（\\s* 在 f-string 里 \\ → \，生成 r'^(Name:\s*)...'）
        f"c = re.sub(r'^(Name:\\s*){re.escape(pkg_name)}(\\s*)$',\n"
        f"           r'\\g<1>{compat_name}\\2', c, flags=re.MULTILINE)\n"
        # 在 Name 行后插入 Provides
        f"c = re.sub(r'(^Name:.*$)',\n"
        f"           r'\\1\\nProvides: {provides}',\n"
        f"           c, count=1, flags=re.MULTILINE)\n"
        "sys.stdout.write(c)\n"
    )
    subprocess.run(
        ["docker", "exec", "-i", container, "bash", "-c",
         f"cat > {tmp}/patch.py"],
        input=patch_py, text=True, check=True
    )

    # 运行 rpmrebuild
    result = subprocess.run(
        ["docker", "exec", container,
         "rpmrebuild",
         "--change-spec-preamble", f"python3 {tmp}/patch.py",
         "--notest-install",
         "-d", f"{tmp}/output",
         "-p", container_rpm],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[WARN] rpmrebuild 失败:\n{result.stdout[-600:]}", file=sys.stderr)
        return False

    # 将 compat RPM 拷回 dist/
    find_result = subprocess.run(
        ["docker", "exec", container, "bash", "-c",
         f"find {tmp}/output -name '*.rpm' 2>/dev/null"],
        capture_output=True, text=True
    )
    found = False
    for rp in find_result.stdout.strip().splitlines():
        rp = rp.strip()
        if rp:
            subprocess.run(
                ["docker", "cp", f"{container}:{rp}", str(dist_dir)], check=True
            )
            print(f"[COMPAT] 已创建: {Path(rp).name}")
            found = True

    subprocess.run(
        ["docker", "exec", container, "rm", "-rf", tmp], capture_output=True
    )
    return found


# ──────────────────────────────────────────────
# dist/ 升级冲突处理
# ──────────────────────────────────────────────

def resolve_dist_conflicts(
    dist_dir: Path,
    new_rpms: List[Path],      # 通过集合差值传入，不依赖时间戳
    container: str,
    repo_dir: str,
    force_upgrade: bool = False,
) -> Tuple[List[Path], List[str]]:
    """
    对 new_rpms 中每个包，查找 dist/ 中是否存在旧版本（name+arch 相同，文件名不同）。

    处理逻辑：
      --force-upgrade            → 跳过 compat，直接删旧版本
      patch/minor + 无反向依赖   → 安全升级，直接替换
      major 或存在反向依赖：
        C 库 / other 类型        → rpmrebuild 创建 compat，成功则删旧版本
                                   失败则删新版本（回滚），抛 RuntimeError
        Python/Java/Ruby/Node   → 删新版本（回滚），抛 RuntimeError 提示手动处理

    返回 (已移除的旧 RPM 列表, 升级说明列表)
    """
    removed:       List[Path] = []
    upgrade_notes: List[str]  = []

    for new_rpm in new_rpms:
        new_info = parse_rpm_nvra(new_rpm.name)
        if not new_info:
            raise ValueError(
                f"无法解析新 RPM 文件名: {new_rpm.name}，"
                "请确认格式为 {name}-{version}-{release}.{arch}.rpm"
            )

        for existing in list(dist_dir.glob("*.rpm")):
            if existing.name == new_rpm.name:
                continue
            existing_info = parse_rpm_nvra(existing.name)
            if not existing_info:
                continue
            if (existing_info["name"] != new_info["name"] or
                    existing_info["arch"] != new_info["arch"]):
                continue

            # ── 发现版本冲突 ──
            change_type = get_version_change_type(
                existing_info["version"], new_info["version"]
            )
            pkg_name = existing_info["name"]
            print(f"\n[UPGRADE] {pkg_name}: "
                  f"{existing_info['version']} → {new_info['version']} ({change_type})")

            # ── --force-upgrade：跳过 compat，直接替换 ──
            if force_upgrade:
                print(f"[UPGRADE] --force-upgrade：直接替换，跳过 compat 检查")
                existing.unlink()
                removed.append(existing)
                upgrade_notes.append(
                    f"{pkg_name}: 强制升级 {existing_info['version']} → {new_info['version']}"
                )
                continue

            # ── 判断是否需要 compat ──
            dependents  = find_rpm_dependents(pkg_name, dist_dir, container)
            needs_compat = (change_type == "major") or bool(dependents)

            if not needs_compat:
                existing.unlink()
                removed.append(existing)
                upgrade_notes.append(
                    f"{pkg_name}: 安全升级 ({change_type})，"
                    f"无反向依赖，已替换"
                )
                continue

            # ── 需要 compat ──
            reason = ("major 版本升级" if change_type == "major"
                      else f"存在反向依赖: {dependents}")
            print(f"[UPGRADE] 需要 compat 包（{reason}）")

            pkg_type = detect_package_type(pkg_name, repo_dir)

            if pkg_type in _NO_COMPAT_TYPES:
                # 安装路径由模块名决定，不含版本信息，新旧版本文件必然冲突
                new_rpm.unlink()   # 回滚：删新版本，保留旧版本
                raise RuntimeError(
                    f"\n[{pkg_name}] {pkg_type} 包不支持自动创建 compat 包，归档中止。\n"
                    f"  原因：{pkg_type} 包文件安装到固定路径（路径不含版本号），"
                    f"新旧版本文件路径冲突。\n\n"
                    f"  解决方案：\n"
                    f"  1. 先升级所有反向依赖包（{dependents}），再归档此包\n"
                    f"  2. 使用 --force-upgrade 强制升级（直接替换，反向依赖包可能运行时受影响）"
                )

            # ── C 库 / 可执行文件：尝试 rpmrebuild ──
            success = create_compat_via_rpmrebuild(
                pkg_name, existing, existing_info, dist_dir, container
            )

            if success:
                existing.unlink()
                removed.append(existing)
                old_major = existing_info["version"].split(".")[0]
                upgrade_notes.append(
                    f"{pkg_name}: {change_type} 升级，"
                    f"已创建 compat 包 {pkg_name}-{old_major}"
                )
            else:
                # compat 构建失败：回滚新版本，保留旧版本
                new_rpm.unlink()
                raise RuntimeError(
                    f"\n[{pkg_name}] compat 包创建失败，已回滚（保留旧版本 "
                    f"{existing_info['version']}）。\n\n"
                    f"  可能原因：\n"
                    f"  - rpmrebuild 未安装或不在 OpenEuler 源中\n"
                    f"  - spec 中有复杂宏依赖，rpmrebuild 无法处理\n\n"
                    f"  解决方案：\n"
                    f"  1. 手动在容器内安装 rpmrebuild 后重试\n"
                    f"  2. 使用 --force-upgrade 强制升级（不创建 compat）"
                )

    return removed, upgrade_notes


# ──────────────────────────────────────────────
# repodata 更新
# ──────────────────────────────────────────────

def update_repodata(dist_dir: Path):
    """运行 createrepo_c --update，失败则抛出 RuntimeError。"""
    if subprocess.run(["which", "createrepo_c"], capture_output=True).returncode != 0:
        raise RuntimeError("createrepo_c 未安装：apt-get install createrepo-c -y")
    print(f"[INFO] 重建 repodata")
    result = subprocess.run(
        ["createrepo_c", "--update", str(dist_dir)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"createrepo_c 执行失败:\n{result.stderr.strip()}")
    print(result.stdout.strip())


# ──────────────────────────────────────────────
# CI 门禁
# ──────────────────────────────────────────────

def run_ci_gate(dist_dir: Path, container: str, new_rpms: list = None):
    """
    将本地 dist/ 复制到容器内，运行 repoclosure 检查本次新增包的依赖可满足性
    （结合容器内已配置的官方 OS/EPOL 源）。
    new_rpms: 本次新增的 RPM Path 列表，只检查这些包；为 None 时检查全部。
    失败则抛出 RuntimeError。
    """
    tmp = "/tmp/_ci_dist"

    # 清理 DNF ci-local 缓存，避免旧元数据干扰
    subprocess.run(
        ["docker", "exec", container, "bash", "-c", "rm -rf /var/cache/dnf/ci-local*"],
        capture_output=True
    )

    print(f"\n[CI] 复制 dist/ 到容器...")
    subprocess.run(["docker", "exec", container, "rm", "-rf", tmp], capture_output=True)
    subprocess.run(["docker", "cp", str(dist_dir), f"{container}:{tmp}"], check=True)

    subprocess.run(
        ["docker", "exec", container,
         "dnf", "install", "-y", "--quiet", "dnf-utils"],
        capture_output=True
    )

    # 构建 repoclosure 命令：只检查本次新增的包（避免历史包的缺失依赖误报）
    cmd = [
        "docker", "exec", container,
        "repoclosure",
        "--repofrompath", f"ci-local,{tmp}",
        "--newest",
    ]
    if new_rpms:
        # 从 RPM 文件名提取包名（去掉版本号和架构后缀）
        import re
        for rpm_path in new_rpms:
            name = rpm_path.name
            # 去掉 .rpm 后缀，再去掉 -ver-rel.arch 部分
            m = re.match(r'^(.+?)-[^-]+-[^-]+\.[^.]+\.rpm$', name)
            pkg_name = m.group(1) if m else name.replace('.rpm', '')
            cmd += ["--pkg", pkg_name]
        print(f"[CI] 运行 repoclosure（检查 {len(new_rpms)} 个新包）...")
    else:
        cmd += ["--check", "ci-local"]
        print(f"[CI] 运行 repoclosure（检查全部包）...")

    result = subprocess.run(cmd, capture_output=True, text=True)
    subprocess.run(["docker", "exec", container, "rm", "-rf", tmp], capture_output=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"CI 门禁未通过 — 依赖检查失败:\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )
    print("[CI] ✓ 依赖检查通过")


# ──────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────

def ensure_repo_file(dist_dir: Path, raw_base_url: str):
    repo_file = dist_dir / "repo-aitest.repo"
    content = (
        f"[repo-aitest]\n"
        f"name=OpenEuler RPM Repository\n"
        f"baseurl={raw_base_url}/dist\n"
        f"enabled=1\n"
        f"gpgcheck=0\n"
    )
    if not repo_file.exists() or repo_file.read_text() != content:
        repo_file.write_text(content)
        print(f"[INFO] 更新 .repo 配置")


def ensure_readme(repo_dir: str, remote_url: str):
    readme = Path(repo_dir) / "README.md"
    if readme.exists():
        return
    clean_url = remote_url.split("@")[-1] if "@" in remote_url else remote_url
    raw_base = clean_url.replace(
        "https://github.com/", "https://raw.githubusercontent.com/"
    ).removesuffix(".git")
    readme.write_text(f"""# OpenEuler RPM 仓库

## 目录结构

```
<pkg-name>/   spec 文件 + 上游源码 tarball
dist/         编译好的 RPM 包 + repodata（yum 软件源）
```

## 使用软件源

```bash
curl -o /etc/yum.repos.d/repo-aitest.repo \\
  {raw_base}/main/dist/repo-aitest.repo
dnf repolist
```

## 仓库地址

{clean_url}
""")
    print("[INFO] 已生成 README.md")


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="归档 RPM 到 GitHub 仓库")
    parser.add_argument("--container",     default="oe-build-env", help="容器名")
    parser.add_argument("--pkgs",          nargs="+", required=True, help="包名列表")
    parser.add_argument("--config",        default="config.json",   help="配置文件路径")
    parser.add_argument("--force-upgrade", action="store_true",
                        help="跳过 compat 逻辑，直接替换旧版本（需要 compat 时慎用）")
    args = parser.parse_args()

    cfg      = load_config(args.config)
    token    = cfg["github"]["token"]
    username = cfg["github"]["username"]
    remote   = cfg["repo"]["remote_url"]
    branch   = cfg["repo"]["branch"]
    local    = cfg["repo"]["local_dir"]
    authed   = auth_url(remote, username, token)

    raw_base = (
        remote
        .replace("https://github.com/", "https://raw.githubusercontent.com/")
        .removesuffix(".git")
    )
    raw_base = f"{raw_base}/{branch}"

    # ── Step 1: 初始化/拉取仓库 ──
    print("\n=== Step 1: 初始化仓库 ===")
    init_or_update_repo(local, authed, branch)
    ensure_readme(local, remote)

    # ── Step 2: 从容器拷出文件，追踪新增 RPM ──
    print("\n=== Step 2: 拷出文件 ===")
    all_copied:    List[str]  = []
    all_new_rpms:  List[Path] = []
    for pkg in args.pkgs:
        print(f"\n[INFO] 处理包: {pkg}")
        copied, new_rpms = copy_pkg_files(args.container, pkg, local)
        all_copied.extend(copied)
        all_new_rpms.extend(new_rpms)
        print(f"[INFO] {pkg}: 归档 {len(copied)} 个文件，新增 RPM {len(new_rpms)} 个")

    # ── Step 3: 升级冲突处理 + repodata 更新 ──
    print("\n=== Step 3: 处理升级冲突，更新 dist ===")
    dist_dir = Path(local) / "dist"
    try:
        removed, upgrade_notes = resolve_dist_conflicts(
            dist_dir, all_new_rpms, args.container, local,
            force_upgrade=args.force_upgrade
        )
        if removed:
            print(f"\n[INFO] 共移除 {len(removed)} 个旧版本 RPM")
        update_repodata(dist_dir)
        ensure_repo_file(dist_dir, raw_base)
    except (ValueError, RuntimeError) as e:
        print(f"\n[ERROR] dist 更新失败: {e}", file=sys.stderr)
        print("[ERROR] 回滚工作区，归档中止", file=sys.stderr)
        # git reset 只能恢复已跟踪文件；新增的文件（新 RPM、新 spec）需用 clean 清除。
        # git_reset_working_tree 已组合了 checkout -- . 和 clean -fd，
        # 对全新仓库（无历史提交）同样能清除 untracked 文件。
        git_reset_working_tree(local)
        # 额外删除 all_new_rpms 中未被 git 跟踪且仍存在的文件（防止 git clean 漏掉）
        for rpm in all_new_rpms:
            if rpm.exists():
                rpm.unlink()
                print(f"[ROLLBACK] 删除未提交的新 RPM: {rpm.name}", file=sys.stderr)
        sys.exit(1)

    # ── Step 4: CI 门禁 ──
    print("\n=== Step 4: CI 门禁 ===")
    try:
        run_ci_gate(dist_dir, args.container, all_new_rpms)
    except RuntimeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        print("[ERROR] 回滚工作区，归档中止", file=sys.stderr)
        git_reset_working_tree(local)
        sys.exit(1)

    # ── Step 5: 提交推送 ──
    print("\n=== Step 5: 提交推送 ===")
    git_commit_and_push(local, branch, authed, f"add {', '.join(args.pkgs)}")

    print(f"""
========================================
RPM 归档报告
========================================
包数量    : {len(args.pkgs)} 个
归档文件  : {len(all_copied)} 个
仓库      : {remote}
软件源    : {raw_base}/dist

升级处理:
{chr(10).join(f'  - {n}' for n in upgrade_notes) if upgrade_notes else '  （无版本冲突）'}

已归档文件:
{chr(10).join(f'  - {f}' for f in all_copied)}
========================================
""")


if __name__ == "__main__":
    main()
