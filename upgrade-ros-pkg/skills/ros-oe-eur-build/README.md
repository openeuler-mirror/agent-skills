# ros-oe-eur-build Skill

用于在 openEuler EUR (COPR) 项目中批量触发 ROS 包的云侧构建。

## 快速开始

### 1. 配置 copr-cli

首先从 EUR 网站获取 API token：
```
https://eur.openeuler.openatom.cn/coprs/<your-username>/api/
```

然后创建配置文件：
```bash
mkdir -p ~/.config
cat > ~/.config/copr << EOF
[copr-cli]
login = <your_login_token>
token = <your_api_token>
copr_url = https://eur.openeuler.openatom.cn
EOF
chmod 600 ~/.config/copr
```

验证配置：
```bash
copr-cli whoami
```

### 2. 准备包列表文件

创建一个文本文件（如 `packages.txt`），每行一个包名，按依赖顺序排列：
```
foonathan_memory_vendor
rpyutils
iceoryx_hoofs
rosidl_cli
fastcdr
```

### 3. 使用 Skill

```bash
# 基本用法
skill: "ros-oe-eur-build", args: "--workspace-dir ~/Desktop/test --package-list-file packages.txt"

# 指定 COPR 项目
skill: "ros-oe-eur-build", args: "--workspace-dir ~/Desktop/test --package-list-file packages.txt --copr-project myuser/my-project"
```

## 直接运行脚本

也可以直接调用脚本：

```bash
./scripts/trigger_eur_builds.sh \
  --workspace-dir /path/to/workspace \
  --package-list-file packages.txt \
  --copr-project your-gitcode-username/your-eur-project
```

## 工作流程

1. **环境验证**：检查 copr-cli 安装和配置
2. **触发构建**：为每个包触发 SCM 构建（从 GitCode humble 分支）
3. **状态监控**：轮询构建状态直到完成或超时
4. **日志收集**：保存构建日志到 `eur_build_logs/` 目录
5. **失败分析**：分析失败的构建并提供修复建议

## 输出

### 成功输出
```
==========================================
EUR Build Summary
==========================================
Total packages: 5
Successful: 5
Failed: 0

Build logs saved to: /path/to/workspace/eur_build_logs/
==========================================
```

### 失败输出
```
==========================================
EUR Build Summary
==========================================
Total packages: 5
Successful: 3
Failed: 2

Failed Builds:
  ✗ rosidl_cli
  ✗ fastcdr

Check build logs for details:
  ls -la /path/to/workspace/eur_build_logs/
==========================================
```

## 测试

Skill 包含5个测试用例，覆盖：
- 基本构建触发
- 自定义 COPR 项目
- copr-cli 认证检查
- 构建状态监控
- 集成工作流程

## 注意事项

1. **分支名称**：默认使用 `humble` 分支，确保所有包在该分支上有代码
2. **依赖顺序**：包列表必须按依赖顺序排列
3. **EUR 项目**：确保 EUR 项目中已添加这些包（可使用 `/ros-oe-eur-init`）
4. **网络稳定性**：从 SCM 构建需要克隆仓库，确保网络连接稳定

## 常见问题

### copr-cli 认证失败
```
Error: You have to provide API key
```
**解决**：按照步骤1配置 copr-cli

### 包未添加到 EUR 项目
```
Package 'xxx' not found in project
```
**解决**：先运行 `/ros-oe-eur-init` 添加包到项目

### 构建超时
```
Build timeout after 120 minutes
```
**解决**：
- 增加 `--max-wait` 参数
- 检查 EUR Web UI 了解构建队列状态
- 查看是否是网络问题

## 相关技能

- `ros-package-spec-merge`: Spec 文件合并
- `ros-oe-eur-init`: EUR 仓库初始化
