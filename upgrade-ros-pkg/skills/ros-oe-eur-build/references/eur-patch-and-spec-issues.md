# EUR 补丁和 Spec 文件常见问题及解决方案

本文档记录了在 EUR (COPR) 构建 ROS 包时遇到的补丁和 spec 文件问题及解决方案。

## 问题 1: 补丁格式错误 - "No file to patch"

### 错误现象
```
+ /usr/bin/patch -p1 -s --fuzz=0 --no-backup-if-mismatch -f
The text leading up to this was:
--------------------------
|diff --git a/include/nav2_util/lifecycle_node.hpp b/include/nav2_util/lifecycle_node.hpp
|--- a/include/nav2_util/lifecycle_node.hpp
|+++ b/include/nav2_util/lifecycle_node.hpp
--------------------------
No file to patch.  Skipping patch.
1 out of 1 hunk ignored
```

### 根本原因
EUR 使用 `patch -p1 --fuzz=0` 应用补丁，要求：
1. 补丁必须使用 `a/` 和 `b/` 路径前缀
2. 上下文必须 100% 精确匹配，零容忍

使用 `diff -u dir.orig dir` 生成的补丁格式不正确。

### 解决方案：使用 Git 生成补丁

**推荐方法**：
```bash
# 1. 解压源码
tar -xzf package-version.tar.gz
cd package-version

# 2. 初始化 git 并提交原始代码
git init
git add .
git commit -m "original"

# 3. 手动修改需要修复的文件
vim src/file.cpp

# 4. 生成补丁 (git diff 自动使用 a/ 和 b/ 前缀)
git diff > ../package-fix.patch

# 5. 验证补丁格式
head -5 ../package-fix.patch
# 应该看到:
# diff --git a/src/file.cpp b/src/file.cpp
# --- a/src/file.cpp
# +++ b/src/file.cpp

# 6. 本地验证补丁
cd ..
rm -rf package-version
tar -xzf package-version.tar.gz
cd package-version
patch -p1 --dry-run --fuzz=0 < ../package-fix.patch
# 必须输出 "checking file ..." 无错误

# 7. 实际应用测试
patch -p1 --fuzz=0 < ../package-fix.patch
```

### 补丁格式要求
```diff
diff --git a/src/file.cpp b/src/file.cpp    # 必须 a/ 和 b/ 前缀
index 6da348c..449a9db 100644
--- a/src/file.cpp                           # 必须 a/ 前缀
+++ b/src/file.cpp                           # 必须 b/ 前缀
@@ -50,7 +50,7 @@                           # 行号必须精确
   context_line_above();                      # 上下文必须完全匹配
-  old_line();
+  new_line();
   context_line_below();
```

---

## 问题 2: %autosetup 目录结构问题 - 补丁路径不匹配

### 错误现象
```
No file to patch.  Skipping patch.
```
即使补丁格式正确，仍然找不到文件。

### 根本原因
spec 文件中 `%autosetup` 参数配置错误，导致目录结构与预期不符。

**错误配置**：
```spec
%define RosPkgName      nav2-util
Name:           ros-%{ros_distro}-%{RosPkgName}

%prep
%autosetup -c -n %{name}-%{version} -p1
```

**问题分析**：
- `%{name}` = `ros-humble-nav2-util`
- `%{RosPkgName}` = `nav2-util`
- tar 包解压后根目录是 `nav2-util-1.1.20/`
- `-c` 参数会创建目录 `ros-humble-nav2-util-1.1.20/`
- `-n %{name}-%{version}` 指定目录名为 `ros-humble-nav2-util-1.1.20`
- 结果变成双层嵌套：`ros-humble-nav2-util-1.1.20/nav2-util-1.1.20/`
- 补丁找 `include/...`，但实际在 `nav2-util-1.1.20/include/...`

### 解决方案

**正确配置**：
```spec
%define RosPkgName      nav2-util
Name:           ros-%{ros_distro}-%{RosPkgName}

%prep
%autosetup -n %{RosPkgName}-%{version} -p1
```

