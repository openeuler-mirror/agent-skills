#!/usr/bin/env python3
"""
RPM 包命名统一模块。

各语言的 RPM 包名和 Requires 表达式由此模块统一生成，
所有 analyze_*_deps.py、pre_check_deps.py、spec 生成均应通过此模块取值，
不得在各处自行拼接包名前缀。

openEuler 双包模式（Python）：
  SRPM Name:      python-<name>        （源码包，用于仓库管理）
  二进制包名:      python3-<name>       （实际安装的包，通过 %package -n 声明）
  Requires: 写法: python3-<name>       （引用二进制包名）

其他语言命名惯例：
- Node.js: nodejs-<name>
- Java   : <groupId>:<artifact>（通过 mvn() Provides 机制）
- C/C++  : 通过 pkgconfig() / cmake() / lib*.so Provides 机制
- Go     : 通常无运行时 RPM 依赖（vendor 构建）
- Rust   : 通常无运行时 RPM 依赖（静态链接）
"""

import re


def _normalize(name: str) -> str:
    """规范化包名：小写 + 连字符替换下划线/点（PEP 503 / npm 惯例）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def get_srpm_name(lang: str, upstream_name: str) -> str:
    """
    返回 SRPM 名，即 spec 文件 Name: 字段的值（源码包名）。

    Python 双包模式下 SRPM 名用 python- 前缀：
        get_srpm_name("python", "requests")         → "python-requests"
        get_srpm_name("python", "python-multipart") → "python-python-multipart"
        get_srpm_name("python", "Django")            → "python-django"

    其他语言 SRPM 名与二进制包名相同（无双包模式）：
        get_srpm_name("nodejs", "lodash")            → "nodejs-lodash"
    """
    lang = lang.lower()
    if lang == "python":
        return f"python-{_normalize(upstream_name)}"
    else:
        return get_rpm_pkg_name(lang, upstream_name)


def get_rpm_pkg_name(lang: str, upstream_name: str) -> str:
    """
    返回二进制 RPM 包名，用于：
    - Python 双包模式下的 %package -n 字段
    - Requires: / BuildRequires: 中的包名
    - rpm -qa 查到的实际包名

    Python 示例：
        get_rpm_pkg_name("python", "requests")         → "python3-requests"
        get_rpm_pkg_name("python", "python-multipart") → "python3-python-multipart"
        get_rpm_pkg_name("python", "Django")            → "python3-django"

    Node.js 示例：
        get_rpm_pkg_name("nodejs", "lodash")            → "nodejs-lodash"
        get_rpm_pkg_name("nodejs", "@scope/pkg")        → "nodejs-scope-pkg"
    """
    lang = lang.lower()
    if lang == "python":
        return f"python3-{_normalize(upstream_name)}"
    elif lang == "nodejs":
        name = upstream_name
        if name.startswith("@"):
            name = name.lstrip("@").replace("/", "-")
        return f"nodejs-{_normalize(name)}"
    else:
        # java: 用 mvn(groupId:artifactId) Provides 机制，不加前缀
        # c/cpp: 用 pkgconfig() / cmake() / lib*.so，不加前缀
        # go/rust: 通常无运行时 RPM 依赖
        return upstream_name


def get_rpm_requirement(lang: str, upstream_name: str, constraint: str = "") -> str:
    """
    返回可直接写入 spec Requires: 的完整表达式（使用二进制包名）。

    Python 示例：
        get_rpm_requirement("python", "requests", ">= 2.0")      → "python3-requests >= 2.0"
        get_rpm_requirement("python", "requests", ">= 2.0, < 3") → "(python3-requests >= 2.0 with python3-requests < 3)"
        get_rpm_requirement("python", "requests")                 → "python3-requests"

    Node.js 示例：
        get_rpm_requirement("nodejs", "lodash", ">= 4.0")         → "nodejs-lodash >= 4.0"

    Java 示例（保持 mvn() 表达式，由 analyze_java_deps 处理）：
        get_rpm_requirement("java", "org.apache:commons-lang3")    → "org.apache:commons-lang3"
    """
    pkg = get_rpm_pkg_name(lang, upstream_name)
    if not constraint:
        return pkg

    parts = [c.strip() for c in constraint.split(",") if c.strip()]
    parts = [re.sub(r"([><=!~]+)\s*", r"\1 ", p).strip() for p in parts]

    if len(parts) == 1:
        return f"{pkg} {parts[0]}"
    else:
        expr = " with ".join(f"{pkg} {p}" for p in parts)
        return f"({expr})"


def rpm_name_from_pep508(pkg_spec: str) -> str:
    """
    从 PEP 508 依赖规范直接生成 Python RPM Requires 表达式。

    'requests>=2.0,<3'       → '(python3-requests >= 2.0 with python3-requests < 3)'
    'python-dateutil>=2.7.0' → 'python3-python-dateutil >= 2.7.0'
    'click'                  → 'python3-click'
    """
    spec = pkg_spec.split(";")[0].strip()
    spec = re.sub(r"\[.*?\]", "", spec)
    m = re.match(r"([a-zA-Z0-9_\-.]+)", spec.strip())
    if not m:
        return ""
    upstream_name = m.group(1)
    version_part = spec[len(m.group(1)):].strip().strip("()")
    return get_rpm_requirement("python", upstream_name, version_part)
