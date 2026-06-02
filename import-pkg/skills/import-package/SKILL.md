---
name: import-package
description: OpenEuler 包引入入口。文件状态机 + Supervisor Loop 驱动，通过 spawn agent 隔离上下文。支持单包和批量引入（--batch）。
argument-hint: "<pkgname> <upstream_url> [--version <ver>] | --batch <list_file>"
allowed-tools:
  - Bash
  - Read
  - Agent
  - Skill
  - Skill
---

你是 OpenEuler 包引入流程的 lead（Supervisor）。

## 核心原则

1. **文件是唯一状态来源** — 所有决策基于读文件，不信任 agent 返回值
2. **通过 spawn agent 调用所有 skill** — 保护 lead 上下文不膨胀
3. **Supervisor Loop 驱动** — 读状态 → 找阻塞点 → 派发最小行动 → 重复
4. **无 team 无消息传递** — Agent 前台 spawn（同步等待），完成即退出

## Agent 定义位置

```
AGENTS_DIR=/tmp/agent-team-test/.claude/agents/pkg-introduce/
  pkg-evaluator.md   ← check + gate 合二为一，输出 gate_result
  pkg-builder.md     ← build-rpm skill 封装，输出 build_rpm_result
  pkg-reviewer.md    ← review-rpm skill 封装，输出 review 报告
```

## 状态文件

```
session_dir/
  workflow_<pkgname>.json   ← 整体目标状态（lead 维护）
  dep_registry.json         ← 所有依赖状态（lead + builder 共同写入）
  pkgs/*/
    gate_result_<pkg>.json  ← evaluator 输出
    build_rpm_result.json   ← builder 输出
    critique_*.json         ← reviewer 输出
```

### workflow_<pkgname>.json

```json
{
  "pkgname": "yellhorn-mcp",
  "goal": "build_success",
  "loop_count": 0,
  "max_loops": 20,
  "built_pkgs": [],
  "reused_pkgs": [],
  "error": null
}
```

### dep_registry.json

每个依赖的状态流转：

```
pending_evaluate
  ├─→ reused          ← evaluator 决策 reuse_official/reuse_user_repo（官方源已有满足版本）
  └─→ evaluate_done   ← evaluator 决策 introduce_new/upgrade_user_repo（需新引入）
           ├─→ build_done    ← builder 构建成功且 CI pass
           └─→ build_failed  ← builder 构建失败
```

`reused` 和 `build_done` 对 lead 语义等价——该依赖已就绪，不需要再处理。

```json
{
  "mcp": {
    "url": "https://github.com/modelcontextprotocol/python-sdk",
    "constraint": ">=1.0",
    "required_by": "yellhorn-mcp",
    "depth": 1,
    "status": "pending_evaluate | reused | evaluate_done | build_done | build_failed"
  }
}
```

## 入口判断

```bash
ARGS="<arguments>"
if echo "$ARGS" | grep -q "^--batch "; then
  LIST_FILE=$(echo "$ARGS" | sed 's/^--batch //')
  # 进入 § 0 批量模式
else
  PKGNAME=$(echo "$ARGS" | awk '{print $1}')
  UPSTREAM_URL=$(echo "$ARGS" | awk '{print $2}')
  VERSION=$(echo "$ARGS" | grep -oP '(?<=--version )\S+' || echo "")
  # 进入 § 1
fi
```

## § 0  批量模式

```bash
mapfile -t LINES < <(grep -v '^\s*#' "$LIST_FILE" | grep -v '^\s*$')
TOTAL=${#LINES[@]}
```

对列表中每个包串行执行 § 1 → § 6：

```
for i, line in enumerate(LINES):
    pkgname, upstream_url = line.split()[0], line.split()[1]
    version = 从 line 提取 --version，无则为空
    执行 § 1 → § 6
    记录 success/failed
```

所有包完成后输出汇总。

## § 1  Session 初始化

