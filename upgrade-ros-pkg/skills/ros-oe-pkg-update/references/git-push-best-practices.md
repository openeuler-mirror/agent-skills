# Git Push 最佳实践

本文总结了在推送 ROS 包更新到 GitCode 时遇到的问题、解决方法和常用命令。

## 问题 1: 大文件处理（> 10MB）⚠️ 重要

### 问题描述

推送大文件（>10 MiB）时 GitCode 报错：

```
error: failed to push some refs to 'gitcode.com:your-gitcode-username/navigation2.git'
remote: Error: Deny by project hooks setting 'default': size of the file 'package.tar.gz', is 21 MiB, which has exceeded the limited size (10 MiB)
remote: The file 'package.tar.gz' should be tracked using Git LFS
```

### ⚠️ 重要：EUR 不支持 Git LFS

**虽然 GitCode 支持 LFS，但 EUR 构建环境不支持！**

如果使用 LFS，EUR 构建会失败：
```
tar: This does not look like a tar archive
```

原因：EUR 的 `rpkg` 不会执行 `git lfs pull`，读取到的是 LFS 指针文件。

### ✅ 正确解决方案：使用 split 切分大包

**步骤 1: 切分 tar 包**

```bash
# 切分为 8MB 的分片
split -b 8M package-1.0.0.tar.gz package-1.0.0.tar.gz.

# 查看生成的分片
ls -lh package-1.0.0.tar.gz.*
# -rw-r--r-- 1 user user 8.0M Mar 28 10:42 package-1.0.0.tar.gz.aa
# -rw-r--r-- 1 user user 8.0M Mar 28 10:42 package-1.0.0.tar.gz.ab
# -rw-r--r-- 1 user user 5.2M Mar 28 10:42 package-1.0.0.tar.gz.ac

# 删除原始大包
rm package-1.0.0.tar.gz
```

**步骤 2: 修改 spec 文件**

```spec
Source0:        %{RosPkgName}-%{version}.tar.gz.aa
Source1:        %{RosPkgName}-%{version}.tar.gz.ab
Source2:        %{RosPkgName}-%{version}.tar.gz.ac

%prep
cat %{SOURCE0} %{SOURCE1} %{SOURCE2} > %{RosPkgName}-%{version}.tar.gz
tar -xzf %{RosPkgName}-%{version}.tar.gz
%autosetup -T -D -p1 -n %{RosPkgName}-%{version}
```

**步骤 3: 提交并推送**

```bash
# 添加分片文件和修改后的 spec
git add package-1.0.0.tar.gz.* package.spec

# 提交
git commit -m "Upgrade package to 1.0.0 with split tarball"

# 推送（分片文件小于 10MB，可以正常推送）
git push origin humble
```

### ❌ 不推荐：Git LFS（仅用于非 EUR 构建）

**警告**：如果你的包需要 EUR 构建，**不要使用 LFS**！

如果只是推送到 GitCode 且不需要 EUR 构建，可以配置 LFS：

#### 步骤 1: 在 GitCode 项目设置中启用 LFS

1. 登录 GitCode 网站
2. 进入项目页面
3. 点击 **设置** → **仓库设置**
4. 找到 **LFS 设置** 区域
5. **关闭** "启用自定义 LFS 存储源" 选项 ⚠️
6. 保存设置

**⚠️ 重要**: 必须关闭"启用自定义 LFS 存储源"，而不是启用它。

#### 步骤 2: 使用 Git LFS 命令行

```bash
# 1. 撤销最后一次提交（保留更改）
git reset --soft HEAD~1

# 2. 初始化 Git LFS
git lfs install

# 3. 配置 LFS 跟踪大文件
git lfs track "*.tar.gz"

# 4. 添加 .gitattributes
git add .gitattributes

# 5. 重新提交所有更改
git add .
git commit -m "Upgrade package with LFS

- Update spec files to latest upstream version
- Synchronize tarballs (using Git LFS)

ROS2 Humble version upgrade"

# 6. 推送到远程
git push origin humble
```

### 大文件处理决策树

```
需要 EUR 构建?
│
├─ YES → 使用 split 切分（推荐）
│         │
│         ├─ split -b 8M file.tar.gz file.tar.gz.
│         ├─ 修改 spec: Source0/1/2 + %prep
│         └─ git add *.tar.gz.* && git push
│
└─ NO  → 可以使用 LFS
          │
          ├─ 在 GitCode 设置中关闭自定义 LFS
          ├─ git lfs track "*.tar.gz"
          └─ git push
```

### 验证推送成功

**split 切分方式**：
```
remote: Start Git Hooks Checking 					[PASSED]
To gitcode.com:your-gitcode-username/package.git
   abc123..def456  humble -> humble
```

