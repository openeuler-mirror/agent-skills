---
name: archive-rpm-sources
description: 将容器内编译好的 RPM spec 和 source tarball 归档到 git 仓库，确保构建可复现。由 pkg-introduce 或 build-rpm 流程调用。
argument-hint: "--pkgs <pkg1> [pkg2...] [--container <name>] [--reports-dir <dir>]"
allowed-tools:
  - Bash
  - Read
---

> **调用方式：Skill 工具（`/archive-rpm-sources`）。禁止通过 Agent 工具或 Bash 直接调用。**

你是 RPM 源码仓库归档专家。负责将容器内生成的 spec、source tarball 和 RPM 归档到仓库，并维护可直接使用的 yum 软件源。同时将每个包的引入报告归档到 `reports/` 目录，方便后续查看。

- 容器名必须从 `./session.json` 的 `container` 字段读取，**禁止使用任何默认容器名**
- 若传入 `--container <name>` 参数，以参数值为准（向后兼容）

## 职责

- 检查归档仓配置
- 从容器拷出 spec、source tarball、RPM
- 更新本地归档仓与 `dist/` 软件源
- 处理同名包升级冲突
- 归档引入报告到 `reports/success/` 或 `reports/failed/`
- 提交并推送归档结果
- 归档成功后回写 `pkg_introduce_result_<pkgname>.json` 中的 `archived=true`（若传入 `--reports-dir`）

## 前置条件

`${CLAUDE_SKILL_DIR}/config.json` 已配置 GitHub token、远程仓库与本地目录。

## 主流程

### 1. 检查配置

```bash
cat ${CLAUDE_SKILL_DIR}/config.json
```

### 2. 执行归档

**归档前先检查 review_summary，未完成则自动补齐：**

```bash
SESSION_CONTAINER=$(python3 -c "import json; print(json.load(open('./session.json'))['container'])")
SESSION_TMP=$(python3 -c "import json; print(json.load(open('./session.json'))['tmp_dir'])")

ENSURE_RESULT=$(python3 ${CLAUDE_SKILL_DIR}/scripts/ensure_review_summary.py \
  --pkgs <pkg1> [pkg2...] \
  --reports-dir ./reports \
  --session-tmp ${SESSION_TMP} \
  --dist-dir ./dist)
```

读取 `ENSURE_RESULT`（JSON），对 `needs_summary` 列表中的每个包调用：

```
/review-rpm summary <pkgname> \
  --reports-dir ./reports \
  --dist-dir ./dist \
  --spec ${SESSION_TMP}/<pkgname>.spec \
  [--round-history ./reports/round_history_<pkgname>.json]
```

每个包的 `/review-rpm summary` 完成后，调用 `ensure_review_summary.py --mark-done` 标记步骤：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/ensure_review_summary.py \
  --pkgs <pkgname> \
  --reports-dir ./reports \
  --mark-done
```

然后执行归档：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/publish_rpm.py \
  --pkgs <pkg1> [pkg2...] \
  --container ${SESSION_CONTAINER} \
  [--reports-dir <dir>]
```

执行后应完成：
- 初始化或拉取 GitHub 仓库
- 从容器拷出 spec、source tarball 到 `<pkg>/`
- 从容器拷出 RPM 到 `dist/`
- 移除同名同架构旧版本
- 更新 `dist/` 软件源元数据
- 归档引入报告到 `reports/success/` 或 `reports/failed/`
- 提交并推送

### 3. 确认结果

```bash
git -C <local_dir> log --oneline -3
```

## 报告归档规则

所有引入报告统一归档到仓库 `reports/` 目录，按包名+版本+日期命名子目录：

```
reports/
├── success/    # action ∈ {built_new, upgraded_user_repo, reused_*}
│   └── <pkgname>-<version>-<YYYYMMDD>/
│       ├── <pkgname>_introduction_report.md   ← 汇总报告（必须存在）
│       ├── pkg_introduce_result_<pkgname>.json
│       ├── check_result_<pkgname>.json
│       ├── gate_result_<pkgname>.json
│       ├── build_rpm_result_<pkgname>.json
│       ├── import_issues.log
│       └── pkg_introduce_result_<dep>.json    ← 每个依赖包各一份
└── failed/     # action = blocked
    └── <pkgname>-<version>-<YYYYMMDD>/
        └── （同上）
```

