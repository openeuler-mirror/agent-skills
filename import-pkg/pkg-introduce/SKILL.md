---
name: pkg-introduce
description: OpenEuler 包引入统一入口：合规检查、源码下载、语言/版本检测、版本感知复用/升级决策、构建调度、归档。顶层包和依赖包走同一流程，通过 --mode 区分。构建完成后调用 /review-rpm 进行事后反馈，经验沉淀到 lessons 文件供下次参考。
argument-hint: "<pkgname> <upstream_url> [--version <ver>] [--mode top-level|dependency] [--depth N]"
allowed-tools:
  - Bash
  - Read
  - Skill
---

你是 OpenEuler 包引入流程协调专家。无论是顶层包还是依赖包，均通过此 skill 完成准入检查、版本感知决策和构建调度。

- 默认构建容器名为 `oe-build-env`
- 若后续子流程支持自定义容器名，应显式透传该容器名，避免在 skill 文档外层隐式假设

## 参数

| 参数 | 说明 |
|------|------|
| `<pkgname>` | 包名 |
| `<upstream_url>` | 上游地址 |
| `--version <ver>` | 可选。指定期望版本；下载阶段按该版本选择对应 git tag/branch，后续仍以真实源码提取版本做权威决策 |
| `--mode top-level|dependency` | 调用模式；默认 `top-level`。显式传 `--mode dependency` 表示依赖包模式：构建完成后安装，不触发归档 |
| `--depth N` | 当前递归深度，默认 0，由调用方传入，**不要手动指定** |

> **调用链：**
>
> ```
> import-package
>   └─ pkg-introduce <main> <url>                     # 顶层，mode=top-level，depth=0
>        └─ build-rpm ... --depth 0
>             └─ 缺包 → pkg-introduce <dep-A> --mode dependency --depth 1
>                            └─ build-rpm ... --install --depth 1
>                                 └─ 缺包 → pkg-introduce <dep-B> --mode dependency --depth 2
>                                                └─ build-rpm ... --install --depth 2
>                                                     └─ ...（最深 depth=5）
> ```
>
> 循环依赖和超深链路由状态文件拦截，`build-rpm` 负责在调用前检查。

`import-package` 中的预扫描只是提示；**本 skill 在拿到真实 `<lang>` / `<version>` 后执行的 existing-check 才是权威决策。**

## 执行边界（必须遵守）

### 总原则

- **默认在宿主机执行**：源码下载、文本/元数据解析、API 查询、JSON 汇总、流程编排均应在宿主机执行。
- **仅将容器相关子步骤放到 `oe-build-env`**：凡是需要 OpenEuler 官方源真值或真实 RPM/构建环境的动作，必须通过 `docker exec oe-build-env ...` 在容器内执行。
- **不要把整个流程整体搬进容器运行**：除非步骤本身就是容器内命令；否则应保持"宿主机跑脚本，容器跑查询/构建命令"的模式。

### 本 skill 直接执行的命令 / skill / 容器步骤

- `check_repo.py`：用于上游仓库合规检查。
- `download_source.py`：用于下载上游源码。
- `check_license.py`：用于 License 合规检查。
- `check_existing_package.py`：在宿主机执行，但其中 `official` 判定必须查询 `oe-build-env` 容器内可见的 OpenEuler 官方 DNF 软件源；`user_repo` 仅扫描 AI 源本地克隆目录。
- `pkg_introduce_result.py`：用于结果写入与状态更新。
- `/setup-build-env`：用于准备或重建 `oe-build-env` 容器。
- `docker ps` / `docker stop` / `docker rm` / `docker cp`：属于本 skill 直接流程的一部分，可直接使用。


### 失败处理

- 若权威步骤需要容器，但 `oe-build-env` 不存在或不可用：应阻断并写清原因，不允许静默回退到本地目录或任意非官方数据源。
- 若只是宿主机静态分析步骤：不得为了"方便"强制放进容器执行。

---

## 主流程

### 1. 初始化（仅顶层）

