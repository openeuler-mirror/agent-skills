---
name: import-package-step
description: >
  OpenEuler 包引入单步执行。由 /loop 动态模式驱动，每次执行一步：读状态 → 按优先级 spawn 一个 agent → 更新状态文件 → ScheduleWakeup。
  不要手动调用，由 /import-package 通过 /loop 触发。
argument-hint: "<session_dir>"
allowed-tools:
  - Bash
  - Read
  - Agent
  - Skill
  - ScheduleWakeup
---

你是 OpenEuler 包引入流程的 Supervisor，**每次只执行一步**，然后用 ScheduleWakeup 安排下次唤醒。

## 输入

从参数中读取 `session_dir`：

```bash
SESSION_DIR="<arguments>"
SKILL_DIR=$(python3 -c "
import pathlib, sys
for p in [pathlib.Path('$SESSION_DIR')] + list(pathlib.Path('$SESSION_DIR').parents):
    c = p / '.claude' / 'skills' / 'import-package-step'
    if c.exists():
        print(c); sys.exit(0)
sys.exit(1)
")
SUPERVISOR="$SKILL_DIR/scripts/step_supervisor.py"
```

## 执行步骤

### 1. 读状态，确定下一步 action

```bash
STEP=$(python3 "$SUPERVISOR" --session-dir "$SESSION_DIR")
ACTION=$(echo "$STEP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['action'])")
TARGET=$(echo "$STEP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['target'])")
DELAY=$(echo "$STEP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['delay'] or '')")
PKGNAME=$(echo "$STEP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['pkgname'])")
LOOP=$(echo "$STEP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['loop'])")
echo "[step] loop=$LOOP action=$ACTION($TARGET)"
```

### 2. 执行 ACTION

#### evaluate

```bash
Agent(
  subagent_type="pkg-evaluator",
  prompt=f"""
pkgname: {TARGET}
mode: dependency
session_dir: {SESSION_DIR}

按 pkg-evaluator.md 执行。dep URL 从 dep_registry.json 读取。
  """
)

# 读 gate_result，更新 dep_registry
GATE_DECISION=$(python3 -c "
import json, pathlib
gate = json.loads(pathlib.Path('$SESSION_DIR/pkgs/$TARGET/gate_result_$TARGET.json').read_text())
print(gate.get('result', {}).get('decision', ''))
")
python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
  --update-action evaluate --update-target "$TARGET" \
  --gate-decision "$GATE_DECISION"
```

#### build_dep

```bash
Agent(
  subagent_type="pkg-builder",
  prompt=f"""
pkgname: {TARGET}
mode: install-dep
session_dir: {SESSION_DIR}

按 pkg-builder.md 执行。
  """
)

BUILD_STATUS=$(python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('$SESSION_DIR/pkgs/$TARGET/build_rpm_result.json').read_text())
print(r.get('status', ''))
")
CI_STATUS=$(python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('$SESSION_DIR/pkgs/$TARGET/build_rpm_result.json').read_text())
print(r.get('ci_status', ''))
")
python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
  --update-action build_dep --update-target "$TARGET" \
  --build-result "$BUILD_STATUS" --ci-status "$CI_STATUS"
```

#### build_main

```bash
Agent(
  subagent_type="pkg-builder",
  prompt=f"""
pkgname: {PKGNAME}
mode: build
session_dir: {SESSION_DIR}

按 pkg-builder.md 执行。
  """
)

BUILD_STATUS=$(python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('$SESSION_DIR/pkgs/$PKGNAME/build_rpm_result.json').read_text())
print(r.get('status', ''))
")
CI_STATUS=$(python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('$SESSION_DIR/pkgs/$PKGNAME/build_rpm_result.json').read_text())
print(r.get('ci_status', ''))
")
python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
  --update-action build_main --update-target "$PKGNAME" \
  --build-result "$BUILD_STATUS" --ci-status "$CI_STATUS"
```

#### rebuild

```bash
# 先更新状态（删除旧 critique 文件，递增重试计数）
python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
  --update-action critique_retry --update-target "$PKGNAME"

Agent(
  subagent_type="pkg-builder",
  prompt=f"""
pkgname: {PKGNAME}
mode: rebuild
session_dir: {SESSION_DIR}

按 pkg-builder.md 执行。
  """
)

BUILD_STATUS=$(python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('$SESSION_DIR/pkgs/$PKGNAME/build_rpm_result.json').read_text())
print(r.get('status', ''))
")
CI_STATUS=$(python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('$SESSION_DIR/pkgs/$PKGNAME/build_rpm_result.json').read_text())
print(r.get('ci_status', ''))
")
python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
  --update-action build_main --update-target "$PKGNAME" \
  --build-result "$BUILD_STATUS" --ci-status "$CI_STATUS"
```

