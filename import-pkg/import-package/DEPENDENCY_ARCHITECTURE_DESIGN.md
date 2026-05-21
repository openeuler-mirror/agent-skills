# 依赖版本解析与递归框架优化设计文档

## 1. 背景

当前 `build-rpm -> pkg-introduce -> build-rpm` 已形成可工作的递归依赖引入链路，并且已经补上：

- 结构化依赖预检输出
- 会话级版本锁 `resolved_versions.json`
- 依赖尝试历史 `dependency_attempts.json`
- 依赖执行结果 `dependency_outcomes.json`
- resolver 候选版本生成
- finalize 统一收口
- `retryable_*` / `non_retryable_*` 失败分类
- Phase 1 执行器 `build-rpm/scripts/execute_dependency_resolution.py`

这条方向是正确的，且已经具备最小闭环能力。

但从长期演进、可维护性和复杂依赖场景支持来看，当前架构仍存在：

- 状态分散
- 控制流分散在 skill 文档和脚本之间
- resolver 同时承担 plan 和部分 side effect
- 递归框架仍是“边发现边递归边执行”的深度优先模型

因此需要从两个维度做设计优化：

1. **整体架构优化**：明确 analysis / resolution / execution 分层，收敛状态模型和职责边界。
2. **递归框架优化**：从“发现即递归”升级为“本层先收集与规划，再受控执行”的递归模型。

---

## 2. 当前架构评估

### 2.1 现有优点

当前方案的优点：

- 保留了现有递归引入框架，没有推翻主链路
- 版本选择已前置为独立 resolver 层
- 已引入会话级版本锁，避免同名依赖被重复任意选版本
- 已引入 attempts 历史，避免重复试错
- finalize 已成为统一收口点
- 失败类型已能区分 retryable / non-retryable
- 对 Python 生态已具备不错的可落地性

### 2.2 当前主要问题

#### 问题一：状态分散，缺少单一会话真相源

当前状态文件包括：

- `pre_check_<pkg>.json`
- `pkg_introduce_result_<pkg>.json`
- `resolved_versions.json`
- `dependency_attempts.json`
- `introduced.txt`
- `building.txt`

这些文件各自有明确用途，但整体上缺少一个统一的 session state 视图，导致：

- 调试时需要同时查看多个文件
- 字段之间的职责界限依赖约定维持
- 未来扩展时容易产生语义漂移

#### 问题二：控制流过多地存在于 SKILL.md 中

当前很多关键执行语义体现在 skill 文档的命令块中，例如：

- resolver 如何调用
- candidate 如何轮询
- finalize 如何回灌状态

这对流程说明是好的，但对长期维护不够稳：

- 文档与脚本实现容易漂移
- 关键流程难以作为单一可复用执行单元复用
- 一旦字段或脚本接口变化，需要同步修改多个层面

#### 问题三：resolver 目前既负责决策，也承担部分副作用

当前 `resolve_dependency_versions.py` 已能：

- 生成 candidate 列表
- 根据 finalize 结果更新 lock / attempt 状态

短期上这简化了接线成本，但长期上会导致：

- resolver 的职责不再纯粹
- 难以清晰区分“决策阶段”和“执行回写阶段”
- 不利于未来增强冲突策略或引入更强规划器

#### 问题四：依赖领域对象尚未显式建模

当前系统已经隐含存在以下核心对象：

- dependency request
- resolution plan
- dependency attempt
- dependency outcome

但这些更多体现为 JSON 约定，而不是一套清晰的数据模型，导致：

- 阅读代码时需要靠字段猜语义
- 状态和职责缺少统一命名
- 后续扩展生态支持时不够稳

---

## 3. 当前递归框架评估

### 3.1 当前递归模型本质

当前递归框架本质上是：

> **深度优先（DFS）、边发现边决策、边执行边写状态**

典型流程：

1. 构建 A
2. 发现缺 B
3. 立刻递归处理 B
4. B 构建时发现缺 C
5. 立刻递归处理 C
6. C 完成后回到 B，再回到 A

这个模型在早期实现简单、心智清晰，但在引入：

