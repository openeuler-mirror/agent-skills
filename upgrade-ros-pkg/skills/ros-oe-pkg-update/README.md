# ros-oe-pkg-update skill

用于在本地将上游（upstream）ROS 包的 spec 文件、补丁、tar 包更新到 openEuler 仓库中。

## 快速开始

```bash
skill: "ros-oe-pkg-update", args: "--openeuler-dir /path/to/src-openeuler --upstream-dir /path/to/ros-oe-upstream-init/output/repo --package-list-file dependency_order.txt"
```

## 主要功能

1. **智能 Spec 更新**: 根据 upstream spec 更新 openEuler spec 文件
2. **补丁智能处理**: 自动处理四种补丁差异情况
3. **Tar 包管理**: 自动删除旧 tar 包,拷贝新 tar 包
4. **差异记录**: 记录 spec 升级差异点用于 git commit
5. **Code Agent 集成**: 遇到未知差异自动暂停并调用 agent 深入分析

## 工作流程

对每个包执行:

1. 创建备份文件
2. 删除旧 tar 包
3. 用 upstream spec 覆盖 openEuler spec
4. 使用 git diff 分析差异
5. 根据差异类型处理:
   - 预期差异: 自动处理
   - 补丁差异: 智能处理或调用 agent
   - 未知差异: 调用 code agent 分析
6. 拷贝新 tar 包
7. 删除备份文件

## 补丁处理的四种情况

| 情况 | 操作 | 说明 |
|------|------|------|
| 老有补丁,新无补丁 | 恢复补丁 | openEuler 定制化补丁需要保留 |
| 老有补丁 A,新有补丁 A | 无需修改 | 补丁未变化,正常升级 |
| 老有补丁 A,新有补丁 B | 调用 agent 分析 | 补丁内容或名称变更,需要深入分析 |
| 老无补丁,新有补丁 | 拷贝新补丁 | 新上游补丁需要同步到 openEuler |

## 输出文件

- `local_update.log`: 详细操作日志 (生成到 `.tmp/logs/`)
- `local_update_success.txt`: 成功更新的包列表 (生成到 `.tmp/state/`)
- `local_update_failure.txt`: 失败的包列表 (生成到 `.tmp/state/`)
- `agent_analysis_request.json`: 需要 agent 分析的包列表 (生成到 `.tmp/state/`)

## 与其他 Skill 配合

```
ros-oe-pkg-prep → 生成依赖列表
       ↓
       ↓
ros-oe-pkg-update → 本地更新 spec、tar 包
       ↓
       ↓
测试与提交
```

## 详细文档

完整的文档请参考 SKILL.md,包含:
- 详细的使用方法
- 参数说明
- 处理逻辑
- 补丁处理的四种情况
- Code Agent 调用机制
- 错误处理
- 注意事项
