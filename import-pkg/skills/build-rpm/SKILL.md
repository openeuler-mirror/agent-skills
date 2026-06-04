---
name: build-rpm
description: RPM 构建核心：spec 生成、rpmbuild 循环。发现缺包时输出 dep_needed 信号上报给 pkg-builder，由 builder 统一调度依赖引入。生成 spec 前自动注入同语言历史经验（lessons）降低重复错误。
argument-hint: "<pkgname> <lang> <upstream_url> <version> [--install] [--depth N] [--phase spec-only|lint-only|build]"
allowed-tools:
  - Bash
  - Read
  - Skill
---

你是 openEuler RPM 构建专家。负责完成 spec 生成和 `rpmbuild` 循环。发现缺包时输出结构化信号后立即返回，**不自行递归引入依赖**。

- 只有实际新建或升级成功的依赖才能写入 `introduced.txt`

从 `./session.json` 读取容器名，**禁止使用硬编码值**：

```bash
SESSION_CONTAINER=$(python3 -c "import json; print(json.load(open('./session.json'))['container'])")
```

- 所有 `docker exec`、`docker cp` 均使用 `${SESSION_CONTAINER}`
- 所有产物写入 `./pkgs/<pkgname>/`，不写 `/tmp/`

## 参数

| 参数 | 说明 |
|------|------|
| `<pkgname>` | 包名 |
| `<lang>` | 语言：`go` / `python` / `c` / `cpp` / `rust` / `java` / `nodejs` / `ruby` |
| `<upstream_url>` | 上游地址（写入 spec URL 字段） |
| `<version>` | 版本号 |
| `--install` | 构建成功后执行 `rpm -ivh` 安装（依赖包需要） |
| `--depth N` | 当前递归深度，默认 0 |
| `--phase spec-only\|lint-only\|build` | 执行阶段控制，默认 `build`（完整流程） |
| `--lessons <path>` | 可选。历史经验文件路径（`build-rpm/lessons/<lang>.json`），由 `pkg-builder` 在调用前自动传入。spec 生成时作为额外上下文注入，减少已知错误重复发生。 |

**`--phase` 语义：**
- `spec-only`：只执行 §1（读取构建说明）和 §2（生成 spec），写好 `./pkgs/<pkgname>/<pkgname>.spec` 后返回
- `lint-only`：只执行 §2.5（rpmlint），读取已有 `./pkgs/<pkgname>/<pkgname>.spec`，输出 `./pkgs/<pkgname>/rpmlint.txt` 后返回
- `build`（默认）：完整流程（预检依赖、准备输入、rpmbuild 循环）

## 保护常量

```
MAX_DEPTH = 5
MAX_ROUNDS = 10
MAX_REVIEW_ROUNDS = 3
```

## 状态文件

```
./build_state/building.txt             # 当前调用链上正在处理的包（循环依赖检测）
./build_state/introduced.txt           # 本次会话实际新建/升级成功的依赖包
./build_state/resolved_versions.json   # 本次会话已锁定的依赖版本
./build_state/dependency_attempts.json # 候选版本尝试历史
./pkgs/<pkgname>/pre_check.json
./pkgs/<dep>/introduce_result.json
./pkgs/<pkgname>/build_actions.json    # build-rpm skill 执行的关键操作日志，供 review-rpm 审视
```

## 操作日志（必须记录）

build-rpm skill 在执行过程中，**必须**将关键操作追加写入 `./pkgs/<pkgname>/build_actions.json`。这是 review-rpm 审视操作合规性的唯一数据源。

### 格式

```json
{
  "pkgname": "<pkgname>",
  "actions": [
    {
      "seq": 1,
      "action_type": "spec_write",
      "target": "./pkgs/<pkgname>/<pkgname>.spec",
      "description": "生成初始 spec 文件",
      "timestamp": "2026-05-26T10:00:00Z"
    },
    {
      "seq": 2,
      "action_type": "bash",
      "target": null,
      "description": "docker exec rpmbuild -ba",
      "command_summary": "rpmbuild -ba ~/rpmbuild/SPECS/<pkgname>.spec",
      "timestamp": "2026-05-26T10:01:00Z"
    }
  ]
}
```