- 版本约束
- session lock
- fallback candidate
- 多路径同名依赖复用
- 失败分流
- 多生态支持

之后，会逐渐暴露局部最优、决策过晚、执行流过深等问题。

### 3.2 当前递归框架的主要问题

#### 问题一：发现依赖后立即递归，决策过于局部

当前逻辑是：

- 遍历 `pending[]`
- 发现一个依赖就立刻递归

这样会导致：

- 同层缺失依赖没有统一规划
- 同名依赖多路径约束无法先合并再决策
- 会话锁虽然能兜底，但本质上仍是“先到先得”

这意味着：

- 第一个路径先锁定的版本，会影响后续所有路径
- 更适合简单链路，不利于复杂版本冲突场景

#### 问题二：DFS 容易把局部最优当全局最优

DFS 的问题包括：

- 谁先被发现，谁先锁版本
- 某条深链会长期占据执行流
- 深层才暴露的冲突，回溯成本更高

DFS 本身不是错，但如果没有前置 planning，就会放大局部决策的副作用。

#### 问题三：`building.txt` 只能做路径级循环检测

当前 `building.txt` 可以很好地挡住显式循环：

- A -> B -> C -> A

但它不能表达：

- 多路径对同一依赖提出了不可兼容约束
- 同名依赖虽然不是路径级循环，但已经构成 resolution 冲突

也就是说，它能检测“调用栈上的环”，但不能检测“请求图上的冲突”。

#### 问题四：递归对象仍然是“包”，而不是“依赖节点”

当前系统递归的单位仍然是包。

但在版本解析增强后，真正更合适的执行对象应该是：

- 某个依赖名字
- 来自哪些上游请求
- 汇总后的约束是什么
- 这次计划尝试哪些 candidate
- 当前状态是什么

也就是一个更明确的 `DependencyNode`。

---

## 4. 推荐的目标架构

建议采用 **三层架构**：

1. Analysis 层
2. Resolution 层
3. Execution 层

### 4.1 Analysis 层

职责：**发现依赖需求，不负责版本决策**。

输入：

- 源码
- language analyzer
- existing-check

输出：

- 标准化的 `DependencyRequest[]`

建议标准结构：

```json
{
  "name": "pymongo",
  "ecosystem": "python",
  "upstream_url": "https://github.com/mongodb/mongo-python-driver",
  "requirement": ">=4.6,<5",
  "constraint_type": "range",
  "requirement_info": {},
  "version_source": "manifest",
  "requested_by": "motor",
  "category": "runtime"
}
```

原则：

- `pre_check_deps.py` 只负责描述“需要什么”
- 不负责选择最终版本
- 不直接承担 retry / attempts / session lock 控制

### 4.2 Resolution 层

职责：**基于 request 和 session state 生成 resolution plan**。

输入：

- `DependencyRequest`
- session state
- 版本枚举 provider（PyPI / git tags / crates / npm 等）

输出：

- `ResolutionPlan`

示例：

```json
{
  "name": "pymongo",
  "status": "planned",
  "strategy": "reuse_locked_version",
  "locked_version": "4.7.3",
  "candidates": ["4.7.3"],
  "constraints": [],
  "requested_by": ["motor"],
  "reason": "locked version satisfies constraint"
}
```

或冲突场景：

```json
{
  "name": "pymongo",
  "status": "conflict",
  "strategy": "locked_version_conflict",
  "candidates": [],
  "reason": "locked version 4.6.3 does not satisfy >=4.8,<5"
}
```

原则：

- resolver 应尽量保持“纯决策”职责
- 默认输出 plan，而不是在同一阶段承担过多 side effect
- 状态回写可通过单独执行层完成，或以清晰的 apply API 单独承接

### 4.3 Execution 层

职责：**真正执行 candidate loop，并把 finalize 结果回写到 session state**。

建议新增独立执行器，例如：

- `build-rpm/scripts/execute_dependency_resolution.py`

输入：

- 单个 `DependencyRequest`
- `ResolutionPlan`

输出：

- `DependencyOutcome`

执行职责：

