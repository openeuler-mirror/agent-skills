---
name: pkg-introduce
description: OpenEuler 包引入统一入口：合规检查、源码下载、语言/版本检测、版本感知复用/升级决策、构建调度、归档。顶层包和依赖包走同一流程，通过 --install 区分。
argument-hint: "<pkgname> <upstream_url> [--install] [--depth N]"
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
| `--install` | 缺省：顶层包调用，构建完成后归档；存在：依赖包调用，构建完成后安装，不归档 |
| `--depth N` | 当前递归深度，默认 0，由调用方传入，**不要手动指定** |

> **调用链：**
>
> ```
> import-package
>   └─ pkg-introduce <main> <url>                     # 顶层，depth=0
>        └─ build-rpm ... --depth 0
>             └─ 缺包 → pkg-introduce <dep-A> --install --depth 1
>                            └─ build-rpm ... --install --depth 1
>                                 └─ 缺包 → pkg-introduce <dep-B> --install --depth 2
>                                                └─ build-rpm ... --install --depth 2
>                                                     └─ ...（最深 depth=5）
> ```
>
> 循环依赖和超深链路由状态文件拦截，`build-rpm` 负责在调用前检查。

`import-package` 中的预扫描只是提示；**本 skill 在拿到真实 `<lang>` / `<version>` 后执行的 existing-check 才是权威决策。**

## 执行边界（必须遵守）

### 总原则

- **脚本默认在宿主机执行**：源码下载、文本/元数据解析、API 查询、JSON 汇总、流程编排均应在宿主机执行。
- **仅将容器相关子步骤下沉到 `oe-build-env`**：凡是需要 OpenEuler 官方源真值或真实 RPM/构建环境的动作，必须通过 `docker exec oe-build-env ...` 在容器内执行。
- **不要把整个流程整体搬进容器运行**：除非步骤本身就是容器内命令；否则应保持“宿主机跑脚本，容器跑查询/构建命令”的模式。

### 本 skill 实际直接调用的脚本 / skill / 容器步骤

- `check_repo.py`：宿主机执行，用于上游仓库合规检查。
- `download_source.py`：宿主机执行，用于下载上游源码。
- `check_license.py`：宿主机执行，用于 License 合规检查。
- `check_existing_package.py`：宿主机执行，但其中 `official` 判定必须查询 `oe-build-env` 容器内可见的 OpenEuler 官方 DNF 软件源；`user_repo` 仅扫描 AI 源本地克隆目录。
- `pkg_introduce_result.py`：宿主机执行，用于结果写入与状态更新。
- `/setup-build-env`：用于准备或重建 `oe-build-env` 容器。
- `docker ps` / `docker stop` / `docker rm` / `docker cp`：属于本 skill 直接流程的一部分，可直接使用。

### 本 skill 不应展开说明的内部实现

- 各语言依赖分析脚本、`pre_check_deps.py`、`finalize_dependency_result.py` 属于 `/build-rpm` 链路内部实现，由 `build-rpm` skill 负责说明。
- `rpm_batch_lookup.py`、`container_exec.py`、`analyze_package.py` 不是本 skill 当前流程的直接调用脚本，不要在本 skill 中展开操作说明。

### 失败处理

- 若权威步骤需要容器，但 `oe-build-env` 不存在或不可用：应阻断并写清原因，不允许静默回退到本地目录或任意非官方数据源。
- 若只是宿主机静态分析步骤：不得为了“方便”强制放进容器执行。

---

## 主流程

### 1. 初始化（仅顶层）

- 顶层包调用（未设置 `--install`）时初始化 `./build_state`、`./reports`、`./sources`。
- 依赖包调用（已设置 `--install`）跳过此步，复用顶层状态文件。

```bash
# 检查上次是否有异常退出留下的残留
if [ -s ./build_state/building.txt ]; then
  echo "[警告] building.txt 有残留，上次可能异常退出，涉及包："
  cat ./build_state/building.txt
fi
rm -rf ./build_state
mkdir -p ./build_state ./reports ./sources
touch ./build_state/building.txt ./build_state/introduced.txt
```

### 2. 上游合规检查

执行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/check_repo.py <upstream_url> \
  -o reports/repo_check_<pkgname>.json