- 顶层包调用（默认 `--mode top-level`）时，skill 应在初始化阶段调用 `run_pkg_introduce_flow.py` 的对应子命令，负责重置 `./build_state`、`./reports`、`./sources`，检查 `building.txt` 残留，并创建 `resolved_versions.json`、`dependency_attempts.json` 等状态文件。
- 依赖包调用（显式 `--mode dependency`）跳过此步，复用顶层状态文件。

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py init \
  --pkg <pkgname> \
  --mode <mode> \
  --build-state-dir ./build_state \
  --reports-dir ./reports \
  --sources-dir ./sources
```

### 2. 上游合规检查

skill 在上游合规检查阶段调用 `run_pkg_introduce_flow.py` 的对应子命令：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py repo-check \
  --pkg <pkgname> \
  --upstream-url <upstream_url> \
  --reports-dir ./reports
```

检查 `blocking`、`message`、`platform`、`last_updated`。

- 若 `blocking: true`：按"统一失败处理"写结果并终止。
- 若 API 查询失败但非阻断：允许继续，但需在最终报告中注明。

### 3. 下载源码

skill 在下载阶段调用 `run_pkg_introduce_flow.py` 的对应子命令：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py download \
  --pkg <pkgname> \
  --upstream-url <upstream_url> \
  [--version <expected_version>] \
  --sources-dir ./sources \
  --reports-dir ./reports
```

规则：
- 未指定 `--version`：下载默认分支后，脚本自动检测源码中声明的版本号；若含 `SNAPSHOT`、`dev`、`alpha`、`beta`、`rc`、`pre`、`nightly` 等不稳定后缀，**立即阻断**并列出上游可用 tag，要求调用方显式指定 `--version`。
- 指定 `--version <ver>` 且上游为 git 仓库：按版本优先解析并尝试以下 ref 顺序：`refs/tags/v<ver>` → `refs/tags/<ver>` → `refs/tags/<pkgname>-<ver>` → `refs/heads/v<ver>` → `refs/heads/<ver>` → `refs/heads/release-<ver>` → `refs/heads/release/<ver>` → `refs/heads/<major>.<minor>` → `refs/heads/v<major>.<minor>`。
- 若所有候选 ref 均不存在：明确失败，不允许静默回退到默认分支。
- 若上游为 tarball：不自动改写 URL；若指定了 `--version`，仅在后续真实版本校验阶段检查是否匹配。

### 4. License 检查

skill 在 License 检查阶段调用 `run_pkg_introduce_flow.py` 的对应子命令：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py license-check \
  --pkg <pkgname> \
  --source-dir ./sources/<pkgname> \
  --reports-dir ./reports
```

读取 `category`、`blocking`、`needs_ai_fallback`、`license_ids`、`message`，按三段式决策：

1. **规则直接通过**
   - 若 `category` 为 `permissive` / `weak_copyleft` / `strong_copyleft`
   - 且 `blocking=false`、`needs_ai_fallback=false`
   - 则直接继续后续流程。

2. **规则直接阻断**
   - 若 `category` 为 `no_commercial`
   - 或结果显式 `blocking=true`
   - 则按"统一失败处理"写结果并终止。

3. **需要 AI 兜底判断**
   - 若 `category` 为 `unknown` / `unlicensed`
   - 或结果显式 `needs_ai_fallback=true`
   - 则不能直接继续，也不能直接判失败；必须进入 AI 兜底判断。

AI 兜底判断要求采用统一模板，必须提供以下内容：

- **触发条件**
  - 当规则检测结论不确定时触发。
  - 即：规则脚本输出 `unknown` / `unlicensed`，或结果显式要求 `needs_ai_fallback=true` 时触发。

- **输入**
  - 规则脚本的原始输出结果。
  - manifest 中的原始 license 字段值（若有）。
  - `LICENSE` / `COPYING` / `NOTICE` / `README` 中与许可证相关的候选文本片段（若有）。

- **期望输出**
  - 推断出的许可证名称与 SPDX 标识（若能判断）。
  - 支撑结论的证据片段。
  - 结论置信度与可解释理由。
  - `can_continue: yes/no`：是否足以继续引入。

