---
name: build-rpm
description: RPM 构建核心：spec 生成、rpmbuild 循环、传递依赖引入。支持任意深度依赖链，内置循环依赖检测和深度上限保护。由 pkg-introduce 调用，顶层包和依赖包共用同一流程。生成 spec 前自动注入同语言历史经验（lessons）降低重复错误。
argument-hint: "<pkgname> <lang> <upstream_url> <version> [--install] [--depth N] [--phase spec-only|lint-only|build]"
allowed-tools:
  - Bash
  - Read
  - Skill
---

> **调用方式：Skill 工具（`/build-rpm`）。禁止通过 Agent 工具或 Bash 直接调用。**

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
| `--phase spec-only\|lint-only\|build` | 执行阶段控制，默认 `build`（完整流程） |
| `--lessons <path>` | 可选。历史经验文件路径（`build-rpm/lessons/<lang>.json`），由 `pkg-introduce` 在调用前自动传入。spec 生成时作为额外上下文注入，减少已知错误重复发生。 |

**`--phase` 语义：**
- `spec-only`：只执行 §1（读取构建说明）和 §2（生成 spec），写好 `/tmp/<pkgname>.spec` 后返回
- `lint-only`：只执行 §2.5（rpmlint），读取已有 `/tmp/<pkgname>.spec`，输出 `/tmp/<pkgname>_rpmlint.txt` 后返回
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
./reports/pre_check_<pkgname>.json
./reports/pkg_introduce_result_<dep>.json
./reports/critique_round<N>_<pkgname>.json   # review-fix 循环每轮 Critic 输出
./reports/round_history_<pkgname>.json       # review-fix 循环轮次汇总，供 Judge 读取
```

---

## 执行边界（必须遵守)

### 本 skill 直接执行的命令 / skill / 容器步骤

- `pre_check_deps.py`：用于在 `rpmbuild` 前做结构化依赖预检，并在递归前补全/修正缺失或可疑的 upstream URL；必要时可通过 WebSearch 搜索可信源码仓根地址。
- `finalize_dependency_result.py`：用于统一清理 `building.txt` 并决定是否写入 `introduced.txt`。
- `/pkg-introduce`：在依赖递归时直接调用；依赖模式统一显式传 `--mode dependency`。
- `/resolve-rpm-conflicts`：仅在依赖包安装冲突时直接调用。
- `docker exec` / `docker cp`：直接执行的容器构建步骤。

### 不在本 skill 中展开的内部实现

- 各语言依赖分析脚本、`check_existing_package.py`、`rpm_batch_lookup.py` 等属于 `pre_check_deps.py` 或 `pkg-introduce` 内部实现，不在本 skill 中展开。

---

## 主流程

### 0. 权威执行入口

本 skill 的默认执行路径应直接调用权威 orchestrator：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_build_rpm_flow.py \
  <pkgname> <lang> <upstream_url> <version> \
  [--install] \
  [--depth N] \
  [--container oe-build-env] \
  [--source-dir ./sources/<pkgname>] \
  [--spec /tmp/<pkgname>.spec] \
  [--build-state-dir ./build_state] \
  [--reports-dir ./reports] \
  -o ./reports/build_rpm_result_<pkgname>.json
```

- `run_build_rpm_flow.py` 是 `build-rpm` 的权威流程闭环实现，但其边界明确限定为：**在 spec 已确定之后，且所有依赖已就绪时**，负责完成确定性构建阶段。
- 当前已纳入脚本闭环的阶段包括：
  - `pre_check_deps.py` 结构化预检
  - `prepare_build_inputs.py` 构建输入准备
  - `dnf builddep` / `rpmbuild -ba` / 可选 `rpm -ivh`
  - 输出结构化 `build_rpm_result_<pkgname>.json`