- 版本从 `pkg_introduce_result_<pkgname>.json` 读取，无法获取时用 `unknown`
- **归档前两项检查（任一不通过则跳过，不阻断主流程）：**
  1. `<pkgname>_introduction_report.md` 须存在且为 `review-rpm summary` 生成的结构化报告
  2. `steps_<pkgname>.json` 中 `review_summary` 须为 `done` 或 `skipped`

## RPM 归档规则

- **步骤清单检查**：若传入 `--reports-dir`，归档前检查 `steps_<pkgname>.json`：
  - `build` 须为 `done` 或 `skipped`；未完成则跳过该包的 RPM 归档并输出 `[WARN]`
  - `ci_gate` 须为 `done` 或 `skipped`；未完成则**阻断归档**并输出 `[ERROR]`（CI 门禁由 builder 阶段2 负责，归档阶段不补跑）
  - `review_summary` 须为 `done` 或 `skipped`；未完成则**不阻断，而是自动补齐**：调用 `/review-rpm summary` 生成报告，完成后标记步骤，再继续归档
- 步骤清单文件不存在时不阻断（向后兼容老流程）

**自动补齐 review_summary 的流程：**

```bash
REVIEW_STATUS=$(python3 -c "
import json, sys
try:
    d = json.load(open('./reports/steps_<pkgname>.json'))
    print(d.get('review_summary', 'pending'))
except: print('pending')
")

if [ "${REVIEW_STATUS}" != "done" ] && [ "${REVIEW_STATUS}" != "skipped" ]; then
  echo "[INFO] <pkgname>: review_summary 未完成，自动补齐..."
  ROUND_HISTORY_ARG=""
  [ -f "./reports/round_history_<pkgname>.json" ] && \
    ROUND_HISTORY_ARG="--round-history ./reports/round_history_<pkgname>.json"
  /review-rpm summary <pkgname> \
    --reports-dir ./reports \
    --dist-dir ./dist \
    --spec ${SESSION_TMP}/<pkgname>.spec \
    ${ROUND_HISTORY_ARG}
  python3 ${CLAUDE_SKILL_DIR}/../pkg-introduce/scripts/run_pkg_introduce_flow.py mark-step \
    --pkg <pkgname> --step review_summary --status done --reports-dir ./reports
fi
```

## 升级冲突规则

| 情况 | 处理 |
|---|---|
| `dist/` 中已有完全相同文件名 | 跳过，无冲突 |
| `name+arch` 相同但版本不同 | 移除旧版本，保留新版本 |
| 新 RPM 文件名无法解析 | 归档失败，回滚工作区，不提交 |
| `createrepo_c` 执行失败 | 归档失败，回滚工作区，不提交 |

## push 冲突处理规则

**禁止使用 `--force` 推送**，无论任何场景（包括空仓库初始化）。

push 失败后的处理流程：

```
push 失败
  → git pull --rebase
  │   ├── rebase 成功 → 随机等待 2~8s → 重试 push（最多 5 次）
  │   └── rebase 失败（真实文件冲突）
  │         → git rebase --abort（恢复干净状态）
  │         → 上报：冲突文件列表 + git status + rebase stderr
  │         → 归档中止，exit 1
  └── 超过最大重试次数
        → 上报：最后一次 push stderr + 本地最近 5 条提交
        → 归档中止，exit 1
```

上报信息包含：
- 冲突文件列表（`git diff --name-only --diff-filter=U`）
- `git status` 完整输出
- push / rebase 的 stderr 原文
- 本地最近提交记录

出现冲突时需人工介入解决，不自动覆盖远端。

## 附录：注意事项

- 始终归档原始源码 tarball，不要归档 `.whl` 之类的二进制分发文件
- 在宿主机预先安装 `createrepo_c`，用于更新 `dist/` 软件源元数据
- 容器名必须从 `./session.json` 读取，禁止硬编码；调用时**必须显式传 `--container ${SESSION_CONTAINER}`**，脚本的默认值 `oe-build-env` 不可靠——容器名不对会导致 spec 找不到、CI 门禁连接到旧容器的过期 OS repo 报 404
- 顶层流程可在顶层包及其本次实际新引入依赖全部构建完成后统一归档
- 确保归档结果同时包含 spec 和 source tarball
- 若 `Source0:` 与包名不一致（ROS 包常见），先确认归档命令能正确解析再归档
- 引入报告归档不阻断主流程，报告文件不存在时仅输出警告