- **决策规则**
  - 向模型提供原始元数据和候选证据，并要求返回带证据的结构化结论。
  - 只有当模型返回高置信、可解释的分类结果时，才允许继续。
  - 若 AI 判断为可接受开源许可证且证据充分：继续流程。
  - 若 AI 判断为 `no_commercial` 或其他明确不合规：按"统一失败处理"阻断。
  - 若 AI 仍无法给出有证据、可解释的分类结论：按"统一失败处理"阻断，进入人工复核。

### 5. 识别语言与版本

skill 在语言与真实版本识别阶段调用 `run_pkg_introduce_flow.py` 的对应子命令：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py detect \
  --pkg <pkgname> \
  --source-dir ./sources/<pkgname> \
  [--expected-version <expected_version>] \
  --reports-dir ./reports
```

根据源码目录识别 `<lang>`，并提取真实 `<version>`。要求在 existing-check 前必须拿到二者。

若用户指定了 `--version <expected_version>`，则流程语义为：
- 下载阶段先按 `expected_version` 选择对应源码 ref（git 场景）或直接下载给定 tarball。
- 下载完成后，仍必须从源码中提取真实版本 `version`。
- 真实版本与期望版本必须一致，允许做轻量规范化匹配（例如忽略前缀 `v`）。
- 若二者不匹配：按"统一失败处理"写结果并终止，禁止继续沿用期望版本进入构建链路。
- 后续 `check_existing_package.py --version` 与 `/build-rpm ... <version>` 一律传真实版本 `version`，不传期望版本。

语言识别规则：

| 特征文件 | `<lang>` |
|---------|----------|
| `go.mod` | `go` |
| `Cargo.toml` | `rust` |
| `package.xml` + `CMakeLists.txt` 中存在 `ament_*` / `rosidl_generate_interfaces` | `cpp` |
| `CMakeLists.txt` / `configure.ac` / `meson.build` | `c` |
| `setup.py` / `pyproject.toml` | `python` |
| `pom.xml` / `build.gradle` | `java` |
| `package.json` | `nodejs` |
| `*.gemspec` / `Gemfile` | `ruby` |

版本提取统一通过共享入口执行：

```bash
git -C ./sources/<pkgname> describe --tags --abbrev=0 2>/dev/null | sed 's/^v//'
python3 ${CLAUDE_SKILL_DIR}/scripts/extract_version.py <lang> ./sources/<pkgname>
```

语言特定策略：
- `python`：`pyproject.toml`（`project.version` / `tool.poetry.version`）→ `VERSION` → `setup.py` 
- `rust`：`Cargo.toml` 的 `package.version`
- `nodejs`：`package.json` 的 `version`
- `java`：`pom.xml` → `gradle.properties` → `build.gradle(.kts)`
- `go` / `c` / `cpp` / `ruby`：当前先使用 `VERSION` 兜底，若无则依赖 `git tag`

版本识别按三段式决策：

1. **规则直接通过**
   - 若规则提取能够明确得到 `<lang>` 和真实 `<version>`
   - 则直接继续后续流程。

2. **需要 AI 兜底判断**
   - 若规则无法识别 `<lang>`
   - 或规则无法可靠提取真实 `<version>`
   - 则不能直接继续，也不能直接判失败；必须进入 AI 兜底判断。

AI 兜底判断要求采用统一模板，必须提供以下内容：

- **触发条件**
  - 当规则无法识别语言或无法可靠提取真实版本时触发。

- **输入**
  - 规则提取结果（包括空结果）。
  - `git describe --tags --abbrev=0` 的结果（若有）。
  - 源码目录中的候选元数据文件内容片段，例如 `pyproject.toml`、`Cargo.toml`、`package.json`、`pom.xml`、`build.gradle`、`VERSION`、`setup.py`、`package.xml`、`CMakeLists.txt`、`*.gemspec`、`Gemfile`。
  - 用户指定的 `expected_version`（若有）。

- **期望输出**
  - 推断出的 `<lang>`。
  - 推断出的真实 `<version>`。
  - 支撑结论的证据片段。
  - 结论置信度与可解释理由。
  - `can_continue: yes/no`：是否足以继续引入。

- **决策规则**
  - 向模型提供原始元数据和候选证据，并要求返回带证据的结构化结论。
  - 只有当模型返回高置信、可解释的语言和版本结论时，才允许继续。
  - 若 AI 能可靠识别 `<lang>` 和真实 `<version>`，且与 `expected_version`（若有）一致：继续流程。
  - 若 AI 能识别出真实 `<version>`，但与 `expected_version` 不一致：按"统一失败处理"阻断。
  - 若 AI 仍无法给出有证据、可解释的 `<lang>` 或 `<version>` 结论：按"统一失败处理"阻断，进入人工复核。

### 6. 准备容器并注入双源

在执行权威 `existing-check` 前，先确保 `oe-build-env` 已存在且可用，并在容器内同时具备：
- OpenEuler 官方 DNF 软件源
- `archive-rpm-sources/config.json` 中 `repo.remote_url` 对应的 AI RPM 源

顶层包调用：直接调用 `setup-build-env` skill（`./sources/<pkgname> <lang>`）重建干净环境；必须显式透传前面已识别出的权威语言类型。旧容器清理与重建由 `setup-build-env` skill 统一负责（`start_container` 会自动 `docker rm -f` 旧容器）。
依赖包调用：要求复用已有 `oe-build-env`；若容器不存在，按"统一失败处理"阻断。

### 7. 执行权威 existing-check

skill 在 existing-check 阶段调用 `run_pkg_introduce_flow.py` 的对应子命令：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py existing-check \
  --pkg <pkgname> \
  --lang <lang> \
  --version <version> \
  --container oe-build-env \
  --reports-dir ./reports
```

