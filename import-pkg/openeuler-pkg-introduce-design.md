# openEuler RPM 包引入自动化系统设计方案

---

## 一、解决了什么问题

### 1.1 当前痛点

openEuler 生态中，当用户需要一个官方源里没有的 RPM 包时，唯一的路是自己手动走完整个引入流程：

1. **合规审查**：判断上游 License 是否可用，仓库是否还有人维护
2. **语言与版本识别**：确定构建方式（Python / Node / Go / Rust…）和目标版本
3. **传递依赖分析**：列出所有缺失依赖，逐一确认是否需要先构建
4. **spec 文件编写**：按 openEuler 打包规范从头写 spec，容易踩坑
5. **rpmbuild 调试**：反复修 spec 直到构建通过，出错信息晦涩
6. **质量审核**：用 rpmlint 检查，确认符合社区标准后再提交

这个流程对经验依赖极强，熟练者也要花数小时，新人则往往在第一步就卡住。

### 1.2 本系统做了什么

以 AI Agent 为核心，将上述六步全部自动化：用户只需提供上游地址，系统完成评估 → 构建 → 审核 → 归档的全流程，最终将 RPM 自动并入 openeuler-ai-repo，用户直接 `dnf install` 即可使用。

**典型收益：**

| 维度 | 手动流程 | 本系统 |
|------|---------|-------|
| 首次引入耗时 | 2~8 小时 | 10~30 分钟 |
| 依赖分析 | 人工逐条查 | 全自动递归 |
| 重复踩坑 | 无积累 | 经验写入 lessons，后续同语言引入自动规避 |
| 上手门槛 | 需要打包经验 | 只需提 Issue |

### 1.3 支持的语言

| 语言 | 说明 |
|------|------|
| Python | 支持 pyproject / setuptools / hatchling 等构建后端 |
| Node.js | 支持 npm / pnpm monorepo，自动识别并阻断 monorepo 根包 |
| Go | 支持 vendor 模式与直接构建两种路径 |
| Rust | 含 MSRV / nightly 检测，vendor 离线构建 |
| Java | 基于 pom.xml 解析，支持 Maven 多模块 |
| C / C++ | 支持 CMake / Autoconf，自动检测 ROS Humble 包 |

---

## 二、用户如何引入一个包

用户不需要本地安装任何工具，不需要了解 RPM 打包流程，只需在 openeuler-ai-pkg 仓库（GitCode）上提一个 Issue，后续全部由机器人完成。

### 2.1 添加 openeuler-ai-repo 软件源

在提 Issue 之前，先在机器上添加软件源，包引入成功后即可直接安装。

**方法一：一键添加（推荐）**

```bash
dnf config-manager --add-repo https://aipkg.openeuler.org/repo/openeuler-ai-repo.repo
```

**方法二：手动创建 repo 文件**

创建 `/etc/yum.repos.d/openeuler-ai-repo.repo`，写入以下内容：

```ini
[openeuler-ai-repo]
name=openEuler AI Package Repository
baseurl=https://aipkg.openeuler.org/repo/
enabled=1
gpgcheck=0
```

添加后验证：

```bash
dnf repolist | grep openeuler-ai-repo
```

### 2.2 操作步骤

```
Step 1  在 openeuler-ai-pkg 仓库提 Issue，选择「包引入申请」模板

Step 2  填写包信息并提交：包名 + 上游地址 + 可选版本号

Step 3  等待机器人评估（1~3 分钟），查看回复评论
          ├─ 评估通过 → 等待自动构建（10~30 分钟）
          └─ 评估阻断 → 按 Bot 提示修改后重新提 Issue

Step 4  构建成功 → RPM 自动并入 openeuler-ai-repo（aipkg.openeuler.org）
          └─ 直接使用：dnf install --enablerepo=openeuler-ai-repo <pkgname>
```

### 2.3 Issue 申请模板

```markdown
## 包引入申请

**包名**：copy-to-clipboard
**上游地址**：https://github.com/sudodoki/copy-to-clipboard
**版本（可选）**：3.3.1（不填则自动选最新稳定版）
**申请原因**：xxx 项目依赖此包，当前 openEuler 官方源中缺失。

---

### 高级选项（可选，不填则使用仓库默认配置）

**版本冲突处理模式**（dep_conflict_mode）：`compat`
<!--
  block  — 官方源已有旧版本时直接阻断，不引入（最安全，适合对稳定性要求高的场景）
  compat — 以 compat 包名（如 java-foo-2）引入新版本，与官方版本共存；
           仅对 c / cpp / java 生效，Python / Go / Rust / Node.js 不支持 compat，遇冲突自动降为 block
-->
```