- **依赖递归、spec 生成、spec 修订、复杂构建失败诊断、AI fallback 与整体阶段推进仍由 `build-rpm` skill 负责**，不在脚本内。
- `run_build_rpm_flow.py` 返回码语义：
  - `rc=0`：构建成功
  - `rc=1`：构建失败（non_retryable 或预检错误）
  - `rc=2`：发现 pending 依赖，`status=pending_deps`，`dependency_resolution.pending_deps` 列出需要递归引入的包列表，**skill 应读取此列表并逐个调用 `/pkg-introduce`，完成后重新调用本 orchestrator**
- 本 skill 负责根据用户输入与当前构建状态决定何时生成/修订 spec，何时调用该 orchestrator，并读取其结构化结果向用户汇总；不要再把下面各步骤当作需要手工逐条编排的默认路径。

### 1. 读取挂载源码中的构建说明

skill 在构建说明读取阶段调用 `run_build_rpm_flow.py` 的对应子命令/阶段入口；在当前阶段，`run_build_rpm_flow.py` 已是 `build-rpm` 的权威阶段闭环实现，负责根据调用参数继续完成预检、递归、构建输入准备、builddep、rpmbuild 与可选安装，并输出 `build_rpm_result_<pkgname>.json`。

这里读取的是容器启动时挂载到 `/build/source` 的源码目录，用于分析构建方式与生成 spec；不依赖后续上传到 `~/rpmbuild/SOURCES/` 的 source tarball。

```bash
docker exec oe-build-env bash -c "
  cat /build/source/BUILD.md 2>/dev/null \
  || cat /build/source/BUILDING.md 2>/dev/null \
  || head -200 /build/source/README.md 2>/dev/null"

date "+%a %b %d %Y"
```

### 2. 生成 spec

每次构建**必须重新生成 spec**，不得复用 `/tmp/<pkgname>.spec` 等任何已存在的旧 spec 文件。

在宿主机 `/tmp/<pkgname>.spec` 生成 spec，并根据 `<lang>` 选择模板。

ROS 检测规则：
- `c` 与 `cpp` 都先按普通 C/C++ 输入
- 若同时存在 `package.xml`，且 `CMakeLists.txt` 含 `ament_` 或 `rosidl_generate_interfaces`，切换到 ROS Humble 模板

生成 spec 前，**必须先读取对应语言的规范文件**，以规范文件中的模板和规则为准：

- `python`：Read `/root/.claude/skills/build-rpm/spec-rules-python.md`，再生成 spec
- `nodejs`：Read `/root/.claude/skills/build-rpm/spec-rules-nodejs.md`，再生成 spec
- `java`：Read `/root/.claude/skills/build-rpm/spec-rules-java.md`，再生成 spec；直接读 pom.xml（及子模块 pom）提取元数据，不调用任何脚本
- `c` / `cpp`：Read `/root/.claude/skills/build-rpm/spec-rules-cpp.md`，再生成 spec
- 其他语言：沿用现有模板与既有规则，后续逐步拆分到独立规范文件

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

### 2.5 rpmlint 校验

spec 生成后执行 rpmlint，输出保存供事后 feedback 参考。

```bash
docker exec oe-build-env bash -c "rpm -q rpmlint || dnf install -y rpmlint"
docker cp /tmp/<pkgname>.spec oe-build-env:/tmp/<pkgname>.spec
docker exec oe-build-env bash -c "rpmlint /tmp/<pkgname>.spec 2>&1" \
  > /tmp/<pkgname>_rpmlint.txt
LINT_RC=$?
```

处理规则：
- `rpmlint` 本身不可用（安装失败）：记录警告，跳过本步骤，继续执行。
- rpmlint 输出写入 `/tmp/<pkgname>_rpmlint.txt`，构建完成后由 `pkg-introduce` 传给 `review-rpm feedback`。

问题日志格式（仅记录，不阻断）：