权威语义固定为：
- `official` = `oe-build-env` 容器内可见的 **OpenEuler 官方 DNF 软件源**
- `user_repo` = `oe-build-env` 容器内注入的 **AI RPM 软件源**（源地址来自 `archive-rpm-sources/config.json` 的 `repo.remote_url`）

读取 `decision`、`reason`、`official.highest.version`、`user_repo.highest.version`、`should_skip`。

- 若 `decision` 为 `reuse_*` 或 `block_*`：按"决策语义"和"统一失败/结果写入规则"立即处理并结束流程。
- 若 `decision` 为 `upgrade_user_repo` 或 `introduce_new`：进入构建分支。

### 8. 构建分支：调用 build-rpm

在调用 `build-rpm` 前，先检查是否有对应语言的历史经验文件：

```bash
LESSONS_FILE="${CLAUDE_SKILL_DIR}/../build-rpm/lessons/<lang>.json"
LESSONS_ARG=""
[ -f "$LESSONS_FILE" ] && LESSONS_ARG="--lessons $LESSONS_FILE"
```

写入初始化结果（`build pending`），然后调用完整构建：

```
/build-rpm <pkgname> <lang> <upstream_url> <version> [--install] [--depth N] $LESSONS_ARG
```

- 顶层包调用：不传 `--install`
- 依赖包调用：传 `--install`
- `run_pkg_introduce_flow.py` 负责：读取 `build_rpm_result_<pkgname>.json`，依据 `status` / `action` / `reason` 更新 `pkg_introduce_result_<pkgname>.json` 终态
- 若 `build-rpm` 成功：更新 `action` 为真实值；若失败：写结果（`action=blocked`），然后继续执行 §10 反馈步骤

### 9. 构建分支：归档（仅顶层且实际发生构建时）

仅当顶层调用且 `action ∈ {built_new, upgraded_user_repo}` 时触发。

```bash
INTRODUCED=$(sort -u ./build_state/introduced.txt | tr '\n' ' ')
ALL_PKGS="<pkgname> ${INTRODUCED}"
```

```
/archive-rpm-sources --pkgs <ALL_PKGS> --reports-dir ./reports
```

要求：
- `introduced.txt` 只包含 **实际 built/upgraded** 的依赖，`reuse_*` 不进入归档集合
- 归档脚本推送成功后自动回写 `pkg_introduce_result_<pkgname>.json` 中的 `archived=true`