**LFS 方式**：
```
Uploading LFS objects: 100% (40/40), 45 MB | 14 MB/s, done.
remote: Start Git Hooks Checking 					[PASSED]
```

### 常见错误

#### 错误 1: "project lfs not enabled"

```
batch response: {"message": "Access forbidden. project lfs not enabled.", "documentation_url": ""}
```

**解决**:
1. 在 GitCode 项目设置中关闭"启用自定义 LFS 存储源"
2. 或改用 split 切分方式（推荐用于 EUR 构建）

#### 错误 2: EUR 构建时 tar 解压失败

```
tar: This does not look like a tar archive
```

**原因**: 使用了 LFS，但 EUR 不支持

**解决**: 改用 split 切分方式

---

## 问题 2: Non-fast-forward 推送失败

### 问题描述

```bash
error: failed to push some refs to 'gitcode.com:your-gitcode-username/ros2cli.git'
hint: Updates were rejected because the tip of your current branch is behind
hint: its remote counterpart. Integrate the remote changes (e.g.
hint: 'git pull ...') before pushing again.
```

### 根本原因

本地分支落后于远程分支，可能的原因：
1. 远程有其他提交
2. 本地使用了 `git reset --soft` 回退
3. 远程分支被强制推送过

### 解决方法

#### 方法 1: Force Push（仅在确认本地更改正确时使用）

```bash
git push origin humble --force
```

**⚠️ 警告**:
- **仅在确认本地更改正确且不需要远程更改时使用**
- 会覆盖远程分支的历史
- 团队协作时慎用，- 确保其他协作者知晓

#### 方法 2: Pull + Rebase（推荐用于团队协作）

```bash
# 1. 拉取远程更改并 rebase
git pull --rebase origin humble

# 2. 解决冲突（如果有）
# 查看冲突文件
git status

# 手动解决冲突后
git add <resolved_files>
git rebase --continue

# 3. 推送
git push origin humble
```

### 如何选择

**使用 Force Push 的场景**:
- 个人项目，确认本地更改正确
- 远程更改不重要或已过时
- 需要快速更新远程分支

**使用 Pull + Rebase 的场景**:
- 团队协作项目
- 需要保留远程更改
- 不确定远程有什么更改

---

## 批量提交推送脚本

### 场景

需要批量提交并推送多个仓库的更新，其中部分仓库包含大文件需要 LFS。

### 完整脚本

```bash
#!/bin/bash

# 批量提交并推送所有仓库（英文 commit 信息，自动执行）
# 支持 LFS 和 force push

REPOS=(
  "ros2cli"
  "navigation2"
  "angles"
  "bond_core"
  "diagnostics"
  "joint_state_publisher"
  "pcl_msgs"
  "perception_pcl"
  "realsense-ros"
  "rtabmap_ros"
  "vision_opencv"
  "xacro"
  "BehaviorTree.CPP"
  "ompl"
  "librealsense"
  "rtabmap"
)

BASE_DIR="/path/to/src-openeuler-forked"

echo "========================================"
echo "Batch Commit and Push All Repositories"
echo "========================================"

success_count=0
failed_count=0
failed_repos=()

for repo in "${REPOS[@]}"; do
  repo_dir="$BASE_DIR/$repo"

  echo ""
  echo "----------------------------------------"
  echo "Processing: $repo"
  echo "----------------------------------------"

  cd "$repo_dir" || {
    echo "✗ Cannot enter directory: $repo_dir"
    failed_count=$((failed_count + 1))
    failed_repos+=("$repo (directory not found)")
    continue
  }

  # 1. Add all changes
  echo "1. Adding all changes..."
  git add -A

  # 2. Extract version information
  main_spec=$(ls *.spec 2>/dev/null | head -1)
  if [ -n "$main_spec" ]; then
    version=$(grep "^Version:" "$main_spec" | awk '{print $2}' | head -1)

    # Generate commit message in English
    commit_msg="Upgrade $repo to version $version

- Update spec files to latest upstream version
- Synchronize tarballs
- Update patches if any

ROS2 Humble version upgrade"
  else
    commit_msg="Upgrade $repo

- Update spec files
- Synchronize tarballs

ROS2 Humble version upgrade"
  fi

  # 3. Commit
  echo "2. Committing changes..."
  echo "Version: $version"

  if git commit -m "$commit_msg"; then
    echo "✓ Commit successful"

    # 4. Push
    echo "3. Pushing to origin humble..."
    if git push origin humble; then
      echo "✓ Push successful"
      success_count=$((success_count + 1))
    else
      # 5. Try force push if normal push fails
      echo "⚠ Push failed, trying with --force..."
      if git push origin humble --force; then
        echo "✓ Force push successful"
        success_count=$((success_count + 1))
      else
        echo "✗ Force push failed"
        failed_count=$((failed_count + 1))
        failed_repos+=("$repo (push failed)")
      fi
    fi
  else
    echo "✗ Commit failed"
    failed_count=$((failed_count + 1))
    failed_repos+=("$repo (commit failed)")
  fi
done

echo ""
echo "========================================"
echo "Batch Commit and Push Complete"
echo "========================================"
echo "Success: $success_count"
echo "Failed: $failed_count"

if [ $failed_count -gt 0 ]; then
  echo ""
  echo "Failed repositories:"
  printf '  - %s\n' "${failed_repos[@]}"
fi
```