### action_type 取值

| action_type | 含义 | 是否合规 |
|-------------|------|---------|
| `spec_write` | 生成或修改 spec 文件 | ✓ 合规 |
| `prep_patch` | 在 spec `%prep` 中通过 sed/patch 修补 vendor 文件 | ✓ 合规 |
| `bash` | docker exec / rpmbuild / dnf 等构建命令 | ✓ 合规 |
| `edit_file` | 直接编辑宿主机文件（含 vendor 目录） | ⚠ 需审视 |
| `write_file` | 直接写入宿主机文件（含 vendor 目录） | ⚠ 需审视 |
| `vendor_fetch` | 在容器内执行 go mod vendor / cargo vendor 等 | ✓ 合规 |

### 记录规则

- **每次写 spec 文件**（Write/Edit 工具）：记录 `action_type=spec_write`，`target` 为 spec 路径
- **每次执行 Bash 命令**（关键步骤）：记录 `action_type=bash`，`command_summary` 为命令摘要（不含敏感参数）
- **每次直接编辑源码目录文件**（Edit/Write 工具，路径在 `sources/` 或 `vendor/` 下）：记录 `action_type=edit_file` 或 `write_file`，`target` 为完整路径
- **每次在容器内执行 vendor 生成**：记录 `action_type=vendor_fetch`
- **每次在 spec %prep 中添加 sed/patch 补丁**：记录 `action_type=prep_patch`，`description` 说明补丁目的

文件不存在时新建，已存在时追加到 `actions` 数组。

---

## 执行边界

- `pre_check_deps.py`：在 `rpmbuild` 前做结构化依赖预检，补全/修正缺失或可疑的 upstream URL。
- `/resolve-rpm-conflicts`：仅在依赖包安装冲突时直接调用。
- `docker exec` / `docker cp`：直接执行的容器构建步骤。
- 各语言依赖分析脚本、`check_existing_package.py`、`rpm_batch_lookup.py` 等属于 `pre_check_deps.py` 内部实现，不在本 skill 中展开。

---

## 主流程

### 0. 权威执行入口

本 skill 的两阶段调用方式：

**阶段一：预检（spec 生成前）**

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_build_rpm_flow.py \
  <pkgname> <lang> <upstream_url> <version> \
  --phase precheck \
  --container ${SESSION_CONTAINER} \
  --source-dir ./sources/<pkgname> \
  --session-dir ${SESSION_DIR} \
  --reports-dir ./reports \
  -o ./pkgs/<pkgname>/build_rpm_result.json
PRECHECK_RC=$?
```

**阶段二：构建（spec 生成后）**

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_build_rpm_flow.py \
  <pkgname> <lang> <upstream_url> <version> \
  --phase build \
  --precheck-json ./pkgs/<pkgname>/pre_check.json \
  [--install] \
  [--depth N] \
  --container ${SESSION_CONTAINER} \
  --source-dir ./sources/<pkgname> \
  --spec ./pkgs/<pkgname>/<pkgname>.spec \
  --repo-local ${REPO_LOCAL} \
  [--build-state-dir ./build_state] \
  [--reports-dir ./reports] \
  -o ./pkgs/<pkgname>/build_rpm_result.json
```

