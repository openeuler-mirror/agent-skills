---
name: build-rpm
description: RPM 构建核心：spec 生成、rpmbuild 循环、传递依赖引入。支持任意深度依赖链，内置循环依赖检测和深度上限保护。由 pkg-introduce 调用，顶层包和依赖包共用同一流程。
argument-hint: "<pkgname> <lang> <upstream_url> <version> [--install] [--depth N]"
allowed-tools:
  - Bash
  - Read
  - Skill
---

你是 OpenEuler RPM 构建专家。负责完成 spec 生成、`rpmbuild` 循环和依赖递归引入，并根据预检结果区分复用、构建和阻断。

- 默认构建容器名为 `oe-build-env`
- 若后续脚本或命令支持自定义容器名，应显式透传该容器名，避免写死到实现之外的调用方
- 只有实际新建或升级成功的依赖才能写入 `introduced.txt`

## 参数

| 参数 | 说明 |
|------|------|
| `<pkgname>` | 包名 |
| `<lang>` | 语言：`go` / `python` / `c` / `cpp` / `rust` / `java` / `nodejs` / `ruby` |
| `<upstream_url>` | 上游地址（写入 spec URL 字段） |
| `<version>` | 版本号 |
| `--install` | 构建成功后执行 `rpm -ivh` 安装（依赖包需要） |
| `--depth N` | 当前递归深度，默认 0 |

## 保护常量

```
MAX_DEPTH = 5
MAX_ROUNDS = 10
```

## 状态文件

```
./build_state/building.txt   # 当前调用链上正在处理的包（循环依赖检测）
./build_state/introduced.txt # 本次会话实际新建/升级成功的依赖包
./reports/pre_check_<pkgname>.json
./reports/pkg_introduce_result_<dep>.json
```

---

## 执行边界（必须遵守)

### 直接调用的脚本 / skill / 容器步骤

- `pre_check_deps.py`：本 skill 直接调用，用于在 `rpmbuild` 前做结构化依赖预检。
- `finalize_dependency_result.py`：本 skill 直接调用，用于统一清理 `building.txt` 并决定是否写入 `introduced.txt`。
- `/pkg-introduce`：本 skill 在依赖递归时直接调用。
- `/resolve-rpm-conflicts`：仅在依赖包安装冲突时直接调用。
- `docker exec` / `docker cp`：本 skill 直接执行的容器构建步骤。

### 不应在本 skill 展开的内部实现

- 各语言依赖分析脚本、`check_existing_package.py`、`rpm_batch_lookup.py` 等属于 `pre_check_deps.py` 或 `pkg-introduce` 内部实现，不在本 skill 中展开。

---

## 主流程

### 1. 读取构建说明

```bash
docker exec oe-build-env bash -c "
  cat /build/source/BUILD.md 2>/dev/null \
  || cat /build/source/BUILDING.md 2>/dev/null \
  || head -200 /build/source/README.md 2>/dev/null"

date "+%a %b %d %Y"
```

### 2. 生成 spec

在宿主机 `/tmp/<pkgname>.spec` 生成 spec，并根据 `<lang>` 选择模板。

ROS 检测规则：
- `c` 与 `cpp` 都先按普通 C/C++ 输入
- 若同时存在 `package.xml`，且 `CMakeLists.txt` 含 `ament_` 或 `rosidl_generate_interfaces`，切换到 ROS Humble 模板

模板保持原有约束：Go / Python / C/C++ / ROS / Rust 各自沿用既有规则。

### 2.5 Spec 规范校验

spec 生成后、进入 `rpmbuild` 前，先在容器内执行 `rpmlint` 校验：

```bash
# 确保 rpmlint 已安装
docker exec oe-build-env bash -c "rpm -q rpmlint || dnf install -y rpmlint"

# 将 spec 上传到容器临时目录并执行 lint
docker cp /tmp/<pkgname>.spec oe-build-env:/tmp/<pkgname>.spec
docker exec oe-build-env bash -c "rpmlint /tmp/<pkgname>.spec 2>&1"
LINT_RC=$?
```

处理规则：

- `rpmlint` 输出中含 `E:` 行（Error）：记录到问题日志，**阻断构建**，终止并上报。
- 仅含 `W:` 行（Warning）：记录到问题日志，**不阻断**，继续执行。
- `rpmlint` 本身不可用（安装失败）：记录警告，跳过此步骤，继续执行。

问题日志格式：

