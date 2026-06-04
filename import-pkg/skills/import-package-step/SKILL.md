---
name: import-package-step
description: >
  openEuler 包引入单步执行。由 /loop 动态模式驱动，每次执行一步：读状态 → 按优先级 spawn 一个 agent → 更新状态文件 → ScheduleWakeup。
  不要手动调用，由 /import-package 通过 /loop 触发。
argument-hint: "<session_dir>"
allowed-tools:
  - Bash
  - Read
  - Agent
  - Skill
  - ScheduleWakeup
---

## 输入

```bash
SESSION_DIR="<arguments>"
SUPERVISOR="$CLAUDE_SKILL_DIR/scripts/step_supervisor.py"
```

## 执行步骤

### 1. 读状态，确定下一步 action

```bash
eval "$(python3 "$SUPERVISOR" --session-dir "$SESSION_DIR")"
echo "[step] loop=$LOOP action=$ACTION($TARGET)"
```

### 2. 执行 ACTION

```bash
case "$ACTION" in

evaluate)
  Agent(
    subagent_type="pkg-evaluator",
    prompt=f"pkgname: {TARGET}\nmode: dependency\nconstraint: {CONSTRAINT}\nsession_dir: {SESSION_DIR}"
  )
  eval "$(python3 "$CLAUDE_SKILL_DIR/scripts/read-gate-result.py" \
    --session-dir "$SESSION_DIR" --pkgname "$TARGET")"
  python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
    --update-action evaluate --update-target "$TARGET" \
    --gate-decision "$GATE_DECISION"
  ;;

build_dep)
  Agent(
    subagent_type="pkg-builder",
    prompt=f"pkgname: {TARGET}\nmode: install-dep\nsession_dir: {SESSION_DIR}"
  )
  eval "$(python3 "$CLAUDE_SKILL_DIR/scripts/read-build-result.py" \
    --session-dir "$SESSION_DIR" --pkgname "$TARGET")"
  python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
    --update-action build_dep --update-target "$TARGET" \
    --build-result "$BUILD_STATUS"
  ;;

build_main)
  Agent(
    subagent_type="pkg-builder",
    prompt=f"pkgname: {PKGNAME}\nmode: build\nsession_dir: {SESSION_DIR}"
  )
  eval "$(python3 "$CLAUDE_SKILL_DIR/scripts/read-build-result.py" \
    --session-dir "$SESSION_DIR" --pkgname "$PKGNAME")"
  python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
    --update-action build_main --update-target "$PKGNAME" \
    --build-result "$BUILD_STATUS"
  ;;

rebuild)
  python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
    --update-action critique_retry --update-target "$PKGNAME"
  Agent(
    subagent_type="pkg-builder",
    prompt=f"pkgname: {PKGNAME}\nmode: rebuild\nsession_dir: {SESSION_DIR}"
  )
  eval "$(python3 "$CLAUDE_SKILL_DIR/scripts/read-build-result.py" \
    --session-dir "$SESSION_DIR" --pkgname "$PKGNAME")"
  python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
    --update-action build_main --update-target "$PKGNAME" \
    --build-result "$BUILD_STATUS"
  ;;

critique)
  Agent(
    subagent_type="pkg-reviewer",
    prompt=f"pkgname: {PKGNAME}\nstage: critique\nround: 1\nsession_dir: {SESSION_DIR}"
  )
  ;;

feedback)
  Agent(
    subagent_type="pkg-reviewer",
    prompt=f"pkgname: {PKGNAME}\nstage: feedback\nsession_dir: {SESSION_DIR}"
  )
  ;;

done)
  SESSION_CONTAINER=$(python3 -c "import json; print(json.load(open('$SESSION_DIR/session.json'))['container'])")
  INTRODUCED=$(sort -u "$SESSION_DIR/build_state/introduced.txt" 2>/dev/null | tr '\n' ' ')
  Agent(
    prompt=f"/archive-rpm-sources --pkgs {PKGNAME} {INTRODUCED} --container {SESSION_CONTAINER} --reports-dir {SESSION_DIR}/pkgs/{PKGNAME}"
  )
  docker rm -f "$SESSION_CONTAINER"
  python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
    --update-action done --update-target "$PKGNAME"
  DELAY=""
  ;;

fail)
  SESSION_CONTAINER=$(python3 -c "import json; print(json.load(open('$SESSION_DIR/session.json'))['container'])")
  python3 "$SUPERVISOR" --session-dir "$SESSION_DIR" \
    --update-action fail --update-target "$TARGET"
  Agent(
    prompt=f"/archive-rpm-sources --pkgs {PKGNAME} --container {SESSION_CONTAINER} --reports-dir {SESSION_DIR}/pkgs/{PKGNAME}"
  )
  docker rm -f "$SESSION_CONTAINER"
  echo "[import-package-step] FAILED: $TARGET"
  DELAY=""
  ;;

esac
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