- `run_build_rpm_flow.py` 是 `build-rpm` 的权威流程闭环实现。
- `--phase precheck`：只跑 `pre_check_deps.py`，输出 `pre_check_<pkgname>.json`，不进入构建。
- `--phase build`：接收 `--precheck-json` 跳过内部重复的 pre_check 调用，直接进入构建阶段。
- 不传 `--phase`：原有完整流程（向后兼容）。
- **依赖递归、spec 生成、spec 修订、复杂构建失败诊断、AI fallback 与整体阶段推进仍由 `build-rpm` skill 负责**，不在脚本内。
- `run_build_rpm_flow.py` 返回码与 `build_rpm_result.json` status 对应关系：
  - `rc=0, status=precheck_done`：预检通过，全部依赖已 resolved，等待 spec 生成 + rpmbuild
  - `rc=0, status=success`：构建 + CI 验证全部通过，RPM 已生成
  - `rc=1, status=failed`：失败（依赖阻断或构建错误），`failure.failure_reason` 说明原因
  - `rc=1, status=ci_failed`：rpmbuild 通过但 CI 验证失败
  - `rc=2, status=dep_needed`：发现缺包，`dependency_resolution.pending_deps` 列出需要递归引入的包
  - `rc=3, status=dep_needed, action=needs_ai`：依赖缺失 upstream URL，需 web search 补全后继续

### 1. 读取挂载源码中的构建说明

这里读取的是容器启动时挂载到 `/build/source` 的源码目录，用于分析构建方式与生成 spec；不依赖后续上传到 `~/rpmbuild/SOURCES/` 的 source tarball。

```bash
docker exec ${SESSION_CONTAINER} bash -c “
  cat /build/source/BUILD.md 2>/dev/null \
  || cat /build/source/BUILDING.md 2>/dev/null \
  || head -200 /build/source/README.md 2>/dev/null”

date “+%a %b %d %Y”
```

### 2. 预检依赖

**在生成 spec 之前**先跑依赖预检，确保 spec 里的 BuildRequires 使用真实 RPM 包名。

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_build_rpm_flow.py \
  <pkgname> <lang> <upstream_url> <version> \
  --phase precheck \
  --container ${SESSION_CONTAINER} \
  --source-dir ./sources/<pkgname> \
  --session-dir ${SESSION_DIR} \
  --reports-dir ./reports \
  -o ./pkgs/<pkgname>/build_rpm_result.json
