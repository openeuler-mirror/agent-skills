# openEuler RPM 包引入自动化系统

基于 Claude Code 的 AI Agent 流水线，将 openEuler RPM 包引入从"提供上游地址"到"归档入库"全程自动化。

## 前置依赖

### 必须安装的软件

| 软件 | 版本要求 | 说明 |
|------|----------|------|
| Docker | 20.10+ | 构建容器（需支持 `linux/arm64` 平台） |
| Python | 3.9+ | 脚本依赖 |
| Git | 任意 | 归档仓操作 |
| rpmbuild / rpm-build | — | 容器内已包含，宿主机无需安装 |

> Docker 需能拉取 `repo.openeuler.org` 的 aarch64 镜像（默认使用 openEuler 24.03 LTS SP3）。

### Python 依赖

```bash
pip install requests
```

---

## 配置

系统有两个配置文件需要在首次使用前填写。

### 1. 包引入主配置

复制示例文件并填写：

```bash
cp .claude/skills/pkg-introduce/config.json.example \
   .claude/skills/pkg-introduce/config.json
```

编辑 `config.json`：

```jsonc
{
  "gitcode": {
    "base_url": "https://gitcode.com/api/v5",
    "token": "<YOUR_GITCODE_TOKEN>"        // GitCode 个人访问令牌
  },
  "repo": {
    "remote_url": "https://gitcode.com/<org>/<rpm-repo>.git",  // RPM 归档仓地址
    "branch": "main",
    "local_dir": "/root/.claude/skills/rpm-repo"               // 本地克隆路径
  },
  "dep_conflict": {
    "mode": "force_compat"
    // block: 社区源有旧版时阻断（默认安全）
    // compat: 用 compat 包名共存（nodejs/c/cpp/java/ruby）
    // force_compat: 所有语言强制 compat
  }
}
```

### 2. 归档仓配置

```bash
cp .claude/skills/archive-rpm-sources/config.json.example \
   .claude/skills/archive-rpm-sources/config.json
```

编辑 `config.json`：

```jsonc
{
  "github": {
    "token": "<YOUR_GITHUB_TOKEN>",        // GitHub 个人访问令牌（如归档仓在 GitHub）
    "username": "<YOUR_GITHUB_USERNAME>"
  },
  "repo": {
    "remote_url": "https://github.com/<org>/<rpm-repo>.git",
    "branch": "arm_test",
    "local_dir": "/root/.claude/skills/rpm-repo-github"
  }
}
```

---

## 使用方法

### 引入一个包

```bash
/import-package <pkgname> <upstream_url> [--version <ver>]
```

示例：

```bash
# 引入最新稳定版
/import-package copy-to-clipboard https://github.com/sudodoki/copy-to-clipboard

# 指定版本
/import-package copy-to-clipboard https://github.com/sudodoki/copy-to-clipboard --version 3.3.1
```

流程会自动完成：合规检查 → 版本决策 → 依赖解析 → spec 生成 → rpmbuild → 质量审核 → 归档入库。

### 支持的语言

Python、Node.js、Go、Rust、Java、C/C++、Ruby

### 查看引入结果

引入完成后，产物写入 `<pkgname>/pkgs/<pkgname>/` 目录：

```
gate_result_<pkgname>.json   评估决策
<pkgname>.spec               生成的 spec 文件
build_rpm_result.json        构建结果
build.log                    rpmbuild 原始输出
rpmlint.txt                  rpmlint 检查结果
feedback_<pkgname>.json      经验总结
```

---
