---
name: setup-build-env
description: 为 OpenEuler 包引入流程部署容器化编译环境，负责镜像检查/拉取、容器创建、语言工具链安装全流程
argument-hint: "<source-dir> <lang>"
allowed-tools:
  - Bash
  - Read
  - Glob
  - Skill
---

> **调用方式：Skill 工具（`/setup-build-env`）。禁止通过 Agent 工具或 Bash 直接调用。**

你是 OpenEuler 容器化编译环境部署专家。给定本地源码目录和上游已识别的语言类型后，负责完成镜像检查、容器重建、基础环境安装和工具链验证。

- 默认构建容器名为 `oe-build-env`
- `<lang>` 为上游传入的权威语言类型，`setup-build-env` 不再重复检测源码语言

## 参数

| 参数 | 说明 |
|------|------|
| `<source-dir>` | 本地源码目录，例如 `./sources/python-slugify` |
| `<lang>` | 上游传入的权威语言类型：`go` / `python` / `java` / `rust` / `nodejs` / `c` / `cpp` |

## 职责

- 消费上游传入的语言类型 `<lang>`
- 准备 OpenEuler 镜像
- 重建 `oe-build-env` 容器
- 安装基础构建环境与语言工具链
- 验证容器可用性

## 主流程

### 1. 读取上游传入的语言类型

- 直接消费调用方传入的 `<lang>`。
- 要求 `<lang>` 取值为：`go` / `python` / `java` / `rust` / `nodejs` / `c` / `cpp`。
- 若未提供 `<lang>` 或值不在支持列表中：立即失败，并提示调用方先完成权威语言识别。

### 2. 检查镜像

```bash
docker images | grep openeuler-25.09
```

- 已存在：继续下一步
- 不存在：执行

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_latest_image.py --load --output-dir ${CLAUDE_SKILL_DIR}/images
```

说明：
- `--load` 用于确保最新镜像被导入本地 Docker，供后续容器创建命令继续以 `openeuler-25.09:latest` 创建容器
- `--output-dir` 用于固定 tarball 缓存路径，便于复用和排障
- 镜像 tar 与容器内 OS / everything / update / EPOL 仓来自同一个固定 rc8 构建目录

### 3. 重建容器

每次调用都必须重建，禁止复用旧容器：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/setup_container.py --source-dir <source-dir> --install-base --lang <lang>
```

成功后：
- 容器名：`oe-build-env`
- 源码挂载路径：`/build/source`

### 4. 验证工具链

按 `<lang>` 检查版本：

| 语言 | 验证命令 |
|------|---------|
| go | `docker exec oe-build-env go version` |
| python | `docker exec oe-build-env python3 --version` |
| java | `docker exec oe-build-env java -version` |
| rust | `docker exec oe-build-env rustc --version` |
| nodejs | `docker exec oe-build-env node --version` |
| c/cpp | `docker exec oe-build-env gcc --version` |

## 附录：输出与注意事项

### 输出摘要

```
========================================
编译环境部署报告
========================================
源码目录    : <source-dir>
语言类型    : <lang>
容器名      : oe-build-env
容器内路径  : /build/source
镜像        : openeuler-25.09:latest
工具链版本  :
  - <tool> <version>
  - ...
状态        : ✓ 就绪
========================================
```

### 注意事项

- 将 `python3 ${CLAUDE_SKILL_DIR}/scripts/setup_container.py --source-dir <source-dir> --install-base --lang <lang>` 视为基础包和语言工具链的统一入口，无需额外手工 `dnf install`
- 为 Go 项目额外设置 `CGO_ENABLED=0`、`GOPROXY=https://goproxy.cn,direct` 和 `GOFLAGS=-buildvcs=false`
- 若 `python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_latest_image.py --load --output-dir ${CLAUDE_SKILL_DIR}/images` 下载超时，优先检查 `${CLAUDE_SKILL_DIR}/images/` 中的缓存 tar 文件并手动 `docker load -i <file>`
