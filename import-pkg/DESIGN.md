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
6. [review-rpm：质量审查与报告](#6-review-rpm-质量审查与报告)
7. [archive-rpm-sources：归档与发布](#7-archive-rpm-sources-归档与发布)

**第三部分：规范参考**

8. [各语言 Spec 模板](#8-各语言-spec-模板)
9. [License 分类处理规则](#9-license-分类处理规则)
10. [特殊场景处理指南](#10-特殊场景处理指南)

**附录：架构决策记录**

A. [关键架构决策](#a-关键架构决策)

---

# 第一部分：架构设计

## 1. 系统背景与设计原则

### 1.1 问题定义

openEuler 社区通过 PR 流程引入生态包：开发者向 community 仓库提交含 `upstream:` 字段的 YAML 文件，经 TC 审核后建仓，由维护者手工编写 spec、编译 RPM 并归档。这个人工流程存在三个核心痛点：

| 痛点 | 具体表现 |
|------|---------|
| **依赖链深且复杂** | 一个包可能有几十个传递依赖，Python/Go/C 的依赖体系规则各异 |
| **合规检查依赖经验** | License 判断、仓库活跃度评估需要经验，人工容易遗漏 |
| **spec 编写繁琐** | 不同语言的 spec 模板和宏用法差异大，容易写错 |

**import-package 的目标：** 将从 PR 解析到 RPM 归档的全过程自动化。核心理念是"先跑起来"——用 rpmbuild 循环驱动依赖发现，遇到缺包立即递归引入，直到整条依赖链构建完成。

---

### 1.2 主包与依赖包的区分

流程中处理的包分为两类，决定了部分行为差异：

| 维度 | 主包 | 依赖包 |
|------|------|--------|
| **来源** | PR yaml 中 `upstream:` 字段直接引用 | rpmbuild 失败时发现的缺失依赖 |
| **调用标志** | `pkg-introduce <pkg> <url>`（无 `--install`） | `pkg-introduce <pkg> <url> --install --depth N` |
| **构建后操作** | 不安装到容器，由顶层统一归档 | 安装到容器供后续依赖使用 |
| **归档** | 顶层 `pkg-introduce` 负责调用 `archive-rpm-sources` | 由顶层统一归档，自身不触发归档 |

---

### 1.3 核心设计原则

| 原则 | 做法 |
|------|------|
| **构建驱动依赖发现** | 不做全量静态依赖锁定，`pre_check_deps.py` 先做一次结构化预检减少轮次，`rpmbuild` 循环继续兜底发现遗漏依赖 |
| **统一递归链路** | 顶层包和依赖包共用同一条 `pkg-introduce → build-rpm` 链路；依赖包仅通过 `--install` 区分 |
| **版本感知优先复用** | 在构建前先做权威 existing-check，优先复用官方源或 AI RPM 源中已满足版本的包 |
| **容器隔离** | 每次顶层引入必须重建容器，依赖包复用同一容器；权威查询与真实构建都在容器内完成 |
| **状态文件防护** | `building.txt` 检测循环依赖，`introduced.txt` 只记录实际 `built_new / upgraded_user_repo` 的依赖 |
| **合规前置** | 上游仓库合规和 License 检查在下载源码后立即执行，不合规立即阻断 |
| **源码版本锁定** | 无 `--version` 时，下载后自动检测 manifest 版本并切换到对应不可变 tag，保证构建可复现 |
| **CRITIC+SCALAR 质量循环** | rpmbuild 成功后进入 review-fix 循环（最多 3 轮）：Critic（review-rpm critique）以工具输出为锚点发现 E 级问题，Actor（build-rpm）按结构化修复指令修 spec 重构；循环外 Judge（review-rpm feedback/summary）提炼经验并生成最终报告 |
| **归档原子性** | 主包构建完成后统一归档主包和本次实际新引入依赖，不归档纯复用依赖 |

---

## 2. 系统组件与整体流程

### 2.1 核心组件

| 组件 | 类型 | 职责 |
|------|------|------|
| **import-package** skill | 总入口 | 解析 PR 链接，提取 upstream URL，调度 `pkg-introduce` |
| **pkg-introduce** skill | 引入协调 | 合规检查、源码下载、语言/版本识别、容器准备、权威 existing-check、构建调度、顶层归档 |
| **build-rpm** skill | 构建核心 | spec 生成、`rpmlint` 校验、依赖预检、依赖递归引入、`rpmbuild` 循环、review-fix 质量循环、运行时依赖验证 |
| **review-rpm** skill | 质量审查 | Critic（loop 内）：以 rpmbuild/rpmlint 工具输出为锚点，输出结构化修复指令；Judge（loop 外）：提炼 lessons、生成引入报告 |
| **archive-rpm-sources** skill | 归档发布 | 将 spec + tarball + RPM 推送到 GitHub，维护 yum 软件源 |
| **setup-build-env** skill | 环境部署 | 创建 openEuler 容器，安装语言工具链 |
| **resolve-rpm-conflicts** skill | 冲突处理 | 安装 RPM 时遇到冲突，自动解决后重试 |

### 2.2 调用链结构

```
import-package
  └─ pkg-introduce <main> <url>                     # 顶层，depth=0
       ├─ check_repo.py                              # 上游合规检查
       ├─ download_source.py                         # 源码下载（自动切换到版本对应 tag）
       ├─ check_license.py                           # License 检查
       ├─ extract_version.py / 语言识别              # 语言与版本确定
       ├─ setup-build-env                            # 顶层重建容器
       ├─ check_existing_package.py                  # 权威 existing-check
       └─ build-rpm <main> <lang> <url> <ver>        # depth=0
            ├─ rpmlint                               # spec 规范校验
            ├─ pre_check_deps.py                     # 预检依赖
            │    └─ pkg-introduce <dep-A> --install --depth 1
            │         └─ build-rpm ... --install --depth 1
            │              └─ pkg-introduce <dep-B> --install --depth 2
            │                   └─ ...（最深 depth=5）
            ├─ rpmbuild 循环（最多 10 轮）
            │    └─ 发现缺包 / 包名不匹配 / 文件列表问题 → 修 spec 或递归引入
            ├─ review-fix 质量循环（最多 3 轮）      # rpmbuild 成功后触发
            │    ├─ review-rpm critique              # Critic：工具输出 → 结构化修复指令
            │    ├─ 按 fix_instruction 修 spec       # Actor：执行修复
            │    └─ 重跑 rpmbuild（仅修复轮）
            └─ 运行时依赖验证 / 依赖包安装
       ├─ archive-rpm-sources --pkgs <main> <dep-A> <dep-B> ...
       ├─ review-rpm feedback                        # Judge：提炼 lessons
       └─ review-rpm summary                         # Judge：生成引入报告
```

### 2.3 状态文件

整个引入会话使用以下状态文件，位于 `./build_state/` 和 `./reports/`：

| 文件 | 内容 | 作用 |
|------|------|------|
| `build_state/building.txt` | 当前调用链上正在处理的包名（每行一个） | 循环依赖检测 |
| `build_state/introduced.txt` | 本次会话实际 `built_new / upgraded_user_repo` 成功的依赖包名 | 去重 + 顶层归档时确定归档列表 |
| `reports/critique_round<N>_<pkgname>.json` | review-fix 循环每轮 Critic 输出的结构化 verdict | orchestrator 读取 verdict / fix_instruction 决定下一步 |
| `reports/round_history_<pkgname>.json` | 所有轮次 critique JSON 汇总 + exit_reason | Judge（feedback/summary）读取，生成修复过程摘要 |

顶层 `pkg-introduce` 在第一步初始化 `building.txt` 和 `introduced.txt`（清空）；依赖包调用复用已有文件。

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
│ pkg-introduce（主包，无 --install）            │
│  ① 状态文件初始化                             │
│  ② 上游仓库合规检查                           │  ← 阻断线 1
│  ③ download_source.py 下载源码               │
│     （自动切换到版本对应不可变 tag）           │
│  ④ License 合规检查                           │  ← 阻断线 2
│  ⑤ 语言检测 + 版本号提取                      │
│  ⑥ 重建编译容器（setup-build-env）            │
│  ⑦ 权威 existing-check                        │
│  ⑧ 打包 tarball → 上传容器                   │
│  ⑨ 调用 build-rpm                            │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ build-rpm（depth=0）                          │
│  ① 读取构建说明（BUILD.md / README.md）        │
│  ② 生成 spec 文件（注入 lessons 历史经验）     │
│  ③ rpmlint 规范校验                           │
│  ④ pre_check_deps.py 预检并递归引入缺失包      │
│  ⑤ rpmbuild 循环（最多 10 轮）                │  ← 阻断线 3
│     - dnf builddep 失败 → 修正包名或递归引入  │
│     - %build 失败 → 补 BuildRequires 或引入   │
│     - %files/pyproject 问题 → 调整 spec       │
│  ⑥ 运行时依赖验证                             │
│  ⑦ review-fix 质量循环（最多 3 轮）           │
│     - review-rpm critique（Critic）           │
│       Oracle: rpmbuild log + rpmlint          │
│       输出: structured_verdict.json           │
│     - PASS → 退出循环进归档                   │
│     - FIX_REQUIRED → 修 spec 重跑 rpmbuild    │
│     - ABORT → 短路失败，进入报告              │
│  ⑧ 依赖包模式下安装 RPM                       │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ archive-rpm-sources                           │
│  归档 spec + tarball + RPM → GitHub           │
│  createrepo_c 重建 yum 索引                   │
└────────────────────┬─────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ review-rpm（Judge 角色）                      │
│  feedback：分析过程 + 提炼 lessons            │
│  summary：生成含修复过程摘要的引入报告        │
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
| **第三条**（构建失败） | build-rpm 第④步 | 循环依赖 / 超出最大深度 / 超出最大轮次 / 同一错误无法修复 | 终止当前包构建，不触发归档 |

---

# 第二部分：流程详解

## 3. import-package：PR 解析与调度

### 3.1 PR 链接解析

支持平台：`atomgit.com`、`gitcode.com`（同一 Gitea 平台，API 兼容）

```bash
cd ${CLAUDE_SKILL_DIR}
mkdir -p reports

# 示例：https://atomgit.com/shuyingbanbao/community/pull/1
# owner=shuyingbanbao, repo=community, pr_number=1
python3 scripts/extract_pr_info.py <owner> <repo> <pr_number> --no-diff
```

从 `pr_<N>_info.json` 提取变更的 `.yaml` 文件中的 `upstream:` 字段。

> **注意：** `files[].patch` 是 dict（含 `diff` 子字段），不是字符串。Python 解析时须先取 `patch['diff']`，直接切片会报 `TypeError`。

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
  - <dep2>（depth=2）

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
| `--install` | 存在时为依赖包调用，构建后安装到容器，不触发归档 |
| `--depth N` | 当前递归深度，由调用方传入 |

### 第一步：状态文件初始化（仅顶层）

```bash
rm -rf ./build_state
mkdir -p ./build_state ./reports ./sources
touch ./build_state/building.txt ./build_state/introduced.txt
```

依赖包调用（`--install` 已设置）跳过此步。

### 第二步：上游仓库合规检查

```bash
python3.11 ${SCRIPT_DIR}/check_repo.py <upstream_url> \
  -o reports/repo_check_<pkgname>.json
```

检查项：
- 仓库平台是否在白名单内（GitHub、GitLab、Gitee、AtomGit 等）
- 最近活跃时间：超过 5 年不活跃则 `blocking: true`
- API 请求失败时输出警告，需人工确认后方可继续

### 第三步：下载上游源码

统一使用 `download_source.py`：

```bash
rm -rf ./sources/<pkgname>
python3 ${CLAUDE_SKILL_DIR}/scripts/download_source.py \
  --upstream-url <upstream_url> \
  --output-dir ./sources -o reports/download_result_<pkgname>.json
```

### 第四步：License 合规检查

```bash
python3.11 ${SCRIPT_DIR}/check_license.py ./sources/<pkgname> \
  --pkg <pkgname> -o reports/license_check_<pkgname>.json
```

详见 [第 9 节 License 分类处理规则](#9-license-分类处理规则)。

### 第五步：检测语言类型并确定版本号

| 特征文件 | 语言 |
|---------|------|
| `go.mod` | `go` |
| `Cargo.toml` | `rust` |
| `CMakeLists.txt` / `configure.ac` / `meson.build` | `c` |
| `setup.py` / `pyproject.toml` | `python` |
| `pom.xml` / `build.gradle` | `java` |
| `package.json` | `nodejs` |
| `*.gemspec` / `Gemfile` | `ruby` |

版本号提取优先级：`git describe --tags` → `VERSION` 文件 → 语言特定配置文件（`Cargo.toml`、`setup.py` 等）

### 第六步：准备编译容器

**顶层调用（无 `--install`）：必须重建容器**

```bash
docker stop oe-build-env 2>/dev/null || true
docker rm   oe-build-env 2>/dev/null || true
/setup-build-env ./sources/<pkgname>
```

**依赖包调用（有 `--install`）：复用已有容器**，容器不存在则报错终止。

### 第七步：执行权威 existing-check

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/check_existing_package.py <pkgname> \
  --version <version> \
  --lang <lang> \
  --container oe-build-env \
  -o reports/existing_check_<pkgname>.json
```

权威语义：
- `official` = `oe-build-env` 容器内可见的 OpenEuler 官方 DNF 软件源
- `user_repo` = `oe-build-env` 容器内注入的 AI RPM 软件源

决策语义：
- `reuse_official`：官方源已有满足要求版本，立即成功返回
- `reuse_user_repo`：AI 源已有满足要求版本，立即成功返回
- `block_official_older`：官方源已有同名旧版，但版本不足，需人工决策
- `upgrade_user_repo`：AI 源已有同名旧版，但版本不足，需要继续构建
- `introduce_new`：官方源和 AI 源都无满足要求版本，需要继续构建

若决策是 `reuse_*` 或 `block_*`，在此步直接结束；若决策是 `upgrade_user_repo` 或 `introduce_new`，进入构建分支。

### 第八步：打包 tarball 并上传容器

```bash
cp -r ./sources/<pkgname> /tmp/<pkgname>-<version>
tar czf /tmp/<pkgname>-<version>.tar.gz -C /tmp <pkgname>-<version>
rm -rf /tmp/<pkgname>-<version>
docker cp /tmp/<pkgname>-<version>.tar.gz oe-build-env:/tmp/
```

> 目录必须重命名为 `<pkgname>-<version>`，使 `%autosetup` 能自动找到解压目录。

### 第八步：调用 build-rpm

```
/build-rpm <pkgname> <lang> <upstream_url> <version> [--install] [--depth N]
```

### 第九步：归档（仅顶层）

```bash
INTRODUCED=$(cat ./build_state/introduced.txt | tr '\n' ' ')
ALL_PKGS="<pkgname> ${INTRODUCED}"
/archive-rpm-sources --pkgs ${ALL_PKGS}
```

---

## 5. build-rpm：RPM 构建与依赖递归

### 保护常量

```
MAX_DEPTH        = 5    # 最大递归深度
MAX_ROUNDS       = 10   # 单包最大编译轮次
MAX_REVIEW_ROUNDS = 3   # review-fix 质量循环最大轮次
```

### 第一步：读取构建说明

```bash
docker exec oe-build-env bash -c "
  cat /build/source/BUILD.md 2>/dev/null \
  || cat /build/source/BUILDING.md 2>/dev/null \
  || head -200 /build/source/README.md 2>/dev/null"

date "+%a %b %d %Y"   # 获取 %changelog 日期
```

### 第二步：生成 spec 文件

根据 `<lang>` 选择模板，在宿主机 `/tmp/<pkgname>.spec` 生成。各语言模板见 [第 8 节](#8-各语言-spec-模板)。

### 第二步（补充）：rpmlint 规范校验

spec 生成后、进入 `rpmbuild` 前，先在容器内执行 `rpmlint`：

```bash
docker cp /tmp/<pkgname>.spec oe-build-env:/tmp/<pkgname>.spec
docker exec oe-build-env bash -c "rpmlint /tmp/<pkgname>.spec 2>&1"
```

当前策略：
- `W:` 记录但不阻断；
- `E:` 先按提示修 spec 并重跑；
- 只有同类问题连续无法解决，才最终阻断。

### 第三步：预分析依赖（pre_check_deps.py）

在启动 `rpmbuild` 之前，用预检脚本扫描源码依赖并分类为：
- `resolved[]`：仓内已满足，可直接复用
- `pending[]`：仓内缺失，需要递归引入
- `blocked[]`：缺可靠 upstream URL 或其他无法继续的问题

```bash
PRE_CHECK=/root/.claude/skills/build-rpm/scripts/pre_check_deps.py
python3 ${PRE_CHECK} <pkgname> <lang> ${SOURCES}/<pkgname> \
  --container oe-build-env -o ./reports/pre_check_<pkgname>.json
```

当前实现以结构化 JSON 结果为主，而不是只消费 stdout 文本。对 `pending[]` 中的依赖，先修正 upstream URL，再进入依赖包引入流程。

### 第四步：准备 rpmbuild 目录并上传 spec

```bash
docker exec oe-build-env bash -c "mkdir -p ~/rpmbuild/{SPECS,SOURCES,BUILD,RPMS,SRPMS}"
docker exec oe-build-env bash -c "cp /tmp/<pkgname>-<version>.tar.gz ~/rpmbuild/SOURCES/"
docker cp /tmp/<pkgname>.spec oe-build-env:/root/rpmbuild/SPECS/
```

### 第五步：rpmbuild 循环（最多 MAX_ROUNDS 轮）

每轮：

```bash
docker exec oe-build-env bash -c "dnf builddep -y ~/rpmbuild/SPECS/<pkgname>.spec 2>&1"
docker exec oe-build-env bash -c "rpmbuild -ba ~/rpmbuild/SPECS/<pkgname>.spec 2>&1"
```

**失败处理：**

| 情况 | 处理方式 |
|------|---------|
| **A：dnf builddep 找不到某包** | `dnf search <pkg>` 找到正确名则修正 spec；否则进入依赖包引入流程 |
| **B：%build 编译失败（缺头文件 / .so）** | `dnf provides '*/foo.h'` 找到则补 BuildRequires；否则进入依赖包引入流程 |
| **C：%install / %files / pyproject 宏链问题** | 查看 BUILDROOT 实际安装产物；优先保留 `%pyproject_build + %pyproject_install`，必要时回退为手工 `%files` |
| **D：同一错误连续两轮未解决** | 终止，上报错误 |
|
**Python 包当前已验证的构建策略：**
- 优先使用 `%pyproject_build + %pyproject_install`；
- 对 hatchling / setuptools / flit 类简单 Python 包优先补显式 `BuildRequires`；
- 当 `%pyproject_save_files` 或相关 record 链不兼容时，回退到手工 `%files`；
- 可按实际 wheel/install 产物修正 `dist-info`、entry-point 脚本、`py.typed`，必要时补 `__pycache__`。

### 依赖包引入流程

发现 dnf 找不到 `<dep_pkgname>` 时，在调用 `pkg-introduce` 前执行三项检查：

**检查 1：深度上限**
```bash
if [ "${CURRENT_DEPTH}" -ge "${MAX_DEPTH}" ]; then
  echo "❌ 已达最大递归深度 ${MAX_DEPTH}"; exit 1
fi
```

**检查 2：循环依赖检测**
```bash
if grep -qx "<dep_pkgname>" ./build_state/building.txt 2>/dev/null; then
  echo "❌ 检测到循环依赖：<dep_pkgname> 正在当前调用链上构建"; exit 1
fi
```

**检查 3：已引入去重**
```bash
if grep -qx "<dep_pkgname>" ./build_state/introduced.txt 2>/dev/null; then
  echo "✓ <dep_pkgname> 已引入，跳过"
  # 不退出，继续编译
fi
```

三项检查通过后：

```bash
echo "<dep_pkgname>" >> ./build_state/building.txt
/pkg-introduce <dep_pkgname> <dep_upstream_url> --install --depth <N+1>
# 无论成功失败，立即从 building.txt 清理：
grep -v "^<dep_pkgname>$" ./build_state/building.txt > /tmp/building_tmp.txt \
  && mv /tmp/building_tmp.txt ./build_state/building.txt
```

引入成功 → 追加到 `introduced.txt`，继续编译循环。
引入失败 → 终止当前包构建。

### 5.1 当前已验证能力与边界

**当前已验证能力：**
- 主包与依赖包统一走 `pkg-introduce → build-rpm` 递归链路；
- 顶层包会统一归档主包和本次实际新引入依赖；
- Python 包已验证 hatchling / setuptools / flit 三类常见构建后端；
- `rpmlint` 已纳入 spec 生成后的修复闭环，不再是首个 `E:` 就立即阻断；
- `%pyproject_build + %pyproject_install` 是当前 Python 包优先路径；当 pyproject 文件清单宏不兼容时，可回退为手工 `%files`；
- `introduced.txt` / `building.txt` 可稳定支撑依赖去重、循环检测和统一收口。

**当前边界：**
- 自动/手工 spec 修复主要解决宏兼容、包名映射、文件打包、entry points、dist-info 命名等问题；
- 若上游声明了更高的真实运行时最低版本，而 openEuler 当前仓内只有更低版本，流程仍会在最终运行时依赖验证或安装阶段暴露真实阻塞；
- 例如 `litestar` 上游明确要求 `msgspec>=0.19.0`，而当前 openEuler 可用版本为 `0.18.6`，这属于真实依赖版本缺口，不是单纯的 spec 语法问题。

### 第五步（循环后）：运行时依赖验证

rpmbuild 成功后，检查 RPM 的 `Requires` 在 OpenEuler 源里是否可满足：

```bash
RPM_FILE=$(docker exec oe-build-env bash -c "find ~/rpmbuild/RPMS -name '*.rpm' ! -name '*.src.rpm' | head -1")

# 排除虚拟 Provides（rpmlib/路径/abi/python dist）
REQUIRES=$(docker exec oe-build-env bash -c "
  rpm -qp --requires ${RPM_FILE} 2>/dev/null \
  | grep -v '^rpmlib' | grep -v '^/' \
  | grep -v '(abi)' \
  | grep -v 'python[0-9.]*dist(' \
  | grep -v '^(' \
  | awk '{print \$1}' | sort -u")
```

对每个 `MISSING` 的依赖，同样进入依赖包引入流程。

### 第六步：安装 RPM（仅 `--install` 时）

```bash
docker exec oe-build-env bash -c "
  rpm -ivh ~/rpmbuild/RPMS/aarch64/<pkgname>-*.rpm 2>&1 \
  || rpm -ivh ~/rpmbuild/RPMS/noarch/<pkgname>-*.rpm 2>&1"
```

若安装报冲突：
```
/resolve-rpm-conflicts <pkgname> ~/rpmbuild/RPMS/aarch64/<pkgname>-*.rpm
```

冲突解决后安装 devel 包：
```bash
docker exec oe-build-env bash -c \
  "rpm -ivh ~/rpmbuild/RPMS/aarch64/<pkgname>-devel-*.rpm 2>/dev/null || true"
```

---

## 6. review-rpm：质量审查与报告

review-rpm 实现了 **CRITIC + SCALAR 融合模式**，分为两个完全不同的角色：

### 6.1 Critic 角色（loop 内，critique stage）

由 build-rpm 的 review-fix 循环在每轮 `rpmbuild` 成功后调用。

**核心约束：每条 issue 必须有 oracle_evidence**（引用 rpmbuild log 或 rpmlint 的原始输出），不得凭空发表意见。

输出 `./reports/critique_round<N>_<pkgname>.json`：

```json
{
  "verdict": "FIX_REQUIRED",
  "continue_loop": true,
  "spec_hash": "sha256:7f68dda1...",
  "e_count": 1,
  "issues": [
    {
      "severity": "E",
      "category": "reproducibility",
      "location": "%build section",
      "oracle_evidence": "cargo build --release --locked --ignore-rust-version",
      "description": "--ignore-rust-version 绕过了上游 MSRV 声明，破坏跨环境可复现性",
      "fix_instruction": "删除 --ignore-rust-version；当前容器 rustc 满足 Cargo.toml 的 rust-version 要求"
    }
  ]
}
```

**verdict 语义：**

| verdict | 含义 | orchestrator 动作 |
|---------|------|-----------------|
| `PASS` | 零 E 级问题 | 退出循环，进归档 |
| `FIX_REQUIRED` | 有 E 级问题 | 按 fix_instruction 修 spec，重跑 rpmbuild |
| `ABORT` | 结构性问题，修 spec 无法解决 | 短路失败，跳归档，进 Judge |

**振荡检测：** orchestrator 记录每轮 `spec_hash`；连续两轮 hash 相同则强制退出，避免死循环。

**E 级问题类型：**

| 类别 | 典型触发 |
|------|---------|
| `reproducibility` | `--ignore-rust-version` / `--force` / `--no-verify` 等绕过 flag；Source0 指向 branch 而非 tag；缺少 `--locked` |
| `lint_error` | rpmlint `E:` 前缀错误行 |
| `build_failure` | rpmbuild 非零退出（防御性保留） |

### 6.2 Judge 角色（loop 外，feedback + summary stage）

由 pkg-introduce 在归档完成后调用，与 loop 完全解耦。

**feedback stage：**
- 读取最终 spec、rpmlint、build log、round_history
- 分析构建过程质量（包括 review-fix 循环是否合理）
- 提炼本次发现的新经验写入 `build-rpm/lessons/<lang>.json`（按语言分文件，保留最近 30 条）
- 输出 `./reports/feedback_<pkgname>.json`

**summary stage：**
- 读取所有流程 JSON + round_history
- 生成 `./reports/<pkgname>_introduction_report.md`
- 报告含"修复过程摘要"章节（有 round_history 时），逐轮列出修复内容、Oracle 依据和结果

### 6.3 lessons 机制

lessons 文件是跨会话的经验记忆，路径 `build-rpm/lessons/<lang>.json`。

- **写入时机：** feedback stage（Judge）在 loop 结束后写，不在 loop 内写
- **注入时机：** build-rpm 在 spec 生成前读取，筛选 `applies_to` 相关条目注入推理上下文
- **效果：** 本次发现的问题（如"不得出现 --ignore-rust-version"）在下次同语言包引入时提前规避

---

## 7. archive-rpm-sources：归档与发布

### 触发方式

由顶层 `pkg-introduce` 在所有包构建成功后统一调用：

```
/archive-rpm-sources --pkgs <main_pkg> <dep1> <dep2> ...
```

> **注意：** archive-rpm-sources 的 SKILL.md 中写"每个包构建完成后立即归档"，但实际调用逻辑在 pkg-introduce 的第九步（顶层归档），是**所有包统一归档**。以 pkg-introduce 的实际实现为准。

### 仓库结构

```
repo-aitest/
├── python3-foo/          ← spec + source tarball
│   ├── python3-foo.spec
│   └── foo-1.0.tar.gz
├── dist/                 ← 编译好的 RPM + repodata（yum 软件源）
│   ├── python3-foo-1.0-1.noarch.rpm
│   ├── repodata/
│   └── repo-aitest.repo
└── README.md
```

### 执行流程

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/publish_rpm.py \
  --pkgs <pkg1> [pkg2...] \
  [--container oe-build-env]
```

脚本完成：
1. 初始化/拉取 GitHub 仓库
2. 从容器拷出 spec、source tarball → `<pkg>/` 目录
3. 从容器拷出编译好的 RPM → `dist/` 目录
4. 升级冲突检测：扫描 dist/ 中同名旧版本，自动移除
5. `createrepo_c --update dist/` 重建索引
6. 一次提交推送

### 升级冲突处理

| 情况 | 处理 |
|---|---|
| dist/ 中已有完全相同的文件名 | 跳过，无冲突 |
| name+arch 相同但版本不同 | 移除旧版本，保留新版本 |
| 新 RPM 文件名无法解析 | 归档失败，回滚工作区 |
| createrepo_c 执行失败 | 归档失败，回滚工作区 |

### 前置条件

已配置 `${CLAUDE_SKILL_DIR}/config.json`：
```json
{
  "github": { "token": "...", "username": "..." },
  "repo": {
    "remote_url": "https://github.com/<owner>/repo-aitest.git",
    "branch": "main",
    "local_dir": "/path/to/local/repo"
  }
}
```

---

# 第三部分：规范参考

## 8. 各语言 Spec 模板

### 7.1 Go 包

```spec
Name:           <pkgname>
Version:        <version>
Release:        1%{?dist}
Summary:        <从 README 提取的一句话描述>
License:        <SPDX_ID>
URL:            <upstream_url>
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  golang >= <go_version>

%description
<描述>

%prep
%autosetup

%build
export CGO_ENABLED=0
export GOFLAGS=-buildvcs=false
export GOPROXY=https://goproxy.cn,direct
%gobuild -o %{name} .

%install
install -Dpm 0755 %{name} %{buildroot}%{_bindir}/%{name}

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}

%changelog
* <date> OpenEuler Package Import <pkg@openeuler.org> - <version>-1
- Initial package
```

**关键注意：** 必须设置 `CGO_ENABLED=0 GOFLAGS=-buildvcs=false GOPROXY=https://goproxy.cn,direct`

### 7.2 Python 包

```spec
Name:           python-<pkgname>
Version:        <version>
Release:        1%{?dist}
Summary:        <描述>
License:        <SPDX_ID>
URL:            <upstream_url>
Source0:        <pkgname>-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-devel
BuildRequires:  python3-pip
# 按实际后端补充，例如：
# BuildRequires:  python3-hatchling
# BuildRequires:  python3-setuptools
# BuildRequires:  python3-flit

%description
<描述>

%prep
%autosetup -n <pkgname>-%{version}

%build
%pyproject_build

%install
%pyproject_install

%files
%license LICENSE
%doc README.md
%{python3_sitelib}/<module_or_package>/
%{python3_sitelib}/<dist-info>*.dist-info/
# 如有脚本/类型标记/字节码，按实际产物补充：
# %{_bindir}/<cmd>
# %{python3_sitelib}/<module_or_package>/py.typed
# %{python3_sitelib}/__pycache__/<module>.cpython-*.pyc

%changelog
* <date> OpenEuler Package Import <pkg@openeuler.org> - <version>-1
- Initial package
```

**关键注意：**
- 优先使用 `%pyproject_build` + `%pyproject_install`；
- 纯 Python 包必须加 `BuildArch: noarch`；
- 对 hatchling / setuptools / flit 等简单 Python 包，优先显式补齐构建后端对应 `BuildRequires`；
- 若 `%pyproject_save_files` 或相关 record 链与当前 openEuler 宏实现不兼容，优先回退为手工 `%files`，不要继续试错不兼容宏组合；
- 含 entry_points 须在 `%files` 加 `%{_bindir}/<cmd>`；
- wheel 实际版本、dist-info 目录名、`py.typed`、必要时 `__pycache__`，都应以 BUILDROOT 实际产物为准。

### 7.3 C/C++ 库（CMake）

```spec
Name:           lib<pkgname>
Version:        <version>
Release:        1%{?dist}
Summary:        <描述>
License:        <SPDX_ID>
URL:            <upstream_url>
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  cmake gcc gcc-c++
BuildRequires:  <其他依赖-devel>

%description
<描述>

%package        devel
Summary:        Development files for %{name}
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description    devel
Development headers and libraries for %{name}.

%prep
%autosetup

%build
%cmake -DCMAKE_BUILD_TYPE=Release
%cmake_build

%install
%cmake_install

%files
%license LICENSE
%doc README.md
%{_libdir}/*.so.*

%files devel
%{_includedir}/*
%{_libdir}/*.so
%{_libdir}/pkgconfig/*.pc

%changelog
* <date> OpenEuler Package Import <pkg@openeuler.org> - <version>-1
- Initial package
```

**关键注意：** C 库须同时生成主包（`.so.*`）和 devel 包（`.h` + `.so` 软链接 + `.pc`）

### 8.4 Rust 包

```spec
Name:           <pkgname>
Version:        <version>
Release:        1%{?dist}
Summary:        <描述>
License:        <SPDX_ID>
URL:            <upstream_url>
Source0:        https://github.com/<author>/%{name}/archive/refs/tags/v%{version}/%{name}-%{version}.tar.gz

%global debug_package %{nil}

BuildRequires:  rust cargo gcc

%description
<描述>

%prep
%autosetup

%build
export CARGO_HOME=$(pwd)/.cargo-home
mkdir -p "${CARGO_HOME}"
cargo build --release --locked

%install
install -Dpm 0755 target/release/%{name} %{buildroot}%{_bindir}/%{name}

%check
# offline build; test fixtures not vendored

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}

%changelog
* <date> OpenEuler Package Import <pkg@openeuler.org> - <version>-1
- Initial package
```

**关键注意：**
- `Source0` 必须使用完整 URL 指向不可变 tag，不得只写文件名（rpmlint `invalid-url` 检查）
- `cargo build` 必须加 `--locked`，锁定 `Cargo.lock` 中的依赖版本
- **禁止** 使用 `--ignore-rust-version`；若容器 rustc 版本不满足 `Cargo.toml` 声明的 `rust-version`，需升级编译器，而非绕过约束
- `CARGO_HOME` 显式设定，避免依赖宿主机隐式路径
- `%global debug_package %{nil}` 避免 debuginfo 子包 `%files` 为空导致构建失败
- 必须有 `%check` section（即使为空注释），否则 rpmlint 报 `no-%check-section`
- `Packager:` 和 `Group:` 字段不写（分别触发 `hardcoded-packager-tag` 和冗余字段警告）

### 7.5 通用注意事项

- `%changelog` 日期用 `date "+%a %b %d %Y"` 获取，星期须与实际日期匹配
- `Release` 字段统一使用 `1%{?dist}`
- 不修改源码，只通过调整 spec 和引入依赖包解决构建问题

---

## 9. License 分类处理规则

`check_license.py` 输出 `category` 和 `blocking` 字段：

| 分类 | 代表许可证 | blocking | 处理 |
|------|-----------|---------|------|
| `permissive` | MIT / Apache-2.0 / BSD / ISC | false | 直接通过 |
| `weak_copyleft` | LGPL / MPL | false | 通过，报告记录 |
| `strong_copyleft` | GPL-2.0 / GPL-3.0 / AGPL | false | 通过，spec License 字段须正确填写 |
| `no_commercial` | CC-BY-NC / BUSL / SSPL | **true** | 阻断 |
| `unknown` | 无法识别 | **true** | 阻断，需人工确认 |
| `unlicensed` | 无任何声明 | **true** | 阻断 |

> GPL 类强 Copyleft 许可证**不阻断**引入流程，只要 spec 中 License 字段正确填写即可。这是当前实现与早期设计的主要差异点。

---

## 10. 特殊场景处理指南

### 9.1 依赖链深度爆炸

当递归深度接近 MAX_DEPTH=5 时：

```
❌ 已达最大递归深度 5，无法继续引入 <dep_pkgname>
   依赖链：main → dep-A → dep-B → dep-C → dep-D → dep-E
```

**处理：** 手动预先引入深层依赖包（先执行 `/import-package` 或 `/pkg-introduce` 引入该包），再重新触发主包引入。

### 9.2 循环构建依赖

```
❌ 检测到循环依赖：<dep_pkgname> 正在当前调用链上构建
   building.txt 内容：main dep-A dep-pkgname
```

**处理：** 分析循环依赖关系，考虑：
1. 使用 Bootstrap spec（精简版，不含循环依赖的功能）
2. 先手动构建其中一个包的最小版本安装到容器，再引入另一个

# 附录：架构决策记录

## A. 关键架构决策

### A.1 构建驱动 vs. 静态分析

**问题：** 如何发现一个包的所有依赖？

**方案对比：**

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **静态分析前置** | 解析 go.mod / pyproject.toml / CMakeLists.txt，提前生成完整依赖列表 | 能提前发现所有依赖 | 各语言分析逻辑差异大，容易遗漏条件依赖、可选依赖 |
| **构建驱动发现**（当前实现） | rpmbuild 失败时才发现缺包，递归引入 | 精确，只引入真正需要的包 | 可能需要多轮编译才能收敛 |

**决策：** 采用**构建驱动 + 预检辅助**的混合策略。`pre_check_deps.py` 做一次预检减少轮次，rpmbuild 循环负责兜底发现遗漏的依赖。这符合"先跑起来"的核心理念。

### A.2 递归引入 vs. 批量引入

**问题：** 依赖包应该批量分析后统一引入，还是发现一个引入一个？

**决策：** 采用**按需递归**。发现缺包时立即调用 `pkg-introduce <dep> --install`，`pkg-introduce` 内部会对该依赖包重复完整流程（合规 + 构建 + 安装）。

优势：
- 流程统一，主包和依赖包走同一路径
- 自然形成拓扑序（深层依赖先安装）
- 状态文件（building.txt + introduced.txt）天然处理循环和去重

### A.3 顶层统一归档 vs. 逐包立即归档

**问题：** 应在每个包构建完成后立即归档，还是等所有包完成后统一归档？

**决策：** 采用**顶层统一归档**（pkg-introduce 第九步）。

原因：
- 归档是 Git 推送操作，频繁推送性能差
- 若某个后续包构建失败，已归档的中间包难以回滚
- 统一归档保证"要么全部成功，要么不归档"的原子性

> archive-rpm-sources 的 SKILL.md 中有"每个包构建完成后立即归档"的描述，但 pkg-introduce 的实际实现是统一归档。若需要更细粒度的归档，可修改 pkg-introduce 的第八步，在 build-rpm 成功后立即调用 archive-rpm-sources。

### A.4 GPL 许可证处理

**早期设计：** 强 Copyleft（GPL/AGPL）评估传染性，动态链接且主包为宽松许可证时阻断。

**当前实现：** GPL 类不阻断，只要 spec 中 `License` 字段正确填写即可。

**原因：** 传染性评估逻辑复杂，且 openEuler 生态包的 License 管理最终依赖社区 review 流程（PR 审核），自动化工具只需确保 spec 信息准确，不需要替代人工做法律判断。

---

*文档版本：v4.0 | 2026-05-21*