```
## <YYYY-MM-DD> | <pkgname> | spec lint
- rpmlint 输出已保存至 /tmp/<pkgname>_rpmlint.txt
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

- 若预检阻断：记录问题并终止当前构建。
- `resolved[]`：仅列出，不调用 `/pkg-introduce`，不写状态文件。
- `pending[]`：进入“依赖递归规则”。

### 4. 准备 rpmbuild 构建输入

这里的目标不是再次分析源码，而是把宿主机源码目录转换成 `rpmbuild` 可直接消费的标准输入：source tarball、spec 文件和容器内 `~/rpmbuild` 目录结构。

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/prepare_build_inputs.py \
  --pkg <pkgname> \
  --version <version> \
  --source-dir ./sources/<pkgname> \
  --spec /tmp/<pkgname>.spec \
  --container oe-build-env
```

### 5. `rpmbuild` 循环

每轮执行：

```bash
docker exec oe-build-env bash -c “dnf builddep -y ~/rpmbuild/SPECS/<pkgname>.spec 2>&1”
docker exec oe-build-env bash -c “rpmbuild -ba ~/rpmbuild/SPECS/<pkgname>.spec 2>&1” \
  | tee /tmp/<pkgname>_build.log
RPMBUILD_RC=${PIPESTATUS[0]}
```

- 原始输出同步保存到 `/tmp/<pkgname>_build.log`，供事后 feedback 分析失败根因。
- 成功（`rc=0`）：进入运行时依赖验证。
- 失败：按”失败分类处理”修复或终止。

### 6. 运行时依赖验证

`rpmbuild` 成功后，检查普通包名 `Requires` 在 OpenEuler 源里是否可安装。

- 若 `dnf search` 能找到替代包名：修正 spec 后重构。
- 若完全找不到：进入”依赖递归规则”。

同样遵守：
- 复用依赖不写 `introduced.txt`
- 仅实际 built/upgraded 才写 `introduced.txt`

- `build-rpm --install` 仍只表示”构建成功后安装 RPM”，与 `pkg-introduce --mode dependency` 的调用模式语义已经解耦。

### 6.5 review-fix 质量循环（CRITIC + SCALAR 模式）

`rpmbuild` 成功且运行时依赖验证通过后，进入质量修复循环。循环内 `/review-rpm critique` 扮演 **Critic** 角色，将 Oracle（rpmbuild log + rpmlint）的客观输出翻译为结构化修复指令；orchestrator（本 skill）负责读取指令、修改 spec、重跑 rpmbuild。

循环结束后 review-rpm 的 `feedback` / `summary` stage 扮演 **Judge** 角色，产出最终报告，不参与循环。

#### 循环伪码

```
SPEC_HASHES=[]
REVIEW_ROUND=1

while REVIEW_ROUND <= MAX_REVIEW_ROUNDS:

  # 1. 计算当前 spec hash（用于振荡检测）
  SPEC_HASH=$(sha256sum /tmp/<pkgname>.spec | cut -c1-16)
  if SPEC_HASH in SPEC_HASHES:
    log(“振荡检测：spec 连续两轮未变化，退出 review-fix 循环”)
    EXIT_REASON=”oscillation”
    break
  SPEC_HASHES.append(SPEC_HASH)

  # 2. 重新运行 rpmlint（更新 Oracle 信号）
  docker cp /tmp/<pkgname>.spec oe-build-env:/tmp/<pkgname>.spec
  docker exec oe-build-env rpmlint /tmp/<pkgname>.spec > /tmp/<pkgname>_rpmlint.txt 2>&1

  # 3. 调用 Critic
  /review-rpm critique <pkgname> \
    --lang <lang> \
    --spec /tmp/<pkgname>.spec \
    --rpmlint /tmp/<pkgname>_rpmlint.txt \
    --build-log /tmp/<pkgname>_build.log \
    --round <REVIEW_ROUND> \
    --reports-dir ./reports

  # 4. 读取 verdict
  VERDICT = read ./reports/critique_round<REVIEW_ROUND>_<pkgname>.json .verdict
  append_to_round_history(REVIEW_ROUND, verdict_json)

  if VERDICT == “PASS”:
    EXIT_REASON=”pass”
    break

  if VERDICT == “ABORT”:
    EXIT_REASON=”abort”
    mark_build_failed(“Critic 判定结构性问题，无法通过修 spec 解决”)
    break

  # 5. VERDICT == “FIX_REQUIRED”：按 E 级 fix_instruction 修改 spec
  E_ISSUES = [i for i in issues if severity == “E” and fix_instruction != null]
  if not E_ISSUES:
    EXIT_REASON=”pass”   # 只剩 W 级，可接受
    break

  for issue in E_ISSUES:
    apply fix_instruction to /tmp/<pkgname>.spec
    log issue to ./reports/import_issues.log

  # 6. 重跑 rpmbuild（仅 %build + %install，不重做 pre_check）
  docker cp /tmp/<pkgname>.spec oe-build-env:/tmp/<pkgname>.spec
  docker exec oe-build-env rpmbuild -ba ~/rpmbuild/SPECS/<pkgname>.spec \
    | tee /tmp/<pkgname>_build.log
  if rpmbuild failed:
    log(“review-fix 循环：修复后重构失败，退出循环”)
    EXIT_REASON=”rebuild_failed”
    break

  REVIEW_ROUND += 1

# 循环结束后写 round_history
write ./reports/round_history_<pkgname>.json:
  {
    “pkgname”: “<pkgname>”,
    “total_rounds”: <REVIEW_ROUND>,
    “exit_reason”: “<EXIT_REASON>”,   # pass / abort / oscillation / max_rounds / rebuild_failed
    “rounds”: [ <critique_round1_json>, <critique_round2_json>, ... ]
  }
```