```
## <YYYY-MM-DD> | <pkgname> | spec lint
- 问题：rpmlint E: <错误内容>
- 原因：spec 不符合规范
- 修复：根据 rpmlint 提示修正 spec 后重试
```

### 3. 预检依赖

执行：

```bash
PRE_CHECK=${CLAUDE_SKILL_DIR}/scripts/pre_check_deps.py
SOURCES=./sources
PRE_CHECK_JSON=./reports/pre_check_<pkgname>.json

python3 ${PRE_CHECK} <pkgname> <lang> ${SOURCES}/<pkgname> \
  --container oe-build-env -o ${PRE_CHECK_JSON}
PRECHECK_RC=$?
```

读取 `resolved[]`、`pending[]`、`blocked[]`、`dependency_decisions[]`。

- 若预检阻断：记录问题并终止当前构建。
- `resolved[]`：仅列出，不调用 `/pkg-introduce`，不写状态文件。
- `pending[]`：进入“依赖递归规则”。

### 4. 准备 rpmbuild 目录并上传文件

```bash
docker exec oe-build-env bash -c "mkdir -p ~/rpmbuild/{SPECS,SOURCES,BUILD,RPMS,SRPMS}"
docker exec oe-build-env bash -c "rm -f ~/rpmbuild/SOURCES/<pkgname>-*.tar.gz"
docker exec oe-build-env bash -c "cp /tmp/<pkgname>-<version>.tar.gz ~/rpmbuild/SOURCES/"
docker cp /tmp/<pkgname>.spec oe-build-env:/root/rpmbuild/SPECS/
```

### 5. `rpmbuild` 循环

每轮执行：

```bash
docker exec oe-build-env bash -c "dnf builddep -y ~/rpmbuild/SPECS/<pkgname>.spec 2>&1"
docker exec oe-build-env bash -c "rpmbuild -ba ~/rpmbuild/SPECS/<pkgname>.spec 2>&1"
```

- 成功：进入运行时依赖验证。
- 失败：按“失败分类处理”修复或终止。

### 6. 运行时依赖验证

`rpmbuild` 成功后，检查普通包名 `Requires` 在 OpenEuler 源里是否可安装。

- 若 `dnf search` 能找到替代包名：修正 spec 后重构。
- 若完全找不到：进入“依赖递归规则”。

同样遵守：
- 复用依赖不写 `introduced.txt`
- 仅实际 built/upgraded 才写 `introduced.txt`

### 7. 安装 RPM（仅 `--install`）

```bash
docker exec oe-build-env bash -c "
  rpm -ivh ~/rpmbuild/RPMS/aarch64/<pkgname>-*.rpm 2>&1 \
  || rpm -ivh ~/rpmbuild/RPMS/noarch/<pkgname>-*.rpm 2>&1"
```

- 若安装冲突：调用 `/resolve-rpm-conflicts <pkgname> <rpm_path>`。
- 成功后可继续尝试安装 `devel` 包。

### 8. 输出结果

- 成功：输出 spec、RPM、预检已解决依赖、本轮实际新引入依赖、本轮复用依赖。
- 失败：输出失败原因。

---

## 依赖递归规则

对每个 `pending` 依赖按以下规则处理：

### 1. 深度上限

- 若 `CURRENT_DEPTH >= MAX_DEPTH`：终止。

### 2. 循环依赖检测

- 若 `dep_pkgname` 已在 `./build_state/building.txt` 中：终止。

### 3. 会话内实际引入去重

- 若 `dep_pkgname` 已在 `./build_state/introduced.txt` 中：说明本次会话已实际新建/升级成功，可跳过。

### 4. 调用 `/pkg-introduce`

通过检查后：

```bash
echo "<dep_pkgname>" >> ./build_state/building.txt
```

```
/pkg-introduce <dep_pkgname> <dep_upstream_url> --install --depth <N+1>
```

记录返回码 `PKG_INTRODUCE_RC`。

### 5. 统一收口依赖结果（无论上一步成功还是失败，必须执行）

**无论 `/pkg-introduce` 成功还是失败，都必须执行 finalize，确保 `building.txt` 被清理：**

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/finalize_dependency_result.py \
  <dep_pkgname> --build-state-dir ./build_state --reports-dir ./reports --json
