#!/usr/bin/env python3
"""
openEuler 包引入依赖分析脚本

支持以下分析方法：
  1. 有 spec 文件 → dnf builddep + mock 验证
  2. 无 spec 文件 → 迭代编译法 + strace 捕获

并提供架构自动检测（x86_64 / aarch64）
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────
# 架构检测
# ─────────────────────────────────────────────

# ARM 专属内核头文件
ARM_HEADERS = {
    "asm/cputype.h", "asm/neon.h", "asm/fpsimdmacros.h",
    "asm/hwcap.h", "asm/sve_context.h", "asm/sysreg.h",
}
# x86 专属内核头文件
X86_HEADERS = {
    "asm/cpufeature.h", "asm/msr.h", "asm/processor.h",
    "asm/special_insns.h", "asm/fpu/api.h",
}
# ARM 汇编指令特征
ARM_ASM_PATTERNS = [
    r"\baarch64\b", r"\barmv8\b", r"\barm64\b",
    r"\bldp\b", r"\bstp\b",           # AArch64 指令
    r"\.arch\s+armv",                  # .arch armv8-a 等
    r"mrs\s+x\d+",                     # ARM 系统寄存器读取
]
# x86 汇编指令特征
X86_ASM_PATTERNS = [
    r"\bx86_64\b", r"\bamd64\b", r"\bi386\b",
    r"\brdmsr\b", r"\bwrmsr\b",        # x86 MSR 指令
    r"\bvmovaps\b", r"\bvpxor\b",      # AVX 指令
    r"\.code64\b",
]


def detect_arch_from_source(source_dir: str) -> Dict:
    """
    静态分析源码，推断目标架构。

    返回:
        {
            "arch": "aarch64" | "x86_64" | "noarch" | "unknown",
            "confidence": "high" | "medium" | "low",
            "reasons": ["..."]
        }
    """
    reasons_arm: List[str] = []
    reasons_x86: List[str] = []
    src = Path(source_dir)

    # ── 1. spec 文件 ExclusiveArch ────────────────────────────────────
    for spec in src.rglob("*.spec"):
        for line in spec.read_text(errors="ignore").splitlines():
            m = re.match(r"ExclusiveArch\s*:\s*(.+)", line, re.IGNORECASE)
            if m:
                archs = m.group(1).lower()
                if "aarch64" in archs and "x86" not in archs:
                    reasons_arm.append(f"spec ExclusiveArch: {m.group(1).strip()}")
                elif "x86" in archs and "aarch64" not in archs:
                    reasons_x86.append(f"spec ExclusiveArch: {m.group(1).strip()}")

    # ── 2. CMakeLists.txt / configure.ac 架构条件 ────────────────────
    for fname in ["CMakeLists.txt", "configure.ac", "configure"]:
        for fpath in src.rglob(fname):
            text = fpath.read_text(errors="ignore")
            if re.search(r"aarch64|arm64|armv8", text, re.IGNORECASE):
                reasons_arm.append(f"{fpath.name} 含 aarch64/arm64 条件")
            if re.search(r"x86_64|amd64|i686", text, re.IGNORECASE):
                reasons_x86.append(f"{fpath.name} 含 x86_64/amd64 条件")

    # ── 3. C/C++ 头文件扫描 ──────────────────────────────────────────
    c_files = list(src.rglob("*.c")) + list(src.rglob("*.h")) + list(src.rglob("*.cpp"))
    for fpath in c_files:
        text = fpath.read_text(errors="ignore")
        for include in re.findall(r'#include\s*[<"]([^>"]+)[>"]', text):
            if include in ARM_HEADERS:
                reasons_arm.append(f"#include <{include}> in {fpath.name}")
            if include in X86_HEADERS:
                reasons_x86.append(f"#include <{include}> in {fpath.name}")
        # arm_neon.h / arm_acle.h 是用户态 ARM intrinsics
        if re.search(r'#include\s*[<"]arm_neon\.h[>"]', text):
            reasons_arm.append(f"#include <arm_neon.h> in {fpath.name}")
        if re.search(r'#include\s*[<"]immintrin\.h[>"|xmmintrin\.h|emmintrin\.h]', text):
            reasons_x86.append(f"#include <immintrin.h/xmmintrin.h> in {fpath.name}")

    # ── 4. 汇编文件扫描 ──────────────────────────────────────────────
    asm_files = list(src.rglob("*.S")) + list(src.rglob("*.s")) + list(src.rglob("*.asm"))
    for fpath in asm_files:
        text = fpath.read_text(errors="ignore")
        for pat in ARM_ASM_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                reasons_arm.append(f"汇编文件 {fpath.name} 含 ARM 指令: {pat}")
                break
        for pat in X86_ASM_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                reasons_x86.append(f"汇编文件 {fpath.name} 含 x86 指令: {pat}")
                break

    # ── 5. 预编译二进制文件检测（.so / .a）───────────────────────────
    for lib in list(src.rglob("*.so")) + list(src.rglob("*.a")):
        try:
            out = subprocess.run(
                ["file", str(lib)], capture_output=True, text=True
            ).stdout
            if "ARM aarch64" in out or "AArch64" in out:
                reasons_arm.append(f"预编译库 {lib.name} 为 aarch64 ELF")
            elif "x86-64" in out or "80386" in out:
                reasons_x86.append(f"预编译库 {lib.name} 为 x86_64 ELF")
        except Exception:
            pass

    # ── 结论 ─────────────────────────────────────────────────────────
    # 去重
    reasons_arm = list(dict.fromkeys(reasons_arm))
    reasons_x86 = list(dict.fromkeys(reasons_x86))

    score_arm = len(reasons_arm)
    score_x86 = len(reasons_x86)

    if score_arm == 0 and score_x86 == 0:
        return {"arch": "noarch", "confidence": "low", "reasons": ["未找到架构特征，推测为架构无关"]}
    elif score_arm > 0 and score_x86 == 0:
        confidence = "high" if score_arm >= 2 else "medium"
        return {"arch": "aarch64", "confidence": confidence, "reasons": reasons_arm}
    elif score_x86 > 0 and score_arm == 0:
        confidence = "high" if score_x86 >= 2 else "medium"
        return {"arch": "x86_64", "confidence": confidence, "reasons": reasons_x86}
    else:
        # 两边都有特征，取分数高的，置信度降低
        arch = "aarch64" if score_arm >= score_x86 else "x86_64"
        return {
            "arch": arch,
            "confidence": "low",
            "reasons": reasons_arm + reasons_x86,
            "warning": f"同时检测到 aarch64({score_arm}) 和 x86_64({score_x86}) 特征，建议人工确认",
        }


def fetch_latest_oe_image(arch: str) -> Tuple[str, str]:
    """
    从华为云镜像站探测最新 openEuler 版本，返回 (镜像 tar URL, dist_tag)。
    arch: "aarch64" 或 "x86_64"（noarch/unknown 均用 x86_64）
    """
    import urllib.request

    index_url = "https://mirrors.huaweicloud.com/openeuler/"
    try:
        with urllib.request.urlopen(index_url, timeout=10) as resp:
            html = resp.read().decode()
    except Exception as e:
        print(f"  ⚠ 无法访问华为云镜像站: {e}，回退到默认镜像")
        return "", ".oe2403sp2"

    versions = re.findall(r'openEuler-(\d+\.\d+)-LTS(?:-SP(\d+))?/', html)
    if not versions:
        return "", ".oe2403sp2"

    def version_key(v):
        major_minor = tuple(int(x) for x in v[0].split('.'))
        sp = int(v[1]) if v[1] else 0
        return (major_minor, sp)

    latest = max(versions, key=version_key)
    major_minor, sp = latest
    oe_version = f"openEuler-{major_minor}-LTS" + (f"-SP{sp}" if sp else "")
    dist_tag = ".oe" + major_minor.replace(".", "") + (f"sp{sp}" if sp else "")

    arch_dir = arch if arch == "aarch64" else "x86_64"
    base_url = f"https://mirrors.huaweicloud.com/openeuler/{oe_version}/docker_img/{arch_dir}/"

    try:
        with urllib.request.urlopen(base_url, timeout=10) as resp:
            listing = resp.read().decode()
        tarball = re.search(r'openEuler-docker[^"\']+\.tar\.xz', listing)
        tar_url = base_url + tarball.group(0) if tarball else ""
    except Exception:
        tar_url = ""

    print(f"  最新版本：{oe_version}，dist tag：{dist_tag}，镜像包：{tar_url or '(未找到)'}")
    return tar_url, dist_tag


def arch_to_platform(arch: str) -> str:
    """架构映射到 Docker --platform 参数"""
    return "linux/arm64" if arch == "aarch64" else "linux/amd64"


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def run_in_container(container: str, cmd: str, timeout: int = 300) -> Tuple[int, str]:
    """在容器中执行命令，返回 (exit_code, output)"""
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout + result.stderr


def extract_repo_urls_from_pr(pr_files: List[Dict]) -> Dict[str, str]:
    """从 PR 文件变更中提取 src-openeuler 目录下的包名和 upstream URL"""
    repo_info = {}
    for f in pr_files:
        filename = f.get("filename", "")
        if "src-openeuler" not in filename or not filename.endswith(".yaml"):
            continue
        package_name = Path(filename).stem
        patch = f.get("patch", {})
        diff = patch.get("diff", "") if isinstance(patch, dict) else patch
        m = re.search(r"upstream:\s*(\S+)", diff)
        if m:
            repo_info[package_name] = m.group(1)
    return repo_info


def clone_repository(url: str, target_dir: str) -> bool:
    """浅克隆仓库"""
    print(f"  克隆: {url}")
    r = subprocess.run(
        ["git", "clone", "--depth", "1", url, target_dir],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"  克隆失败: {r.stderr.strip()}")
    return r.returncode == 0


def detect_build_system(source_dir: str) -> Optional[str]:
    """检测构建系统，递归查找子目录"""
    p = Path(source_dir)
    if list(p.glob("*.spec")) or list(p.rglob("*.spec")):  return "rpm"
    if (p / "CMakeLists.txt").exists(): return "cmake"
    if (p / "configure").exists() or (p / "configure.ac").exists(): return "autotools"
    if (p / "setup.py").exists() or (p / "pyproject.toml").exists(): return "python"
    # Makefile 可能在子目录（内核模块常见）
    if (p / "Makefile").exists() or list(p.rglob("Makefile")): return "kbuild_or_make"
    return None


# ─────────────────────────────────────────────
# 方法一：有 spec → dnf builddep + mock 验证
# ─────────────────────────────────────────────

def analyze_with_spec(container: str, spec_file: str) -> Dict:
    """
    有 spec 文件时的分析流程：
      1. dnf builddep 解析 BuildRequires
      2. 尝试实际安装，记录缺失包
      3. rpmbuild -bp（只展开 %prep）验证 source 可用性
    """
    print("  [方法一] spec 文件路径:", spec_file)
    result = {
        "method": "spec+builddep",
        "build_requires": [],
        "missing": [],
        "build_status": "unknown",
        "logs": {},
    }

    # Step 1: 解析 BuildRequires（不安装）
    rc, out = run_in_container(
        container,
        f"rpm -q --requires --specfile {spec_file} 2>/dev/null || "
        f"grep -i '^BuildRequires' {spec_file}"
    )
    result["logs"]["parse_requires"] = out
    for line in out.splitlines():
        m = re.search(r"BuildRequires\s*:\s*(.+)", line, re.IGNORECASE)
        if m:
            for dep in re.split(r"[,\s]+", m.group(1)):
                dep = re.sub(r"[><=].*", "", dep).strip()
                if dep:
                    result["build_requires"].append(dep)

    # Step 2: dnf builddep —— 安装所有 BuildRequires，记录缺失
    rc, out = run_in_container(
        container,
        f"dnf builddep -y {spec_file} 2>&1",
        timeout=600
    )
    result["logs"]["builddep"] = out
    for line in out.splitlines():
        if "No match for argument" in line or "No matching package" in line:
            m = re.search(r":\s*(\S+)", line)
            if m:
                result["missing"].append(m.group(1))
        if "Error:" in line:
            result["errors"] = result.get("errors", []) + [line.strip()]

    # Step 3: rpmbuild -bp（只跑 %prep，验证 source 是否可下载解压）
    rc2, out2 = run_in_container(
        container,
        f"cd /build && rpmbuild -bp {spec_file} 2>&1 | tail -20"
    )
    result["logs"]["rpmbuild_prep"] = out2
    result["build_status"] = "success" if rc == 0 else "failed"
    return result


# ─────────────────────────────────────────────
# 方法二：无 spec → 迭代编译 + strace 捕获
# ─────────────────────────────────────────────

def install_pkg(container: str, pkg: str) -> bool:
    rc, _ = run_in_container(container, f"dnf install -y {pkg} 2>&1")
    return rc == 0


def try_build(container: str, build_system: str) -> Tuple[int, str]:
    """根据构建系统执行一次构建，返回 (exit_code, output)"""
    cmds = {
        "cmake": (
            "cd /build && rm -rf _build && mkdir _build && cd _build && "
            "cmake .. 2>&1 && make -j$(nproc) 2>&1"
        ),
        "autotools": (
            "cd /build && "
            "([ -f configure ] || autoreconf -fi 2>&1) && "
            "./configure 2>&1 && make -j$(nproc) 2>&1"
        ),
        "kbuild_or_make": (
            "cd /build && "
            "KVER=$(ls /lib/modules/ | head -1) && "
            "make KDIR=/lib/modules/$KVER/build 2>&1"
        ),
        "python": "cd /build && pip3 install -e . 2>&1",
    }
    cmd = cmds.get(build_system, "cd /build && make 2>&1")
    return run_in_container(container, cmd, timeout=600)


def parse_missing_from_output(output: str, build_system: str) -> List[Dict]:
    """
    从编译输出中提取缺失依赖，返回列表：
    [{"missing": "libfoo", "type": "header|library|pkg-config|command", "raw": "..."}]
    """
    found = []
    patterns = [
        # C 头文件缺失
        (r"fatal error:\s*([^\s:]+\.h):\s*No such file",          "header"),
        # pkg-config 包缺失
        (r"Package '([^']+)' not found",                           "pkg-config"),
        (r"No package '([^']+)' found",                            "pkg-config"),
        # CMake 找不到包
        (r"Could not find\s+(\S+)",                                "cmake-module"),
        (r"Could NOT find\s+(\S+)",                                "cmake-module"),
        # 链接器找不到库
        (r"cannot find -l(\S+)",                                   "library"),
        (r"ld: library not found for -l(\S+)",                     "library"),
        # Python 缺模块
        (r"ModuleNotFoundError: No module named '([^']+)'",        "python-module"),
        (r"ImportError: No module named '([^']+)'",                "python-module"),
        # 命令不存在
        (r"command not found:\s*(\S+)",                            "command"),
        (r"(\S+): not found",                                      "command"),
    ]
    for line in output.splitlines():
        for pat, dep_type in patterns:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                found.append({"missing": m.group(1), "type": dep_type, "raw": line.strip()})
                break
    # 去重
    seen = set()
    deduped = []
    for item in found:
        key = (item["missing"], item["type"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def resolve_dep_to_package(container: str, dep: Dict) -> Optional[str]:
    """
    将缺失依赖映射到 dnf 可安装的包名。
    优先使用 dnf provides，其次用常见规则推导。
    """
    missing = dep["missing"]
    dep_type = dep["type"]

    if dep_type == "header":
        # 用 dnf provides 反查头文件属于哪个包
        rc, out = run_in_container(container, f"dnf provides '*/{missing}' 2>&1 | head -5")
        m = re.search(r"^(\S+)\s*:", out, re.MULTILINE)
        if m:
            # 去掉版本号，取包名
            pkg = re.sub(r"-\d+:.*", "", m.group(1))
            pkg = re.sub(r"-\d+\.\d+.*", "", pkg)
            return pkg
        # 回退规则：openssl/ssl.h → openssl-devel
        base = missing.split("/")[0]
        return f"{base}-devel"

    elif dep_type == "library":
        rc, out = run_in_container(container, f"dnf provides '*lib{missing}.so*' 2>&1 | head -5")
        m = re.search(r"^(\S+)\s*:", out, re.MULTILINE)
        if m:
            pkg = re.sub(r"-\d+:.*", "", m.group(1))
            return re.sub(r"-\d+\.\d+.*", "", pkg)
        return f"lib{missing}-devel"

    elif dep_type == "pkg-config":
        rc, out = run_in_container(container, f"dnf provides '{missing}.pc' 2>&1 | head -5")
        m = re.search(r"^(\S+)\s*:", out, re.MULTILINE)
        if m:
            return re.sub(r"-\d+[:\.].*", "", m.group(1))
        return f"{missing}-devel"

    elif dep_type == "cmake-module":
        return f"{missing.lower()}-devel"

    elif dep_type == "python-module":
        return f"python3-{missing}"

    elif dep_type == "command":
        rc, out = run_in_container(container, f"dnf provides '{missing}' 2>&1 | head -5")
        m = re.search(r"^(\S+)\s*:", out, re.MULTILINE)
        if m:
            return re.sub(r"-\d+[:\.].*", "", m.group(1))
        return missing

    return None


def analyze_with_strace(container: str, build_system: str) -> Dict:
    """
    无 spec 文件时：迭代编译 + strace 捕获
      循环：编译 → 解析报错 → dnf provides 反查包名 → 安装 → 重新编译
      直到编译成功或无新依赖
    """
    print("  [方法二] 迭代编译 + strace")
    result = {
        "method": "iterative+strace",
        "installed_deps": [],   # 成功安装的依赖
        "missing": [],          # 最终仍缺失的依赖
        "build_status": "unknown",
        "iterations": [],
        "strace_libs": [],
    }

    # 安装基础构建工具
    base_tools = {
        "cmake":          "cmake gcc gcc-c++ make pkg-config",
        "autotools":      "autoconf automake libtool gcc gcc-c++ make pkg-config",
        "kbuild_or_make": "gcc make kernel-devel kernel-headers",
        "python":         "python3 python3-pip python3-devel",
    }
    run_in_container(
        container,
        f"dnf install -y {base_tools.get(build_system, 'gcc make')} strace 2>&1",
        timeout=300
    )

    max_iterations = 10
    for i in range(1, max_iterations + 1):
        print(f"  迭代 #{i} ...")
        rc, output = try_build(container, build_system)

        iteration = {"round": i, "exit_code": rc, "new_installed": [], "new_missing": []}

        if rc == 0:
            result["build_status"] = "success"
            iteration["result"] = "build succeeded"
            result["iterations"].append(iteration)
            break

        # 解析缺失依赖
        missing_deps = parse_missing_from_output(output, build_system)
        if not missing_deps:
            # 没有识别出缺失依赖，用 strace 兜底
            print("  未从报错中识别依赖，启动 strace 分析...")
            strace_libs = capture_deps_with_strace(container, build_system)
            result["strace_libs"] = strace_libs
            result["build_status"] = "failed"
            iteration["result"] = "no parseable error, strace fallback"
            result["iterations"].append(iteration)
            break

        # 尝试安装每个缺失依赖
        any_installed = False
        for dep in missing_deps:
            pkg = resolve_dep_to_package(container, dep)
            if not pkg:
                result["missing"].append(dep)
                iteration["new_missing"].append(dep)
                continue

            rc2, _ = run_in_container(container, f"dnf install -y {pkg} 2>&1", timeout=120)
            if rc2 == 0:
                result["installed_deps"].append({"pkg": pkg, "for": dep["missing"]})
                iteration["new_installed"].append(pkg)
                any_installed = True
                print(f"    安装: {pkg}  (为 {dep['missing']})")
            else:
                result["missing"].append(dep)
                iteration["new_missing"].append(dep)
                print(f"    无法安装: {pkg}")

        result["iterations"].append(iteration)

        if not any_installed:
            result["build_status"] = "failed"
            print("  本轮无新安装，停止迭代")
            break
    else:
        result["build_status"] = "failed"
        print("  达到最大迭代次数")

    return result


def capture_deps_with_strace(container: str, build_system: str) -> List[str]:
    """
    用 strace 跟踪编译过程中打开的 .so 和 .h，
    反查属于哪些包，作为迭代法的兜底补充。
    """
    cmds = {
        "cmake":          "cd /build/_build && strace -e openat -f make 2>&1",
        "autotools":      "cd /build && strace -e openat -f make 2>&1",
        "kbuild_or_make": "cd /build && strace -e openat -f make 2>&1",
        "python":         "cd /build && strace -e openat -f python3 setup.py build 2>&1",
    }
    cmd = cmds.get(build_system, "cd /build && strace -e openat -f make 2>&1")
    _, out = run_in_container(container, cmd, timeout=300)

    # 提取访问的 .so 文件（排除系统标准路径）
    so_files = set()
    for line in out.splitlines():
        m = re.search(r'openat.*"(/[^"]+\.so[^"]*)"', line)
        if m and "ENOENT" in line:   # 只关心找不到的
            so_files.add(m.group(1))

    # 反查包名
    libs = []
    for so in list(so_files)[:20]:   # 最多查 20 个
        _, qout = run_in_container(container, f"dnf provides '{so}' 2>&1 | head -3")
        m = re.search(r"^(\S+)\s*:", qout, re.MULTILINE)
        if m:
            libs.append(re.sub(r"-\d+[:\.].*", "", m.group(1)))
    return list(set(libs))


# ─────────────────────────────────────────────
# 容器生命周期管理
# ─────────────────────────────────────────────

def start_container(source_dir: str, image: str, platform: str = "linux/amd64") -> str:
    """启动分析容器，返回容器名"""
    name = f"oe-build-{os.getpid()}"
    subprocess.run([
        "docker", "run", "-d",
        "--platform", platform,
        "--name", name,
        "-v", f"{source_dir}:/build",
        image,
        "sleep", "3600"
    ], check=True, capture_output=True)
    print(f"  容器已启动: {name} (镜像: {image}, 平台: {platform})")
    return name


def stop_container(name: str):
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    print(f"  容器已清理: {name}")


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

def analyze_package(package_name: str, upstream_url: str) -> Dict:
    """分析单个包的编译依赖"""
    print(f"\n{'='*60}")
    print(f"分析包: {package_name}")
    print(f"上游:   {upstream_url}")
    print(f"{'='*60}")

    result = {
        "package_name": package_name,
        "upstream_url": upstream_url,
        "arch_detection": {},
        "build_system": None,
        "analysis": {},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, package_name)

        # 1. 克隆
        if not clone_repository(upstream_url, src):
            result["error"] = "克隆仓库失败"
            return result

        # 2. 架构检测
        arch_info = detect_arch_from_source(src)
        result["arch_detection"] = arch_info
        print(f"\n  架构检测: {arch_info['arch']} (置信度: {arch_info['confidence']})")
        for r in arch_info["reasons"]:
            print(f"    - {r}")
        if "warning" in arch_info:
            print(f"  ⚠ {arch_info['warning']}")

        # 3. 检测构建系统
        build_system = detect_build_system(src)
        result["build_system"] = build_system
        print(f"\n  构建系统: {build_system or '未检测到'}")

        if not build_system:
            result["error"] = "无法识别构建系统"
            return result

        # 4. 从华为云镜像站获取最新镜像并加载
        tar_url, oe_dist_tag = fetch_latest_oe_image(arch_info["arch"])
        platform = arch_to_platform(arch_info["arch"])
        result["oe_dist_tag"] = oe_dist_tag
        result["docker_platform"] = platform

        if tar_url:
            tarball = tar_url.split("/")[-1]
            local_tar = f"/tmp/{tarball}"
            if not os.path.exists(local_tar):
                print(f"  下载镜像包：{tar_url}")
                subprocess.run(["curl", "-C", "-", "-L", tar_url, "-o", local_tar], check=True)
            load_out = subprocess.run(
                ["docker", "load", "-i", local_tar],
                capture_output=True, text=True
            )
            m = re.search(r"Loaded image:\s*(\S+)", load_out.stdout)
            image = m.group(1) if m else "openeuler/openeuler:latest"
            print(f"  镜像已加载：{image}")
        else:
            image = "openeuler/openeuler:latest"
            print(f"  使用默认镜像：{image}")

        container = start_container(src, image, platform)

        try:
            # 5. 选择分析方法
            spec_files = list(Path(src).glob("*.spec"))
            if spec_files:
                print(f"\n  发现 spec 文件: {spec_files[0].name}")
                analysis = analyze_with_spec(container, f"/build/{spec_files[0].name}")
            else:
                print(f"\n  未发现 spec 文件，使用迭代编译法")
                analysis = analyze_with_strace(container, build_system)

            result["analysis"] = analysis
        finally:
            stop_container(container)

    return result


def print_report(result: Dict):
    """打印分析报告"""
    print(f"\n{'='*60}")
    print(f"分析报告: {result['package_name']}")
    print(f"{'='*60}")
    print(f"上游仓库:   {result['upstream_url']}")

    arch = result.get("arch_detection", {})
    print(f"目标架构:   {arch.get('arch', 'unknown')} (置信度: {arch.get('confidence', '?')})")
    print(f"构建系统:   {result.get('build_system', 'unknown')}")

    analysis = result.get("analysis", {})
    print(f"分析方法:   {analysis.get('method', 'N/A')}")
    print(f"编译状态:   {analysis.get('build_status', 'unknown')}")

    if analysis.get("build_requires"):
        print(f"\nBuildRequires ({len(analysis['build_requires'])} 项):")
        for dep in analysis["build_requires"]:
            print(f"  - {dep}")

    if analysis.get("installed_deps"):
        print(f"\n迭代安装的依赖 ({len(analysis['installed_deps'])} 项):")
        for item in analysis["installed_deps"]:
            print(f"  - {item['pkg']}  (为 {item['for']})")

    if analysis.get("missing"):
        print(f"\n仍缺失的依赖 ({len(analysis['missing'])} 项):")
        for item in analysis["missing"]:
            if isinstance(item, dict):
                print(f"  - [{item['type']}] {item['missing']}")
                print(f"      {item['raw']}")
            else:
                print(f"  - {item}")

    if analysis.get("strace_libs"):
        print(f"\nstrace 捕获到的额外库依赖:")
        for lib in analysis["strace_libs"]:
            print(f"  - {lib}")

    if "warning" in arch:
        print(f"\n⚠ 架构警告: {arch['warning']}")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_package.py <pr_info.json>")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        pr_data = json.load(f)

    repo_info = extract_repo_urls_from_pr(pr_data.get("files", []))
    print(f"从 PR 中识别到 {len(repo_info)} 个包: {list(repo_info.keys())}")

    all_results = {}
    for pkg_name, url in repo_info.items():
        res = analyze_package(pkg_name, url)
        print_report(res)
        all_results[pkg_name] = res

    out_file = "package_analysis_result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n完整结果已保存至: {out_file}")


if __name__ == "__main__":
    main()