### 10. 事后反馈（build-rpm 有执行时触发，顶层和依赖包均适用）

当 `build-rpm` 实际执行过（无论成功或失败，`action ∈ {built_new, upgraded_user_repo, blocked}`），归档步骤完成后（或失败直接跳过归档后），调用 `/review-rpm`：

```bash
LESSONS_FILE="${CLAUDE_SKILL_DIR}/../build-rpm/lessons/<lang>.json"
ROUND_HISTORY_ARG=""
[ -f "./reports/round_history_<pkgname>.json" ] && \
  ROUND_HISTORY_ARG="--round-history ./reports/round_history_<pkgname>.json"
```

```
/review-rpm feedback <pkgname> \
  --lang <lang> \
  --spec /tmp/<pkgname>.spec \
  --rpmlint /tmp/<pkgname>_rpmlint.txt \
  --build-result ./reports/build_rpm_result_<pkgname>.json \
  --build-log /tmp/<pkgname>_build.log \
  --lessons ${LESSONS_FILE} \
  --reports-dir ./reports \
  ${ROUND_HISTORY_ARG}
```

`review-rpm` 配置了 `context: fork`，会在独立的隔离上下文中执行，完成后将结果写入文件返回主流程。

**约束：只允许读上述输入文件，只允许写 `./reports/feedback_<pkgname>.json` 和 `${LESSONS_FILE}`，不执行任何命令。**

### 11. 生成汇总报告（所有调用，必须执行；顶层包和依赖包均生成独立报告）

无论 `action` 是什么、无论顶层还是依赖包，流程结束前必须为每个包生成一份报告。

**复用场景**（`action ∈ {reused_official, reused_user_repo}`）：直接用 Bash 写一条简短 summary，不调用 `/review-rpm`：

```bash
cat > ./reports/<pkgname>_introduction_report.md << EOF
# 引入报告：<pkgname>

| 字段 | 值 |
|------|-----|
| 决策 | <decision> |
| 动作 | <action> |
| 原因 | <reason> |
| 版本 | <version> |
| 引入日期 | $(date +%Y-%m-%d) |

> 复用已有包，未执行构建。
EOF
```

**构建场景或失败场景**（`action ∈ {built_new, upgraded_user_repo, blocked}`）：调用 `/review-rpm` 生成完整报告：

```bash
ROUND_HISTORY_ARG=""
[ -f "./reports/round_history_<pkgname>.json" ] && \
  ROUND_HISTORY_ARG="--round-history ./reports/round_history_<pkgname>.json"
```

```
/review-rpm summary <pkgname> \
  --reports-dir ./reports \
  --dist-dir ./dist \
  --spec /tmp/<pkgname>.spec \
  ${ROUND_HISTORY_ARG}
```

`review-rpm summary` 根据 `pkg_introduce_result_<pkgname>.json` 中的 `action` 自动判断场景：
- **复用场景**（`reused_*`）：模块说明和 RPM 产物等章节标注"不适用（复用已有包，未执行构建）"
- **构建场景**（`built_new` / `upgraded_user_repo`）：所有章节填充真实数据；若有 `round_history`，额外生成"修复过程摘要"章节
- **失败场景**（`blocked`）：在各阶段结论汇总中标注失败原因；若 `exit_reason=abort`，标注"Critic 判定结构性问题"

依赖包报告与顶层报告格式相同，基本信息中需标注包类型（依赖包）和被引入原因（由哪个顶层包触发）。

输出文件：`./reports/<pkgname>_introduction_report.md`

### 12. 输出结果

- 顶层：输出完整结果摘要
- 依赖包：输出精简结果摘要

---

## 决策语义

- `reuse_official`：官方源已有满足要求的版本，立即成功返回；不构建、不归档。
- `reuse_user_repo`：AI 源已有满足要求的版本，立即成功返回；不构建、不归档。
- `block_official_older`：官方源已有同名但版本不足，需人工决策；立即阻断。
- `upgrade_user_repo`：AI 源已有同名但版本不足，需要继续构建；最终 `action=upgraded_user_repo`。
- `introduce_new`：官方源与 AI 源均无满足要求的包，需要继续构建；最终 `action=built_new`。