FINALIZE_RC=$?
```

该脚本负责：
- 从 `building.txt` 清理 `<dep_pkgname>`（即使结果文件缺失或 action 为空也会清理）
- 若结果为 `built_new` / `upgraded_user_repo`，追加到 `introduced.txt`
- 若结果为 `reused_official` / `reused_user_repo`，不追加
- 若结果为 `blocked`、结果文件缺失或 action 为空，返回失败

若 `PKG_INTRODUCE_RC != 0` 且 `FINALIZE_RC != 0`，则当前包构建失败，终止。

### 6. 根据 action 决定当前包是否继续

- `built_new` / `upgraded_user_repo` → 继续当前包构建
- `reused_official` / `reused_user_repo` → 视为成功复用，继续当前包构建
- `blocked` → 当前包构建失败，终止

---

## 失败分类处理

### 预检阻断

- `PRECHECK_RC=1` 或 `blocked[]` 非空：记录问题并终止。

### `dnf builddep` 找不到某包

- 先尝试 `dnf search <missing_pkg>`
- 找到正确包名：修正 spec，继续下一轮
- 完全找不到：进入依赖递归流程

### `%build` 缺头文件 / `.so` / 命令

- 用 `dnf provides` 定位
- 找到：补充 `BuildRequires`，继续下一轮
- 找不到：进入依赖递归流程

### `%install` / `%files` 漏打包文件

- 查看 `BUILDROOT`
- 调整 `%files`，继续下一轮

### 同一错误连续两轮未解决

- 停止循环，上报错误

---

## 附录：结果与日志

### 输出摘要

成功示例：

```
✓ build-rpm 成功：<pkgname>-<version>（depth=<N>）

spec  : ~/rpmbuild/SPECS/<pkgname>.spec
RPM   : ~/rpmbuild/RPMS/...
预检已解决依赖：<count>
本轮实际新引入依赖：
  - <dep1>（action=built_new）
  - <dep2>（action=upgraded_user_repo）
本轮复用依赖：
  - <dep3>（action=reused_official）
```

失败示例：

```
❌ build-rpm 失败：<pkgname>（depth=<N>）
原因：<错误描述>
```

### 问题日志

问题日志文件：`./reports/import_issues.log`。

执行前确保目录存在：

```bash
ISSUES_LOG=./reports/import_issues.log
mkdir -p ./reports
```

日志格式：

```
## <YYYY-MM-DD> | <pkgname> | 轮次 <N>
- 问题：<错误描述>
- 原因：<根本原因>
- 修复：<具体修复措施>
```

| 触发情况 | 记录内容示例 |
|---------|------------|
| 预检阻断 | `问题：依赖预检阻断；修复：停止并等待人工处理` |
| dnf builddep 找不到某包，需递归处理 | `问题：缺少 BuildRequires xxx；修复：按预检结果递归调用 pkg-introduce` |
| %build 编译报错缺头文件 / .so | `问题：找不到 foo.h；修复：补充 BuildRequires libfoo-devel` |
| %files 漏打包文件 | `问题：%files 未覆盖 /usr/lib/xxx；修复：追加 %{_libdir}/xxx` |
| 依赖只是复用 | `问题：依赖 xxx 已存在；修复：复用已有包，不进入 introduced.txt` |
| 依赖实际新建/升级 | `问题：依赖 xxx 源内缺失；修复：递归构建成功并写入 introduced.txt` |
| 超出最大轮次 / 深度 / 循环依赖 | `问题：构建终止原因` |

成功构建且无问题时，追加：

```
## <YYYY-MM-DD> | <pkgname> | ✓ 构建成功，无问题
```

---

## 注意事项

- `%changelog` 日期用 `date "+%a %b %d %Y"` 获取
- `Release` 字段统一使用 `1%{?dist}`
- Python 包使用 `%pyproject_build` + `%pyproject_install`
- 普通 C 库须同时生成主包和 devel 包
- ROS 包必须显式 `Requires`，并使用 `%global __requires_exclude_from ^/opt/ros/.*`
- `building.txt` 必须在错误路径也清理，避免污染后续调用
- 不修改源码，只通过调整 spec 和引入依赖包解决问题
- `introduced.txt` 只记录实际新建/升级成功的依赖，不记录复用依赖
- `pre_check_deps.py` 的 stdout 兼容旧格式，但本 skill 应优先消费 `pre_check_<pkgname>.json` 的结构化结果
- `/pkg-introduce` 成功不等于“本次新引入成功”；必须读取 `pkg_introduce_result_<dep>.json` 再决定是否写 `introduced.txt`