#### apply fix_instruction 规则

`fix_instruction` 是 Critic 给出的”可直接执行的修改指令”，orchestrator 按以下方式解读：

- 指令以”删除 X”开头：从 spec 对应 section 中移除该行/该 flag
- 指令以”替换 X 为 Y”开头：在 spec 中找到 X 并替换为 Y
- 指令以”添加 X 到 Y section”开头：在 spec 对应 section 追加该内容
- 若指令超出上述模式，由 skill 根据自然语言理解执行最小化修改

**原则：每次只修改 fix_instruction 明确指出的内容，不引入额外改动。**

#### 循环参数说明

| 参数 | 值 | 说明 |
|------|------|------|
| `MAX_REVIEW_ROUNDS` | 3 | 超出后以当前状态退出，E 级问题在报告中标注未解决 |
| 振荡检测 | spec hash 连续两轮相同 | 强制退出，避免死循环 |
| `ABORT` 处理 | 短路 | 直接退出 review-fix 循环，进入 §7 / §8 失败路径 |
| W 级问题 | 不触发重构 | 记录在报告中，不阻止归档 |

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

### 4. 版本解析与候选选择

- 先读取 `./build_state/resolved_versions.json` 与 `./build_state/dependency_attempts.json`。
- 对当前 `pending` 依赖，不建议再按发现顺序逐个直接递归，而应先做“同层汇总”并生成统一 layer plan：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/aggregate_dependency_requests.py \
  --summary-json ./reports/pre_check_<pkgname>.json \
  --requested-by <pkgname> \
  -o ./reports/dependency_requests_<pkgname>.json

python3 ${CLAUDE_SKILL_DIR}/scripts/plan_dependency_layer.py \
  --requests-json ./reports/dependency_requests_<pkgname>.json \
  --build-state-dir ./build_state \
  --requested-by <pkgname> \
  -o ./reports/dependency_layer_plan_<pkgname>.json
