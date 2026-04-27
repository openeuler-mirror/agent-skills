# import-package skill set

OpenEuler 包引入自动化 skill 集合，支持从 PR 链接一键完成合规检查、源码下载、RPM 构建、依赖递归引入和归档发布。

## Skills 列表

| Skill | 职责 |
|-------|------|
| `import-package` | 入口：解析 PR 链接，提取包信息，调度引入流程 |
| `pkg-introduce` | 单包引入协调：合规检查 → 下载 → License → 构建 → 归档 |
| `build-rpm` | RPM 构建核心：spec 生成、rpmbuild 循环、依赖递归引入 |
| `archive-rpm-sources` | 归档发布：spec/tarball/RPM 推送到 GitHub RPM 仓库 |
| `setup-build-env` | 容器化编译环境：OpenEuler 镜像准备、容器初始化 |
| `resolve-rpm-conflicts` | RPM 冲突解决：识别冲突类型并自动修复 |

## 调用链

```
import-package <PR链接>
  └─ pkg-introduce <pkgname> <upstream_url>
       ├─ check_repo / download_source / check_license
       ├─ setup-build-env
       └─ build-rpm <pkgname> <lang> <url> <version>
            ├─ pre_check_deps → pkg-introduce <dep> --install --depth N
            └─ rpmbuild 循环（最多10轮）
       └─ archive-rpm-sources --pkgs <pkgname> [deps...]
```

## 配置

每个 skill 目录下有 `config.json.example`，复制为 `config.json` 并填入真实 token：

- `import-package/config.json` — Gitcode API token（用于解析 PR）
- `pkg-introduce/config.json` — Gitcode API token（用于查询 RPM 仓库）
- `archive-rpm-sources/config.json` — GitHub token + RPM 仓库地址

## 安装

将各 skill 目录放置到 Claude Code skills 目录（通常为 `~/.claude/skills/`），Claude Code 会自动识别 `SKILL.md`。

## 支持语言

Go / Python / Rust / C / C++ / Java / Node.js / Ruby
