# Python spec 规范

当 `<lang>=python` 时，spec 初稿应优先采用 openEuler Python 打包的保守路径，先保证在当前容器环境中可稳定通过 `rpmlint`、`dnf builddep` 与 `rpmbuild`，再考虑进一步自动化。

## 1. 适用范围

该规范优先适用于：
- 存在 `pyproject.toml`、`setup.py` 或 `setup.cfg`
- 单包 Python 项目
- 不包含复杂 monorepo / workspace 布局
- 不依赖特殊二进制打包器或自定义发布链路

若项目包含多子包、原生扩展、生成代码步骤或高度自定义安装逻辑，不能直接套用基础模板，需进入特殊分支处理。

## 2. 总原则

- 优先使用 openEuler 当前环境中稳定可用的 Python 宏链
- `%pyproject_build` + `%pyproject_install` 是标准路径，优先使用
- 若 `%pyproject_save_files` 或 record 链与当前平台宏实现不兼容，优先回退为手工 `%files`
- 不要为了追求"自动生成完整 spec"而在不兼容宏组合上反复试错
- 最终以容器内 openEuler 软件源真值、`rpmlint`、`dnf builddep`、`rpmbuild` 和缺包递归引入结果为准

## 3. 命名规则（双包模式）

openEuler 社区采用**双包模式**：SRPM 用 `python-` 前缀，二进制包用 `python3-` 前缀。

| 字段 | 来源 | 示例（PyPI 名 `requests`） |
|---|---|---|
| `Name:`（SRPM 名） | `srpm_name` = `get_srpm_name("python", pypi_name)` | `python-requests` |
| `%package -n`（二进制包名） | `rpm_pkg_name` = `get_rpm_pkg_name("python", pypi_name)` | `python3-requests` |
| `Requires:` | `rpm_requirement` = `rpm_name_from_pep508(dep_spec)` | `python3-click >= 8.0` |

规则：
- `Name:` 永远用 `srpm_name`（`python-<normalized>`），不用 `python3-`
- `%package -n` 永远用 `rpm_pkg_name`（`python3-<normalized>`）
- `%files`、`%description`、`%install` 等节都跟 `%package -n` 的名字走
- `%package -n` 下必须加 `Provides: python-<name>`，满足旧依赖引用

| 场景 | srpm_name | rpm_pkg_name |
|---|---|---|
| 标准库 | `python-requests` | `python3-requests` |
| 带下划线 | `python-typing-extensions` | `python3-typing-extensions` |
| PyPI 名含 `python-` 前缀 | `python-python-multipart` | `python3-python-multipart` |
| Django（大写）| `python-django` | `python3-django` |

## 4. 版本格式

| 上游版本 | RPM Version 写法 |
|---|---|
| `1.2.3` | `1.2.3` |
| `1.2.3b0`（beta） | `1.2.3~b0` |
| `1.2.3rc1`（rc） | `1.2.3~rc1` |
| `1.2.3.post1` | `1.2.3^post1` |
| `1.2.3.dev0` | `1.2.3~dev0` |

RPM 中 `~` 表示版本比基础版本低（pre-release），`^` 表示比基础版本高（post-release）。

## 5. Source0 URL 规范

Source0 **必须**填写完整的上游下载 URL，不得只写文件名。

### 5.1 PyPI 包（推荐：`%{pypi_source}` 宏）

绝大多数 Python 包发布在 PyPI，使用 `%{pypi_source}` 宏自动展开为 PyPI tarball URL：

```spec
# 包名与 PyPI 名相同时（最简写法）
Source0: %{pypi_source}

# 包名与 PyPI 名不同时，显式指定 PyPI 名
Source0: %{pypi_source <pypi_name>}

# 指定版本（通常不需要，%{version} 自动使用）
Source0: %{pypi_source <pypi_name> %{version}}
```

`%{pypi_source <name>}` 展开后等价于：
`https://files.pythonhosted.org/packages/source/<首字母>/<name>/<name>-%{version}.tar.gz`

### 5.2 GitHub 直接下载

部分包不发布到 PyPI，从 GitHub 下载：

