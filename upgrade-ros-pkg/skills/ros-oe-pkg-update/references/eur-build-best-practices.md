# EUR 构建最佳实践

本文总结了在 EUR (COPR) 平台构建 ROS 包时遇到的常见问题和解决方案。

## 问题 1: 大 Tar 包处理（> 10MB）

### 问题描述

当 tar 包超过 10MB 时，会遇到以下问题：

1. **Git LFS 不被 EUR 支持**：
   - COPR 的 `rpkg` 构建工具**不会执行 `git lfs pull`**
   - 读取到的是 LFS 指针文件（约 20 字节），而非真实 tar 包
   - 构建失败：`tar: This does not look like a tar archive`

2. **GitHub 链接不稳定**：
   - 使用 GitHub archive URL 作为 Source0 会因网络问题导致构建失败
   - 错误：`curl: (35) OpenSSL SSL_connect: Connection reset by peer`

3. **Git 单文件大小限制**：
   - GitCode 限制单个文件不能超过 100MB
   - 直接提交大文件可能失败

### 根本原因

EUR 构建环境的限制：
```bash
# EUR 执行的操作
git clone https://gitcode.com/username/repo.git
git checkout humble

# EUR 不执行的操作
git lfs pull  # ❌ 不会执行这一步

# EUR 构建时
rpkg -q srpm  # 只克隆仓库，不处理 LFS
```

### ✅ 正确解决方案：使用 split 切分大包

**步骤 1: 检测 tar 包大小**

```bash
# 检查 tar 包大小
tar_size=$(stat -c%s "package.tar.gz")
tar_size_mb=$((tar_size / 1024 / 1024))

if [ $tar_size_mb -gt 10 ]; then
    echo "检测到大 tar 包 (${tar_size_mb}MB)，需要切分"
fi
```

**步骤 2: 切分 tar 包**

```bash
# 切分为 8MB 的分片（推荐大小）
split -b 8M package-1.0.0.tar.gz package-1.0.0.tar.gz.

# 查看生成的分片
ls -lh package-1.0.0.tar.gz.*
# 输出:
# -rw-r--r-- 1 user user 8.0M Mar 28 10:42 package-1.0.0.tar.gz.aa
# -rw-r--r-- 1 user user 8.0M Mar 28 10:42 package-1.0.0.tar.gz.ab
# -rw-r--r-- 1 user user 5.2M Mar 28 10:42 package-1.0.0.tar.gz.ac

# 删除原始大包
rm package-1.0.0.tar.gz
```

**步骤 3: 修改 spec 文件**

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

**%autosetup 参数说明**：
- `-T`: **T**arball 处理 - 不要再次解压（因为已经手动解压了）
- `-D`: **D**irectory 保留 - 不要删除已解压的目录
- `-p1`: Patch 前缀 - 应用补丁时使用 -p1 路径前缀
- `-n <name>`: **n**ame - 指定解压后的目录名

**步骤 4: 提交到 Git 仓库**

```bash
# 添加所有分片文件
git add package-1.0.0.tar.gz.*

# 添加修改后的 spec
git add package.spec

# 提交
git commit -m "Upgrade package to 1.0.0 with split tarball for EUR compatibility"

# 推送
git push origin humble
```

### ❌ 错误做法

#### 错误 1: 使用 Git LFS

```bash
# ❌ 错误：EUR 不支持 LFS
git lfs track "*.tar.gz"
git add .gitattributes
git add large.tar.gz
git commit -m "Add large tar with LFS"
```

**后果**：EUR 构建时会读取到 LFS 指针文件，导致解压失败

#### 错误 2: 使用 GitHub 链接

```spec
# ❌ 错误：网络不稳定会导致构建失败
Source0: https://github.com/org/repo/archive/refs/tags/%{version}.tar.gz
```

**后果**：网络超时、连接重置等导致构建失败

#### 错误 3: 忘记 %autosetup 的 -T -D 参数

```spec
# ❌ 错误：会导致重复解压或找不到文件
%prep
cat %{SOURCE0} %{SOURCE1} > package.tar.gz
tar -xzf package.tar.gz
%autosetup -p1 -n package  # 缺少 -T -D
```

**后果**：tar 包已被手动解压，%autosetup 会再次尝试解压导致失败

### 实际案例：rtabmap (21MB)

```bash
# 1. 检测大小
ls -lh rtabmap-0.22.1.tar.gz
# -rw-r--r-- 1 user user 21M Mar 28 10:42 rtabmap-0.22.1.tar.gz

# 2. 切分
split -b 8M rtabmap-0.22.1.tar.gz rtabmap-0.22.1.tar.gz.
# 生成: rtabmap-0.22.1.tar.gz.aa (8MB), .ab (8MB), .ac (5MB)

# 3. 删除原始大包
rm rtabmap-0.22.1.tar.gz

# 4. 修改 spec
cat > rtabmap.spec << 'EOF'
Source0:        rtabmap-0.22.1.tar.gz.aa
Source1:        rtabmap-0.22.1.tar.gz.ab
Source2:        rtabmap-0.22.1.tar.gz.ac

%prep
cat %{SOURCE0} %{SOURCE1} %{SOURCE2} > rtabmap-0.22.1.tar.gz
tar -xzf rtabmap-0.22.1.tar.gz
%autosetup -T -D -p1 -n rtabmap-0.22.1
EOF

# 5. 提交
git add rtabmap-0.22.1.tar.gz.* rtabmap.spec
git commit -m "Upgrade rtabmap to 0.22.1 with split tarball"
git push origin humble

# 6. EUR 构建成功 ✅
```

## 问题 2: Ubuntu 与 openEuler 包名差异