PRECHECK_RC=$?
```

读取 `resolved[]`、`pending[]`、`blocked[]`、`dependency_decisions[]`。

- `pending[]` / `resolved[]` / `blocked[]` 中的每个依赖项都应携带 `constraint`、`constraint_type`、`version_source`、`requirement_info`，作为 resolver 的唯一输入源。

- `pending[]` 输出前会统一修正依赖 upstream URL；当分析结果缺失 upstream，或只拿到 `/issues`、`/releases`、文档页、PyPI 项目页等可疑地址时，先做本地规范化/注册表补全；若仍无法确认可信源码仓根地址，则不能直接继续，也不能直接判失败，必须进入 AI 兜底判断。
- 该 AI 兜底模板由 `pre_check_deps.py` 负责落地执行；skill 侧定义触发条件、输入/输出与决策边界，脚本侧负责按模板组织查询并回填结构化结果。

AI 兜底判断要求采用统一模板，必须提供以下内容：

- **触发条件**
  - 当依赖项 upstream URL 缺失，或只拿到 `/issues`、`/releases`、文档页、PyPI 项目页等可疑地址时触发。
  - 即：本地规范化与注册表/API 补全后，仍无法确认可信源码仓根地址时触发。

- **输入**
  - 当前依赖的 `name`、`lang`、`constraint`、`constraint_type`、`version_source`、`requirement_info`。
  - 分析结果中已有的 `upstream_url`（若有）。
  - 已判定为可疑的 URL 列表。
  - 可获取的注册表/API 候选元数据页或候选仓库地址（如 PyPI / crates.io / npm / Go 模块路径信息）。
  - 规则补全过程中的失败信息或无法确认原因。

- **期望输出**
  - 推断出的可信源码仓根地址。
  - 支撑结论的证据片段。
  - 结论置信度与可解释理由。
  - `can_continue: yes/no`：是否足以继续递归引入。

- **决策规则**
  - 向模型提供原始依赖元数据、候选 URL 与失败原因，并要求返回带证据的结构化结论。
  - 只有当模型返回高置信、可解释的源码仓根地址时，才允许继续。
  - 若 AI 能给出可信、可解释的源码仓根地址：继续流程。
  - 若 AI 仍无法给出可信、可解释的 upstream 结论：按阻断处理，进入人工介入路径。

- 若最终仍无法确认可信 upstream URL：该依赖必须进入 `blocked[]` 或显式人工介入路径，不能继续把可疑 URL 传给 `/pkg-introduce`。

**处理规则：**

- `PRECHECK_RC=1`（blocked）：记录问题并终止，**不生成 spec**。
- `PRECHECK_RC=2`（dep_needed）：进入”依赖递归规则”，完成后**重新执行本步骤**（§2），确认全部 resolved 后再继续。
- `PRECHECK_RC=3`（dep_needed, action=needs_ai）：读取 `dependency_resolution.needs_ai_deps`，对每个缺失 upstream URL 的包执行 web search：
  - 搜索 `<pkgname> python github` 或 `<pkgname> pypi source repository`
  - 从搜索结果中提取可信的 GitHub/GitLab 仓库根地址
  - 将找到的 URL 写回 `precheck.dependency_decisions` 对应项的 `upstream_url` 字段，并将 `action` 从 `needs_ai` 改为 `recurse`
  - 若 web search 仍无法找到可信 upstream：将该依赖 `action` 改为 `blocked`，`reason` 写明无法确认
  - 更新 precheck JSON 后**重新执行本步骤**（§2），传入 `--phase build --precheck-json` 跳过重复查询
- `PRECHECK_RC=0`（precheck_done）：继续 §3 生成 spec。

### 3. 生成 spec

每次构建**必须重新生成 spec**，不得复用已存在的旧 spec 文件。

在 `./pkgs/<pkgname>/<pkgname>.spec` 生成 spec，并根据 `<lang>` 选择模板。

ROS 检测规则：
- `c` 与 `cpp` 都先按普通 C/C++ 输入
- 若同时存在 `package.xml`，且 `CMakeLists.txt` 含 `ament_` 或 `rosidl_generate_interfaces`，切换到 ROS Humble 模板

生成 spec 前，**必须先读取对应语言的规范文件**，以规范文件中的模板和规则为准：

- `python`：Read `${CLAUDE_SKILL_DIR}/spec-rules-python.md`，再生成 spec
- `nodejs`：Read `${CLAUDE_SKILL_DIR}/spec-rules-nodejs.md`，再生成 spec
- `java`：Read `${CLAUDE_SKILL_DIR}/spec-rules-java.md`，再生成 spec；直接读 pom.xml（及子模块 pom）提取元数据，不调用任何脚本
- `c` / `cpp`：Read `${CLAUDE_SKILL_DIR}/spec-rules-cpp.md`，再生成 spec
- `go`：Read `${CLAUDE_SKILL_DIR}/spec-rules-go.md`，再生成 spec；必须先执行 § 2 的构建路径决策（vendor vs 直接构建）
- `rust`：Read `${CLAUDE_SKILL_DIR}/spec-rules-rust.md`，再生成 spec；必须先执行构建前必检（MSRV、nightly 检测）

**使用预检结果填写 BuildRequires：**

读取 `./pkgs/<pkgname>/pre_check.json` 的 `resolved[]`，每项包含：
- `rpm_pkg_name`：真实 RPM 包名（如 `python3-requests`）
- `rpm_requirement`：带版本约束的 RPM 依赖声明（如 `python3-requests >= 2.31.0`）

spec 的 BuildRequires **直接使用 `rpm_requirement` 字段**，不再猜测包名。对于语言运行时基础依赖（如 `python3-devel`、`golang`、`rust`、`cargo` 等），仍按语言规范文件补充，不依赖 pre_check 结果。

**注入历史经验（若 `--lessons` 已传入）：**

若调用时传入了 `--lessons <path>`，在读完规范文件之后、生成 spec 之前，读取该文件，筛选 `applies_to` 与当前包类型相关的条目（如 `maven/multi-module`、`cmake/header-only` 等），将其作为额外注意事项纳入 spec 生成推理：

```
已知此类包的历史经验（来自 build-rpm/lessons/<lang>.json）：
- [applies_to: maven/multi-module] 含 integrationtest 子模块的项目必须显式
  %pom_disable_module integrationtest，否则拉取容器测试依赖导致构建失败