> 仓库维护者可在 GitCode 项目设置中预置此模板，用户提 Issue 时自动加载。高级选项中的字段名对应 `config.json` 中的配置项，Bot 解析时按字段名匹配，不填则沿用仓库全局默认值。

### 2.4 全程 Bot 交互示例

**阶段一：收到申请（5 秒内）**

```
✅ 已收到包引入申请

- 包名：copy-to-clipboard
- 上游：https://github.com/sudodoki/copy-to-clipboard
- 任务 ID：#job-a1b2c3
- 状态：评估中...
```

**阶段二：评估结果（1~3 分钟后）**

| 评估结论 | Bot 回复 | 后续动作 |
|----------|---------|---------|
| 官方源已有满足版本 | `ℹ️ 官方源已有 3.3.1，无需引入，可直接使用` | 关闭 Issue |
| License 不合规 | `❌ License BUSL-1.1 属商业限制，无法引入。建议寻找 MIT/Apache 替代包` | Issue 保持 Open |
| 仓库不活跃 | `❌ 仓库超过 5 年未更新，存在维护风险，请确认是否继续` | 等待用户回复 `/confirm` |
| 通过，开始构建 | `📦 评估通过，开始构建。语言：Node.js，版本：4.0.2，发现依赖 1 个（将自动处理），预计 15 分钟` | 进入构建流程 |

**阶段三：构建进度（每步完成后追加，可折叠）**

```html
<details>
<summary>📋 构建进展（第 3 步 / 共约 7 步）</summary>

| 包 | 状态 |
|---|---|
| copy-to-clipboard | 🔨 构建中 |
| toggle-selection  | ✅ 已就绪（官方源复用） |

</details>
```

**阶段四：最终结果**

构建成功：
```
🎉 引入成功！已自动合并入 openeuler-ai-repo。

- RPM：nodejs-copy-to-clipboard-4.0.2-1.noarch.rpm
- 安装命令：`dnf install --enablerepo=openeuler-ai-repo nodejs-copy-to-clipboard`
- 软件源地址：https://aipkg.openeuler.org
- 归档仓：https://gitcode.com/org/rpm-repo/tree/main/dist/
- 同时引入的依赖：toggle-selection（已捆绑，无需单独安装）
```
→ 自动打 `resolved` 标签，关闭 Issue。

构建失败：
```
❌ 引入失败

原因：fluentui 为 monorepo 根包（package.json: private: true），无可发布产物。
建议：改用具体子包，例如：
  - @fluentui/react（npm tarball: https://registry.npmjs.org/...）
  - @fluentui/react-components

请按建议修正后重新提 Issue。
```
→ Issue 保持 Open，等待用户跟进。

### 2.5 整体系统架构（供运维参考）

```
┌─────────────────────────────────────────────────────────────────┐
│  GitCode openeuler-ai-pkg 仓库                                    │
│  ┌──────────────────┐  issues.opened  ┌──────────────────────┐  │
│  │  用户提 Issue     │───────────────▶│  Webhook 网关         │  │
│  │  （包引入申请）   │                └──────────┬───────────┘  │
│  │                  │◀── Bot 评论 ───────────────┘              │
│  └──────────────────┘                                           │
│                          ↑ RPM 归档 PR 自动合并                  │
└──────────────────────────┼──────────────────────────────────────┘
                           │
                ┌──────────┴───────────────────────────┐
                │  调度服务（后端）                      │
                │  Issue解析 → 队列 → Worker → 回写     │
                │  Claude Code CLI /import-package      │
                └──────────────────────────────────────┘
                           │
                ┌──────────▼───────────────────────────┐
                │  RPM 归档仓（Git）                     │
                │  spec + SRPM + RPM                   │
                │  dist/ → createrepo → yum 软件源      │
                └──────────────────────────────────────┘
```

---

## 三、版本冲突的识别与解决

包引入过程中最常见的问题是版本冲突：用户想要的版本与系统已有的包不兼容，或传递依赖之间相互约束。系统通过**评估决策 + 构建前预检 + 版本锁定**三道机制来处理。

### 3.1 评估阶段：先查后建

在真正构建之前，系统会查询容器内（官方源 + openeuler-ai-repo）的已有包版本，给出明确决策，而不是盲目开始构建：