#### critique

```bash
Agent(
  subagent_type="pkg-reviewer",
  prompt=f"""
pkgname: {PKGNAME}
stage: critique
round: 1
session_dir: {SESSION_DIR}

按 pkg-reviewer.md 执行。
  """
)
# critique 结果由 reviewer 写入 critique_round1_<pkg>.json，下次 step 读取
```

#### feedback

```bash
Agent(
  subagent_type="pkg-reviewer",
  prompt=f"""
pkgname: {PKGNAME}
stage: feedback
session_dir: {SESSION_DIR}

按 pkg-reviewer.md 执行。
  """
)
# feedback 结果由 reviewer 写入 feedback_<pkg>.json，下次 step 读取
```

#### done

```bash
# 归档
Agent(
  prompt=f"""
cd {SESSION_DIR}
SKILLS_DIR=$(python3 -c "import pathlib,sys; [print(p/'.claude'/'skills') or sys.exit(0) for p in [pathlib.Path('{SESSION_DIR}')]+list(pathlib.Path('{SESSION_DIR}').parents) if (p/'.claude'/'skills').exists()]; sys.exit(1)")
SESSION_CONTAINER=$(python3 -c "import json; print(json.load(open('./session.json'))['container'])")
INTRODUCED=$(sort -u ./build_state/introduced.txt 2>/dev/null | tr '\\n' ' ')
ALL_PKGS="{PKGNAME} ${{INTRODUCED}}"
/archive-rpm-sources --pkgs ${{ALL_PKGS}} --container ${{SESSION_CONTAINER}} --pkg-dir ./pkgs
  """
)

# summary
Agent(
  prompt=f"""
cd {SESSION_DIR}
/review-rpm summary {PKGNAME} \
  --reports-dir ./pkgs/{PKGNAME} \
  --dist-dir ./dist \
  --spec ./pkgs/{PKGNAME}/{PKGNAME}.spec
  """
)

# 清理容器
python3 -c "
import json, subprocess, pathlib
container = json.loads(pathlib.Path('$SESSION_DIR/session.json').read_text())['container']
subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
"

# 标记完成（goal_achieved=True，loop_count+1）
python3 -c "
import json, pathlib
sd = pathlib.Path('$SESSION_DIR')
wf_path = list(sd.glob('workflow_*.json'))[0]
wf = json.loads(wf_path.read_text())
wf['goal_achieved'] = True
wf['loop_count'] = wf.get('loop_count', 0) + 1
wf_path.write_text(json.dumps(wf, indent=2, ensure_ascii=False))
"
DELAY=""  # 停止循环
```

#### fail

```bash
python3 -c "
import json, pathlib
sd = pathlib.Path('$SESSION_DIR')
wf_path = list(sd.glob('workflow_*.json'))[0]
wf = json.loads(wf_path.read_text())
wf['error'] = '$TARGET'
wf['goal_achieved'] = False
wf['loop_count'] = wf.get('loop_count', 0) + 1
wf_path.write_text(json.dumps(wf, indent=2, ensure_ascii=False))
"
echo "[import-package-step] FAILED: $TARGET"
DELAY=""  # 停止循环
```

### 3. 安排下次唤醒或输出结果

```python
if DELAY:
    ScheduleWakeup(
        delaySeconds=int(DELAY),
        prompt=f"/import-package-step {SESSION_DIR}",
        reason=f"{ACTION}({TARGET}) done, next step in {DELAY}s"
    )
    print(f"[step] → next wakeup in {DELAY}s")
else:
    # 读最终状态输出摘要
    python3 -c "
import json, pathlib
sd = pathlib.Path('$SESSION_DIR')
wf = json.loads(list(sd.glob('workflow_*.json'))[0].read_text())
status_str = 'SUCCESS' if wf.get('goal_achieved') else 'FAILED'
built = ' '.join(wf.get('built_pkgs', []))
reused = ' '.join(wf.get('reused_pkgs', []))
print(f\"[{wf['pkgname']}] {status_str} | built: {built} | reused: {reused} | loops: {wf['loop_count']}\")
"
```