---

## 统一失败处理与结果写入规则

### 立即阻断类

以下情况必须写结果并立即终止：
- 上游合规检查阻断
- License 检查阻断
- 版本无法可靠确定
- `block_official_older`
- 依赖包调用时容器缺失
- `build-rpm` 失败

统一要求：
- 记录 `./reports/import_issues.log`
- 调用 `pkg_introduce_result.py write` 或 `update` 写入当前状态
- 给出明确 `reason`
- 对可供上层回退判断的失败，补充 `failure_type` / `failure_reason`

### 非构建结束类

以下情况也必须立即写结果，但属于成功结束：
- `reuse_official` → `action=reused_official`
- `reuse_user_repo` → `action=reused_user_repo`

### 进入构建类

以下情况先写初始化结果，待构建后再更新：
- `upgrade_user_repo`
- `introduce_new`

初始化写入要求：
- `decision` 写真实值
- `action=blocked`
- `reason="build pending"`
- 如已知失败分类为空，可显式保留 `failure_type=""`

构建成功后再更新为：
- `built_new` 或 `upgraded_user_repo`
- 同步写入真实 `requested_version` / `version`

---

## 附录：结果与日志

### 结果输出建议

顶层包建议格式：

```
========================================
pkg-introduce 结果：<pkgname>
========================================
上游地址    : <upstream_url>
请求版本    : <requested_version 或 空>
语言 / 版本 : <lang> / <version>
决策        : <decision>
动作        : <action>
原因        : <reason>
License     : <spdx_id>（<category>）

本次实际新引入依赖 : <N> 个
归档状态          : <archived>
结果文件          : ./reports/pkg_introduce_result_<pkgname>.json
========================================
```

依赖包建议格式：

```
✓ dep 处理完成：<pkgname>（depth=<N>）
  decision=<decision>  action=<action>  requested_version=<requested_version 或 空>  lang=<lang>  version=<version>
```

### 问题日志

问题日志文件：`./reports/import_issues.log`。

执行前确保目录存在：

```bash
mkdir -p ./reports
```

| 触发情况 | 追加内容 |
|---------|---------|
| 权威 existing-check 命中官方复用 | `## <日期> \| <pkgname> \| ↷ 复用官方包：<reason>` |
| 权威 existing-check 命中用户仓库复用 | `## <日期> \| <pkgname> \| ↷ 复用用户仓库包：<reason>` |
| 官方仓库已有旧版，阻断 | `## <日期> \| <pkgname> \| ❌ 官方已有旧版，需人工决策：<reason>` |
| 上游合规检查阻断 | `## <日期> \| <pkgname> \| ❌ 合规阻断：<reason>` |
| License 检查阻断 | `## <日期> \| <pkgname> \| ❌ License 阻断：<category> <license_ids>` |
| 版本无法确定 | `## <日期> \| <pkgname> \| ❌ 版本检测失败` |
| 容器不存在（依赖包调用） | `## <日期> \| <pkgname> \| ❌ 容器异常：oe-build-env 不存在` |
| build-rpm 失败 | `## <日期> \| <pkgname> \| ❌ 构建失败（详见 build-rpm 记录）` |
| 构建成功且为新建/升级 | `## <日期> \| <pkgname> \| ✓ 构建完成：<action>` |

---

## 注意事项

- 顶层包调用初始化 `./build_state`，整个引入会话共享
- `build-rpm` 负责在调用本 skill 之前检查循环依赖和深度上限，本 skill 不重复做该检查
- `introduced.txt` 只记录 **实际 built_new / upgraded_user_repo** 的依赖，不记录 `reuse_*`
- 统一使用 `python3` 调用本 skill 下的 Python 脚本
- ROS message/interface 包不能按普通 C/C++ `%cmake` 模板处理；检测到 `package.xml` + `ament_*` / `rosidl_generate_interfaces` 时，应让 `build-rpm` 走 ROS Humble 分支
