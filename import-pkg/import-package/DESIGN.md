# import-package 设计文档

> openEuler 生态包引入系统 — 从 PR 解析到 RPM 归档的完整自动化流程

---

## 目录

**第一部分：架构设计**
1. [系统背景与设计原则](#1-系统背景与设计原则)
2. [系统组件与整体流程](#2-系统组件与整体流程)

**第二部分：流程详解**

3. [import-package：PR 解析与调度](#3-import-package-pr-解析与调度)
4. [pkg-introduce：单包引入流程](#4-pkg-introduce-单包引入流程)
5. [build-rpm：RPM 构建与依赖递归](#5-build-rpm-rpm-构建与依赖递归)
6. [archive-rpm-sources：归档与发布](#6-archive-rpm-sources-归档与发布)
7. [review-rpm：事后反馈与报告](#7-review-rpm-事后反馈与报告)

**第三部分：规范参考**

8. [各语言 Spec 规范](#8-各语言-spec-规范)
9. [License 分类处理规则](#9-license-分类处理规则)
10. [特殊场景处理指南](#10-特殊场景处理指南)

**第四部分：AI 仓库运作方案**

11. [AI 仓库合入与长期运作](#11-ai-仓库合入与长期运作)

**附录：架构决策记录**

A. [关键架构决策](#a-关键架构决策)

---

# 第一部分：架构设计

## 1. 系统背景与设计原则

### 1.1 问题定义

openEuler 社区通过 PR 流程引入生态包：开发者向 community 仓库提交含 `upstream:` 字段的 YAML 文件，经 TC 审核后建仓，由维护者手工编写 spec、编译 RPM 并归档。这个人工流程存在三个核心痛点：

| 痛点 | 具体表现 |
|------|---------|
| **依赖链深且复杂** | 一个包可能有几十个传递依赖，Python/Go/C/Java 的依赖体系规则各异 |
| **合规检查依赖经验** | License 判断、仓库活跃度评估需要经验，人工容易遗漏 |
| **spec 编写繁琐** | 不同语言的 spec 模板和宏用法差异大，容易写错 |

**import-package 的目标：** 将从 PR 解析到 RPM 归档的全过程自动化。核心理念是"先跑起来"——用 rpmbuild 循环驱动依赖发现，遇到缺包立即递归引入，直到整条依赖链构建完成。

---

### 1.2 主包与依赖包的区分

流程中处理的包分为两类，决定了部分行为差异：

| 维度 | 主包 | 依赖包 |
|------|------|--------|
| **来源** | PR yaml 中 `upstream:` 字段直接引用 | rpmbuild 失败时发现的缺失依赖 |
| **调用标志** | `pkg-introduce <pkg> <url>`（默认 `--mode top-level`） | `pkg-introduce <pkg> <url> --mode dependency --depth N` |
| **构建后操作** | 不安装到容器，由顶层统一归档 | 安装到容器供后续依赖使用 |
| **归档** | 顶层 `pkg-introduce` 负责调用 `archive-rpm-sources` | 由顶层统一归档，自身不触发归档 |
| **引入报告** | 生成完整报告（所有章节） | 生成独立完整报告（标注包类型=依赖包） |

---

### 1.3 核心设计原则

| 原则 | 做法 |
|------|------|
| **构建驱动依赖发现** | 不做全量静态依赖锁定；`pre_check_deps.py` 先做结构化预检减少轮次，`rpmbuild` 循环继续兜底 |
| **统一递归链路** | 顶层包和依赖包共用同一条 `pkg-introduce → build-rpm` 链路，通过 `--mode` 区分 |
| **版本感知优先复用** | 构建前先做权威 existing-check，优先复用官方源或 AI RPM 源中已满足版本的包 |
| **容器隔离** | 每次顶层引入必须重建容器；依赖包复用同一容器；权威查询与真实构建都在容器内完成 |
| **状态文件防护** | `building.txt` 检测循环依赖；`introduced.txt` 只记录实际 `built_new / upgraded_user_repo` 的依赖 |
| **合规前置** | 上游仓库合规和 License 检查在下载源码后立即执行，不合规立即阻断 |
| **经验驱动 spec 生成** | 每次构建自动注入同语言历史经验（`lessons/<lang>.json`），降低重复错误 |
| **事后反馈闭环** | 构建完成后触发 `review-rpm`：提炼经验写入 lessons，生成引入报告；顶层包和依赖包均生成独立报告 |
| **归档原子性** | 主包构建完成后统一归档主包和本次实际新引入依赖，不归档纯复用依赖 |

---

## 2. 系统组件与整体流程

### 2.1 核心组件

| 组件 | 类型 | 职责 |
|------|------|------|
| **import-package** | Skill（用户入口） | 解析 PR 链接，提取 upstream URL，调度 `pkg-introduce` |
| **pkg-introduce** | Skill | 合规检查、源码下载、语言/版本识别、容器准备、权威 existing-check、构建调度、归档、召唤 review-rpm |
| **build-rpm** | Skill | spec 生成（注入历史经验）、rpmlint 校验、依赖预检、依赖层规划与递归引入、rpmbuild 循环、运行时依赖验证 |
| **archive-rpm-sources** | Skill | spec + tarball + RPM 推送到 GitHub，维护 yum 软件源，CI 门禁（repoclosure + dnf builddep） |
| **review-rpm** | Agent（只读写） | 构建完成后复盘：提炼经验写入 lessons，生成结构化 feedback 和汇总引入报告 |
| **setup-build-env** | Skill | 创建 openEuler 容器，安装语言工具链 |
| **resolve-rpm-conflicts** | Skill | 安装 RPM 时遇到冲突，自动解决后重试 |
| **oe-build-env 容器** | 编译环境 | 隔离的 openEuler 系统，执行权威 repo 查询、`dnf builddep`、`rpmbuild`、依赖安装 |
| **GitHub RPM repo** | 存储 | 存放所有归档的 spec、tarball、RPM 及 repodata（yum 软件源） |

### 2.2 调用链结构

```
import-package
  └─ pkg-introduce <main> <url>                         # 顶层，depth=0
       ├─ check_repo.py                                  # 上游合规检查
       ├─ download_source.py                             # 源码下载
       ├─ check_license.py                               # License 检查
       ├─ extract_version.py / 语言识别                  # 语言与版本确定
       ├─ setup-build-env                                # 顶层重建容器
       ├─ check_existing_package.py                      # 权威 existing-check
       └─ build-rpm <main> <lang> <url> <ver>            # depth=0
            ├─ lessons/<lang>.json 注入历史经验
            ├─ 生成 spec → rpmlint 校验
            ├─ pre_check_deps.py 结构化预检
            │    └─ aggregate → plan → execute 依赖层
            │         └─ pkg-introduce <dep-A> --mode dependency --depth 1
            │              └─ build-rpm ... --install --depth 1
            │                   └─ pkg-introduce <dep-B> ... --depth 2
            │                        └─ ...（最深 depth=5）
            ├─ rpmbuild 循环（最多 10 轮）
            │    └─ 发现缺包 → 修 spec 或递归引入
            └─ 运行时依赖验证 / 依赖包安装
       ├─ archive-rpm-sources --pkgs <main> <dep-A> <dep-B> ...
       ├─ Agent(review-rpm, "feedback ...")              # 分析质量，写 lessons
       └─ Agent(review-rpm, "summary ...")               # 生成引入报告
```

> **调用约束：** `/skill-name` 写法 → 用 Skill 工具（内联执行）；`Agent(review-rpm, ...)` 写法 → 用 Agent 工具（独立 context）；`python3 script.py` → 用 Bash 工具。三者不可混用。

### 2.3 状态文件

整个引入会话使用以下状态文件，位于 `./build_state/`：

| 文件 | 内容 | 作用 |
|------|------|------|
| `building.txt` | 当前调用链上正在处理的包名（每行一个） | 循环依赖检测 |
| `introduced.txt` | 本次会话实际 `built_new / upgraded_user_repo` 成功的依赖包名 | 去重 + 顶层归档时确定归档列表 |
| `resolved_versions.json` | 本次会话已锁定的依赖版本（`pkgname → version`） | 防止同名依赖被重复选不同版本 |
| `dependency_attempts.json` | 各依赖包的候选版本尝试历史 | 避免重复试错同一失败版本 |

顶层 `pkg-introduce` 在第一步初始化所有状态文件（清空）；依赖包调用复用已有文件。

### 2.4 整体流程概览

```
PR 链接输入
     │
     ▼
┌──────────────────────────────────────────────┐
│ import-package                                │
│  解析 PR → 提取 upstream URL → pkgname        │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ pkg-introduce（主包，mode=top-level）          │
│  ① 状态文件初始化                             │
│  ② 上游仓库合规检查                           │  ← 阻断线 1
│  ③ download_source.py 下载源码                │
│  ④ License 合规检查                           │  ← 阻断线 2
│  ⑤ 语言检测 + 版本号提取                      │
│  ⑥ 重建编译容器（setup-build-env）            │
│  ⑦ 权威 existing-check                        │
│  ⑧ 调用 build-rpm                            │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ build-rpm（depth=0）                          │
│  ① 读取 lessons 历史经验                      │
│  ② 生成 spec 文件（注入历史经验）              │
│  ③ rpmlint 规范校验（先修复，再决定阻断）      │
│  ④ pre_check_deps.py 预检 → 依赖层规划与引入  │  ← 阻断线 3
│  ⑤ rpmbuild 循环（最多 10 轮）                │
│     - dnf builddep 失败 → 修包名或递归引入    │
│     - %build 失败 → 补 BuildRequires 或引入   │
│     - %files/pyproject 问题 → 调整 spec       │
│  ⑥ 运行时依赖验证                             │
│  ⑦ 依赖包模式下安装 RPM                       │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ archive-rpm-sources                           │
│  归档 spec + tarball + RPM → GitHub           │
│  CI 门禁：repoclosure + dnf builddep           │
│  createrepo_c 重建 yum 索引                   │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ review-rpm feedback（Agent）                  │
│  分析 spec 质量 + 构建过程                    │
│  提炼新经验写入 lessons/<lang>.json           │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ review-rpm summary（Agent）                   │
│  生成 <pkgname>_introduction_report.md        │
│  顶层包和依赖包均生成独立报告                  │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
                  引入报告输出
```

### 2.5 三条阻断线

| 阻断线 | 位置 | 触发条件 | 处理方式 |
|--------|------|---------|---------|
| **第一条**（上游合规） | pkg-introduce 第②步 | 仓库超 5 年不活跃 / 不在平台白名单 | 终止流程，输出合规报告 |
| **第二条**（License） | pkg-introduce 第④步 | no_commercial / unknown / unlicensed | 终止流程，输出 License 报告 |
| **第三条**（构建失败） | build-rpm 第④⑤步 | 循环依赖 / 超出最大深度 / 超出最大轮次 / 同一错误无法修复 | 终止当前包构建，仍触发 review-rpm feedback |

---

# 第二部分：流程详解

## 3. import-package：PR 解析与调度

### 3.1 PR 链接解析

支持平台：`atomgit.com`、`gitcode.com`（同一 Gitea 平台，API 兼容）

```bash
cd ${CLAUDE_SKILL_DIR}
mkdir -p reports

# 示例：https://atomgit.com/shuyingbanbao/community/pull/1
python3 scripts/extract_pr_info.py <owner> <repo> <pr_number> --no-diff
```

从 `pr_<N>_info.json` 提取变更的 `.yaml` 文件中的 `upstream:` 字段。

> **注意：** `files[].patch` 是 dict（含 `diff` 子字段），不是字符串。Python 解析时须先取 `patch['diff']`。

**pkgname 规则：取 upstream URL 的最后一段路径（仓库名）**，如 `https://github.com/pallets/flask` → `flask`

### 3.2 调用 pkg-introduce

```
/pkg-introduce <pkgname> <upstream_url>
```

### 3.3 整合输出引入报告

```
========================================
OpenEuler 生态包引入报告
========================================
PR 地址       : <pr_url>
软件包名      : <pkgname>
上游地址      : <upstream_url>
语言类型      : <lang>
编译状态      : ✓ 成功

【合规检查】
仓库平台      : <platform>（最后更新：<date>）
License       : <spdx_id>（<category>）

BuildRequires（来自 OpenEuler 源）:
  - <pkg1>

BuildRequires（通过 pkg-introduce 新打）:
  - <dep1>（depth=1）

新增 RPM 包（已归档）:
  - <pkgname>
  - <dep1>
========================================
```

---

## 4. pkg-introduce：单包引入流程

### 参数

| 参数 | 说明 |
|------|------|
| `<pkgname>` | 包名 |
| `<upstream_url>` | 上游地址 |
| `--version <ver>` | 可选。指定期望版本；下载阶段按此选 git tag/branch，后续以真实版本为权威 |
| `--mode top-level\|dependency` | 默认 `top-level`；`--mode dependency` 表示依赖包：构建后安装，不触发归档 |
| `--depth N` | 当前递归深度，由调用方传入 |

### 第一步：状态文件初始化（仅顶层）

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py init \
  --pkg <pkgname> --mode top-level \
  --build-state-dir ./build_state \
  --reports-dir ./reports \
  --sources-dir ./sources
```

依赖包调用（`--mode dependency`）跳过此步，复用顶层状态文件。

### 第二步：上游仓库合规检查

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py repo-check \
  --pkg <pkgname> --upstream-url <upstream_url> --reports-dir ./reports
```

检查项：仓库平台白名单、近 5 年活跃度。`blocking: true` 则立即阻断。

### 第三步：下载上游源码

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py download \
  --pkg <pkgname> --upstream-url <upstream_url> \
  [--version <expected_version>] \
  --sources-dir ./sources --reports-dir ./reports
```

- 未指定 `--version`：自动检测版本号；含不稳定后缀（`SNAPSHOT`/`beta`/`rc` 等）则阻断并列出可用 tag
- 指定 `--version`：按版本解析 git ref（`v<ver>` → `<ver>` → `<pkg>-<ver>` → 分支等顺序尝试）
- 所有候选 ref 均不存在：失败，不静默回退到默认分支

### 第四步：License 合规检查

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py license-check \
  --pkg <pkgname> --source-dir ./sources/<pkgname> --reports-dir ./reports
```

三段式决策：规则直接通过 → 规则直接阻断 → AI 兜底判断（输出置信度与证据片段）。详见[第 9 节](#9-license-分类处理规则)。

### 第五步：识别语言与版本

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py detect \
  --pkg <pkgname> --source-dir ./sources/<pkgname> \
  [--expected-version <ver>] --reports-dir ./reports
```

真实版本必须与 `--version` 一致（允许忽略 `v` 前缀），不一致则阻断。

### 第六步：准备容器并注入双源

- 顶层调用：直接调用 `/setup-build-env ./sources/<pkgname> <lang>` 重建容器，同时注入官方源 + AI RPM 源
- 依赖包调用：复用已有 `oe-build-env`；若容器不存在则阻断

### 第七步：权威 existing-check

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_pkg_introduce_flow.py existing-check \
  --pkg <pkgname> --lang <lang> --version <version> \
  --container oe-build-env --reports-dir ./reports
```

| 决策 | 语义 | 后续动作 |
|------|------|---------|
| `reuse_official` | 官方源已有满足版本 | 立即成功返回，不构建不归档 |
| `reuse_user_repo` | AI 源已有满足版本 | 立即成功返回，不构建不归档 |
| `block_official_older` | 官方源有同名旧版 | 阻断，需人工决策 |
| `upgrade_user_repo` | AI 源有同名旧版 | 进入构建分支，最终 `action=upgraded_user_repo` |
| `introduce_new` | 两源均无 | 进入构建分支，最终 `action=built_new` |

### 第八步：调用 build-rpm

先检查是否有历史经验文件，再调用完整构建：

```bash
LESSONS_FILE="${CLAUDE_SKILL_DIR}/../build-rpm/lessons/<lang>.json"
LESSONS_ARG=""
[ -f "$LESSONS_FILE" ] && LESSONS_ARG="--lessons $LESSONS_FILE"
```

```
/build-rpm <pkgname> <lang> <upstream_url> <version> [--install] [--depth N] $LESSONS_ARG
```

- 顶层包：不传 `--install`
- 依赖包：传 `--install`

### 第九步：归档（仅顶层且实际发生构建时）

```bash
INTRODUCED=$(sort -u ./build_state/introduced.txt | tr '\n' ' ')
ALL_PKGS="<pkgname> ${INTRODUCED}"
/archive-rpm-sources --pkgs ${ALL_PKGS} --reports-dir ./reports
```

`introduced.txt` 只包含实际 built/upgraded 的依赖，纯复用包不进入归档集合。

### 第十步：事后反馈（build-rpm 有执行时触发，顶层和依赖包均适用）

```
Agent(review-rpm, "feedback <pkgname>
  --lang <lang>
  --spec /tmp/<pkgname>.spec
  --rpmlint /tmp/<pkgname>_rpmlint.txt
  --build-result ./reports/build_rpm_result_<pkgname>.json
  --build-log /tmp/<pkgname>_build.log
  --lessons ${LESSONS_FILE}
  --reports-dir ./reports")
```

约束：只读指定输入文件，只写 `feedback_<pkgname>.json` 和 lessons 文件，不执行任何命令。

### 第十一步：生成引入报告（所有调用，顶层和依赖包均生成）

- **复用场景**（`reused_*`）：Bash 直接写简短 summary
- **构建/失败场景**：
```
Agent(review-rpm, "summary <pkgname>
  --reports-dir ./reports --dist-dir ./dist
  --spec /tmp/<pkgname>.spec")
```

报告包含：基本信息、上游合规、License、版本决策、模块说明（多模块项目）、RPM 产物说明（含每个包用途）、构建摘要、spec 要点、依赖包情况、归档状态、质量反馈、新增经验、结论。

依赖包报告标注包类型（依赖包）和被引入原因（由哪个顶层包触发）。

---

## 5. build-rpm：RPM 构建与依赖递归

### 保护常量

```
MAX_DEPTH  = 5    # 最大递归深度
MAX_ROUNDS = 10   # 单包最大编译轮次
```

### 权威执行入口

构建的确定性阶段（预检完成、依赖就绪后）由 orchestrator 脚本统一执行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_build_rpm_flow.py \
  <pkgname> <lang> <upstream_url> <version> \
  [--install] [--depth N] \
  --container oe-build-env \
  --source-dir ./sources/<pkgname> \
  --spec /tmp/<pkgname>.spec \
  --build-state-dir ./build_state \
  --reports-dir ./reports \
  -o ./reports/build_rpm_result_<pkgname>.json
```

返回码语义：
- `rc=0`：构建成功
- `rc=1`：构建失败（non_retryable 或预检错误）
- `rc=2`：发现 pending 依赖（`status=pending_deps`），skill 应读取 `dependency_resolution.pending_deps` 并逐个调用 `/pkg-introduce`，完成后重新调用 orchestrator

**spec 生成、spec 修订、复杂失败诊断、依赖递归仍由 skill 负责，不在脚本内。**

### 第一步：读取构建说明

```bash
docker exec oe-build-env bash -c "
  cat /build/source/BUILD.md 2>/dev/null \
  || cat /build/source/BUILDING.md 2>/dev/null \
  || head -200 /build/source/README.md 2>/dev/null"

date "+%a %b %d %Y"
```

### 第二步：生成 spec（注入历史经验）

每次构建必须重新生成 spec，不复用旧文件。先读对应语言规范文件，再读 lessons：

- `java`：Read `spec-rules-java.md`
- `python`：Read `spec-rules-python.md`
- `nodejs`：Read `spec-rules-nodejs.md`
- `c/cpp`：Read `spec-rules-cpp.md`

若传入 `--lessons <path>`，在读完规范后、生成 spec 前，筛选 `applies_to` 相关条目注入推理：

```
已知此类包的历史经验（来自 lessons/<lang>.json）：
- [applies_to: maven/multi-module] 含 integrationtest 子模块必须显式 %pom_disable_module...
```

### 第三步：rpmlint 校验

```bash
docker cp /tmp/<pkgname>.spec oe-build-env:/tmp/<pkgname>.spec
docker exec oe-build-env bash -c "rpmlint /tmp/<pkgname>.spec 2>&1" \
  > /tmp/<pkgname>_rpmlint.txt
```

- `W:`：记录，不阻断
- `E:`：先按提示修 spec 并重跑；同类问题连续无法解决才最终阻断

### 第四步：预检依赖（pre_check_deps.py）

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/pre_check_deps.py \
  <pkgname> <lang> ./sources/<pkgname> \
  --container oe-build-env -o ./reports/pre_check_<pkgname>.json
```

输出：`resolved[]`（可复用）、`pending[]`（需引入）、`blocked[]`（有问题）。

`pending[]` 中每个依赖附带 `constraint`、`upstream_url`（本步骤修正可疑 URL，必要时 AI 兜底）。

### 第五步：依赖层规划与引入

不再逐个直接调用 `/pkg-introduce`，先做同层汇总与规划：

```bash
# 聚合同层依赖请求
python3 ${CLAUDE_SKILL_DIR}/scripts/aggregate_dependency_requests.py \
  --summary-json ./reports/pre_check_<pkgname>.json \
  --requested-by <pkgname> \
  -o ./reports/dependency_requests_<pkgname>.json

# 生成执行计划（含版本候选、冲突检测）
python3 ${CLAUDE_SKILL_DIR}/scripts/plan_dependency_layer.py \
  --requests-json ./reports/dependency_requests_<pkgname>.json \
  --build-state-dir ./build_state \
  --requested-by <pkgname> \
  -o ./reports/dependency_layer_plan_<pkgname>.json
```

- `PLAN_RC=0`：无预阻断，继续执行 `planned[]` 中的依赖
- `PLAN_RC=2`：存在 `blocked[]`，终止并输出阻断原因

对 `planned[]` 中每个依赖，按 candidates 顺序尝试：

```bash
echo "<dep_name>" >> ./build_state/building.txt
/pkg-introduce <dep_name> <upstream_url> --version <candidate> --mode dependency --depth <N+1>
python3 ${CLAUDE_SKILL_DIR}/scripts/finalize_dependency_result.py <dep_name> \
  --build-state-dir ./build_state --reports-dir ./reports --json
```

`finalize` 的 `action` 决定下一步：
- `built_new / upgraded_user_repo / reused_*`：成功，继续下一个依赖
- `blocked` 且 `failure_type` 含 `retryable`：尝试下一个 candidate
- `blocked` 且候选耗尽：终止

### 第六步：rpmbuild 循环（最多 MAX_ROUNDS 轮）

```bash
docker exec oe-build-env bash -c "dnf builddep -y ~/rpmbuild/SPECS/<pkgname>.spec 2>&1"
docker exec oe-build-env bash -c "rpmbuild -ba ~/rpmbuild/SPECS/<pkgname>.spec 2>&1" \
  | tee /tmp/<pkgname>_build.log
```

**失败处理：**

| 情况 | 处理方式 |
|------|---------|
| dnf builddep 找不到某包 | `dnf search` 找到正确名则修 spec；否则进入依赖引入流程 |
| %build 缺头文件/.so | `dnf provides` 定位；找到则补 BuildRequires；否则进入依赖引入流程 |
| %install/%files 问题 | 查 BUILDROOT；Python 包优先 `%pyproject_build + %pyproject_install`，宏不兼容则回退手工 `%files` |
| 同一错误连续两轮未解决 | 终止，上报错误 |

### 第七步：运行时依赖验证

rpmbuild 成功后，检查 RPM `Requires` 在 openEuler 源中是否可满足；缺失的进入依赖引入流程。

### 第八步：安装 RPM（仅 `--install`）

```bash
docker exec oe-build-env bash -c "
  rpm -ivh ~/rpmbuild/RPMS/aarch64/<pkgname>-*.rpm 2>&1 \
  || rpm -ivh ~/rpmbuild/RPMS/noarch/<pkgname>-*.rpm 2>&1"
```

安装冲突时调用 `/resolve-rpm-conflicts <pkgname> <rpm_path>`。

### 5.1 当前已验证能力与边界

**已验证：**
- C++ / Python / Go / Java（Maven 多模块）/ ROS Humble 包的完整构建流程
- Python 包：hatchling / setuptools / flit 三类后端；`%pyproject_save_files` 不兼容时回退手工 `%files`
- Java 包：xmvn 离线构建；自举型注解处理器三阶段构建（core 预构建 → javac -proc:only 预生成 → 全量 %mvn_build）
- ROS 包：`__requires_exclude_from ^/opt/ros/.*` + 显式 package-name Requires；禁用 LTO + BTI flags
- 历史经验（lessons）注入：已验证降低 Java/Maven 重复错误
- 子包 RPM 匹配：`%package -n <name>` 声明的子包自动纳入归档范围

**边界：**
- 真实运行时版本缺口（上游要求版本高于 openEuler 仓内版本）需人工决策
- 循环构建依赖（A 构建依赖 B，B 构建依赖 A）需 Bootstrap spec 或人工预处理

---

## 6. archive-rpm-sources：归档与发布

### 触发方式

由顶层 `pkg-introduce` 第九步统一调用：

```
/archive-rpm-sources --pkgs <main_pkg> <dep1> <dep2> ... --reports-dir ./reports
```

### 仓库结构

```
repo-aitest/
├── <pkgname>/          ← spec + 源码 tarball（可重现构建）
│   ├── <pkgname>.spec
│   └── <pkgname>-<version>.tar.gz
├── dist/               ← 编译好的 RPM + repodata（yum 软件源）
│   ├── <pkgname>-<version>-1.noarch.rpm
│   ├── repodata/
│   └── repo-aitest.repo
└── README.md
```

### 执行流程

1. 拉取/初始化 GitHub 仓库
2. 从容器拷出 spec、source tarball → `<pkg>/`
3. 从容器拷出所有 RPM（含 `%package -n <name>` 子包）→ `dist/`
4. 升级处理：扫描同名旧版本，按变更类型决策（安全升级直接替换；major 升级或有反向依赖则创建 compat 包或报错）
5. `createrepo_c --update dist/` 重建索引
6. **CI 门禁**（提交前）：
   - `repoclosure`：验证新 RPM 的运行时依赖在仓内可满足
   - `dnf builddep`：验证新包的 spec BuildRequires 可满足
   - 任一失败则回滚工作区，不提交
7. 一次 `git commit + push`
8. 回写 `pkg_introduce_result_<pkgname>.json` 中的 `archived=true`

### 子包 RPM 匹配

归档脚本自动读取 spec 中的 `%package -n <name>` 声明，将子包名加入匹配范围。例如 `tools-gem` spec 声明了 `gem-api` 和 `gem-processor` 子包，这两个 RPM 文件会自动被识别并归档，无需单独传包名。

---

## 7. review-rpm：事后反馈与报告

`review-rpm` 是一个**只读写**的 Agent（不执行任何 shell 命令），在 `pkg-introduce` 构建完成后被召唤，分两个阶段：

### stage=feedback

**触发条件：** build-rpm 实际执行过（无论成功或失败），顶层包和依赖包均触发。

**输入：** spec、rpmlint 输出、build_rpm_result json、原始构建日志、现有 lessons 文件

**产出：**
- `./reports/feedback_<pkgname>.json`：spec 质量发现（`spec_findings`）、构建过程发现（`process_findings`）、新提炼经验（`new_lessons`）、verdict（good/acceptable/needs_improvement/failed）
- 追加写入 `build-rpm/lessons/<lang>.json`：经验去重，每语言保留最近 30 条

**lessons 格式：**
```json
{
  "lang": "java",
  "lessons": [
    {
      "pkgname": "mapstruct", "version": "1.6.3", "date": "2026-05-19",
      "applies_to": "java/annotation-processor",
      "finding": "xmvn 离线模式下 annotationProcessorPaths 无法下载处理器，需三阶段构建预生成",
      "suggestion": "processor 模块先 xmvn 构建 core → javac -proc:only 预生成 → %mvn_build -proc:none"
    }
  ]
}
```

### stage=summary

**触发条件：** 所有调用（顶层包和依赖包），无论 action 是什么。

**产出：** `./reports/<pkgname>_introduction_report.md`

报告章节：
1. 基本信息（包类型、版本、语言、日期）
2. 上游合规
3. License
4. 版本决策
5. **模块说明**（多模块项目）：每个模块的状态（已构建/不安装/已禁用）和禁用原因
6. **RPM 产物说明**：每个 RPM 文件的类型和用途
7. 构建过程摘要
8. spec 要点
9. 依赖包情况（链接到各依赖包的独立报告）
10. 归档状态
11. 质量反馈
12. 新增经验
13. 结论

---

# 第三部分：规范参考

## 8. 各语言 Spec 规范

各语言的完整 spec 规范见独立文件：

| 语言 | 规范文件 |
|------|---------|
| Java/Maven | `build-rpm/spec-rules-java.md` |
| Python | `build-rpm/spec-rules-python.md` |
| Node.js | `build-rpm/spec-rules-nodejs.md` |
| C/C++ | `build-rpm/spec-rules-cpp.md` |
| Go / Rust / Ruby | 沿用通用模板，见下方简要说明 |

### 通用注意事项

- `%changelog` 日期用 `date "+%a %b %d %Y"` 获取，星期须与实际日期匹配
- `Release` 字段统一使用 `1%{?dist}`
- `BuildArch: noarch` 适用于纯 Java、纯 Python 等平台无关包
- 不修改源码，只通过调整 spec 和引入依赖包解决问题

### Go 包关键点

```bash
export CGO_ENABLED=0
export GOFLAGS=-buildvcs=false
export GOPROXY=https://goproxy.cn,direct
%gobuild -o %{name} .
```

### C/C++ 库关键点

- 必须同时生成主包（`.so.*`）和 devel 包（`.h` + `.so` 软链接 + `.pc`）
- 使用 `%cmake -DCMAKE_BUILD_TYPE=Release` + `%cmake_build` + `%cmake_install`

### ROS Humble 包关键点

- 使用 `__requires_exclude_from ^/opt/ros/.*` + 显式 package-name Requires
- `%build` 节必须禁用 LTO 和 BTI flags
- 归档前须清理 DNF ci-local 缓存

---

## 9. License 分类处理规则

`check_license.py` 输出 `category` 和 `blocking` 字段：

| 分类 | 代表许可证 | blocking | 处理 |
|------|-----------|---------|------|
| `permissive` | MIT / Apache-2.0 / BSD / ISC | false | 直接通过 |
| `weak_copyleft` | LGPL / MPL | false | 通过，报告记录 |
| `strong_copyleft` | GPL-2.0 / GPL-3.0 / AGPL | false | 通过，spec License 字段须正确填写 |
| `no_commercial` | CC-BY-NC / BUSL / SSPL | **true** | 阻断 |
| `unknown` | 无法识别 | **true** | AI 兜底判断；仍无法确认则阻断 |
| `unlicensed` | 无任何声明 | **true** | 阻断 |

> GPL 类强 Copyleft 许可证**不阻断**引入流程，只要 spec 中 `License` 字段正确填写即可。传染性评估由社区 PR 审核流程负责，自动化工具只确保 spec 信息准确。

---

## 10. 特殊场景处理指南

### 10.1 依赖链深度爆炸

```
❌ 已达最大递归深度 5，无法继续引入 <dep_pkgname>
   依赖链：main → dep-A → dep-B → dep-C → dep-D → dep-E
```

**处理：** 手动预先引入深层依赖包（`/pkg-introduce <dep>`），再重新触发主包引入。

### 10.2 循环构建依赖

```
❌ 检测到循环依赖：<dep_pkgname> 正在当前调用链上构建
```

**处理：**
1. 使用 Bootstrap spec（精简版，不含循环依赖的功能）
2. 先手动构建其中一个包的最小版本安装到容器，再引入另一个

### 10.3 Java 自举型注解处理器

当 processor 模块在编译时依赖自身生成的类（如 mapstruct）：

1. **阶段一**：`xmvn --projects parent,build-config,core package`（仅构建 core）
2. **阶段二**：`javac -proc:only` 预生成 Gem 辅助类，复制回源码树
3. **阶段三**：`%mvn_build -f -- -proc:none`（全量构建，禁用注解处理器再次运行）

关键：必须同时移除 `annotationProcessorPaths` **并**注入 `-proc:none`，缺一不可。

### 10.4 版本冲突阻断

遇到依赖约束冲突时，**禁止自行降级或切换版本**，必须立即阻断：
- 指出冲突原因（哪个依赖、官方版本是多少、要求是多少）
- 给出可选方案（先引入更新的依赖包，或改用兼容的目标包版本）
- 由用户决策后重新发起引入

---

# 第四部分：AI 仓库运作方案

## 11. AI 仓库合入与长期运作

### 11.1 现状

当前 AI 仓库（`repo-aitest`）状态：

| 维度 | 现状 |
|------|------|
| RPM 数量 | ~150 个，覆盖 C++/Java/Python/Go/ROS Humble 等 |
| 构建验证 | 每个包通过 repoclosure + dnf builddep CI 门禁 |
| 附带产物 | 引入报告（合规/License/构建过程/spec 要点/经验教训）、spec 文件、源码 tarball |
| 与官方关系 | 独立运行，未与 src-openeuler 对接 |

### 11.2 方案一：PR 驱动渐进合入

**核心思路：** AI 仓库作为"构建验证暂存区"，每个包通过正式 PR 流程向官方 src-openeuler 申请合入，合入后从 AI 仓库下架。

**合入流程：**

```
AI 仓库（已构建验证）
    │
    ├─ 自动生成 PR 草稿
    │   - spec 文件 + 引入报告 → openeuler-bot 格式
    │   - PR body 填充：合规结论、License、rpmlint 结果、构建日志摘要
    │
    ├─ TC/维护者审核
    │   - 审 spec 质量（rpmlint、依赖声明、License 字段）
    │   - 上游活跃度（已在引入报告里，无需重复查）
    │   - 依赖链完整性
    │
    ├─ CI 自动验证
    │   - 以 AI 仓库 RPM 做 repoclosure
    │   - dnf builddep 验证
    │
    └─ 合入 src-openeuler → AI 仓库标记 archived=official
```

**需要开发的能力：** PR 草稿自动生成脚本（从引入报告 + spec 生成 openeuler-bot 格式的 PR）

**优点：** 符合 openEuler 治理规范，每个包有人工背书，合入后维护责任转给社区。  
**缺点：** 合入周期长（等待 TC 审核）；依赖链中的中间依赖包需逐个提 PR，批量引入时工作量大。

**适合场景：** 成熟稳定包、已在社区有关注度的包。

---

### 11.3 方案二：AI 仓库作为长期独立扩展源

**核心思路：** AI 仓库不并入官方，作为持续维护的"社区扩展源"长期运行，类似 EPEL 之于 RHEL。

**分层结构：**

```
openEuler 官方源          ← 基础层（TC 治理，稳定优先）
    +
AI 扩展源（repo-aitest）  ← 扩展层（自动维护，覆盖官方源没有的包）
```

**版本追踪与自动更新：**

```
定期轮询 upstream（每周/每月）
    │
    ├─ 发现新版本 → 触发 pkg-introduce 重新构建
    ├─ 构建成功 → 自动更新 AI 仓库，repoclosure 验证通过后推送
    └─ 构建失败 → 生成告警报告，等待人工介入
```

**与官方源的冲突策略：**
- AI 源只包含官方源没有的包（`introduce_new`）
- `upgrade_user_repo` 场景（官方源有旧版）：单独维护，不自动覆盖官方包

**需要开发的能力：** upstream 版本轮询脚本、自动重建触发机制、告警通知

**优点：** 不依赖 TC 审核，迭代快；新包 24h 内可用；适合长尾生态包。  
**缺点：** 长期维护成本高（上游废弃、依赖链破坏需持续跟踪）；用户对 AI 生成 RPM 的信任度存疑。

**适合场景：** 长尾生态包、快速迭代的新兴语言/框架包、实验性需求。

---

### 11.4 方案三：混合模式（推荐）

**核心思路：** AI 仓库持续运行作为实验源，定期批量向官方提交经过观察期的包。

**两条并行轨道：**

```
轨道 A：AI 实验源（快速通道）
─────────────────────────────────────────────
新包请求 → 构建验证（CI 门禁）→ 24h 内发布到 AI yum 源
用户可立即安装（明确标注"AI 构建，实验性"）
累积 30 天无问题（repoclosure 持续通过）→ 进入批量合入候选队列

轨道 B：官方合入（质量通道）
─────────────────────────────────────────────
每月从 AI 仓库筛选稳定包
→ 批量生成 PR（每个包附引入报告）
→ TC 审核（引入报告大幅降低 review 成本）
→ 合入 src-openeuler → AI 仓库标记 archived=official，停止维护
```

**批量合入筛选条件：**

| 条件 | 说明 |
|------|------|
| 在 AI 仓库存活 ≥ 30 天 | 有基本稳定性观察期 |
| repoclosure 持续通过 | 未被后续包引入的依赖更新破坏 |
| 上游近 1 年有提交 | 非废弃项目 |
| License 明确（非 unknown） | 无需人工额外确认 |
| 有完整引入报告 | 方便 TC 快速 review |
| rpmlint 零 E 错误 | spec 基本合规 |

**月度批量合入节奏：**

```
每月 1 日
    │
    ├─ 扫描 AI 仓库，筛选符合条件的包（通过上述 6 条）
    ├─ 自动生成 PR 批次（每 PR 包含 spec + 引入报告摘要）
    ├─ 通知维护者/TC review
    └─ 合入的包从 AI 仓库 dist/ 下架（official 源接管）
         未合入的包继续保留，等下个月再评估
```

**需要开发的能力：**
- 包稳定性追踪（记录每个包在 AI 仓库的存活时间和 CI 状态）
- 批量 PR 生成脚本（引入报告 → PR 格式转换）
- 合入状态追踪（`archived=official` 标记）

**优点：** 兼顾速度和质量；AI 仓库的引入报告直接作为 PR 审核材料，降低 TC review 负担；不打断现有自动化流程；长期演进路径清晰。

**缺点：** 需要额外开发批量 PR 生成和稳定性追踪能力（估计 2-3 周开发量）。

---

### 11.5 方案对比

| 维度 | 方案一（PR 驱动） | 方案二（长期独立源） | 方案三（混合，推荐） |
|------|-------------------|---------------------|----------------------|
| 合入 openEuler 官方 | 是，逐包 | 否 | 是，每月批量 |
| 上线速度 | 慢（等审核） | 快（自动） | 两者兼顾 |
| 长期维护成本 | 低（合入后官方接管） | 高（持续跟踪所有包） | 中 |
| 用户信任度 | 高（官方背书） | 低（AI 生成无背书） | 中高（有观察期 + TC 审核） |
| TC review 效率 | 低（逐包手动） | N/A | 高（引入报告 + 批量） |
| 对现有流程改动 | 小（加 PR 生成脚本） | 最小（加版本轮询） | 中（加 PR 生成 + 稳定性追踪） |
| 适合阶段 | 成熟包、小批量 | 快速实验、长尾包 | 规模化运营 |

### 11.6 建议路线

- **短期（当前）：** 维持方案二，保持 AI 仓库持续运行，积累包数量（目标 500+）和稳定性数据。
- **中期（3 个月内）：** 切换到方案三，开发批量 PR 生成和稳定性追踪能力，每月向官方提交一批。
- **长期：** 随合入包数量增加，逐步减少 AI 仓库中的"已合入包"维护负担，AI 仓库聚焦在"官方源还没有"的长尾包和快速迭代包。

---

# 附录：架构决策记录

## A. 关键架构决策

### A.1 构建驱动 vs. 静态分析

**问题：** 如何发现一个包的所有依赖？

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **静态分析前置** | 解析 go.mod / pyproject.toml / CMakeLists.txt，提前生成完整依赖列表 | 能提前发现所有依赖 | 各语言分析逻辑差异大，容易遗漏条件依赖 |
| **构建驱动发现**（当前实现） | rpmbuild 失败时才发现缺包，递归引入 | 精确，只引入真正需要的包 | 可能需要多轮编译才能收敛 |

**决策：** 采用**构建驱动 + 预检辅助**的混合策略。`pre_check_deps.py` 做一次预检减少轮次，rpmbuild 循环负责兜底。

### A.2 递归引入 vs. 同层规划后执行

**问题：** 依赖包应该发现一个引入一个，还是同层汇总后规划再执行？

**演进：** 早期采用"发现即递归"的深度优先模型；升级为"同层先收集与规划（aggregate → plan），再受控执行"。

**当前实现：** `aggregate_dependency_requests.py` + `plan_dependency_layer.py` + 按 planned[] 逐个执行。

优势：版本约束可在规划阶段提前检测冲突；多个同名依赖请求可合并；blocked[] 在执行前即可发现。

### A.3 顶层统一归档 vs. 逐包立即归档

**决策：** 采用**顶层统一归档**（pkg-introduce 第九步）。

原因：归档是 Git 推送操作，频繁推送性能差；若某个后续包构建失败，已归档的中间包难以回滚；统一归档保证原子性。

### A.4 GPL 许可证处理

**当前实现：** GPL 类不阻断，只要 spec 中 `License` 字段正确填写即可。传染性评估逻辑复杂，且 openEuler 生态包的 License 管理最终依赖社区 PR 审核，自动化工具只需确保 spec 信息准确。

### A.5 Skill 工具 vs. Agent 工具调用

**问题：** SKILL.md 中 `/skill-name` 写法容易被误解为 Agent 调用。

**决策：** 在每个被调用 skill 的 SKILL.md 顶部加一行调用约束声明：

```markdown
> **调用方式：Skill 工具（`/skill-name`）。禁止通过 Agent 工具或 Bash 直接调用。**
```

调用规则：`/skill-name args` → Skill 工具；`Agent(name, ...)` → Agent 工具（仅 SKILL.md 显式写出时）；`python3 script.py` → Bash 工具。

**局限：** 这是提示层面的约束（70-90% 遵从率），无法 100% 保证。社区研究表明 PreToolUse hooks 能提供确定性执行，但目前 Claude Code 尚无"强制走 skill 入口"的产品级机制。

---

*文档版本：v4.0 | 2026-05-19*