1. 调 resolver 生成 plan
2. 如有 conflict，直接阻断
3. 遍历 candidates
4. 每轮：
   - 调 `/pkg-introduce --version <candidate> --mode dependency`
   - 调 finalize
   - 记录 attempt
   - 成功则写 resolution lock
   - retryable 切下一候选
   - non-retryable 立即终止
5. 输出最终 outcome

示例：

```json
{
  "name": "pymongo",
  "status": "resolved",
  "action": "built_new",
  "requested_version": "4.7.3",
  "version": "4.7.3",
  "attempts": 2,
  "failure_history": [
    {
      "version": "4.8.0",
      "failure_type": "retryable_version_conflict"
    }
  ]
}
```

---

## 5. 推荐的递归框架优化

## 5.1 核心思路

不是完全去掉递归，而是把当前“发现即递归”的模型，优化为：

> **本层先收集依赖，再统一规划，再受控递归执行**

也就是：

- 保留整体递归框架
- 在递归前加一层 planning
- 让递归对象逐步从“包”过渡到“依赖节点”

---

## 5.2 推荐的新递归模型

### 当前模型

- 发现一个 `pending`
- 立刻递归

### 推荐模型

#### Phase A：本层依赖发现与汇总

对当前包：

1. 跑 `pre_check_deps.py`
2. 收集全部 `pending[]`
3. 按依赖名分组
4. 合并同名依赖的约束
5. 为每个依赖生成统一 request

当前实现方向：
- 使用 `build-rpm/scripts/aggregate_dependency_requests.py` 将 `pre_check_<pkg>.json` 中同层 `pending[]` 聚合成标准化 request 列表
- 为后续 execution layer 提供 `DependencyRequest[]` 输入，而不是直接按原始发现顺序递归

#### Phase B：本层依赖规划

对汇总后的每个 request：

1. 查询 session lock
2. 检查 attempts 历史
3. 生成 resolution plan
4. 如有冲突，尽早阻断

#### Phase C：本层依赖执行

对每个 resolution plan：

1. candidate loop
2. `/pkg-introduce --mode dependency`
3. finalize
4. 写 attempts / lock
5. 决定 success / retry / blocked

#### Phase D：回到当前包继续构建

全部依赖处理完成后，再返回当前包继续构建。

---

## 5.3 推荐的执行语义

建议将一轮依赖处理理解为：

```text
当前包构建失败 -> 预检拿到 pending[]
    -> 汇总为 dependency requests
    -> 合并同名约束
    -> 为每个 request 生成 resolution plan
    -> 顺序执行每个 dependency node
        -> candidate 1
        -> finalize
        -> success / retry / blocked
    -> 全部依赖完成后，回到当前包继续构建
```

当前实现进展：
- `aggregate_dependency_requests.py` 已会输出合并后的主 `constraint`
- 同时保留 `all_constraints` 以便后续更强的约束求交与冲突分析
- 对明显不兼容的 exact / 基础 range 情况，会在进入执行器前尽早阻断

这仍保留递归感，但明显优于“遇到一个缺失就立即递归”。

---

## 6. 推荐的数据模型

建议明确四类核心对象。

### 6.1 DependencyRequest

表示“某个上游包对某个依赖提出的需求”。

关键字段：

- `name`
- `ecosystem`
- `upstream_url`
- `requirement`
- `constraint_type`
- `requirement_info`
- `version_source`
- `requested_by`
- `category`
- `all_constraints`
- `node_state`
- `conflict`
- `conflict_reason`

### 6.2 ResolutionPlan

表示“对于当前 request，会话中准备尝试哪些版本”。

关键字段：

- `name`
- `status`：planned / conflict
- `strategy`
- `locked_version`
- `candidates`
- `constraints`
- `requested_by`
- `reason`

### 6.3 DependencyAttempt

表示“某个候选版本的一次尝试”。

关键字段：

- `version`
- `result`
- `reason`
- `failure_type`

### 6.4 DependencyOutcome

表示“某个依赖节点最终处理结果”。

关键字段：

- `status`: resolved / blocked
- `action`
- `requested_version`
- `version`
- `attempts`
- `failure_history`

---

