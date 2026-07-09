# EUR Package 验证最佳实践

## 问题：仓库名映射错误导致 403 错误

### 根本原因

EUR package 的 `clone_url` 必须与 GitCode 上的**实际 fork 仓库名**匹配。如果配置错误，会导致：

```
fatal: unable to access 'https://gitcode.com/your-gitcode-username/ompl-release.git/': 
The requested URL returned error: 403
remote: <CH.00905403> The project you were looking for could not be found.
```

### 常见错误案例

| 包名 | 上游仓库名 | openEuler 仓库名 | 错误的 clone_url | 正确的 clone_url |
|------|-----------|-----------------|----------------|------------------|
| ompl | ompl-release | ompl | ❌ ompl-release.git | ✅ ompl.git |

### 验证步骤

**1. 检查 ros-pkg.list 映射**
```bash
grep "^ompl" ros-oe-upstream-init/output/ros-pkg.list
# 输出: ompl    ompl-release    1.7.0-1    None    master
# 第二列是上游仓库名，但 GitCode fork 仓库名可能不同
```

**2. 验证 GitCode 仓库是否存在**
```bash
git ls-remote https://gitcode.com/your-gitcode-username/ompl-release.git HEAD
# ❌ fatal: repository 'ompl-release' not found

git ls-remote https://gitcode.com/your-gitcode-username/ompl.git HEAD  
# ✅ 成功返回 commit hash
```

**3. 检查本地目录**
```bash
ls ~/workspace/src-openeuler-forked/
# 实际 fork 的仓库名：ompl (不是 ompl-release)
```

## 修复流程

**1. 删除错误的 EUR package**
```bash
copr-cli delete-package --name ompl PROJECT_NAME
```

**2. 使用正确的仓库名重新创建**
```bash
copr-cli add-package-scm \
  --name ompl \
  --clone-url https://gitcode.com/your-gitcode-username/ompl.git \
  --commit humble \
  --spec ompl.spec \
  --method rpkg \
  --type git \
  PROJECT_NAME
```

## 自动化验证脚本

```bash
#!/bin/bash
# scripts/verify_eur_packages.sh

FAILED_PKGS=(
  "angles"
  "navigation2"
  "ompl"
  # ... 其他包
)

for pkg in "${FAILED_PKGS[@]}"; do
  echo "=== Verifying $pkg ==="
  
  # 获取 EUR 配置
  config=$(copr-cli get-package --name $pkg PROJECT_NAME 2>&1)
  clone_url=$(echo "$config" | grep clone_url | awk '{print $2}' | tr -d ',')
  
  echo "Clone URL: $clone_url"
  
  # 验证仓库是否存在
  timeout 10 git ls-remote "$clone_url" HEAD >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "✅ Repository accessible"
  else
    echo "❌ Repository NOT accessible - needs fix!"
  fi
done
```

## 经验总结

### ✅ DO
1. **优先查看 EUR 构建日志**分析失败原因
2. **验证仓库名映射**确保 clone_url 正确
3. **检查本地目录名**与 EUR 配置是否一致

### ❌ DON'T
1. **不要假设仓库名**与包名相同
2. **不要忽略 403 错误**这通常意味着仓库名配置错误
3. **不要依赖 ros-pkg.list 的仓库名**它记录的是上游仓，不是 openEuler fork 仓

---

## 问题：大 tar 包（> 10MB）导致构建失败

### 根本原因

EUR 构建环境对大文件处理有以下限制：

1. **Git LFS 不支持**：EUR 的 rpkg 构建工具不会执行 `git lfs pull`，导致读取的是 LFS 指针文件而非真实 tar 包
   ```
   version https://git-lfs.github.com/spec/v1
   oid sha256:abc123...
   size 12345678
   ```

2. **GitHub Archive 链接不稳定**：将 Source0 改为 GitHub archive URL 会因网络问题导致构建失败
   ```
   error: curl: (35) OpenSSL SSL_connect: Connection reset by peer
   ```

3. **Git 单文件大小限制**：直接提交大文件可能超过 GitCode 的限制

### 解决方案：使用 split 切分大包

#### 步骤 1: 切分 tar 包

```bash
# 查看原始包大小
ls -lh rtabmap-0.22.1.tar.gz
# -rw-r--r-- 1 user user 21M Mar 28 10:42 rtabmap-0.22.1.tar.gz

# 切分为 8MB 的分片（推荐大小）
split -b 8M rtabmap-0.22.1.tar.gz rtabmap-0.22.1.tar.gz.

# 查看生成的分片
ls -lh rtabmap-0.22.1.tar.gz.*
# -rw-r--r-- 1 user user 8.0M Mar 28 10:42 rtabmap-0.22.1.tar.gz.aa
# -rw-r--r-- 1 user user 8.0M Mar 28 10:42 rtabmap-0.22.1.tar.gz.ab
# -rw-r--r-- 1 user user 5.2M Mar 28 10:42 rtabmap-0.22.1.tar.gz.ac

# 删除原始大包
rm rtabmap-0.22.1.tar.gz
```

#### 步骤 2: 修改 spec 文件

```spec
# 定义多个 Source（按字母顺序）
Source0:        %{RosPkgName}-%{version}.tar.gz.aa
Source1:        %{RosPkgName}-%{version}.tar.gz.ab
Source2:        %{RosPkgName}-%{version}.tar.gz.ac

%prep
# 1. 合并所有分片文件
cat %{SOURCE0} %{SOURCE1} %{SOURCE2} > %{RosPkgName}-%{version}.tar.gz

# 2. 手动解压 tar 包
tar -xzf %{RosPkgName}-%{version}.tar.gz

# 3. 使用特殊的 %autosetup 参数
%autosetup -T -D -p1 -n %{RosPkgName}-%{version}
```