| 决策结果 | 含义 | 处理方式 |
|----------|------|---------|
| `reuse_official` | 官方源已有满足版本，无需新建 | 直接复用，不构建 |
| `reuse_user_repo` | openeuler-ai-repo 已有满足版本 | 直接复用，不构建 |
| `introduce_new` | 全新包，官方和 openeuler-ai-repo 都没有 | 进入构建流程 |
| `upgrade_user_repo` | openeuler-ai-repo 有旧版本，需要升级 | 构建新版本，覆盖旧版 |
| `block_official_older` | 官方源版本比请求版本更新，引入旧版会造成降级冲突 | **阻断**，通知用户使用官方源版本 |

最后一种情况是最重要的冲突场景：用户要 1.2.0，但官方源已经有了 1.3.0——如果强行引入，会造成版本倒退并破坏依赖关系，系统直接阻断并给出说明。

### 3.2 构建前预检：依赖版本解析

评估通过后，在正式构建之前，系统会先对目标包的所有依赖做一次批量预检（`pre_check_deps.py`），找出缺失依赖并锁定版本：

```
precheck 流程：
  读取语言依赖清单（requirements.txt / go.mod / Cargo.toml / pom.xml / package.json）
    │
    ▼
  批量查询 RPM 包名（官方源 + openeuler-ai-repo）
    │
    ├─ 已有且版本满足 → resolved[]，直接复用
    ├─ 有包但版本不满足 → 记录版本约束，加入待构建队列
    ├─ 包不存在 → 补全 upstream URL → 加入待构建队列
    └─ URL 无法确认 → blocked[]，等待人工介入或 AI 兜底
```

**AI 辅助的 URL 兜底：** 当依赖的上游地址缺失或可疑时（指向 `/issues`、`/releases`、PyPI 项目主页等非源码地址），触发 AI 判断：AI 会给出可信源码仓根地址 + 证据片段 + 置信度，高置信度则继续，低置信度则加入 `blocked[]` 等待人工确认。

### 3.3 版本锁定与冲突回溯

所有已解析的依赖版本写入 `resolved_versions.json`，历史尝试记录保存在 `dependency_attempts.json`。这两个文件的作用：

- **防止同一次引入内的版本震荡**：Supervisor 在调度多个依赖时，统一读取已锁定版本，避免 A 依赖 foo>=1.0 而 B 依赖 foo<=0.9 时各自构建出不同版本
- **支持冲突回溯**：如果某个版本尝试失败，可以从 `dependency_attempts.json` 中找到已验证过的候选版本，而不是从头重试

### 3.4 构建失败时的冲突修复循环

rpmbuild 过程中如果因为依赖版本问题失败，构建引擎不会立即报错退出，而是：

1. 识别失败原因（缺包 / 版本不满足 / spec 错误）
2. 缺包 → 上报 `dep_needed` 信号，由 Supervisor 统一调度依赖引入后重试
3. spec 错误 → 自动修复 spec / BuildRequires / `%files`，进入下一轮（最多 10 轮）
4. 审核发现问题（critique FIX_REQUIRED）→ 修复后重建（最多 3 次）

### 3.5 兼容包（compat 包）机制

当官方源已有某个包的旧版本，但用户需要引入更高版本时，直接覆盖会破坏依赖该旧版本的现有包。兼容包是解决这一问题的核心手段：在不替换旧版本的前提下，以一个带版本后缀的新包名引入新版本，两个版本在系统上共存。

**命名规则：**

```
原包名 + 主版本号后缀

示例（版本 4.12.3 引入，官方源有 3.x）：
  Java:    log4j                      → log4j-2
  C 库:    libfoo（官方有 libfoo-1）  → libfoo-2（rpmrebuild 从旧版改名）
```

主版本号提取规则：版本号 < 10 时取 `major.minor`（如 4.12.3 → `4.12`），版本号 ≥ 10 或日期版本时只取 `major`（如 2024.1 → `2024`）。

**compat 包在 spec 中的 Provides 声明：**

compat 包会通过 `Provides` 字段声明自己能提供原包名的该版本能力，使依赖该新版本的包在写 `Requires: log4j >= 2.0` 时能正常被解析到。

**两个触发场景：**