## 7. 状态模型改进建议

### 7.1 短期方案

短期继续保留当前文件：

- `resolved_versions.json`
- `dependency_attempts.json`
- `building.txt`
- `introduced.txt`

因为它们已经可工作，且改造成本低。

### 7.2 长期方案

建议逐步收敛到统一的 `session_state.json`。

示例：

```json
{
  "dependencies": {
    "pymongo": {
      "resolution": {
        "status": "resolved",
        "version": "4.7.3",
        "requested_version": "4.7.3",
        "source": "range_latest_compatible",
        "resolution_type": "range"
      },
      "constraints": [
        {
          "from": "motor",
          "ecosystem": "python",
          "constraint": ">=4.6,<5"
        }
      ],
      "attempts": [
        {
          "version": "4.8.0",
          "result": "retryable_version_conflict",
          "reason": "..."
        },
        {
          "version": "4.7.3",
          "result": "success",
          "reason": "build succeeded"
        }
      ]
    }
  },
  "building": ["motor"],
  "introduced": ["pymongo"]
}
```

优点：

- 会话状态一处可见
- 调试更容易
- 未来扩展冲突分析、图视图、执行状态机更方便

---

## 8. 字段设计建议

### 8.1 `constraint_type` 建议细化

当前：

- `exact`
- `range`
- `unbounded`
- `unknown`

建议未来扩展为：

- `exact`
- `bounded_range`
- `lower_bounded`
- `upper_bounded`
- `unbounded`
- `unknown`

原因：

- `>=1.2` 与 `>=1.2,<2` 的回退策略不应完全相同
- 纯下界约束更容易选到“过新导致次级冲突”的版本

### 8.2 拆清三个 source/strategy 概念

建议区分：

#### `version_source`
表示约束来源：

- `lockfile`
- `manifest`
- `solver`
- `manual`
- `unknown`

#### `resolution_strategy`
表示这次怎么选版本：

- `reuse_locked_version`
- `exact_version`
- `range_latest_compatible`
- `stable_candidates`
- `manual_override`

#### `result_source`
表示最终成功来源：

- `built_new`
- `upgraded_user_repo`
- `reused_user_repo`
- `reused_official`

这样可以避免字段含义重叠。

### 8.3 失败分类可进一步拆维度

当前枚举方式已够用，但长期可考虑拆成：

- `retryability`: retryable / non_retryable
- `failure_type`: version_conflict / repo_blocked / license_blocked / build_failure / source_missing

这样会比把两者揉进一个枚举更容易扩展。

---

## 9. 递归框架优化方案分级

### 方案 A：最小改进版（推荐优先做）

保留递归框架，只增强进入递归前的 planning。

做法：

1. 当前包先收集全部 `pending[]`
2. 按依赖名分组
3. 合并约束
4. 统一跑 resolver
5. 再开始递归执行

当前已落地的第一步：
- `build-rpm/scripts/aggregate_dependency_requests.py` 已能完成同层 `pending[]` 聚合
- 聚合结果已补充 `node_state` 与基础 `conflict` / `conflict_reason` 字段
- `execute_dependency_resolution.py` 会把节点状态推进到 `planned` / `attempting` / `resolved` / `blocked`

优点：

- 改动最小
- 不破坏当前主流程
- 能显著缓解“先到先锁”问题

### 方案 B：半调度器版

保留递归，但引入显式 work queue。

做法：

- `discovered queue`
- `planned queue`
- 当前包预检后把 pending 放进 queue
- queue 中的每个依赖先 planning，再执行
- 新发现依赖继续回填 queue

优点：

- 可观测性更好
- 更容易批处理和调试
- 更适合作为中期演化形态

### 方案 C：dependency graph executor

长期最优，但不建议当前阶段一步到位。

做法：

- 构建 dependency request graph
- 统一 resolution planning
- 按 graph 执行

优点：

- 最适合复杂冲突与多生态场景
- 最适合长期演进

缺点：

- 成本高
- 会显著改变现有心智模型

---

## 10. 推荐的近期重构优先级

### P1：补一个真正的 execution script

建议新增：