### 问题描述

ROS 2 包的依赖在 Ubuntu 和 openEuler 上包名不同，导致构建失败：

```
No matching package to install: 'liboctomap-dev'
```

### 解决方案

**Ubuntu 包名 → openEuler 包名映射**：

| Ubuntu 包名 | openEuler 包名 | 说明 |
|------------|---------------|------|
| `lib*-dev` | `*-devel` | 开发包命名规范 |
| `liboctomap-dev` | `octomap-devel` | 示例 |
| `libeigen3-dev` | `eigen3-devel` | 示例 |

### 修改 Spec 文件

```spec
# ❌ Ubuntu 风格
BuildRequires: liboctomap-dev

# ✅ openEuler 风格
BuildRequires: octomap-devel
```

## 问题 3: ROS 依赖缺失

### 问题描述

某些 ROS 包依赖其他 ROS 包，但 openEuler 官方源或 EUR 项目中未提供：

```
No matching package to install: 'ros-humble-gtsam'
```

### 解决方案

1. **检查 EUR 项目中是否已构建**：
   ```bash
   copr-cli list-packages your-gitcode-username/your-eur-project | grep gtsam
   ```

2. **如果未构建，需要先构建依赖包**：
   - 将依赖包添加到 EUR 项目
   - 构建成功后，依赖关系自动满足

3. **确保 EUR 项目仓库已启用**：
   - COPR 构建时会自动添加当前项目的仓库
   - 依赖包在同一项目中构建后，其他包可以依赖它

## 问题 4: EUR 服务器 OOM (Out of Memory)

### 问题描述

EUR 构建服务器内存有限，编译大型 C++ 项目时可能 OOM：

```
Killed signal terminated program cc1plus
```

或日志中突然中断，没有明确错误信息。

### 解决方案：减少编译内存占用

在 `%build` section 中添加 CMake 参数，禁用不必要的构建：

```spec
%build
# ROS 2 标准构建配置
%cmake3 \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/opt/ros/%{ros_distro} \
    -DINSTALL_EXAMPLES=OFF \
    -DINSTALL_TESTS=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_TESTS=OFF \
    -DBUILD_UNSTABLE=ON \
    -DUSE_SYSTEM_EIGEN=ON

%cmake3_build
```

### 常用内存优化参数

| 参数 | 说明 | 效果 |
|------|------|------|
| `-DBUILD_EXAMPLES=OFF` | 不构建示例代码 | 减少编译目标 |
| `-DBUILD_TESTS=OFF` | 不构建测试代码 | 大幅减少编译量 |
| `-DBUILD_UNSTABLE=ON` | 构建不稳定特性（按需） | - |
| `-DUSE_SYSTEM_EIGEN=ON` | 使用系统 Eigen 库 | 避免编译第三方库 |
| `-DINSTALL_EXAMPLES=OFF` | 不安装示例 | 减少安装大小 |
| `-DINSTALL_TESTS=OFF` | 不安装测试 | 减少安装大小 |

### GTSAM 示例

```spec
%build
%cmake3 \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/opt/ros/%{ros_distro} \
    -DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF \
    -DGTSAM_BUILD_TESTS=OFF \
    -DGTSAM_BUILD_UNSTABLE=ON \
    -DGTSAM_USE_SYSTEM_EIGEN=ON

%cmake3_build
```

### 其他大型包的优化

对于其他大型 C++ 项目（如 OpenCV, PCL, OMPL 等），需要：
1. 查看项目的 CMakeLists.txt
2. 找到控制示例/测试构建的选项
3. 添加相应的 `-D...=OFF` 参数

## 问题 5: Spec 文件中的路径问题

### 问题描述

使用 `%autosetup` 时，tar 包解压后的目录名与 spec 中的不匹配：

```
error: File not found: /builddir/build/BUILD/angles-1.15.0
```

### 解决方案

1. **下载 tar 包并查看解压后的目录名**：
   ```bash
   wget https://github.com/ros/angles/archive/refs/tags/1.15.0.tar.gz
   tar -tzf 1.15.0.tar.gz | head -1
   # 输出: angles-1.15.0/
   ```

2. **在 spec 中指定正确的目录名**：
   ```spec
   %autosetup -p1 -n angles-1.15.0
   ```

## 完整工作流程

### 1. 检测失败原因

```bash
# 下载构建日志
copr-cli download-build BUILD_ID

# 分析日志
zcat openeuler-24.03_LTS-aarch64/builder-live.log.gz | tail -100
```

### 2. 识别问题类型

- **tar 包损坏**：`gzip: stdin: not in gzip format` → 修改 Source0
- **依赖缺失**：`No matching package to install` → 检查包名或先构建依赖
- **OOM**：`Killed signal` → 添加构建参数
- **路径错误**：`File not found` → 修正 %autosetup

### 3. 修复 Spec 文件

根据问题类型修改 spec 文件。

### 4. 提交并推送

```bash
cd /path/to/repo
git add *.spec
git commit -m "Fix: Update spec for EUR build compatibility

- Change Source0 to https URL (avoid LFS issue)
- Update BuildRequires for openEuler package naming
- Add CMake flags to reduce memory usage
- Fix %autosetup directory name

EUR build fix"
git push origin humble
```

### 5. 重新触发构建

```bash
copr-cli build-package --name PACKAGE_NAME COPR_PROJECT
```

## 参考资源

- [COPR 文档](https://docs.pagure.org/copr.copr/)
- [openEuler 打包指南](https://docs.openeuler.org/zh/docs/22.03_LTS/docs/ApplicationDevelopment/packaging.html)
- [ROS 2 Humble 下载](https://github.com/ros2)