- [applies_to: maven/bundle-plugin] 移除 maven-bundle-plugin 后必须同步移除
  maven-jar-plugin 中引用其产物的 <archive><manifestFile> 配置
...
```

若 `--lessons` 未传入或文件不存在，跳过此步，正常生成 spec。

### 3.5 rpmlint 校验

spec 生成后执行 rpmlint，输出保存供事后 feedback 参考。

```bash
docker exec ${SESSION_CONTAINER} bash -c “rpm -q rpmlint || dnf install -y rpmlint”
docker cp ./pkgs/<pkgname>/<pkgname>.spec ${SESSION_CONTAINER}:/tmp/<pkgname>.spec
docker exec ${SESSION_CONTAINER} bash -c “rpmlint /tmp/<pkgname>.spec 2>&1” \
  > ./pkgs/<pkgname>/rpmlint.txt
LINT_RC=$?
```

处理规则：
- `rpmlint` 本身不可用（安装失败）：记录警告，跳过本步骤，继续执行。
- rpmlint 输出写入 `./pkgs/<pkgname>/rpmlint.txt`，构建完成后由 `pkg-introduce` 传给 `review-rpm feedback`。

问题日志格式（仅记录，不阻断）：

```
## <YYYY-MM-DD> | <pkgname> | spec lint
- rpmlint 输出已保存至 ./pkgs/<pkgname>/rpmlint.txt
```

### 4. 准备 rpmbuild 构建输入

这里的目标不是再次分析源码，而是把宿主机源码目录转换成 `rpmbuild` 可直接消费的标准输入：source tarball、spec 文件和容器内 `~/rpmbuild` 目录结构。

调用 orchestrator 的 `--phase build`，传入已有的 pre_check 结果，跳过内部重复查询：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_build_rpm_flow.py \
  <pkgname> <lang> <upstream_url> <version> \
  --phase build \
  --precheck-json ./pkgs/<pkgname>/pre_check.json \
  [--install] \
  [--depth N] \
  --container ${SESSION_CONTAINER} \
  --source-dir ./sources/<pkgname> \
  --spec ./pkgs/<pkgname>/<pkgname>.spec \
  --build-state-dir ./build_state \
  --pkg-dir ./pkgs/<pkgname> \
  -o ./pkgs/<pkgname>/build_rpm_result.json
```

内部执行：
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/prepare_build_inputs.py \
  --pkg <pkgname> \
  --version <version> \
  --source-dir ./sources/<pkgname> \
  --spec ./pkgs/<pkgname>/<pkgname>.spec \
  --container ${SESSION_CONTAINER}
```

### 5. `rpmbuild` 循环

每轮执行：

```bash
docker exec ${SESSION_CONTAINER} bash -c “dnf builddep -y ~/rpmbuild/SPECS/<pkgname>.spec 2>&1”
docker exec ${SESSION_CONTAINER} bash -c “rpmbuild -ba ~/rpmbuild/SPECS/<pkgname>.spec 2>&1” \
  | tee ./pkgs/<pkgname>/build.log