PLAN_RC=$?
```

- 聚合结果中的每个 request 都代表一个同层 `DependencyNode`，包含：
  - `name`
  - `identity`
  - `constraint` / `all_constraints`
  - `constraint_type`
  - `requested_by`
  - `upstream_url`
  - `upstream_resolution`
  - `member_count`
  - `node_state`
  - `conflict` / `conflict_reason`
- 对于多个同名依赖，聚合脚本会尽量生成更合理的合并后 `constraint`；若检测到明显不兼容约束，则在进入 planner 前直接标记阻断。
- `plan_dependency_layer.py` 负责读取聚合结果与当前会话状态，输出：
  - `planned[]`：当前层可继续执行的依赖节点及其候选版本计划
  - `blocked[]`：当前层在执行前即可确认冲突或无候选的节点
  - `planning_log[]`：逐节点记录输入约束、已锁版本、候选列表、选择策略与选择原因，便于回溯“为什么挑这个版本”
- `PLAN_RC=0` 表示 layer plan 已生成且当前层无预阻断，可进入执行阶段。
- `PLAN_RC=2` 表示 layer plan 中存在 `blocked[]`，应终止当前依赖递归并输出阻断原因。
- 其他非 0 返回码表示 planner 自身执行失败。
- 如需人工快速查看“为什么选这个版本”，可额外渲染可读摘要：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/render_dependency_planning_summary.py \
  --input-json ./reports/dependency_layer_plan_<pkgname>.json \
  -o ./reports/dependency_planning_summary_<pkgname>.txt
```

### 5. 调用 `/pkg-introduce`

layer plan 生成后，由 **skill 直接逐个调用 `/pkg-introduce`**，不经过任何中间脚本桥接：

对 `planned[]` 中的每个依赖节点，按 `candidates` 顺序尝试：

```
/pkg-introduce <dep_name> <upstream_url> --version <candidate> --mode dependency --depth <N+1>
```

调用规则：
- 每次调用前，将 `<dep_name>` 写入 `./build_state/building.txt`
- 调用完成后（无论成功失败），立即执行收口：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/finalize_dependency_result.py <dep_name> \
  --build-state-dir ./build_state \
  --reports-dir ./reports \
  --json
```

- `finalize` 输出的 `action` 决定下一步：
  - `action ∈ {built_new, upgraded_user_repo, reused_official, reused_user_repo}` → 该依赖成功，继续下一个依赖
  - `action = blocked` 且 `failure_type` 含 `retryable` → 尝试下一个 `candidate`
  - `action = blocked` 且候选全部耗尽 → 当前层依赖引入失败，终止

### 6. 收口依赖结果

每个依赖的 `finalize_dependency_result.py` 负责：
- 清理 `building.txt` 中该依赖
- 若 `action ∈ {built_new, upgraded_user_repo}` → 写入 `introduced.txt`
- 写入 `resolved_versions.json`（锁定版本，防止后续递归重复引入不一致版本）

skill 在所有依赖处理完成后汇总结果，输出每个依赖的 `action` / `version` / `reason`。

### 7. 根据 action / failure_type 决定当前包是否继续

- `/pkg-introduce` + `finalize` 输出 `action ∈ {built_new, upgraded_user_repo, reused_official, reused_user_repo}` → 继续当前包构建
- `action = blocked`，候选全部耗尽 → 当前包构建失败，终止
- 成功/复用时，`finalize` 已把真实 `version` / `requested_version` 写入 `resolved_versions.json`

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
- 对 hatchling/简单 Python 包，优先显式 `BuildRequires: pyproject-rpm-macros python3-devel python3-pip`，并按实际 backend 补充如 `python3-hatchling`
- 若 `%pyproject_save_files` 与当前 openEuler 宏实现不兼容，优先改为手工 `%files`，不要继续试验不兼容的宏参数组合
- 普通 C 库须同时生成主包和 devel 包
- ROS 包必须显式 `Requires`，并使用 `%global __requires_exclude_from ^/opt/ros/.*`
- `building.txt` 必须在错误路径也清理，避免污染后续调用
- 不修改源码，只通过调整 spec 和引入依赖包解决问题
- `introduced.txt` 只记录实际新建/升级成功的依赖，不记录复用依赖
- `pre_check_deps.py` 的 stdout 兼容旧格式，但本 skill 应优先消费 `pre_check_<pkgname>.json` 的结构化结果
- 依赖递归的默认路径应使用 `aggregate_dependency_requests.py` → `plan_dependency_layer.py` → `execute_dependency_layer.py`，而不是直接对原始 `pending[]` 手工逐个调用单节点执行器
- `/pkg-introduce` 成功不等于“本次新引入成功”；必须读取 `pkg_introduce_result_<dep>.json` 再决定是否写 `introduced.txt`