```bash
SKILLS_DIR=$(python3 -c "
import pathlib, sys
for p in [pathlib.Path.cwd()] + list(pathlib.Path.cwd().parents):
    c = p / '.claude' / 'skills'
    if c.exists():
        print(c); sys.exit(0)
sys.exit(1)
") || { echo "ERROR: .claude/skills not found"; exit 1; }

SESSION_DIR=$(bash "$SKILLS_DIR/init-session.sh" <pkgname> <upstream_url>) \
  || { echo "ERROR: init-session failed"; exit 1; }
cd "$SESSION_DIR"

python3 -c "
import json
s = json.load(open('./session.json'))
s['pkgname'] = '<pkgname>'
s['upstream_url'] = '<upstream_url>'
s['version'] = '<version>'
json.dump(s, open('./session.json', 'w'), indent=2, ensure_ascii=False)
"

python3 $SKILLS_DIR/archive-rpm-sources/scripts/init_archive_repo.py \
  --session-json ./session.json \
  || { echo "ERROR: init_archive_repo failed"; exit 1; }

SESSION_CONTAINER=$(python3 -c "import json; print(json.load(open('./session.json'))['container'])")
REPO_LOCAL=$(python3 -c "import json; print(json.load(open('./session.json'))['repo_local'])")
```

初始化 workflow（已存在则读取断点继续）：

```bash
python3 -c "
import json, pathlib
p = pathlib.Path('./workflow_<pkgname>.json')
if not p.exists():
    p.write_text(json.dumps({
        'pkgname': '<pkgname>', 'goal': 'build_success',
        'loop_count': 0, 'max_loops': 20,
        'built_pkgs': [], 'reused_pkgs': [], 'error': None
    }, indent=2, ensure_ascii=False))
    print('[workflow] initialized')
else:
    wf = json.loads(p.read_text())
    print('[workflow] resumed, loop_count:', wf['loop_count'])
"
```

## § 2  主包 evaluate（首次进入时执行一次）

若 `./pkgs/<pkgname>/gate_result_<pkgname>.json` 已存在，跳过此步。

spawn pkg-evaluator（前台同步）：

```python
Agent(
  subagent_type="pkg-evaluator",
  prompt="""
pkgname: <pkgname>
mode: top-level
session_dir: <session_dir>

按 /tmp/agent-team-test/.claude/agents/pkg-introduce/pkg-evaluator.md 执行。
  """,
  run_in_background=False
)
```

读 `gate_result_<pkgname>.json` 验证：
- `overall_status != done` → 记录失败，进入 § 5（归档 failed）→ § 6
- `decision == reuse_*` → 记录 reused_pkgs，跳过构建，进入 § 4（review skipped）→ § 5
- `decision == block_official_older` → 记录失败，进入 § 5 → § 6
- `decision ∈ {introduce_new, upgrade_user_repo}` → 进入 § 3

## § 3  启动 Supervisor Loop

初始化完成后，通过 `/loop` 动态模式启动单步执行循环：

```python
# 启动 /loop 动态模式，/import-package-step 每次只执行一步
# 每步完成后由 ScheduleWakeup 安排下次唤醒，直到目标达成或失败
Skill(
  skill="loop",
  args=f"/import-package-step {SESSION_DIR}"
)
```

`/import-package-step` 负责：
- 读状态文件判断当前阶段
- 按优先级 spawn 一个 agent（evaluator/builder/reviewer）
- 更新状态文件
- 调用 `ScheduleWakeup` 安排下次唤醒（目标达成时停止）

归档、summary、清理均在 `/import-package-step` 的 `done` 分支中执行。

## § 4  归档、summary、清理

**由 `/import-package-step` 的 `done` 分支自动执行**，无需在此处理。

`/import-package-step` 完成后会输出：
```
[pkgname] SUCCESS | built: pkg1 pkg2 | reused: pkg3 | loops: N
```
