#!/usr/bin/env python3
"""
Python 包 RPM 依赖分析脚本

依赖来源取并集：
  1. PyPI JSON API（https://pypi.org/pypi/<pkg>/json）— 最权威
  2. 本地源码解析（setup.py / pyproject.toml / requirements.txt）— 兜底

C 扩展检测：
  - PyPI：wheel URL 含架构（amd64/arm64）→ 有 C 扩展
  - 本地：.pyx 文件 / Extension() 调用 / .c 文件

最终用容器内一次性批量查询 `python3dist(...)` 的 RPM Provides 可用性，
完全依赖 RPM 官方 Provides 机制，不猜包名前缀。

用法：
  python3 analyze_python_deps.py <source_dir> [--pkg <pypi_name>]
  python3 analyze_python_deps.py <source_dir> --pkg requests --check-rpm --container oe-build-env
  python3 analyze_python_deps.py <source_dir> --check-rpm -o result.json
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from rpm_batch_lookup import BatchLookupError, fallback_results, provides_query, run_batch_lookup


# ── 1. 包名规范化 ─────────────────────────────────────────────────────────────

def normalize_pkg_name(name: str) -> str:
    """规范化 Python 包名：小写 + 连字符（PEP 503）"""
    return re.sub(r"[-_.]+", "-", name).lower()


def extract_pypi_name(pkg_spec: str) -> str:
    """
    从 PEP 508 依赖规范提取规范化的 PyPI 包名，用于 python3dist() 查询。
    'python-dateutil>=2.7.0; python_version>="3"' → 'python-dateutil'
    'requests[security]>=2.0'                     → 'requests'
    """
    pkg_spec = pkg_spec.split(";")[0].strip()
    pkg_spec = re.sub(r"\[.*?\]", "", pkg_spec)
    m = re.match(r"([a-zA-Z0-9_\-\.]+)", pkg_spec.strip())
    if not m:
        return ""
    return normalize_pkg_name(m.group(1))


def transform_module_name(pkg_spec: str) -> str:
    """
    将 PEP 508 依赖转为带版本约束的 RPM Requires 格式（官方 python3dist 写法）。
    'python-dateutil>=2.7.0' → '(python3dist(python-dateutil) >= 2.7.0)'
    'requests>=2.0,<3'       → '(python3dist(requests) >= 2.0 with python3dist(requests) < 3)'
    """
    pkg_spec_clean = pkg_spec.split(";")[0].strip()
    pkg_spec_clean = re.sub(r"\[.*?\]", "", pkg_spec_clean)
    m = re.match(r"([a-zA-Z0-9_\-\.]+)", pkg_spec_clean.strip())
    if not m:
        return ""
    pypi_name = normalize_pkg_name(m.group(1))
    dist_name = f"python3dist({pypi_name})"
    version_part = pkg_spec_clean[len(m.group(1)):].strip().strip("()")
    if not version_part:
        return dist_name
    # 规范化版本约束：>= 2.0 而非 >=2.0
    constraints = []
    for c in version_part.split(","):
        c = c.strip()
        if c:
            c = re.sub(r"([><=!]+)\s*", r"\1 ", c).strip()
            constraints.append(c)
    if len(constraints) > 1:
        parts = " with ".join(f"{dist_name} {c}" for c in constraints)
        return f"({parts})"
    return f"({dist_name} {constraints[0]})"


def extract_requirement_expr(pkg_spec: str) -> str:
    """提取依赖中的原始版本约束表达式，无法提取时返回空串。"""
    pkg_spec_clean = pkg_spec.split(";")[0].strip()
    pkg_spec_clean = re.sub(r"\[.*?\]", "", pkg_spec_clean)
    m = re.match(r"([a-zA-Z0-9_\-\.]+)", pkg_spec_clean.strip())
    if not m:
        return ""
    version_part = pkg_spec_clean[len(m.group(1)):].strip().strip("()")
    if not version_part:
        return ""
    constraints = []
    for c in version_part.split(","):
        c = c.strip()
        if c:
            constraints.append(re.sub(r"([><=!]+)\s*", r"\1 ", c).strip())
    return ", ".join(constraints)


def project_url_for_pypi_name(pypi_name: str) -> str:
    return f"https://pypi.org/project/{pypi_name}"


def canonical_upstream_url(pypi_json: Optional[Dict], pypi_name: str) -> str:
    """优先返回主流代码托管仓库地址，找不到时回退到 PyPI 项目页。"""
    if pypi_json:
        info = pypi_json.get("info", {})
        candidates = []
        if info.get("home_page"):
            candidates.append(info["home_page"])
        for url in (info.get("project_urls") or {}).values():
            if url:
                candidates.append(url)
        allowed_hosts = {"github.com", "gitlab.com", "gitee.com", "gitcode.com", "atomgit.com", "bitbucket.org"}
        for url in candidates:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            host = urlparse(url).netloc.lower().removeprefix("www.")
            if host in allowed_hosts:
                return url.rstrip("/")
    return project_url_for_pypi_name(pypi_name)


def build_dependency_item(pkg_spec: str, pypi_json: Optional[Dict] = None) -> Optional[Dict[str, str]]:
    pypi_name = extract_pypi_name(pkg_spec)
    if not pypi_name:
        return None
    rpm_requirement = transform_module_name(pkg_spec)
    return {
        "name": pypi_name,
        "spec": pkg_spec,
        "requirement": extract_requirement_expr(pkg_spec),
        "rpm_requirement": rpm_requirement or f"python3dist({pypi_name})",
        "upstream_url": canonical_upstream_url(pypi_json, pypi_name),
    }


def build_dependency_items(requires: List[str], pypi_metadata: Optional[Dict[str, Dict]] = None) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for dep_spec in requires:
        pypi_name = extract_pypi_name(dep_spec)
        item = build_dependency_item(dep_spec, (pypi_metadata or {}).get(pypi_name, {}))
        if not item:
            continue
        key = (item["name"], item["requirement"])
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


# ── 2. PyPI API 查询 ──────────────────────────────────────────────────────────

def fetch_pypi_info(pkg_name: str) -> Optional[Dict]:
    """
    从 PyPI JSON API 获取包元数据。
    返回 None 表示包不存在或网络不通。
    """
    url = f"https://pypi.org/pypi/{pkg_name}/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "analyze_python_deps/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[WARN] PyPI 上未找到包: {pkg_name}", file=sys.stderr)
        else:
            print(f"[WARN] PyPI 请求失败 ({e.code}): {pkg_name}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] PyPI 网络错误: {e}", file=sys.stderr)
    return None


def collect_pypi_metadata(requires: List[str]) -> Dict[str, Dict]:
    """为依赖项批量收集 PyPI 元数据，用于补全 canonical upstream URL。"""
    metadata: Dict[str, Dict] = {}
    seen: Set[str] = set()
    for dep_spec in requires:
        pypi_name = extract_pypi_name(dep_spec)
        if not pypi_name or pypi_name in seen:
            continue
        seen.add(pypi_name)
        pypi_json = fetch_pypi_info(pypi_name)
        if pypi_json:
            metadata[pypi_name] = pypi_json
    return metadata


def parse_pypi_deps(pypi_json: Dict) -> Tuple[List[str], bool, str]:
    """
    从 PyPI JSON 提取依赖和 C 扩展信息。
    返回 (requires_list, has_c_ext, version)
    """
    info = pypi_json.get("info", {})
    requires_dist = info.get("requires_dist") or []

    requires = []
    for r in requires_dist:
        # 过滤掉 extra 可选依赖（如 ; extra == "test"）
        idx = r.find(";")
        if idx != -1:
            marker = r[idx + 1:].strip()
            if "extra" in marker:
                continue
        clean = r[:idx].strip() if idx != -1 else r.strip()
        if clean:
            requires.append(clean)

    # C 扩展检测：检查是否有架构相关的 wheel（参考 pyporter __get_buildarch）
    version = info.get("version", "")
    has_c_ext = False

    # 检查当前版本的 releases
    releases = pypi_json.get("releases", {})
    urls = releases.get(version, []) or pypi_json.get("urls", [])
    for r in urls:
        pkg_type = r.get("packagetype", "")
        url = r.get("url", "")
        # 有架构相关 wheel 说明有 C 扩展
        if pkg_type == "bdist_wheel" and any(
            arch in url for arch in ("amd64", "x86_64", "arm64", "aarch64", "cp3")
        ):
            has_c_ext = True
            break

    return requires, has_c_ext, version


# ── 3. 本地源码解析 ───────────────────────────────────────────────────────────

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        try:
            from pip._vendor import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            tomllib = None  # type: ignore[assignment]


def _load_toml(toml_file: Path) -> Dict:
    """用 tomllib 解析 TOML 文件，不可用时返回空字典"""
    if tomllib is None:
        print("  [WARN] tomllib 不可用，跳过 pyproject.toml 解析", file=sys.stderr)
        return {}
    try:
        with open(toml_file, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"  [WARN] 解析 pyproject.toml 失败: {e}", file=sys.stderr)
        return {}


def parse_local_deps(source_dir: str) -> Tuple[List[str], str]:
    """
    从本地源码解析运行时依赖，优先级：pyproject.toml > setup.py > requirements.txt
    返回 (requires_list, build_backend)
    """
    src = Path(source_dir)

    # pyproject.toml —— 用 tomllib 正确解析，避免正则截断问题
    toml_file = src / "pyproject.toml"
    if toml_file.exists():
        data = _load_toml(toml_file)
        if data:
            project = data.get("project", {})
            deps = list(project.get("dependencies", []))

            if "dependencies" in project.get("dynamic", []):
                print("  [WARN] dependencies 为 dynamic，本地无法静态获取", file=sys.stderr)

            # 从 build-backend 字段提取简短名称
            # 例：hatchling.build → hatchling，setuptools.build_meta → setuptools
            backend_full = data.get("build-system", {}).get("build-backend", "")
            _backend_map = {
                "hatchling": "hatchling", "setuptools": "setuptools",
                "flit_core": "flit",      "poetry":     "poetry",
                "pdm":       "pdm",       "meson":      "meson-python",
            }
            backend = "setuptools"
            for key, name in _backend_map.items():
                if key in backend_full:
                    backend = name
                    break

            return deps, backend

    # setup.py 兜底
    setup_py = src / "setup.py"
    if setup_py.exists():
        content = setup_py.read_text(errors="ignore")
        requires = []
        m = re.search(r"install_requires\s*=\s*[\[\(](.*?)[\]\)]", content, re.DOTALL)
        if m:
            for dep in re.findall(r"""['"]([^'"]+)['"]""", m.group(1)):
                dep = dep.strip()
                if dep:
                    requires.append(dep)
        if requires:
            backend = "flit" if "flit" in content else ("poetry" if "poetry" in content else "setuptools")
            return requires, backend

    # requirements.txt 兜底
    req_file = src / "requirements.txt"
    if req_file.exists():
        requires = []
        for line in req_file.read_text(errors="ignore").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                requires.append(line)
        return requires, "setuptools"

    return [], "setuptools"


def parse_build_system_deps(source_dir: str) -> List[str]:
    """从 pyproject.toml 的 [build-system].requires 提取构建系统依赖"""
    toml_file = Path(source_dir) / "pyproject.toml"
    if not toml_file.exists():
        return []
    data = _load_toml(toml_file)
    return list(data.get("build-system", {}).get("requires", []))


def scan_c_extensions_local(source_dir: str) -> Dict:
    """本地扫描 C 扩展迹象"""
    src = Path(source_dir)
    reasons = []

    setup_py = src / "setup.py"
    if setup_py.exists() and re.search(r"\bExtension\s*\(", setup_py.read_text(errors="ignore")):
        reasons.append("setup.py 包含 Extension() 调用")

    pyx_files = [str(f.relative_to(src)) for f in src.rglob("*.pyx")]
    if pyx_files:
        reasons.append(f"发现 {len(pyx_files)} 个 .pyx 文件（Cython）")

    c_files = []
    for ext in ("*.c", "*.cpp", "*.cc"):
        c_files.extend(str(f.relative_to(src)) for f in src.rglob(ext))
    if c_files:
        reasons.append(f"发现 {len(c_files)} 个 C/C++ 源文件")

    return {
        "has_c_ext": len(reasons) > 0,
        "reasons": reasons,
        "pyx_files": pyx_files[:5],
        "c_files": c_files[:5],
    }


# ── 4. 依赖并集合并 ───────────────────────────────────────────────────────────

def merge_requires(pypi_requires: List[str], local_requires: List[str]) -> List[str]:
    """
    合并 PyPI 和本地解析的依赖，取并集（以包名去重）。
    PyPI 的版本约束优先（更权威），本地的补充 PyPI 没有的包。
    """
    # 用规范化包名作为 key 去重
    seen: Dict[str, str] = {}  # normalized_name -> original_spec

    # PyPI 优先
    for dep in pypi_requires:
        key = normalize_pkg_name(dep.split(";")[0].split("[")[0].split(">=")[0]
                                  .split("<=")[0].split("!=")[0].split("==")[0]
                                  .split(">")[0].split("<")[0].strip())
        if key and key not in seen:
            seen[key] = dep

    # 本地补充 PyPI 没有的
    for dep in local_requires:
        key = normalize_pkg_name(dep.split(";")[0].split("[")[0].split(">=")[0]
                                  .split("<=")[0].split("!=")[0].split("==")[0]
                                  .split(">")[0].split("<")[0].strip())
        if key and key not in seen:
            seen[key] = dep

    return list(seen.values())


def build_lookup_tasks(requires: List[str], pypi_metadata: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    tasks: List[Dict] = []
    for dep_spec in requires:
        item = build_dependency_item(dep_spec, (pypi_metadata or {}).get(extract_pypi_name(dep_spec), {}))
        if not item:
            continue
        pypi_name = item["name"]
        tasks.append({
            "dep": dep_spec,
            "name": pypi_name,
            "requirement": item["requirement"],
            "rpm_requirement": item["rpm_requirement"],
            "upstream_url": item["upstream_url"],
            "rpm_name": f"python3dist({pypi_name})",
            "queries": [provides_query(f"python3dist({pypi_name})", "python3dist()")],
        })
    return tasks


# ── 5. 容器内 dnf 查询 ────────────────────────────────────────────────────────

def check_rpm_availability(container: str, requires: List[str], pypi_metadata: Optional[Dict[str, Dict]] = None) -> Dict:
    """
    批量查询依赖的 RPM 可用性，返回 available / missing 列表。
    通过 python3dist() 官方 Provides 机制查询，不依赖包名前缀猜测。
    """
    tasks = build_lookup_tasks(requires, pypi_metadata)
    print(f"\n[INFO] 在容器 {container} 内查询 RPM 可用性（通过 python3dist，单次批量查询）...")

    try:
        results = run_batch_lookup(container, tasks, timeout=120)
    except (BatchLookupError, OSError, json.JSONDecodeError) as e:
        print(f"[WARN] python3dist 批量查询失败（{e}），跳过依赖检查")
        results = fallback_results(tasks)

    available = []
    missing = []
    for item in results:
        dep_spec = item["dep"]
        label = item["rpm_name"]
        found = item.get("rpm")
        if found:
            version = item.get("version") or ""
            version_label = f" {version}" if version else ""
            print(f"  ✓ {label:<45} → {found}{version_label}")
            available.append({
                "dep": dep_spec,
                "name": item.get("name", ""),
                "requirement": item.get("requirement", ""),
                "rpm_requirement": item.get("rpm_requirement", label),
                "rpm": found,
                "version": item.get("version"),
                "release": item.get("release"),
                "upstream_url": item.get("upstream_url", ""),
            })
        else:
            print(f"  ✗ {label:<45} → 未找到")
            missing.append({
                "dep": dep_spec,
                "name": item.get("name", ""),
                "requirement": item.get("requirement", ""),
                "rpm_requirement": item.get("rpm_requirement", label),
                "rpm_name": label,
                "upstream_url": item.get("upstream_url", ""),
            })

    return {"available": available, "missing": missing}


# ── 6. 报告输出 ───────────────────────────────────────────────────────────────

def build_rpm_requires(c_ext: Dict, rpm_check: Optional[Dict],
                       build_sys_rpms: Optional[List[str]] = None) -> List[str]:
    """生成 spec BuildRequires 列表"""
    result = ["python3-devel", "python3-setuptools", "python3-pip", "python3-wheel"]
    seen = set(result)
    # 构建系统依赖（hatchling 等）优先加入
    for rpm in (build_sys_rpms or []):
        if rpm not in seen:
            result.append(rpm)
            seen.add(rpm)
    if c_ext.get("has_c_ext"):
        result.append("gcc")
        seen.add("gcc")
        if c_ext.get("pyx_files"):
            result.append("python3-Cython")
            seen.add("python3-Cython")
    if rpm_check:
        for item in rpm_check.get("available", []):
            rpm = item["rpm"]
            if rpm not in seen:
                result.append(rpm)
                seen.add(rpm)
    return result


def print_report(source_dir: str, pkg_name: str, version: str,
                 pypi_requires: List[str], local_requires: List[str],
                 merged_requires: List[str], build_backend: str,
                 c_ext_pypi: bool, c_ext_local: Dict,
                 rpm_check: Optional[Dict],
                 build_sys_requires: Optional[List[str]] = None,
                 build_sys_rpm_check: Optional[Dict] = None):
    sep = "=" * 65
    print(f"\n{sep}")
    print("Python 包 RPM 依赖分析报告")
    print(sep)
    print(f"  包名      : {pkg_name or '(未知)'}  {version}")
    print(f"  源码目录  : {source_dir}")
    print(f"  构建后端  : {build_backend}")

    print(f"\n[依赖来源对比]")
    print(f"  PyPI API  : {len(pypi_requires)} 个依赖")
    print(f"  本地解析  : {len(local_requires)} 个依赖")
    print(f"  并集合并  : {len(merged_requires)} 个依赖")

    if merged_requires:
        print(f"\n[合并后依赖列表]")
        pypi_set = {normalize_pkg_name(d.split(";")[0].split("[")[0]
                    .split(">=")[0].split("<=")[0].split("!=")[0]
                    .split("==")[0].split(">")[0].split("<")[0].strip())
                    for d in pypi_requires}
        for dep in merged_requires:
            key = normalize_pkg_name(dep.split(";")[0].split("[")[0]
                  .split(">=")[0].split("<=")[0].split("!=")[0]
                  .split("==")[0].split(">")[0].split("<")[0].strip())
            src_tag = "[PyPI]" if key in pypi_set else "[本地]"
            dist_label = f"python3dist({extract_pypi_name(dep)})"
            print(f"  {src_tag} {dep:<40} → {dist_label}")

    if build_sys_requires:
        print(f"\n[构建系统依赖]  来自 [build-system].requires")
        bs_avail = {item["dep"]: item["rpm"] for item in (build_sys_rpm_check or {}).get("available", [])}
        bs_miss  = {item["dep"] for item in (build_sys_rpm_check or {}).get("missing",  [])}
        for dep in build_sys_requires:
            dist_label = f"python3dist({extract_pypi_name(dep)})"
            if dep in bs_avail:
                print(f"  ✓ {dep:<40} → {bs_avail[dep]}")
            elif dep in bs_miss:
                print(f"  ✗ {dep:<40} → {dist_label}  (未找到)")
            else:
                print(f"  ? {dep:<40} → {dist_label}")

    print(f"\n[C 扩展检测]")
    if c_ext_pypi:
        print("  ✓ PyPI wheel 含架构标记（有 C 扩展）")
    if c_ext_local["has_c_ext"]:
        for r in c_ext_local["reasons"]:
            print(f"  ✓ {r}")
    if not c_ext_pypi and not c_ext_local["has_c_ext"]:
        print("  纯 Python 包，无 C 扩展")

    if rpm_check:
        avail = rpm_check["available"]
        miss = rpm_check["missing"]
        print(f"\n[RPM 可用性]  已有 {len(avail)} 个 / 缺失 {len(miss)} 个")
        if avail:
            for item in avail:
                print(f"  ✓ {item['dep']:<40} → {item['rpm']}")
        if miss:
            print(f"\n  ✗ 缺失（需自行打包或从其他源安装）:")
            for item in miss:
                print(f"    {item['dep']:<40}  ({item['rpm_name']})")

    # 优先用查询到的实际 RPM 包名；无查询结果时用 python3dist() 格式
    if build_sys_rpm_check:
        build_sys_rpms = [item["rpm"] for item in build_sys_rpm_check.get("available", [])]
    else:
        build_sys_rpms = [f"python3dist({extract_pypi_name(d)})"
                          for d in (build_sys_requires or []) if extract_pypi_name(d)]
    br = build_rpm_requires(c_ext_local, rpm_check, build_sys_rpms)
    print(f"\n[BuildRequires 建议]")
    for r in br:
        print(f"  BuildRequires: {r}")
    print(sep)


# ── 7. 主入口 ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Python 包 RPM 依赖分析（PyPI + 本地取并集）")
    parser.add_argument("source_dir", help="Python 项目源码目录")
    parser.add_argument("--pkg", default="",
                        help="PyPI 包名（默认从源码目录名推断）")
    parser.add_argument("--check-rpm", action="store_true",
                        help="在容器内用 dnf 查询 RPM 可用性")
    parser.add_argument("--container", default="oe-build-env",
                        help="已运行的 OpenEuler 容器名（默认 oe-build-env）")
    parser.add_argument("-o", "--output", default="",
                        help="结果输出到 JSON 文件")
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source_dir)
    if not os.path.isdir(source_dir):
        print(f"[ERROR] 目录不存在: {source_dir}", file=sys.stderr)
        sys.exit(1)

    # 推断包名
    pkg_name = args.pkg or Path(source_dir).name
    print(f"[INFO] 分析目录: {source_dir}")
    print(f"[INFO] PyPI 包名: {pkg_name}")

    # ── 来源1：PyPI API ──
    pypi_requires: List[str] = []
    c_ext_pypi = False
    version = ""
    pypi_metadata: Dict[str, Dict] = {}
    print(f"\n[INFO] 查询 PyPI API...")
    pypi_json = fetch_pypi_info(pkg_name)
    if pypi_json:
        pypi_requires, c_ext_pypi, version = parse_pypi_deps(pypi_json)
        print(f"  ✓ 获取成功，版本 {version}，{len(pypi_requires)} 个依赖")
        if c_ext_pypi:
            print("  ✓ 检测到 C 扩展（wheel 含架构标记）")
    else:
        print("  ✗ PyPI 查询失败，仅使用本地解析")

    # ── 来源2：本地源码 ──
    print(f"\n[INFO] 解析本地源码...")
    local_requires, build_backend = parse_local_deps(source_dir)
    build_sys_requires = parse_build_system_deps(source_dir)
    c_ext_local = scan_c_extensions_local(source_dir)
    print(f"  构建后端: {build_backend}，{len(local_requires)} 个依赖")
    if build_sys_requires:
        print(f"  构建系统依赖 [build-system].requires: {build_sys_requires}")

    # ── 取并集 ──
    merged = merge_requires(pypi_requires, local_requires)
    print(f"\n[INFO] 并集合并: PyPI({len(pypi_requires)}) + 本地({len(local_requires)}) → {len(merged)} 个")

    if merged or build_sys_requires:
        print(f"\n[INFO] 收集依赖 PyPI 元数据以规范化 upstream URL...")
        pypi_metadata = collect_pypi_metadata(merged + build_sys_requires)
        print(f"  ✓ 已获取 {len(pypi_metadata)} 个依赖的 PyPI 元数据")

    # ── dnf 查询 ──
    rpm_check = None
    build_sys_rpm_check = None
    if args.check_rpm:
        if not merged:
            print("[INFO] 无运行时依赖，跳过 RPM 查询")
        else:
            rpm_check = check_rpm_availability(args.container, merged, pypi_metadata)
        if build_sys_requires:
            print(f"\n[INFO] 查询构建系统依赖 RPM 可用性...")
            build_sys_rpm_check = check_rpm_availability(args.container, build_sys_requires, pypi_metadata)

    print_report(source_dir, pkg_name, version,
                 pypi_requires, local_requires, merged, build_backend,
                 c_ext_pypi, c_ext_local, rpm_check,
                 build_sys_requires, build_sys_rpm_check)

    if args.output:
        if build_sys_rpm_check:
            build_sys_rpms = [item["rpm"] for item in build_sys_rpm_check.get("available", [])]
        else:
            build_sys_rpms = [f"python3dist({extract_pypi_name(d)})"
                              for d in (build_sys_requires or []) if extract_pypi_name(d)]
        result = {
            "pkg_name": pkg_name,
            "version": version,
            "build_backend": build_backend,
            "pypi_requires": pypi_requires,
            "local_requires": local_requires,
            "merged_requires": merged,
            "dependency_items": build_dependency_items(merged, pypi_metadata),
            "build_sys_requires": build_sys_requires,
            "build_sys_dependency_items": build_dependency_items(build_sys_requires, pypi_metadata),
            "c_ext_pypi": c_ext_pypi,
            "c_ext_local": c_ext_local,
            "rpm_check": rpm_check,
            "build_sys_rpm_check": build_sys_rpm_check,
            "build_requires": build_rpm_requires(c_ext_local, rpm_check, build_sys_rpms),
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] 结果已保存: {args.output}")

    if rpm_check and rpm_check["missing"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