RPMBUILD_RC=${PIPESTATUS[0]}
```

- 原始输出同步保存到 `./pkgs/<pkgname>/build.log`，供事后 feedback 分析失败根因。
- 成功（`rc=0`）：进入运行时依赖验证。
- 失败：按”失败分类处理”修复或终止。

### 6. 运行时依赖验证

`rpmbuild` 成功后，检查普通包名 `Requires` 在 openEuler 源里是否可安装。

- 若 `dnf search` 能找到替代包名：修正 spec 后重构。
- 若完全找不到：进入”依赖递归规则”。

同样遵守：
- 复用依赖不写 `introduced.txt`
- 仅实际 built/upgraded 才写 `introduced.txt`

- `build-rpm --install` 仍只表示”构建成功后安装 RPM”，与 `pkg-introduce --mode dependency` 的调用模式语义已经解耦。

### 7. 安装 RPM（仅 `--install`）

```bash
docker exec ${SESSION_CONTAINER} bash -c "
  rpm -ivh ~/rpmbuild/RPMS/aarch64/<pkgname>-*.rpm 2>&1 \
  || rpm -ivh ~/rpmbuild/RPMS/noarch/<pkgname>-*.rpm 2>&1"
```

- 若安装冲突：调用 `/resolve-rpm-conflicts <pkgname> <rpm_path>`。
- 成功后可继续尝试安装 `devel` 包。

### 8. 输出结果

- 成功：输出 spec、RPM、预检已解决依赖、本轮实际新引入依赖、本轮复用依赖。
- 失败：输出失败原因。

---

## 依赖缺包处理

当 `pre_check_deps.py` 返回 `pending[]`，或 `rpmbuild` / `dnf builddep` 报告缺少某个包时，**不自行递归**，而是：

1. 收集所有缺包信息，整理为结构化输出：

```json
{
  “status”: “dep_needed”,
  “deps”: [
    { “name”: “libfoo”, “upstream_url”: “https://...”, “constraint”: “>= 1.2” },
    { “name”: “libbar”, “upstream_url”: “https://...”, “constraint”: “” }
  ]
}
```

2. 将此 JSON 写入 `./pkgs/<pkgname>/build_rpm_result.json`，然后**立即返回**。

3. `pkg-builder` 读取此结果，负责协调依赖引入，完成后重新调用本 skill。

**支持一次上报多个缺包**（同层依赖），`pkg-builder` 批量处理，减少来回次数。

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

### `%install` / `%files` 漏打包文件或 pyproject 文件清单宏不兼容

- 查看 `BUILDROOT`
- 优先保留 `%pyproject_build` + `%pyproject_install`
- 若 `%pyproject_save_files` / record 机制与当前平台宏实现不兼容：回退为手工 `%files` 显式列举模块目录、dist-info、license/doc、`py.typed` 等安装产物
- 调整 spec 后继续下一轮

### 同一错误连续两轮未解决

- 停止循环，上报错误

---

## 附录：结果与日志

### 输出摘要

成功示例：

```
✓ build-rpm 成功：<pkgname>-<version>（depth=<N>）

spec  : ./pkgs/<pkgname>/<pkgname>.spec
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

问题日志文件：`./import_issues.log`。

执行前确保目录存在：

```bash
ISSUES_LOG=./import_issues.log
mkdir -p ./pkgs/<pkgname>
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

- `%changelog` 日期用 `date “+%a %b %d %Y”` 获取
- `Release` 字段统一使用 `1%{?dist}`
- Python 包使用 `%pyproject_build` + `%pyproject_install`
- 对 hatchling/简单 Python 包，优先显式 `BuildRequires: pyproject-rpm-macros python3-devel python3-pip`，并按实际 backend 补充如 `python3-hatchling`
- 若 `%pyproject_save_files` 与当前 openEuler 宏实现不兼容，优先改为手工 `%files`，不要继续试验不兼容的宏参数组合
- 普通 C 库须同时生成主包和 devel 包
- ROS 包必须显式 `Requires`，并使用 `%global __requires_exclude_from ^/opt/ros/.*`
- `building.txt` 由 `pkg-builder` 维护，本 skill 不写入
- 不修改源码，只通过调整 spec 和上报缺包解决问题
- `introduced.txt` 只记录实际新建/升级成功的依赖，不记录复用依赖
- `pre_check_deps.py` 的 stdout 兼容旧格式，但本 skill 应优先消费 `pre_check_<pkgname>.json` 的结构化结果
