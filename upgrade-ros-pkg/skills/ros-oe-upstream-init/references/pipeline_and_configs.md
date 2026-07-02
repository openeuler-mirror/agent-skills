# ROS Upstream Init 工具链参考指南 (Pipeline & Configurations)

本文档描述了 `ros-oe-upstream-init` 工具链中各个脚本的作用、不同配置文件的用途与关联，以及在遇到错误时如何进行**中间恢复与断点重试（强烈建议在问题排查时避免全量重新执行 VCS 克隆）**。

## 1. 流水线脚本执行顺序与职责 (Pipeline Scripts)

完整的 `ros-upstream-setup.sh` 是对以下脚本的串联包装。在排查问题或修复特定包时，你应该**单独执行**这些脚本，而不是每次都跑全量。

1. **`get-ros-projects.sh`**
   - **功能**：从 ROS 官网状态页抓取特定 distro 的全量包名和状态。
   - **输出**：`output/ros-projects.list`

2. **`get-repo-list.sh`**
   - **功能**：读取包列表，结合第三方包列表 (`ros-projects-third.list`)，解析仓库地址。精准拦截 Bloom 打包专用的以 `-release` 结尾的伪装仓库，并根据配置进行 URL 和版本的替换。
   - **输出**：`output/ros-pkg.list` (包的基础信息)，`output/ros.repos` (VCS克隆清单)，`output/bloom_release_repos.log` (发现的异常 release 地址，需人工或 Agent 修复)。

3. **`vcs import output/repo < output/ros.repos` (非脚本，为系统命令)**
   - **功能**：根据 `.repos` 全量并发克隆代码。
   - **耗时极长，调试时应极力避免全量重跑此步骤**。如果是单个包代码有问题，请直接 `cd output/repo/<pkg>` 手动 `git clone` 或 `git pull`。

4. **`get-pkg-src.sh`**
   - **功能**：在克隆下来的仓库中，寻找对应包的实际源码根目录（即 `package.xml` 所在的目录）。对于没有 `package.xml` 的第三方纯 C++ 包，依赖 `ros-3rdparty-path-fix` 进行路径推断。
   - **版本号清洗（核心机制）**：比对官网爬取的期望版本与 `package.xml` 中的实际版本。
     - 若仅有 **Patch（第三位修订号）** 升级（如 2.4.2 -> 2.4.3），则自动判定为上游正常迭代，**静默覆写**为新版本并放行。
     - 若 **Major（主版本号）** 或 **Minor（次版本号）** 发生跃迁（如 2.5 -> 2.14），则判定为灾难性的跨代代码（Tracking branch 错误），强行拦截。
   - **输出**：`output/ros-pkg-src.list`, `output/version_mismatch.log` (发生致命跨代版本错误的名单，交由 Agent 延后修复)。

5. **`get-pkg-deps.sh`**
   - **功能**：通过 Python 解析 `package.xml`，获取包的 Build、Run、Test 依赖，写入 `output/deps/` 目录。
   - **输出**：`output/deps/<pkg>/` 下的各类型依赖文件。

6. **`gen-pkg-spec.sh`**
   - **功能**：结合前面生成的元数据、全局映射字典 (`pkg.remap`)，套用 Spec 模板生成 RPM Spec 文件。如果发现有特异性的 `custom.spec` 旁路，则跳过模板解析直接替换宏并输出。
   - **输出**：`output/spec/<pkg>.spec`，并在 `output/repo/<pkg>/` 准备好带 Spec 和 Patch 的待构建结构。

---

## 2. 配置文件说明 (Configurations)

工具链的所有干预配置主要分布在三个层级：

### 2.1 全局配置 (`global_config/`)
- **`pkg.remap`**: 将 ROS 里声明的 rosdep 依赖名称（如 `python3-numpy`）转换为 openEuler 系统中真实的 RPM 包名（如 `python3-numpy` -> `python3-numpy`）。跨发行版通用。

### 2.2 发行版级配置 (`ros/humble/config/`)
- **`ros-projects-third.list`**: 纯第三方库名单（这些包不在 ROS 官方 status 页面上，但需要被当做 ROS 包打出来）。格式：`pkg_name \t git_url \t maintained \t version`。
- **`ros-url-fix`**: 用于修正错误的 Git 仓库地址（例如覆盖掉那些指向 Bloom `-release` 的错误 URL，指向真正的 C++ 源码上游仓库）。
- **`ros-version-fix`**: 修正仓库应当 checkout 的分支或 tag。
- **`ros-3rdparty-path-fix`**: 指定源码在 Git 仓库里的相对目录。对于根本没有 `package.xml` 的项目，此处可设为 `.`（根目录），让 `get-pkg-src.sh` 停止报错。

### 2.3 包级定制归档 (`ros/humble/package_fix/<pkg_name>/`)
- **`custom.spec`**: 如果存在该文件，`gen-pkg-spec.sh` 会触发**静态 Spec 旁路机制**，忽略自动推导，仅做 `Version` 等基础宏替换，适用于复杂第三方包。
- **`*.patch`**: 放于此目录的 patch 文件，将在 `gen-pkg-spec.sh` 时自动被追加到生成的 Spec 里。

---

## 3. 问题排查与断点重试策略 (Troubleshooting & Breakpoint Retries)

当遇到错误（如找不到仓库、缺失包、路径错误、Spec 生成错误）时，**绝对不要直接重跑整个 `./ros-upstream-setup.sh`**！这会导致 VCS 重新扫描克隆上千个仓库。

**正确的工作流：**

1. **URL 解析或拉取失败**（`get-repo-list.sh` 阶段）：
   - 修改 `ros/humble/config/ros-url-fix` 或 `ros-version-fix`。
   - 单独运行 `./get-repo-list.sh`。
   - 然后手动到 `output/repo/` 下，针对失败的包执行克隆 `git clone <correct_url>`，不必全量 VCS。

2. **找不到源码目录 / 没有 package.xml**（`get-pkg-src.sh` 阶段）：
   - 如果是标准 ROS 包，检查仓库目录结构；如果是第三方包，确保它在 `ros-projects-third.list` 里，并且在 `ros-3rdparty-path-fix` 中配置了相对路径（如 `. `）。
   - 单独运行 `./get-pkg-src.sh`。

3. **依赖推导错误 / Spec 生成不符合要求**（`gen-pkg-spec.sh` 阶段）：
   - 如果依赖名称错误：修改 `global_config/pkg.remap`。
   - 如果需要特殊编译参数或 patch：放入 `ros/humble/package_fix/<pkg>/`。
   - 如果需要完全干预 Spec（比如去除 CMake 某项测试，移除版本写死）：在 `package_fix/<pkg>/` 下编写 `custom.spec`。
   - 单独运行 `./gen-pkg-spec.sh` 即可重新生成最新的 Spec。

记住：**从哪里跌倒，就从哪个脚本开始往后跑。尽量少走 vcs 的流程！**