**关键点**：
1. **去掉 `-c` 参数**：不要创建额外的目录
2. **使用 `%{RosPkgName}` 而非 `%{name}`**：匹配 tar 包的实际目录名

---

## 问题 3: CMake 源目录路径错误

### 错误现象
```
CMake Error: The source directory "/builddir/build/BUILD/nav2-util-1.1.20/nav2-util-1.1.20" does not exist.
```

### 根本原因
修改 `%autosetup` 后，目录结构变了，但 cmake 的源目录路径没有相应更新。

**之前的结构** (用 `-c` 创建额外目录):
```
/builddir/build/BUILD/ros-humble-nav2-util-1.1.20/nav2-util-1.1.20/
```
从 `.obj-*/` 目录看，`../nav2-util-1.1.20` 是正确的。

**现在的结构** (不用 `-c`):
```
/builddir/build/BUILD/nav2-util-1.1.20/
```
从 `.obj-*/` 目录看，`../nav2-util-1.1.20` 变成了 `nav2-util-1.1.20/nav2-util-1.1.20`，不存在！

### 解决方案

**错误配置**：
```spec
%build
mkdir -p .obj-%{_target_platform} && cd .obj-%{_target_platform}
%cmake3 \
    -DCMAKE_INSTALL_PREFIX="/opt/ros/%{ros_distro}" \
    ...
    ../%{RosPkgName}-%{version}
```

**正确配置**：
```spec
%build
mkdir -p .obj-%{_target_platform} && cd .obj-%{_target_platform}
%cmake3 \
    -DCMAKE_INSTALL_PREFIX="/opt/ros/%{ros_distro}" \
    ...
    ..
```

**关键点**：cmake 源目录从 `../%{RosPkgName}-%{version}` 改成 `..`

---

## 完整的 Spec 文件模板

```spec
%bcond_without tests
%bcond_without weak_deps

%define ros_distro      humble

%global debug_package %{nil}
%global __os_install_post %(echo '%{__os_install_post}' | sed -e 's!/usr/lib[^[:space:]]*/brp-python-bytecompile[[:space:]].*$!!g')
%global __provides_exclude_from ^/opt/ros/%{ros_distro}/.*$
%global __requires_exclude_from ^/opt/ros/%{ros_distro}/.*$

%define RosPkgName      your-package-name

Name:           ros-%{ros_distro}-%{RosPkgName}
Version:        1.0.0
Release:        1%{?dist}%{?release_suffix}
Summary:        Package summary

License:        Apache-2.0
Source0:        %{RosPkgName}-%{version}.tar.gz
Patch0:         ros-%{ros_distro}-%{RosPkgName}-%{version}-fix-xxx.patch

# Requires and BuildRequires...

%description
Package description.

%prep
# 关键：使用 RosPkgName，去掉 -c
%autosetup -n %{RosPkgName}-%{version} -p1

%build
export PYTHONPATH=/opt/ros/%{ros_distro}/lib/python%{python3_version}/site-packages
if [ -f "/opt/ros/%{ros_distro}/setup.sh" ]; then . "/opt/ros/%{ros_distro}/setup.sh"; fi
mkdir -p .obj-%{_target_platform} && cd .obj-%{_target_platform}
%cmake3 \
    -DCMAKE_INSTALL_PREFIX="/opt/ros/%{ros_distro}" \
    -DAMENT_PREFIX_PATH="/opt/ros/%{ros_distro}" \
    -DCMAKE_PREFIX_PATH="/opt/ros/%{ros_distro}" \
    ..
# 关键：cmake 源目录是 .. 不是 ../%{RosPkgName}-%{version}

%make_build

%install
# ...

%files
# ...

%changelog
# ...
```

---

## 检查清单

在推送补丁到 EUR 之前，确保：

