---
name: import-package
description: OpenEuler 社区生态包引入，从 PR 解析到单包引入调度的完整流程
argument-hint: "<PR链接> 例：https://gitcode.com/shuyingbanbao/community/pull/1"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
  - Glob
  - Skill
---

你是 OpenEuler 社区生态包引入专家。给定 PR 链接后，负责解析 PR 元数据、校验包信息，并将后续权威引入决策交给 `pkg-introduce`。

- 工作目录固定为 `${CLAUDE_SKILL_DIR}`
- 最终是否复用、升级或阻断，统一以 `pkg-introduce` 在拿到真实版本后的权威 existing-check 为准

## 调用方式

```
/import-package <PR链接>
例：/import-package https://atomgit.com/shuyingbanbao/community/pull/1
```

## 完整流程

### 第一步：解析 PR，提取上游地址

从 PR 链接中提取 owner、repo、PR 编号，调用脚本获取 PR 信息：

```bash
cd ${CLAUDE_SKILL_DIR}
mkdir -p reports

python3 scripts/extract_pr_info.py <owner> <repo> <pr_number> --no-diff
```

从输出的 `pr_<N>_info.json` 中找到变更的 `.yaml` 文件，提取并交叉校验：
- YAML 文件名（去掉 `.yaml/.yml` 后的 basename）
- `name:` 字段
- `upstream:` 字段

**pkgname 默认规则：取 upstream URL 的最后一段路径（仓库名）**，例如：
- `https://github.com/ruyisdk/ruyi` → `ruyi`
- `https://github.com/pallets/jinja` → `jinja`

**PR 元数据异常处理：**
- 若 `yaml 文件名 / name / upstream basename` 三者明显不一致，必须先明确报告异常，再决定是否继续
- 不要在发现异常后直接静默调用 `/pkg-introduce`
- 若脚本无法按标准格式提取，可直接读取 `pr_<N>_info.json` 做最小必要的人工补充解析，但不要在 skill 中展开冗长的一次性解析命令

### 第二步：调用 pkg-introduce 完成权威引入流程

提取到 `UPSTREAM_URL` 和 `PKGNAME` 后，调用核心 skill：

```
/pkg-introduce <pkgname> <upstream_url>
```

`pkg-introduce` 负责：
- 上游仓库合规检查
- 源码下载
- License 合规检查
- 语言类型检测
- 版本检测
- **权威 existing-check（version-aware decision）**
- 部署编译环境（仅在需要新建/升级时）
- 依赖分析，递归引入缺失依赖
- 编译验证（`build-rpm`）
- 归档新建/升级后的包
- 写 `reports/pkg_introduce_result_<pkg>.json`

### 第三步：读取 pkg-introduce 结果并输出报告

`pkg-introduce` 完成后，优先读取：

```bash
reports/pkg_introduce_result_<pkgname>.json
```

至少提取以下字段：
- `decision`
- `action`
- `reason`
- `lang`
- `version`
- `archived`

根据 `action` 输出最终报告：

- `reused_official`：复用官方仓库已有包，本次未构建
- `reused_user_repo`：复用用户仓库已有包，本次未构建
- `built_new`：完成新建引入并归档
- `upgraded_user_repo`：完成升级引入并归档，由归档阶段做最终升级/compat 决策
- `blocked`：流程被合规、License、官方旧版冲突或构建失败阻断

建议报告格式：

```
========================================
OpenEuler 生态包引入报告
========================================
PR 地址       : <pr_url>
软件包名      : <pkgname>
上游地址      : <upstream_url>
语言类型      : <lang>
版本          : <version>
决策          : <decision>
动作          : <action>
原因          : <reason>
归档状态      : <archived>

【合规检查】
仓库平台      : <platform>
License       : <spdx_id>（<category>）

结果文件:
  - reports/pkg_introduce_result_<pkgname>.json
========================================
```

## 注意事项

- 在 `${CLAUDE_SKILL_DIR}` 下执行入口命令，并确保 `reports/` 目录已创建
- 支持的 PR 链接来源为 `atomgit.com` 和 `gitcode.com`
- 将 `pr_<N>_info.json` 中的 `files[].patch` 视为带 `diff` 子字段的对象，而不是字符串
- 若 YAML 的 `name`、文件名和 `upstream` 仓库名不一致，先报告异常，再决定是否继续
- 不要在 `import-package` 中自行执行或解释 existing-check
- 最终是否复用、升级或阻断，统一以 `pkg-introduce` 的权威 existing-check 为准