| 场景 | 触发时机 | 做法 |
|------|---------|------|
| **引入阶段**：官方源有旧版，要引入新版 | `pre_check_deps.py` 检测到 `block_official_older` 冲突 | 以 compat 包名（带版本后缀）构建新版本，两版本共存 |
| **升级阶段**：AI 源自身升级某包的大版本 | `publish_rpm.py` 检测到 major 变化或存在反向依赖 | 用 `rpmrebuild` 从旧 RPM 直接改名生成旧版的 compat 包，再发布新版本 |

**各语言对 compat 的支持情况：**

能否 compat 的核心判断依据是：**安装路径是否包含版本号**。路径里有版本，新旧两个包就能在文件系统上共存；路径里没有版本，新旧包会安装到同一路径，产生文件冲突。

| 语言 | 引入阶段 compat | 升级阶段 compat | 原因 |
|------|:--------------:|:--------------:|------|
| Python | ⛔ 不支持 | ⛔ 不支持 | 安装路径为 `/usr/lib/python3.x/site-packages/<name>/`，不含版本号，新旧文件必然冲突 |
| Node.js | ⛔ 不支持 | ⛔ 不支持 | 安装路径为 `/usr/lib/node_modules/<name>/`，不含版本号，新旧文件必然冲突 |
| Java | ✅ | ✅（rpmrebuild） | jar 文件名含版本（`commons-lang3-3.12.0.jar`），Maven pom 目录含版本，可共存 |
| Go | ⛔ 不适用 | ⛔ 不支持 | Go 全量 vendor 构建，引入阶段跳过依赖冲突检查；产物为可执行文件，安装路径 `/usr/bin/<name>` 不含版本号，升级时文件冲突 |
| Rust | ⛔ 不适用 | ⛔ 不支持 | 同 Go，静态链接 vendor 构建；产物为可执行文件，升级时文件冲突 |
| C / C++ 库 | ✅ | ✅（rpmrebuild，soname 变化时）| 共享库 soname 含版本（`libfoo.so.1` → `libfoo.so.2`）时文件路径不同，可共存；若 soname 不变则文件冲突，降为 block |

---

## 四、openeuler-ai-pkg 仓库的后续维护

openeuler-ai-pkg 仓库上线后面临三个核心维护挑战：**包版本老化**、**引入经验的持续积累**、**与官方源的关系管理**。以下分别给出设计思路。

> **CVE 说明**：openeuler-ai-repo 不承担 CVE 响应义务。AI 源定位为"快速引入、社区验证前的暂存层"，安全漏洞修复由上游项目和 openEuler 官方社区负责。生产环境用户应自行评估安全风险，或等包进入官方源后切换使用。

### 4.1 包版本更新

**触发方式**：

| 方式 | 说明 |
|------|------|
| 用户主动申请 | 在原 Issue 评论 `/upgrade` 或重新提 Issue 指定新版本 |
| 定期扫描（推荐） | 调度任务每周对仓库内所有包检查上游是否有新版本，生成待更新列表 |
| 官方源同步 | 若官方源发布了对应包的新版本且高于 openeuler-ai-repo，自动触发 `upgrade_user_repo` 决策 |

**升级流程**：复用现有 `/import-package` 流程，传入新版本号，走 `upgrade_user_repo` 路径，构建成功后覆盖旧 RPM，更新 createrepo 索引。

**版本保留策略**：建议保留最近两个可用版本（当前版本 + 前一版本），允许用户 `dnf install pkgname-<old-version>` 降级，超过两个版本的旧 RPM 归入 `archive/` 目录而非直接删除，保留 6 个月后清理。

### 4.2 包自动下架机制

AI 源中的包若 Issue 超过 6 个月无任何互动（无评论、无 `/keep`、无 `/upgrade` 申请），视为无人使用，自动触发下架流程。

**自动下架流程：**

```
定期扫描（每月）检测 Issue 超过 6 个月无互动的包
  │
  ▼
在原申请 Issue 发出"即将下架"预警评论：
  "⚠️ 该包已超过 6 个月无互动，将在 30 天后从 openeuler-ai-repo 下架。
   如仍有使用需求，请回复 /keep 保留，或提 /upgrade 申请更新版本。"
  │
  ├─ 30 天内收到 /keep 或 /upgrade → 重置计时，继续维护
  └─ 30 天内无响应 → 执行下架
       ├─ 从 dist/ 删除 RPM，更新 createrepo
       ├─ 在 Issue 追加最终评论并打 removed 标签，关闭 Issue
       └─ RPM 文件移入 archive/ 保留 90 天后彻底清理
```