```spec
# 使用 %{url} 宏拼接（推荐，与 URL 字段联动）
Source0: %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# 或完整写法
Source0: https://github.com/<owner>/<repo>/archive/v%{version}/<name>-%{version}.tar.gz
```

---

## 6. spec 模板

以下是三种常见场景的模板，选择最接近当前项目的那个。

### 6.1 标准 pyproject（hatchling / flit / pdm）

适用：`pyproject.toml` 存在，`build-backend` 为 `hatchling.build` / `flit_core.buildapi` / `pdm.pep517.api` 等。

```spec
Name:           python-<srpm_name>
Version:        <version>
Release:        1%{?dist}
Summary:        <one-line summary from pyproject.toml>
License:        <SPDX license identifier>
URL:            <upstream homepage>
Source0:        %{pypi_source}
BuildArch:      noarch

%description
<multi-line description>


%package -n python3-<rpm_pkg_name_suffix>
Summary:        <one-line summary>
Provides:       python-<name>
Provides:       python3dist(<pypi_name>) = %{version}
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-<build-backend>

%description -n python3-<rpm_pkg_name_suffix>
<multi-line description>


%package help
Summary:        Development documents and examples for <srpm_name>
Provides:       python3-<rpm_pkg_name_suffix>-doc
%description help
<multi-line description>


%prep
%autosetup -n <pypi_name>-%{version} -p1

%build
%pyproject_build

%install
%pyproject_install

%files -n python3-<rpm_pkg_name_suffix>
%license LICENSE
%{python3_sitelib}/<module>/
%{python3_sitelib}/<dist_name>-%{version}*.dist-info/

%files help
%doc README.md

%changelog
* <date> Python_Bot <Python_Bot@openeuler.org> - <version>-1
- Initial package
```

### 6.2 setuptools（setup.py / setup.cfg）

适用：无 `pyproject.toml`，或 `build-backend = "setuptools.build_meta"`。

```spec
Name:           python-<srpm_name>
Version:        <version>
Release:        1%{?dist}
Summary:        <one-line summary>
License:        <SPDX license identifier>
URL:            <upstream homepage>
Source0:        %{pypi_source}
BuildArch:      noarch

%description
<multi-line description>


%package -n python3-<rpm_pkg_name_suffix>
Summary:        <one-line summary>
Provides:       python-<name>
Provides:       python3dist(<pypi_name>) = %{version}
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools

%description -n python3-<rpm_pkg_name_suffix>
<multi-line description>


%package help
Summary:        Development documents and examples for <srpm_name>
Provides:       python3-<rpm_pkg_name_suffix>-doc
%description help
<multi-line description>


%prep
%autosetup -n <pypi_name>-%{version} -p1

%build
%py3_build

%install
%py3_install

%files -n python3-<rpm_pkg_name_suffix>
%license LICENSE
%{python3_sitelib}/<module>/
%{python3_sitelib}/<dist_name>-%{version}*.egg-info/

%files help
%doc README.md

%changelog
* <date> Python_Bot <Python_Bot@openeuler.org> - <version>-1
- Initial package
```

### 6.3 C 扩展包（含 .c / .pyx 文件）

适用：存在 `.c`、`.pyx` 文件，或 `setup.py` 中有 `Extension()` 调用，或 PyPI wheel 含架构标记。

注意：C 扩展包**不设 `BuildArch: noarch`**。

```spec
Name:           python-<srpm_name>
Version:        <version>
Release:        1%{?dist}
Summary:        <one-line summary>
License:        <SPDX license identifier>
URL:            <upstream homepage>
Source0:        %{pypi_source}

%description
<multi-line description>


%package -n python3-<rpm_pkg_name_suffix>
Summary:        <one-line summary>
Provides:       python-<name>
Provides:       python3dist(<pypi_name>) = %{version}
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  gcc
# BuildRequires:  python3-Cython   # 仅在有 .pyx 文件时取消注释

%description -n python3-<rpm_pkg_name_suffix>
<multi-line description>


%package help
Summary:        Development documents and examples for <srpm_name>
Provides:       python3-<rpm_pkg_name_suffix>-doc
%description help
<multi-line description>


%prep
%autosetup -n <pypi_name>-%{version} -p1

%build
%define _lto_cflags %{nil}
%undefine _hardened_build
%py3_build

%install
%py3_install

%files -n python3-<rpm_pkg_name_suffix>
%license LICENSE
%{python3_sitearch}/<module>/
%{python3_sitearch}/<dist_name>-%{version}*.egg-info/

%files help
%doc README.md

%changelog
* <date> Python_Bot <Python_Bot@openeuler.org> - <version>-1
- Initial package
```

