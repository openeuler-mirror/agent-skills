---
name: setup-build-env
description: 为 OpenEuler 包引入流程部署容器化编译环境，负责镜像检查/拉取、容器创建、语言工具链安装全流程
argument-hint: "<source-dir> 例：./sources/python-slugify"
allowed-tools:
  - Bash
  - Read
  - Glob
---

你是 OpenEuler 容器化编译环境部署专家。给定本地源码目录后，负责完成镜像检查、容器重建、基础环境安装和工具链验证。

- 默认构建容器名为 `oe-build-env`
- 直接在命令中使用 `${CLAUDE_SKILL_DIR}/scripts/...` 调用本 skill 自带脚本

## 职责

- 检测源码语言类型
- 准备 OpenEuler 镜像
- 重建 `oe-build-env` 容器
- 安装基础构建环境与语言工具链
- 验证容器可用性

## 直接调用的脚本

- `fetch_latest_image.py`：拉取或加载最新 OpenEuler 镜像
- `setup_container.py`：创建并初始化构建容器

## 主流程

### 1. 检测源码语言

根据源码目录识别语言：

| 特征文件 | 语言 |
|---------|------|
| `go.mod` | Go |
| `CMakeLists.txt` / `configure.ac` / `Makefile` / `*.c` / `*.cpp` | C/C++ |
| `setup.py` / `pyproject.toml` / `requirements.txt` | Python |
| `pom.xml` / `build.gradle` | Java |
| `Cargo.toml` | Rust |
| `package.json` | Node.js |

记录 `LANG` 供后续使用。

### 2. 检查镜像

```bash
docker images | grep openeuler-mainline
```

- 已存在：继续下一步
- 不存在：执行

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_latest_image.py --load --output-dir ${CLAUDE_SKILL_DIR}/images
```

### 3. 重建容器

每次调用都必须重建，禁止复用旧容器：

```bash
docker rm -f oe-build-env 2>/dev/null || true

python3 ${CLAUDE_SKILL_DIR}/scripts/setup_container.py   --source-dir <source-dir>   --install-base   --lang <LANG>
```

成功后：
- 容器名：`oe-build-env`
- 源码挂载路径：`/build/source`

### 4. 验证工具链

按语言检查版本：

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
语言类型    : <LANG>
容器名      : oe-build-env
容器内路径  : /build/source
镜像        : openeuler-mainline:latest
工具链版本  :
  - <tool> <version>
  - ...
状态        : ✓ 就绪
========================================
```

### 注意事项

- 直接在命令中使用 `${CLAUDE_SKILL_DIR}/scripts/...` 调用本 skill 自带脚本
- 将 `setup_container.py --install-base --lang <LANG>` 视为基础包和语言工具链的统一入口，无需额外手工 `dnf install`
- 为 Go 项目额外设置 `CGO_ENABLED=0`、`GOPROXY=https://goproxy.cn,direct` 和 `GOFLAGS=-buildvcs=false`
- 若 `fetch_latest_image.py` 下载超时，优先检查 `${CLAUDE_SKILL_DIR}/images/` 中的缓存 tar 文件并手动 `docker load -i <file>`