**豁免条件：** 以下包不受自动下架机制约束：
- 被 AI 源内其他包声明为 `Requires` 的依赖包（有反向依赖）
- 维护者手动打了 `pinned` 标签的包

### 4.3 官方源新引入 AI 源已有包的冲突处理

这是 AI 源运行后必然会遇到的场景：AI 源里的包被官方社区采纳正式收录，或官方社区独立引入了同一个包。两个源中出现同名包时，若不处理，用户执行 `dnf update` 可能触发不可预期的版本跳变。

#### 冲突场景分类

**场景一：官方版本 = AI 源版本（完全相同）**

最理想的情况，说明 AI 源的包已被官方采纳或双方独立引入了同一版本。

处理方式：
- 从 AI 源直接下架该包（`dist/` 中删除 RPM，更新 createrepo）
- 在原申请 Issue 评论通知用户："该包已进入官方源，AI 源版本已下架，请切换至官方源使用"
- 已安装该包的用户执行 `dnf update` 后自动切换到官方源版本，无感知

---

**场景二：官方版本 > AI 源版本（官方更新）**

官方收录后继续维护，版本已超过 AI 源。直接下架看似简单，但官方版本高于 AI 源版本意味着存在跨版本跳变，可能破坏依赖 AI 源包的其他软件包。

处理方式：
1. 自动扫描检测到冲突后，**不立即下架**，而是在 AI 源仓库自动创建一个维护 Issue，列出：
   - 冲突包名、AI 源版本、官方版本
   - 依赖该包的其他 AI 源包列表（反向依赖）
2. 维护者评估反向依赖包是否兼容官方新版本
3. 确认无影响后，下架 AI 源版本，通知原申请 Issue："该包已进入官方源（版本更新），AI 源版本已下架，请切换至官方源"
4. 若反向依赖包不兼容新版本，需先升级这些包（或新建引入申请），再下架

---

**场景三：官方版本 < AI 源版本（AI 源超前）**

AI 源引入了更高版本，官方尚未跟进。这是最复杂的场景。

处理方式取决于是否有用户已在依赖 AI 源的高版本：

| 情况 | 处理方式 |
|------|---------|
| 无用户依赖 AI 源高版本 | 评估是否将 AI 源版本提交给官方（走 4.6 节对接流程）；提交后从 AI 源下架 |
| 有用户依赖 AI 源高版本 | 不能直接下架。视语言是否支持 compat：Java / C++ 可保留 compat 包供依赖方使用；Python / Node.js 等不支持 compat 的语言，需通知用户迁移到官方源版本后再下架 |

扫描任务检测到此场景时，自动在关联 Issue 创建评论提醒维护者决策，不自动下架。

---

**场景四：官方包名与 AI 源包名不同（重命名）**

官方社区在收录时按自己的命名规范重命名了包（如 `nodejs-copy-to-clipboard` → `js-clipboard`）。

处理方式：
- AI 源保留原包名，在包的元数据中标注"官方源已有同功能包：`js-clipboard`"
- Bot 在原 Issue 评论通知用户，建议迁移到官方包名
- 设置 6 个月宽限期后下架 AI 源版本

---

#### 自动检测机制

定期扫描任务（建议每日）执行以下检查，结果写入包的元数据文件：

```
对 AI 源每个包：
  查询官方源是否存在同名包
    ├─ 不存在 → 无冲突，继续维护
    ├─ 存在，版本相同或官方更新 → 标记为 pending_removal，通知维护者
    └─ 存在，AI 源更新 → 标记为 official_catching_up，通知维护者评估是否提交官方
```

标记为 `pending_removal` 的包在维护者确认后执行下架，不自动删除。

### 4.4 lessons 经验库的持续优化

系统内置了经验积累机制：每次引入完成后，`pkg-reviewer` 的 feedback 阶段会将本次的可复用经验写入 `build-rpm/lessons/<lang>.json`，下次同语言构建时自动注入。

**长期维护建议：**

1. **定期人工复查 lessons**：每季度检查一次各语言的 lessons 文件，删除因上游变化已失效的条目，合并重复规则。建议由有打包经验的维护者负责，每次修改做 code review。

2. **建立 lessons 质量指标**：统计每条 lesson 被应用的次数以及应用后是否减少了构建轮数，低效条目（从未被命中 / 命中后仍然失败）应当审查或删除。

3. **跨语言通用规则抽取**：当多个语言的 lessons 出现相似模式时（例如"monorepo 根包需阻断"在 Node.js 和 Go 均出现），将其提升为 `lessons/common.json` 的通用规则，避免重复。