- `build-rpm/scripts/execute_dependency_resolution.py`

职责：

- 接单个 `DependencyRequest`
- 调 resolver
- 跑 candidate loop
- 调 `/pkg-introduce --mode dependency`
- 调 finalize
- 写 attempts / locks
- 输出统一 outcome

这是当前最值得补的一层。

### P2：把状态访问彻底收敛到状态模块

继续推进 `dependency_resolution_state.py`，成为唯一状态访问入口，避免各脚本自己拼 JSON。

建议统一提供：

- `load_session_state`
- `record_attempt`
- `record_resolution`
- `append_constraint`
- `mark_building`
- `mark_introduced`

当前实现进展：
- 已补 `append_constraint()`
- 已补 `mark_building()` / `clear_building()`
- 已补 `mark_introduced()`
- 已补 `record_dependency_outcome()`
- 已补 `build_session_snapshot()` / `dump_session_snapshot()`
- 已补 `load_session_state()` / `write_session_snapshot()`
- `show-session` / `dump-session` 已分别对应实时读取与导出快照
- `finalize_dependency_result.py` 已切到状态层 API 清理 building / 标记 introduced
- `execute_dependency_resolution.py` 已复用状态层 API 标记 building 并在执行后刷新 session snapshot

### P3：明确数据结构与状态机

至少在代码注释和设计文档层明确：

- `DependencyRequest`
- `ResolutionPlan`
- `DependencyAttempt`
- `DependencyOutcome`

当前实现进展：
- `aggregate_dependency_requests.py` 已明确作为 `DependencyRequest` 生成入口
- `resolve_dependency_versions.py` 已明确作为 `ResolutionPlan` 生成层
- `execute_dependency_resolution.py` 已明确作为 `DependencyOutcome` 生成层
- `DependencyAttempt` 已在执行器 `attempts` / `failure_history` 中统一表示

并为依赖节点增加状态：

- `discovered`
- `planned`
- `attempting`
- `resolved`
- `blocked`

### P4：减少 skill 文档承担执行编排职责

理想状态：

- skill 文档负责流程语义、边界、输入输出
- 具体命令编排尽量沉到脚本执行器中

---

## 11. 结论

### 11.1 对当前整体架构的结论

当前架构已经是一个正确方向上的、可工作的雏形：

- 方向正确
- 落地性强
- 阶段性闭环已形成

但从长期看，还需要：

- execution layer
- session state 收敛
- 数据模型显式化
- 递归前 planning 增强

### 11.2 对当前递归框架的结论

当前递归框架不是错误，而是：

> 对简单链路很好，对复杂依赖关系开始偏硬。

最值得优化的不是“去掉递归”，而是：

1. **本层先收集依赖，再统一规划**
2. **从纯调用栈递归升级为带状态的依赖节点执行**
3. **逐步从 DFS-only 过渡到 planning-first 的受控递归**

### 11.3 推荐的设计方向

综合考虑成本与收益，建议：

- **短期**：采用方案 A，保留当前递归框架，但对每层 `pending[]` 先 collect + merge + resolve，再执行。
- **中期**：引入 execution script 和 dependency node 状态机。
- **长期**：逐步演进到 session-state 驱动、provider 插件化、graph-aware 的依赖执行器架构。

当前 provider 化进展：
- 已新增 `build-rpm/scripts/providers/pypi.py`
- `resolve_dependency_versions.py` 已改为通过 PyPI provider 获取 Python 候选版本
- 已新增 `build-rpm/scripts/resolution_runtime.py`，将 finalize 后的状态变更从 resolver 中抽离到 runtime 侧
- 已新增 `build-rpm/scripts/pkg_introduce_bridge.py`，将 `/pkg-introduce --mode dependency` 的 Claude CLI bridge 封装为独立适配层
- 后续可按相同边界继续扩展 `git_tags` / `crates` / `npm` provider

---

## 12. 一句话总结

当前系统已经从“简单递归构建器”进化成“具备版本感知能力的递归引入框架”，下一阶段最重要的设计任务是：

> **把版本决策、执行回路和会话状态真正收敛成一套清晰的依赖解析子系统。**