### 使用方法

```bash
# 1. 保存脚本
vim push_package_updates.sh

# 2. 修改 REPOS 数组和 BASE_DIR
# 3. 执行脚本
chmod +x push_package_updates.sh
./push_package_updates.sh 2>&1 | tee commit_push.log
```

---

## Commit 信息规范

### 英文 Commit 信息格式（推荐）

```
Upgrade <package_name> to version <version>

- Update spec files to latest upstream version
- Synchronize tarballs
- Update patches if any

ROS2 Humble version upgrade
```

### 示例

```
Upgrade ros2cli to version 0.18.18

- Update spec files to latest upstream version
- Synchronize tarballs
- Update patches if any

ROS2 Humble version upgrade
```

```
Upgrade navigation2 to version 1.1.20

- Update spec files to latest upstream version
- Synchronize tarballs (using Git LFS)

ROS2 Humble version upgrade
```

### 中文 Commit 信息格式（可选）

```
升级 <package_name> 到版本 <version>

- 更新 spec 文件到最新上游版本
- 同步 tar 包
- 更新补丁（如有）

ROS2 Humble 版本升级
```

### Commit 信息最佳实践

1. **使用祈使句**: "Upgrade" 而不是 "Upgraded" 或 "Upgrading"
2. **首字母大写**: "Upgrade" 而不是 "upgrade"
3. **简洁明了**: 第一行不超过 50 个字符
4. **详细说明**: 在空行后添加详细的变更列表
5. **版本信息**: 包含旧版本 → 新版本信息（如果知道）
6. **关联信息**: 添加 ROS 版本信息（如 "ROS2 Humble version upgrade"）
7. **LFS 说明**: 如果使用 LFS，在第二行注明 "(using Git LFS)"

---

## 检查仓库状态

### 查看所有仓库状态脚本

```bash
#!/bin/bash

REPOS=(
  "ros2cli"
  "navigation2"
  "angles"
  "bond_core"
  # ... 更多仓库
)

BASE_DIR="/path/to/src-openeuler-forked"

echo "========================================"
echo "检查所有仓库的 Git 状态"
echo "========================================"

for repo in "${REPOS[@]}"; do
  repo_dir="$BASE_DIR/$repo"
  if [ -d "$repo_dir" ]; then
    cd "$repo_dir"
    staged=$(git diff --staged --name-only 2>/dev/null | wc -l)
    unstaged=$(git diff --name-only 2>/dev/null | wc -l)
    untracked=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l)

    echo "$repo:"
    echo "  Staged: $staged, Unstaged: $unstaged, Untracked: $untracked"
  fi
done
```

### 输出示例

```
ros2cli:
  Staged: 45, Unstaged: 0, Untracked: 0
navigation2:
  Staged: 120, Unstaged: 0, Untracked: 0
angles:
  Staged: 3, Unstaged: 0, Untracked: 0
```

---

## 实战案例

### 案例 1: 推送16个 ROS 包更新

**场景**: 需要推送16个 ROS 包的更新到 GitCode，其中4个包含大文件需要 LFS。

**执行步骤**:

1. **检查仓库状态**
   ```bash
   ./check_workspace_status.sh
   ```

2. **配置 LFS**（对于大文件仓库）
   - 在 GitCode 项目设置中关闭"启用自定义 LFS 存储源"
   - 项目: navigation2, realsense-ros, librealsense, rtabmap

3. **批量提交推送**
   ```bash
   ./push_package_updates.sh
   ```

4. **结果验证**
   ```
   成功: 16/16 仓库
   LFS 上传: 4个仓库，总计 121 MB
     - navigation2: 45 MB (40 个文件)
     - realsense-ros: 21 MB (3 个文件)
     - librealsense: 33 MB (1 个文件)
     - rtabmap: 22 MB (1 个文件)
   Force push: 12个仓库
   ```

### 案例 2: 处理 LFS 推送失败

**问题**: navigation2 推送失败，提示 LFS 未启用

**解决步骤**:

```bash
# 1. 在 GitCode 项目设置中关闭"启用自定义 LFS 存储源"

# 2. 撤销最后一次提交
cd navigation2
git reset --soft HEAD~1

# 3. 配置 LFS
git lfs install
git lfs track "*.tar.gz"
git add .gitattributes

# 4. 重新提交
git add .
git commit -m "Upgrade navigation2 to version 1.1.20

- Update spec files to latest upstream version
- Synchronize tarballs (using Git LFS)

ROS2 Humble version upgrade"

# 5. 推送
git push origin humble
```

**成功标志**:
```
Uploading LFS objects: 100% (40/40), 45 MB | 14 MB/s, done.
remote: Start Git Hooks Checking 					[PASSED]
To gitcode.com:your-gitcode-username/navigation2.git
   6edaa60..912c7b9  humble -> humble
```

### 案例 3: 处理 Non-fast-forward 推送失败

**问题**: ros2cli 推送失败，提示 non-fast-forward

**解决步骤**:

```bash
# 方法 1: Force push（确认本地更改正确）
cd ros2cli
git push origin humble --force

# 或方法 2: Pull + Rebase（需要保留远程更改）
git pull --rebase origin humble
git push origin humble
```

---

## 常用命令速查

### 仓库状态检查

```bash
# 检查仓库状态
git status

# 查看暂存的文件
git diff --staged --name-only

# 查看未暂存的文件
git diff --name-only

# 查看未跟踪的文件
git ls-files --others --exclude-standard

# 查看所有分支
git branch -a
```

### Git 基本操作

```bash
# 添加所有更改
git add -A

# 添加特定文件
git add <file>

# 提交
git commit -m "message"

# 查看提交历史
git log --oneline -10

# 查看最后一次提交
git show
```

### Git Push 操作

```bash
# 推送到远程
git push origin humble

# Force push
git push origin humble --force

# 推送并设置上游跟踪
git push -u origin humble

# 删除远程分支
git push origin --delete <branch>
```

### Git Reset 操作

```bash
# 撤销最后一次提交（保留更改）
git reset --soft HEAD~1

# 撤销最后一次提交（保留工作目录更改）
git reset --mixed HEAD~1

# 撤销最后一次提交（丢弃所有更改）
git reset --hard HEAD~1
```

### Git LFS 操作

```bash
# 初始化 Git LFS
git lfs install

# 跟踪大文件
git lfs track "*.tar.gz"
git lfs track "*.zip"
git lfs track "*.mp4"

# 查看被 LFS 跟踪的文件
git lfs ls-files

# 查看 LFS 跟踪规则
cat .gitattributes

# 推送 LFS 对象
git lfs push --all origin humble

# 拉取 LFS 对象
git lfs pull
```

### Git Remote 操作

```bash
# 查看远程仓库
git remote -v

# 添加远程仓库
git remote add origin <url>
git remote add upstream <url>

# 删除远程仓库
git remote remove <name>

# 更新远程仓库 URL
git remote set-url origin <new-url>

# 拉取远程分支
git fetch origin
git fetch upstream
```

---

## 总结

### 关键要点

1. **LFS 配置**: 大文件（>10 MiB）必须使用 LFS
   - 在 GitCode 项目设置中**关闭**"启用自定义 LFS 存储源"
   - 使用 `git lfs track` 跟踪大文件

2. **Force Push**: 谨慎使用，仅在确认本地更改正确时使用
   - 会覆盖远程分支历史
   - 团队协作时慎用

3. **批量处理**: 使用脚本自动化批量操作
   - 提高效率
   - 减少人为错误

4. **Commit 规范**: 使用英文，遵循格式规范
   - 清晰的版本信息
   - 详细的变更列表

5. **状态检查**: 操作前后都要检查仓库状态
   - 确保了解当前状态
   - 避免意外覆盖

### 最佳实践流程

1. **操作前检查**: `./check_workspace_status.sh`
2. **配置 LFS**（如需要）: 在 GitCode 项目设置中配置
3. **批量提交推送**: `./push_package_updates.sh`
4. **验证结果**: 检查所有仓库是否成功推送

### 常见错误排查

| 错误 | 原因 | 解决方法 |
|------|------|---------|
| project lfs not enabled | GitCode 项目 LFS 未配置 | 在项目设置中关闭"启用自定义 LFS 存储源" |
| non-fast-forward | 本地分支落后于远程 | Force push 或 pull + rebase |
| commit failed | 没有更改需要提交 | 检查 git status |
| push rejected | 远程拒绝推送 | 检查错误信息，使用 force push |

### 脚本文件

将以下脚本保存到 `<skill_dir>/scripts/` 目录：

1. `check_workspace_status.sh` - 检查仓库状态
2. `push_package_updates.sh` - 批量提交推送

### 参考资源

- [Git LFS 官方文档](https://git-lfs.github.com/)
- [GitCode 帮助文档](https://docs.gitcode.com/)
- [Git Force Push 文档](https://git-scm.com/docs/git-push#Documentation/git-push.txt---force)