**%autosetup 参数详解**：
- `-T`: **T**arball 处理 - 告诉 %autosetup 不要再次解压 tar 包（因为已经手动解压了）
- `-D`: **D**irectory 保留 - 不要删除已解压的目录
- `-p1`: Patch 前缀 - 应用补丁时使用 -p1 路径前缀
- `-n <name>`: **n**ame - 指定解压后的目录名（必须与 tar 包内的目录名匹配）

#### 步骤 3: 提交到 Git 仓库

```bash
# 删除旧的大文件（如果存在）
git rm rtabmap-0.22.1.tar.gz 2>/dev/null || true

# 添加所有分片文件
git add rtabmap-0.22.1.tar.gz.*

# 提交
git commit -m "Split large tarball for EUR compatibility"

# 推送到远程
git push origin humble
```

#### 步骤 4: 验证 EUR 构建

```bash
# 触发构建
copr-cli build-package your-gitcode-username/your-eur-project \
  --name rtabmap --nowait

# 检查构建日志中的 %prep 阶段
# 应该看到：
# + cat rtabmap-0.22.1.tar.gz.aa rtabmap-0.22.1.tar.gz.ab rtabmap-0.22.1.tar.gz.ac
# + tar -xzf rtabmap-0.22.1.tar.gz
# + cd rtabmap-0.22.1
```

### 实际案例：rtabmap (21MB)

**原始问题**：
```
tar: This does not look like a tar archive
tar: Exiting with failure status due to previous errors
```

**解决方案**：
```bash
# 切分 21MB 的 tar 包
split -b 8M rtabmap-0.22.1.tar.gz rtabmap-0.22.1.tar.gz.
# 生成 3 个分片：aa (8MB), ab (8MB), ac (5MB)

# spec 修改
Source0: rtabmap-0.22.1.tar.gz.aa
Source1: rtabmap-0.22.1.tar.gz.ab
Source2: rtabmap-0.22.1.tar.gz.ac

%prep
cat %{SOURCE0} %{SOURCE1} %{SOURCE2} > rtabmap-0.22.1.tar.gz
tar -xzf rtabmap-0.22.1.tar.gz
%autosetup -T -D -p1 -n rtabmap-0.22.1

# 构建成功 ✅
```

### 经验总结

#### ✅ DO - 正确做法

1. **使用 split 切分大包** - 这是处理大 tar 包的标准方法
   ```bash
   split -b 8M large.tar.gz large.tar.gz.
   ```

2. **每个分片 ≤ 8MB** - 避免接近 Git 单文件限制
   ```bash
   # 8MB 是推荐值，平衡了文件数量和大小
   split -b 8M package.tar.gz package.tar.gz.
   ```

3. **手动合并和解压** - 在 %prep 阶段明确控制
   ```spec
   cat %{SOURCE0} %{SOURCE1} > package.tar.gz
   tar -xzf package.tar.gz
   %autosetup -T -D -p1 -n package
   ```

4. **按字母顺序命名 Source** - 保持清晰
   ```spec
   Source0: file.tar.gz.aa  # 第一分片
   Source1: file.tar.gz.ab  # 第二分片
   Source2: file.tar.gz.ac  # 第三分片
   ```

#### ❌ DON'T - 错误做法

1. **不要使用 Git LFS**
   ```bash
   # ❌ 错误：EUR 不支持 LFS
   git lfs track "*.tar.gz"
   git add .gitattributes
   git add large.tar.gz
   ```
   
   **后果**：EUR 构建时会读取到 LFS 指针文件，导致解压失败

2. **不要修改 Source0 为 GitHub 链接**
   ```spec
   # ❌ 错误：网络不稳定会导致构建失败
   Source0: https://github.com/introlab/rtabmap/archive/0.22.1.tar.gz
   ```
   
   **后果**：网络超时、连接重置等导致构建失败

3. **不要忘记 %autosetup 的 -T -D 参数**
   ```spec
   # ❌ 错误：会导致重复解压或找不到文件
   %autosetup -p1 -n %{RosPkgName}-%{version}
   ```
   
   **后果**：tar 包已被手动解压，%autosetup 会再次尝试解压导致失败

4. **不要在本地测试后忘记推送分片文件**
   ```bash
   # ❌ 错误：只推送了 spec，忘记推送分片文件
   git add rtabmap.spec
   git commit -m "Update spec"
   git push
   # 忘记：git add *.tar.gz.*
   ```
   
   **后果**：EUR 构建时找不到 tar 分片文件

### 大文件处理决策树

```
tar 包大小 > 10MB?
│
├─ YES → 使用 split 切分
│         │
│         ├─ split -b 8M file.tar.gz file.tar.gz.
│         ├─ 修改 spec: Source0, Source1, ...
│         ├─ %prep: cat + tar + %autosetup -T -D
│         └─ git add *.tar.gz.* && git push
│
└─ NO  → 直接提交
          │
          ├─ git add file.tar.gz
          └─ spec: Source0: file.tar.gz
                   %autosetup -p1 -n file
```

### 相关文档

- [RPM %autosetup 文档](https://rpm-software-management.github.io/rpm/manual/autosetup.html)
- [split 命令手册](https://man7.org/linux/man-pages/man1/split.1.html)
- [Git LFS 限制说明](https://git-lfs.github.com/)