### 补丁检查
- [ ] 使用 `git diff` 生成补丁
- [ ] 补丁文件开头是 `diff --git a/... b/...`
- [ ] 使用 `--- a/...` 和 `+++ b/...` 格式
- [ ] 本地通过 `patch -p1 --dry-run --fuzz=0` 验证

### Spec 文件检查
- [ ] `%autosetup -n %{RosPkgName}-%{version} -p1` (无 `-c`)
- [ ] cmake 源目录是 `..` 不是 `../%{RosPkgName}-%{version}`
- [ ] Patch0 引用的补丁文件存在于仓库中

### Tar 包检查
- [ ] tar 包解压后根目录名与 `%{RosPkgName}-%{version}` 匹配
- [ ] 补丁中的文件路径相对于 tar 包根目录

---

## 问题 4: OOM (内存不足) 构建失败

### 错误现象
```
c++: fatal error: Killed signal terminated program cc1plus
compilation terminated.
```
或构建日志中看到内存相关的错误。

### 根本原因
EUR 构建环境内存限制为 2GB，大型 C++ 包并行编译时会超出内存限制。

### 解决方案：限制并行编译数

**错误配置**：
```spec
%build
%cmake3 ...
%make_build    # 默认使用所有 CPU 核心，可能导致 OOM
```

**正确配置**：
```spec
%build
%cmake3 ...
make -j1       # 单线程编译，最安全但最慢
# 或
make -j2       # 双线程编译，平衡内存和速度
```

**建议**：
1. 先尝试 `make -j1`
2. 如果成功且想加快速度，可以尝试 `make -j2`
3. 不要添加其他编译参数，先解决 OOM 问题

### 常见需要 -j1 的包
- rtabmap-rviz-plugins
- rtabmap-util
- nav2-costmap-2d
- 其他大型 C++ 模板密集型包

---

## 问题 5: 头文件找不到 - ROS 2 Ament 构建环境问题

### 错误现象
```
fatal error: message_filters/sync_policies/approximate_time.hpp: No such file or directory
```
或者类似的 `.hpp` / `.h` 找不到问题。

### ⚠️ 核心原则：千万不要去改 C++ 源文件的 #include 路径！

如果你发现"只要加深一层路径就能找到文件"，那**绝对是 CMake 缺少了 AMENT_PREFIX_PATH 和编译器 INCLUDE 宏传导所致**，不是代码的问题。

### 根因深度分析

这个问题有**两个超级大坑**同时在起作用：

#### 坑1: ROS 2 Ament 双层目录结构

ROS 2 的 ament_cmake 构建系统使用双层目录 `include/<pkg_name>/<pkg_name>/xxx.hpp` 来防止不同包的头文件冲突。

正常的 ROS 2 编译环境（colcon build）会通过 `ament_target_dependencies` 自动告诉编译器把搜索路径放宽到 `/opt/ros/humble/include/<pkg_name>` 这个特定层级。

**如果 RPM 编译时报 "No such file"，甚至需要手写完整路径才能找到，说明 CMake 在那一刻"瞎了"——它只认得最顶层的 `/opt/ros/humble/include`，没有把 Ament 专属的依赖搜索路径传递给 GCC。**

#### 坑2: `.hpp` vs `.h` 是 ROS 2 版本差异

- **ROS 2 Humble（及以前）**：message_filters、tf2 等从 ROS 1 搬来的祖传代码用 `.h` 结尾
- **ROS 2 Iron/Rolling（新版）**：官方把这些文件全改成了 `.hpp` 结尾
- **rtabmap_ros 等包**同时兼容多个 ROS 2 版本，用 `ROS_DISTRO` 宏判断：
  - 检测到 Humble → `#include <...time.h>`
  - 检测不到或以为是新版 → `#include <...time.hpp>`

**结论**：在 rpmbuild 沙盒里如果丢失了 `ROS_DISTRO=humble` 身份标识，rtabmap 会以为在编译最新版 ROS 2，去找 `.hpp`，当然找不到。