### 4.5 仓库健康度看板（建议新增）

为了让维护者随时了解 openeuler-ai-pkg 仓库状态，建议在归档仓的 README 或独立页面维护一个自动生成的健康看板：

```
openeuler-ai-repo 健康看板（自动更新）

包总数：142          本月新增：18
待更新（落后上游 > 2 个版本）：7
lessons 条目：89     平均构建轮数：2.3 轮
构建成功率（近 30 天）：94.4%
```

看板数据来源于归档仓的元数据文件，由 CI 在每次合并后自动刷新，无需人工维护。

### 4.6 openEuler社区对接

openeuler-ai-repo 作为"预验证暂存层"，在积累足够质量数据后，可逐步对接 openEuler openEuler社区：

| 阶段 | 方案 | 触发条件 |
|------|------|---------|
| **当前（早期）** | 方案 A：用户手动提交 | 构建成功后 Bot 提供材料包下载链接，用户自行向官方提 PR |
| **稳定运行后** | 方案 B：Bot 半自动提交 | critique PASS 率 > 90% 且与社区协商好 Bot 账号准入后，用户回复 `/submit` 一键提 PR |
| **成熟期** | 方案 C：全自动提交 | 社区明确授权且 Bot 质量经过充分验证后，构建通过即自动提 PR |

**两阶段 CI 分工（无论哪种方案均适用）：**

```
openeuler-ai-repo CI（提 PR 前，已完成）          官方 CI（merge 前触发）
────────────────────────────────────         ─────────────────────────────
rpmbuild -ba 成功                    在官方 OBS / EBS 集群重新构建
rpm -ivh 安装验证通过                 更严格合规扫描
rpmlint 0 errors                     多架构验证（x86_64 / aarch64）
AI critique PASS                     maintainer review + merge
```

openeuler-ai-repo CI 做"粗筛"，确保提到官方的 PR 没有基础构建问题；官方 CI 做最终质量门禁。

---

## 附录：系统内部架构

### Agent 分工

```
┌─────────────────────────────────────────────────────────────────┐
│  入口：/import-package <pkgname> <upstream_url> [--version <v>]  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────┐
          │  Supervisor（import-     │   ← 文件状态机，不信任 agent 返回值
          │  package lead）          │   ← 通过 spawn agent 保护自身上下文
          └──────┬───────────────────┘
                 │
    ┌────────────┼─────────────────────────────────┐
    ▼            ▼                   ▼             ▼
pkg-evaluator  pkg-builder       pkg-reviewer  archive
（合规+决策）   （spec+rpmbuild）  （质量审核）  （归档入库）
```

### 状态文件结构

```
pkgname/
  session.json                    ← 容器名、上游 URL、版本等基础信息
  workflow_<pkgname>.json         ← 整体进度（loop_count / built_pkgs / reused_pkgs）
  dep_registry.json               ← 所有依赖的状态（由 Supervisor 和 Builder 共同维护）
  build_state/
    introduced.txt                ← 本次实际新建/升级成功的包（不含 reuse）
    resolved_versions.json        ← 已锁定的依赖版本
    dependency_attempts.json      ← 版本候选尝试历史
  pkgs/<pkgname>/
    gate_result_<pkgname>.json    ← 评估决策（decision / lang / version）
    pre_check.json                ← 预检依赖解析结果
    <pkgname>.spec                ← 生成的 spec 文件
    build_rpm_result.json         ← 构建结果（status / failure_reason / deps）
    build_actions.json            ← 构建关键操作日志（供 critique 审视合规性）
    build.log                     ← rpmbuild 原始输出
    rpmlint.txt                   ← rpmlint 检查结果
    critique_round1_<pkg>.json    ← critique 审核结果（verdict + issues）
    feedback_<pkgname>.json       ← 反馈与经验总结
```

### License 处理规则

| 类别 | 代表 License | 处理 |
|------|-------------|------|
| permissive | MIT, Apache-2.0, BSD, ISC | ✅ 通过 |
| weak_copyleft | LGPL, MPL | ✅ 通过，记录警告 |
| strong_copyleft | GPL-2.0/3.0, AGPL | ✅ 通过，报告记录 |
| no_commercial | CC-BY-NC, BUSL, SSPL | ❌ 阻断 |
| unknown / unlicensed | 无法识别 / 无声明 | ❌ 阻断，需人工确认 |