```

检查 `blocking`、`message`、`platform`、`last_updated`。

- 若 `blocking: true`：按“统一失败处理”写结果并终止。
- 若 API 查询失败但非阻断：允许继续，但需在最终报告中注明。

### 3. 下载源码

统一使用 `download_source.py`：

```bash
rm -rf ./sources/<pkgname>
python3 ${CLAUDE_SKILL_DIR}/scripts/download_source.py \
  --upstream-url <upstream_url> \
  --output-dir ./sources -o reports/download_result_<pkgname>.json
```

### 4. License 检查

执行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/check_license.py ./sources/<pkgname> \
  --pkg <pkgname> -o reports/license_check_<pkgname>.json
```

检查 `blocking`、`category`、`license_ids`、`message`。

- 若 License 阻断：按“统一失败处理”写结果并终止。

### 5. 识别语言与版本

根据源码目录识别 `<lang>`，并提取 `<version>`。要求在 existing-check 前必须拿到二者。

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


- 若版本无法可靠确定：按“统一失败处理”写结果并终止。

### 6. 准备容器并注入双源

在执行权威 `existing-check` 前，先确保 `oe-build-env` 已存在且可用，并在容器内同时具备：
- OpenEuler 官方 DNF 软件源
- `archive-rpm-sources/config.json` 中 `repo.remote_url` 对应的 AI RPM 源

顶层包调用：先删除旧容器，再调用 `/setup-build-env ./sources/<pkgname>` 重建干净环境。  
依赖包调用：要求复用已有 `oe-build-env`；若容器不存在，按“统一失败处理”阻断。

```bash
docker stop oe-build-env 2>/dev/null || true
docker rm   oe-build-env 2>/dev/null || true
```

### 7. 执行权威 existing-check

执行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/check_existing_package.py <pkgname> \
  --version <version> \
  --lang <lang> \
  --container oe-build-env \
  -o reports/existing_check_<pkgname>.json
```

权威语义固定为：
- `official` = `oe-build-env` 容器内可见的 **OpenEuler 官方 DNF 软件源**
- `user_repo` = `oe-build-env` 容器内注入的 **AI RPM 软件源**（源地址来自 `archive-rpm-sources/config.json` 的 `repo.remote_url`）

读取 `decision`、`reason`、`official.highest.version`、`user_repo.highest.version`、`should_skip`。

- 若 `decision` 为 `reuse_*` 或 `block_*`：按“决策语义”和“统一失败/结果写入规则”立即处理并结束流程。
- 若 `decision` 为 `upgrade_user_repo` 或 `introduce_new`：进入构建分支。

### 8. 构建分支：上传源码 tarball

```bash
cp -r ./sources/<pkgname> /tmp/<pkgname>-<version>
tar czf /tmp/<pkgname>-<version>.tar.gz -C /tmp <pkgname>-<version>
rm -rf /tmp/<pkgname>-<version>
docker cp /tmp/<pkgname>-<version>.tar.gz oe-build-env:/tmp/
```

### 9. 构建分支：调用 `build-rpm`

```
/build-rpm <pkgname> <lang> <upstream_url> <version> [--install] [--depth N]
```

- 顶层包调用：不传 `--install`
- 依赖包调用：传入 `--install`，透传 `--depth N`

- 若 `build-rpm` 成功：更新 `pkg_introduce_result_<pkgname>.json` 的 `action` 为真实值。
- 若 `build-rpm` 失败：按“统一失败处理”写结果并终止。

### 10. 构建分支：归档（仅顶层且实际发生构建时）

仅当：
- 顶层包调用（未设置 `--install`）
- 且本次 `action ∈ {built_new, upgraded_user_repo}`

执行：

```bash
INTRODUCED=$(sort -u ./build_state/introduced.txt | tr '\n' ' ')
ALL_PKGS="<pkgname> ${INTRODUCED}"
```

```
/archive-rpm-sources --pkgs <ALL_PKGS>
```

要求：
- `introduced.txt` 只包含 **实际 built/upgraded** 的依赖
- `reuse_*` 不进入归档集合
- 归档成功后更新结果文件中的 `archived=true`

### 11. 输出结果

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

构建成功后再更新为：
- `built_new` 或 `upgraded_user_repo`

---

## 附录：结果与日志

### 结果输出建议

顶层包建议格式：

```
========================================
pkg-introduce 结果：<pkgname>
========================================
上游地址    : <upstream_url>
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
  decision=<decision>  action=<action>  lang=<lang>  version=<version>
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
- `import-package` 的预扫描不是最终 skip 条件；最终是否复用，必须以第六步的权威 existing-check 为准