#### 坑2 的另一种情况：上游版本升级后 `.hpp` 是真实需要的

有时候并非环境问题，而是**上游包版本升级后确实需要 `.hpp` 头文件**：
- message_filters 4.3.7+ 通过 PR #172 添加了 `.hpp` wrapper 文件（只是 `#include "xxx.h"` 的转发）
- tf2 0.25.19 的 `buffer_core.hpp` 直接 `#include "geometry_msgs/msg/velocity_stamped.hpp"`（新增的消息类型）

这种情况下需要**升级依赖包**到提供 `.hpp` 的版本，而不是设环境变量。

### 解决方案

**方案 A（优先）：修复 spec 文件中的构建环境**

```spec
%build
# 1. 强制设定 ROS 身份，告诉代码用 .h 而不是 .hpp
export ROS_DISTRO=humble
export ROS_VERSION=2
export ROS_PYTHON_VERSION=3

# 2. 必须深度 source Humble 的底层环境，激活 Ament 的魔法路径
if [ -f "/opt/ros/%{ros_distro}/setup.sh" ]; then
    source "/opt/ros/%{ros_distro}/setup.sh"
fi

# 3. 放弃红帽系统自带的 %cmake3 宏，手写最纯净的 ament_cmake 调用
mkdir -p build && cd build
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="/opt/ros/%{ros_distro}" \
    -DAMENT_PREFIX_PATH="/opt/ros/%{ros_distro}" \
    -DCMAKE_PREFIX_PATH="/opt/ros/%{ros_distro}" \
    -DBUILD_TESTING=OFF

make %{?_smp_mflags}
```

**方案 B：升级依赖包到提供 `.hpp` 的版本**

当源码**无条件使用** `.hpp`（不是宏判断的结果，而是代码本身就写了 `.hpp`），说明是上游版本迭代导致。此时需要升级依赖包：
- message_filters 4.3.3 → 4.3.14（提供 `.hpp` wrapper）
- geometry_msgs 4.2.3 → 4.9.1（提供 VelocityStamped.msg）
- geometry2/tf2 0.25.2 → 0.25.19（提供 `.hpp` wrapper）

### 排查决策流程

```
代码报 "xxx.hpp: No such file"
    │
    ├─ 代码中有 #ifdef ROS_DISTRO 等条件编译？
    │     └─ YES → 方案 A：设置 ROS_DISTRO=humble 环境变量
    │
    ├─ 代码无条件写死了 #include <xxx.hpp>？
    │     └─ YES → 检查上游该 .hpp 文件从哪个版本开始提供
    │              └─ 升级依赖包到该版本（方案 B）
    │
    └─ 文件存在但路径多了一层（include/pkg/pkg/）？
          └─ 方案 A：CMake 缺少 AMENT_PREFIX_PATH
```

### 开发板辅助排查

开发板环境 ≈ EUR 构建环境，可以直接在板上验证：
```bash
# 确认包是否存在
dnf list | grep message-filters

# 安装并查找头文件实际位置
dnf install ros-humble-message-filters
find /opt/ros/humble/include -name "approximate_time.h*"

# 对比代码中的路径和实际路径
```

---

## 问题 6: SRPM 构建方法错误 - "Attempt to build SRPM have failed"

### 错误现象
构建立即失败，EUR 页面显示：
```
Attempt to build SRPM have failed.
```
且没有 build.log / root.log，只有 srpm-builds 目录下的日志。

### 根本原因
使用 `copr-cli add-package-scm --method make_srpm` 添加包，但仓库中没有 `.copr/Makefile`。

`make_srpm` 方法要求仓库包含 `.copr/Makefile` 来定义如何生成 SRPM。src-openeuler 的 ROS 包仓库使用默认的 `rpkg` 方法，不需要 Makefile。

### 解决方案