## 6. `%files` 填写规则

**手工 `%files` 时，必须根据实际安装产物填写，不要照搬模板占位符。**

确认安装产物的方法：
```bash
docker exec oe-build-env bash -c "find %{buildroot} -type f | sed 's|%{buildroot}||'"
# 或先完成 rpmbuild，检查 BUILDROOT 内容
docker exec oe-build-env bash -c "ls ~/rpmbuild/BUILDROOT/<pkg>-<ver>*/usr/lib/python3.11/site-packages/"
```

常见需要列举的内容：

| 类型 | 路径 |
|---|---|
| 纯 Python 模块目录 | `%{python3_sitelib}/<module>/` |
| C 扩展模块目录 | `%{python3_sitearch}/<module>/` |
| 单文件模块 | `%{python3_sitelib}/<module>.py` |
| dist-info（pyproject） | `%{python3_sitelib}/<Name>-%{version}*.dist-info/` |
| egg-info（setuptools） | `%{python3_sitelib}/<Name>-%{version}*.egg-info/` |
| 命令行入口 | `%{_bindir}/<command>` |
| py.typed 标记 | 已在模块目录内，无需单独列 |
| license 文件 | `%license LICENSE` |
| 文档 | `%doc README.md CHANGELOG.md` |

注意：
- `dist-info` 目录名中的包名部分，连字符和下划线可能不一致，用 `*` 通配
- C 扩展包用 `%{python3_sitearch}`，纯 Python 包用 `%{python3_sitelib}`
- 不要混用两个宏

## 7. Provides 别名规则

若 RPM 包名为 `python-<name>`（非 `python3-` 前缀），但其他包的 `Requires` 可能写成 `python3-<name>`，需在 spec 中显式声明别名：

```spec
Provides:       python3-<name> = %{version}-%{release}
```

这样 `python-multipart` 可同时满足 `Requires: python3-multipart`。

## 8. 禁用 LTO + BTI（C 扩展必须）

C 扩展包在 openEuler 上构建时，必须在 `%build` 前禁用 LTO 和 BTI 标志，否则可能链接失败：

```spec
%build
%define _lto_cflags %{nil}
%undefine _hardened_build
%py3_build
```

## 9. `__requires_exclude_from`（可选路径排除）

若包安装到非标准路径（如 `/opt/ros/`），需排除自动依赖扫描：

```spec
%global __requires_exclude_from ^/opt/ros/.*
```

## 10. BuildArch 规则

| 情况 | BuildArch |
|---|---|
| 纯 Python，无 C 扩展 | `BuildArch: noarch` |
| 含 C 扩展（.so 文件） | 不设 BuildArch（默认按架构构建） |

## 11. 依赖声明规则

- `BuildRequires` 只写构建时真正需要的包，不要预先写运行时依赖
- `Requires` 只写运行时必须的包，约束版本时使用从 `pyproject.toml` 读取的真实约束
- 不要把测试依赖（pytest、coverage 等）写入 spec
- 运行时依赖最终以 `dnf builddep` + `rpmbuild` 实际失败为准，不要机械翻译上游依赖列表

## 12. `%changelog` 格式

```spec
%changelog
* Wed May 07 2026 Python_Bot <Python_Bot@openeuler.org> - 1.2.3-1
- Initial package
```

日期格式：`%a %b %d %Y`（英文，与 `date "+%a %b %d %Y"` 输出一致）。

## 13. 不直接采用的行为

- 无条件启用 `%pyproject_save_files`（在 openEuler 当前宏实现下不稳定）
- 机械翻译上游全部依赖为 RPM `Requires`
- 在初稿阶段写入大量未经验证的 `BuildRequires`
- 复用上次会话中遗留的 `/tmp/<pkg>.spec`（每次构建必须重新生成）
