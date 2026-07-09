# ROS包测试用例编写指南

## 测试用例规范

### 文件格式
- 使用bash脚本（`.sh`）
- 文件命名：`<package_name>_test.sh`
- 包含执行权限：`chmod +x`

### 脚本结构

```bash
#!/bin/bash
# Test: <测试目标描述>
# Package: <包名>
# Author: <作者>
# Date: <日期>

# 1. 环境准备
source /opt/ros/humble/setup.bash

# 2. 测试用例
# Test Case 1: <描述>
# ...

# 3. 清理
# ...

# 4. 结果报告
exit 0  # 成功
# 或
exit 1  # 失败
```

### 最佳实践

1. **清晰的输出**: 每个测试步骤都输出状态
2. **错误处理**: 使用`set -e`或手动检查错误
3. **资源清理**: 后台进程要清理
4. **超时处理**: 长时间运行的测试设置超时
5. **独立性**: 测试用例应该独立，不依赖其他测试

## 常见测试模式

### 模式1: 库文件检查

```bash
#!/bin/bash
# Test: Check if library files exist
# Package: rclcpp

source /opt/ros/humble/setup.bash

LIB_PATH="/opt/ros/humble/lib/librclcpp.so"

if [ -f "$LIB_PATH" ]; then
  echo "[✓] Library found: $LIB_PATH"
  exit 0
else
  echo "[✗] Library not found: $LIB_PATH"
  exit 1
fi
```

### 模式2: 可执行文件测试

```bash
#!/bin/bash
# Test: Test executable functionality
# Package: ros2cli

source /opt/ros/humble/setup.bash

# Test ros2 command
if ros2 --help > /dev/null 2>&1; then
  echo "[✓] ros2 command works"
else
  echo "[✗] ros2 command failed"
  exit 1
fi

# Test ros2 topic list
if timeout 5 ros2 topic list > /dev/null 2>&1; then
  echo "[✓] ros2 topic list works"
else
  echo "[✗] ros2 topic list failed"
  exit 1
fi

exit 0
```

### 模式3: 节点启动测试

```bash
#!/bin/bash
# Test: Test node startup
# Package: demo_nodes_cpp

source /opt/ros/humble/setup.bash

# Start talker node
ros2 run demo_nodes_cpp talker &
talker_pid=$!

# Wait for startup
sleep 3

# Check if process is running
if ps -p $talker_pid > /dev/null; then
  echo "[✓] Node started successfully"

  # Check if publishing topics
  if ros2 topic list | grep -q "/chatter"; then
    echo "[✓] Topic /chatter is being published"
    kill $talker_pid
    exit 0
  else
    echo "[✗] Topic /chatter not found"
    kill $talker_pid
    exit 1
  fi
else
  echo "[✗] Node failed to start"
  exit 1
fi
```

### 模式4: Topic通信测试

```bash
#!/bin/bash
# Test: Test topic communication
# Package: rclcpp

source /opt/ros/humble/setup.bash

# Start publisher
ros2 run demo_nodes_cpp talker &
talker_pid=$!

# Start subscriber and capture output
timeout 5 ros2 run demo_nodes_cpp listener > /tmp/listener_output.txt 2>&1 &
listener_pid=$!

# Wait for messages
sleep 3

# Check if messages were received
if grep -q "I heard" /tmp/listener_output.txt; then
  echo "[✓] Topic communication works"
  kill $talker_pid $listener_pid 2>/dev/null
  rm /tmp/listener_output.txt
  exit 0
else
  echo "[✗] No messages received"
  kill $talker_pid $listener_pid 2>/dev/null
  rm /tmp/listener_output.txt
  exit 1
fi
```

### 模式5: Service调用测试

```bash
#!/bin/bash
# Test: Test service functionality
# Package: example_interfaces

source /opt/ros/humble/setup.bash

# Start service server
ros2 run demo_nodes_cpp add_two_ints_server &
server_pid=$!

# Wait for server to start
sleep 2

# Call service
result=$(ros2 service call /add_two_ints example_interfaces/srv/AddTwoInts "{a: 2, b: 3}" 2>&1)

if echo "$result" | grep -q "sum: 5"; then
  echo "[✓] Service call works"
  kill $server_pid
  exit 0
else
  echo "[✗] Service call failed"
  kill $server_pid
  exit 1
fi
```

### 模式6: 参数服务器测试

```bash
#!/bin/bash
# Test: Test parameter functionality
# Package: rclcpp

source /opt/ros/humble/setup.bash

# Start node with parameters
ros2 run demo_nodes_cpp parameter_blackboard &
node_pid=$!

# Wait for node to start
sleep 2

# Set parameter
if ros2 param set /parameter_blackboard test_param 42 2>&1 | grep -q "Set parameter successful"; then
  echo "[✓] Parameter set works"
else
  echo "[✗] Parameter set failed"
  kill $node_pid
  exit 1
fi

# Get parameter
value=$(ros2 param get /parameter_blackboard test_param 2>&1)
if echo "$value" | grep -q "Integer value is: 42"; then
  echo "[✓] Parameter get works"
  kill $node_pid
  exit 0
else
  echo "[✗] Parameter get failed"
  kill $node_pid
  exit 1
fi
```

## 测试用例分类

### 1. 安装验证测试
- 库文件存在性
- 可执行文件存在性
- 配置文件正确性

### 2. 基础功能测试
- 命令行工具功能
- 节点启动和关闭
- 基本API调用

### 3. 通信功能测试
- Topic发布订阅
- Service请求响应
- Action调用

### 4. 集成测试
- 多节点协同
- 复杂场景测试
- 性能测试

## 错误处理

### 常见错误类型

1. **环境错误**
   - ROS环境未source
   - 环境变量缺失

2. **依赖错误**
   - 缺少运行时依赖
   - 库版本不匹配

3. **权限错误**
   - 文件权限问题
   - 设备访问权限

4. **网络错误**
   - DDS通信问题
   - 防火墙配置

### 调试技巧

```bash
# 启用调试模式
set -x

# 检查ROS环境
echo $ROS_DISTRO
echo $AMENT_PREFIX_PATH

# 检查进程
ps aux | grep ros

# 检查日志
ros2 topic echo /rosout

# 检查网络
ros2 doctor
```

## 测试报告格式

测试脚本应该输出标准化的报告：

```
========================================
测试报告: <package_name>
========================================
测试时间: $(date)
测试环境: ROS2 Humble

测试用例:
  [✓] Test Case 1: <描述>
  [✗] Test Case 2: <描述>
  [✓] Test Case 3: <描述>

统计:
  总数: 3
  通过: 2
  失败: 1

结果: FAIL
========================================
```

## 归档要求

测试用例归档到 `references/test_cases/` 时：

1. **文件命名**: `<package_name>_test.sh`
2. **添加注释**: 包含测试目标、作者、日期
3. **测试通过**: 确保测试用例已验证通过
4. **文档更新**: 在本文件中添加测试用例说明

## 示例用例

已归档的测试用例：

- `rclcpp_test.sh` - rclcpp基础功能测试
- `rclpy_test.sh` - rclpy Python绑定测试
- `fastcdr_test.sh` - Fast-CDR序列化测试
- `ros2cli_test.sh` - ROS2命令行工具测试

## 贡献指南

添加新测试用例时：

1. 遵循本指南的规范
2. 在本地验证测试通过
3. 添加详细的注释说明
4. 提交到 `references/test_cases/` 目录
