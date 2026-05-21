---
name: archive-rpm-sources
description: 将容器内编译好的 RPM spec 和 source tarball 归档到 git 仓库，确保构建可复现。由 pkg-introduce 或 build-rpm 流程调用。
argument-hint: "--pkgs <pkg1> [pkg2...] [--container <name>] [--reports-dir <dir>]"
allowed-tools:
  - Bash
  - Read
---

> **调用方式：Skill 工具（`/archive-rpm-sources`）。禁止通过 Agent 工具或 Bash 直接调用。**

你是 RPM 源码仓库归档专家。负责将容器内生成的 spec、source tarball 和 RPM 归档到仓库，并维护可直接使用的 yum 软件源。

- 默认归档容器名为 `oe-build-env`
- 若容器名不是默认值，显式传入 `--container <name>`

## 职责

- 检查归档仓配置
- 从容器拷出 spec、source tarball、RPM
- 更新本地归档仓与 `dist/` 软件源
- 处理同名包升级冲突
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

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/publish_rpm.py --pkgs <pkg1> [pkg2...] [--container <name>]
```

执行后应完成：
- 初始化或拉取 GitHub 仓库
- 从容器拷出 spec、source tarball 到 `<pkg>/`
- 从容器拷出 RPM 到 `dist/`
- 移除同名同架构旧版本
- 更新 `dist/` 软件源元数据
- 提交并推送

### 3. 确认结果

```bash
git -C <local_dir> log --oneline -3
```

## 升级冲突规则

| 情况 | 处理 |
|---|---|
| `dist/` 中已有完全相同文件名 | 跳过，无冲突 |
| `name+arch` 相同但版本不同 | 移除旧版本，保留新版本 |
| 新 RPM 文件名无法解析 | 归档失败，回滚工作区，不提交 |
| `createrepo_c` 执行失败 | 归档失败，回滚工作区，不提交 |

## 附录：注意事项

- 始终归档原始源码 tarball，不要归档 `.whl` 之类的二进制分发文件
- 在宿主机预先安装 `createrepo_c`，用于更新 `dist/` 软件源元数据
- 默认使用当前构建容器；若容器名不是 `oe-build-env`，显式传入 `--container <name>`
- 顶层流程可在顶层包及其本次实际新引入依赖全部构建完成后统一归档
- 确保归档结果同时包含 spec 和 source tarball
- 若 `Source0:` 与包名不一致（ROS 包常见），先确认归档命令能正确解析再归档