**添加包时不要指定 `--method` 参数**（使用默认的 rpkg 方法）：
```bash
# 错误 - 使用了 make_srpm
copr-cli add-package-scm myproject \
  --name ros-humble-geometry-msgs \
  --clone-url https://gitcode.com/your-gitcode-username/common_interfaces.git \
  --commit humble \
  --spec geometry-msgs.spec \
  --type git \
  --method make_srpm   # ❌ 不要加这个

# 正确 - 不指定 method，使用默认 rpkg
copr-cli add-package-scm myproject \
  --name ros-humble-geometry-msgs \
  --clone-url https://gitcode.com/your-gitcode-username/common_interfaces.git \
  --commit humble \
  --spec geometry-msgs.spec \
  --type git            # ✅ 不加 --method
```

### 如何判断
- 如果构建日志路径在 `srpm-builds/` 而非正常的 `openeuler-24.03_LTS-aarch64/`，说明是 SRPM 创建阶段失败
- 如果看到 `make -f .copr/Makefile srpm` 命令，说明使用了 make_srpm 方法

---

## 问题 7: 头文件版本不匹配 - 上游升级引入的新消息类型

### 错误现象
```
fatal error: geometry_msgs/msg/velocity_stamped.hpp: No such file or directory
```

### 真实案例

tf2 0.25.19 在 `buffer_core.hpp` 中引入了 `lookupVelocity()` 方法，依赖 `geometry_msgs::msg::VelocityStamped` 消息类型。但 EUR 基础仓只有 geometry_msgs 4.2.3，而 `VelocityStamped` 是在 4.2.4 才通过 PR `ros2/common_interfaces#249` 添加的。

### 排查思路

1. **确认缺失的头文件属于哪个包**
   ```bash
   # geometry_msgs/msg/velocity_stamped.hpp → 属于 geometry_msgs 包
   ```

2. **确认当前安装的版本**
   ```bash
   # 在开发板上
   rpm -qa | grep geometry-msgs
   # 输出: ros-humble-geometry-msgs-4.2.3-1.oe2403.aarch64
   ```

3. **确认新头文件在哪个版本引入**
   ```bash
   # 搜索上游 PR/commit
   # geometry_msgs VelocityStamped → ros2/common_interfaces#249 → 版本 4.2.4
   ```

4. **决策：升级依赖包 vs 打补丁**
   - 如果缺失的功能被多个包使用（如 VelocityStamped 被 tf2、tf2-geometry-msgs、tf2-py、tf2-ros 共 4 个包使用），**升级依赖包**更合理
   - 如果只是个别引用，可以考虑打补丁移除

### 解决方案

**升级依赖包到支持新功能的版本**：

以 geometry_msgs 升级为例：
1. Fork 上游仓库（如 common_interfaces）
2. 使用 ros-oe-upstream-init 生成的新 spec 和 tarball
3. 只修改 openEuler 定制化的部分（`%bcond_with tests`、`%autosetup` 路径等）
4. 提交并在 EUR 触发构建
5. 等待依赖包成功后再触发依赖它的包

---

## 问题 8: 多包仓库的分层依赖构建

### 场景

geometry2 是一个多包仓库（14 个子包），子包之间存在依赖关系。必须按依赖层级分批构建，不能一次性全部触发。

### 分层构建策略

**关键原则**：同一层的包可以并行触发，但必须等上一层全部成功后才能触发下一层。

以 geometry2 为例：

| 层级 | 包 | 依赖 |
|------|-----|------|
| Layer 0 | tf2-msgs, tf2-tools, tf2-ros-py, examples-tf2-py | 无 tf2 内部依赖 |
| Layer 1 | **tf2**, tf2-eigen-kdl | 仅依赖 geometry_msgs（外部） |
| Layer 2 | tf2-py, tf2-ros | 依赖 tf2 + tf2-msgs + message-filters |
| Layer 3 | tf2-eigen, tf2-bullet, tf2-kdl, tf2-geometry-msgs, tf2-sensor-msgs | 依赖 tf2-ros |
| Layer 4 | geometry2（元包） | 依赖所有子包 |

