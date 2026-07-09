# ros-oe-pkg-prep

ROS package dependency resolution skill for analyzing recursive dependencies and generating build order.

## 功能特性

1. **递归依赖分析**：分析指定包的所有依赖关系
2. **系统依赖过滤**：自动区分系统依赖和ROS包依赖
3. **构建顺序生成**：使用拓扑排序算法生成正确的构建顺序
4. **详细报告**：输出依赖树分析和构建顺序文件

## 使用方法

```bash
# 解析 rclcpp 包的依赖
/ros-oe-pkg-prep --target-package rclcpp --upstream-dir /path/to/ros-oe-upstream-init

# 解析 ros_base 包的依赖（使用相对路径）
/ros-oe-pkg-prep --target-package ros_base --upstream-dir ./ros-oe-upstream-init
```

## 工作流程

1. **验证输入**：检查上游目录是否存在
2. **依赖解析**：执行依赖分析脚本
3. **生成构建顺序**：输出排序后的包列表
4. **生成报告**：创建 `<target-package>_build_order.txt` 文件

## 输出

- 依赖树分析报告
- 系统依赖过滤结果
- 构建顺序列表
- 构建顺序文件（`<target-package>_build_order.txt`）