### 实现方法

```bash
# 1. 通过 spec 文件分析依赖
grep -E '^(Build)?Requires.*tf2' tf2-ros.spec
# Requires: ros-humble-tf2
# Requires: ros-humble-tf2-msgs
# BuildRequires: ros-humble-message-filters

# 2. 按层级触发
# Layer 1
for pkg in ros-humble-tf2 ros-humble-tf2-eigen-kdl; do
  copr-cli build-package --name "$pkg" myproject --nowait
done

# 3. 轮询等待 Layer 1 完成
# ... 等到所有 Layer 1 包 succeeded ...

# 4. 触发 Layer 2
for pkg in ros-humble-tf2-py ros-humble-tf2-ros; do
  copr-cli build-package --name "$pkg" myproject --nowait
done

# ... 以此类推
```

### 注意事项
- EUR 的依赖解析有延迟，刚成功的包 RPM 可能还没进入仓库索引
- 如果下一层构建因 "dependency not found" 失败，等几分钟后重试
- 使用 `--nowait` 可以并行触发同一层的多个包

---

## 问题 9: %bcond_without vs %bcond_with 的语义陷阱

### 错误现象
```
No matching package to install: 'ros-humble-ament-cmake-google-benchmark'
```
构建因缺少测试依赖而失败，但你认为已经关闭了测试。

### 根本原因

RPM spec 中 `%bcond` 宏的语义容易搞混：

| 写法 | 含义 | 默认值 |
|------|------|--------|
| `%bcond_without tests` | **默认启用**测试 | `with_tests = 1` |
| `%bcond_with tests` | **默认禁用**测试 | `with_tests = 0` |

- `%bcond_without X` = "without X is **not** the default" = **默认有 X**
- `%bcond_with X` = "with X is **not** the default" = **默认没有 X**

### 解决方案

EUR 环境缺少很多测试依赖（如 `ament_cmake_google_benchmark`、`ament_lint_common` 等），应该**默认禁用测试**：

```spec
# 错误 - 默认启用测试，导致安装测试依赖
%bcond_without tests

# 正确 - 默认禁用测试，EUR 上不装测试依赖
%bcond_with tests
```

### 受影响的场景
- ros-oe-upstream-init 生成的 spec 可能使用 `%bcond_without tests`
- openEuler 旧版 spec 也可能使用 `%bcond_without tests`
- **每次升级 spec 都要检查并修改为 `%bcond_with tests`**

---

## 问题 10: Changelog 和 Spec 内容规范

### 原则
升级 spec 时，应以 ros-oe-upstream-init 生成的内容为基准，**不要在 changelog 中写入个人用户名**。

### 正确做法
```spec
# 直接使用 ros-oe-upstream-init 生成的 changelog
%changelog
* Wed Mar 25 2026 Geoffrey Biggs geoff@openrobotics.org - 4.9.1-1
- Autogenerated by ros-oe-upstream-init
```

### 错误做法
```spec
# 不要写自己的名字
%changelog
* Sun Mar 30 2026 your-gitcode-username <maintainer@example.com> - 4.9.1-1
- Upgrade to 4.9.1: adds VelocityStamped message
```

### 需要手动修改的部分（仅限 openEuler 定制化）
只有以下情况需要在 ros-oe-upstream-init 基础上修改：
1. `%bcond_without tests` → `%bcond_with tests`（禁用测试）
2. `%autosetup` 路径修正（`-n %{RosPkgName}-%{version}` 无 `-c`）
3. cmake 源目录修正（`..` 而非 `../%{RosPkgName}-%{version}`）
4. `%global debug_package %{nil}` 确认存在
5. gcc 版本兼容补丁、变量初始化补丁等 openEuler 适配

---

## 相关文档

- [COPR 构建系统文档](https://docs.pagure.org/copr.copr/)
- [RPM Spec 文件参考](https://rpm-packaging-guide.github.io/)
