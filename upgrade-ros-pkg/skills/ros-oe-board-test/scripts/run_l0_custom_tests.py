#!/usr/bin/env python3
"""
ROS2 Test Runner with Intelligent Failure Analysis and Conditional Execution

This runner:
1. Loads test cases from YAML files
2. Conditionally skips tests based on target package list (requires_packages)
3. Uploads test data files to development board (test_data)
4. Executes tests on development board via SSH
5. Detects fatal system errors (OOM, segfault, alloc failure, etc.)
6. Analyzes failures using code agent
7. Automatically iterates with fixes
8. Generates comprehensive reports with PASS/FAIL/SKIP status
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class TestCase:
    """Test case definition."""
    id: str
    name: str
    description: str = ""
    priority: str = "P2"
    command: str = ""
    expect_contains: list[str] = field(default_factory=list)
    expect_not_contains: list[str] = field(default_factory=list)
    timeout: int = 30
    pre_condition: Optional[dict] = None
    setup: Optional[list] = None
    cleanup: Optional[list] = None
    packages_required: Optional[list] = None
    requires_packages: Optional[list] = None  # Conditional execution
    on_failure: Optional[dict] = None


@dataclass
class TestResult:
    """Test execution result."""
    test_id: str
    test_name: str
    passed: bool
    output: str
    error: str
    duration: float
    timestamp: str
    retry_count: int = 0
    diagnosis: str = ""
    fix_applied: str = ""
    skipped: bool = False
    skip_reason: str = ""


class SSHExecutor:
    """Execute commands on remote board via SSH."""

    def __init__(self, host: str, username: str, password: str):
        self.host = host
        self.username = username
        self.password = password

    def exec(self, command: str, timeout: int = 30, source_ros: bool = True) -> tuple[int, str, str]:
        """Execute command and return (returncode, stdout, stderr)."""
        if source_ros:
            full_command = f"source /opt/ros/humble/setup.bash && {command}"
        else:
            full_command = command

        sshpass_cmd = [
            "sshpass", "-p", self.password,
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=10",
            f"{self.username}@{self.host}",
            full_command
        ]

        try:
            result = subprocess.run(
                sshpass_cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    def scp_upload(self, local_path: str, remote_path: str) -> bool:
        """Upload a file to the remote board via SCP."""
        # First create the remote directory
        remote_dir = os.path.dirname(remote_path)
        self.exec(f"mkdir -p {remote_dir}", source_ros=False, timeout=10)

        scp_cmd = [
            "sshpass", "-p", self.password,
            "scp", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            local_path,
            f"{self.username}@{self.host}:{remote_path}"
        ]

        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            print(f"    [ERROR] SCP upload failed: {e}")
            return False

    def cleanup_all_ros_processes(self):
        """Kill all ROS demo and integration test processes."""
        self.exec(
            "pkill -f 'demo_nodes' 2>/dev/null || true; "
            "pkill -f 'realsense2_camera_node' 2>/dev/null || true; "
            "pkill -f 'rtabmap' 2>/dev/null || true; "
            "pkill -f 'rgbd_odometry' 2>/dev/null || true; "
            "pkill -f 'nav2_' 2>/dev/null || true; "
            "pkill -f 'lifecycle_manager' 2>/dev/null || true; "
            "pkill -f 'amcl' 2>/dev/null || true; "
            "pkill -f 'map_server' 2>/dev/null || true; "
            "pkill -f 'ros2.launch' 2>/dev/null || true",
            source_ros=False
        )


class FailureAnalyzer:
    """Analyze test failures and generate fix suggestions."""

    # Known failure patterns and their diagnoses
    FAILURE_PATTERNS = {
        # ---- System / Resource failures ----
        r"Cannot allocate memory": {
            "diagnosis": "内存分配失败（系统内存不足）",
            "category": "system",
            "suggestions": [
                "检查系统可用内存: free -h",
                "关闭不必要的后台进程: pkill -f 'nav2_|rtabmap|realsense'",
                "检查是否有 OOM Killer 记录: dmesg | grep -i oom",
                "考虑添加 swap 分区或减少同时启动的节点数"
            ]
        },
        r"std::bad_alloc": {
            "diagnosis": "C++ 内存分配异常（std::bad_alloc）",
            "category": "system",
            "suggestions": [
                "系统内存不足导致 new/malloc 失败",
                "检查系统可用内存: free -h",
                "检查该进程的内存使用: ps aux --sort=-rss | head",
                "可能需要增加系统内存或减少并发节点"
            ]
        },
        r"Killed|SIGKILL": {
            "diagnosis": "进程被强制终止（可能被 OOM Killer 杀掉）",
            "category": "system",
            "suggestions": [
                "检查 dmesg 是否有 OOM Killer 记录: dmesg | grep -i 'oom\\|killed process'",
                "检查系统可用内存: free -h",
                "嵌入式设备内存有限，可能需要逐一启动节点而非同时启动"
            ]
        },
        r"Segmentation fault|SIGSEGV|core dump": {
            "diagnosis": "段错误（进程崩溃）",
            "category": "crash",
            "suggestions": [
                "检查 core dump 文件: ls /tmp/core.* 或 coredumpctl list",
                "检查动态库兼容性: ldd <binary_path>",
                "可能是包编译与运行环境不匹配（aarch64 特有问题）",
                "检查 dmesg 中的段错误信息: dmesg | grep segfault"
            ]
        },
        r"error while loading shared libraries": {
            "diagnosis": "动态库加载失败",
            "category": "library",
            "suggestions": [
                "检查缺失的动态库: ldd <binary_path> | grep 'not found'",
                "安装缺失的依赖包",
                "检查 LD_LIBRARY_PATH 是否包含 /opt/ros/humble/lib",
                "运行 ldconfig 更新动态库缓存"
            ]
        },
        r"No such file or directory": {
            "diagnosis": "文件或目录不存在",
            "category": "file",
            "suggestions": [
                "检查相关文件/目录是否存在",
                "检查文件路径是否正确",
                "可能是测试数据未上传到开发板"
            ]
        },
        r"what\(\):\s*vector|what\(\):\s*basic_string|what\(\):\s*map": {
            "diagnosis": "C++ STL 异常（容器操作越界或无效）",
            "category": "crash",
            "suggestions": [
                "可能是配置参数格式错误导致解析异常",
                "检查 launch 参数和配置文件格式",
                "查看完整的异常栈信息"
            ]
        },
        r"abort|SIGABRT": {
            "diagnosis": "进程异常中止（SIGABRT）",
            "category": "crash",
            "suggestions": [
                "检查是否有 assert 失败",
                "检查 C++ 异常是否未被捕获",
                "查看完整的错误日志获取调用栈"
            ]
        },
        r"bus error|SIGBUS": {
            "diagnosis": "总线错误（内存对齐或映射问题）",
            "category": "crash",
            "suggestions": [
                "可能是 aarch64 内存对齐问题",
                "检查共享内存映射: ls -la /dev/shm/",
                "检查 tmpfs 空间: df -h /tmp"
            ]
        },
        r"timed out waiting for.*to become available": {
            "diagnosis": "等待服务/节点超时（lifecycle 激活失败）",
            "category": "timeout",
            "suggestions": [
                "嵌入式设备启动慢，增加等待时间",
                "检查被等待的节点是否已崩溃: ps aux | grep <node_name>",
                "检查 lifecycle manager 日志查看具体等待哪个节点"
            ]
        },
        r"Transform.*error|TF.*error|lookup.*transform.*failed": {
            "diagnosis": "TF 坐标变换错误（正常 — 无传感器输入时无 TF 数据）",
            "category": "expected_warning",
            "suggestions": [
                "无传感器时 TF 错误是预期行为",
                "如果节点仍在运行，说明包安装正确",
                "此错误不影响包的安装验证"
            ]
        },
        # ---- Environment failures ----
        r"ROS_DISTRO.*not found": {
            "diagnosis": "ROS环境未加载",
            "category": "environment",
            "suggestions": [
                "执行 source /opt/ros/humble/setup.bash",
                "检查 /opt/ros/humble 目录是否存在",
                "安装 ros-humble-ros-base 包"
            ]
        },
        r"ros2.*command not found": {
            "diagnosis": "ros2 CLI不可用",
            "category": "package",
            "suggestions": [
                "安装 ros-humble-ros2cli 包",
                "检查 PATH 环境变量"
            ]
        },
        r"invalid choice.*launch": {
            "diagnosis": "ros2 launch 命令不可用",
            "category": "package",
            "suggestions": [
                "安装 ros-humble-ros2launch 和 ros-humble-launch-ros 包",
                "dnf install -y ros-humble-ros2launch ros-humble-launch-ros"
            ]
        },
        r"package.*not found.*searching": {
            "diagnosis": "ROS包未安装",
            "category": "package",
            "suggestions": [
                "使用 dnf install 安装对应包",
                "检查 EUR 源是否配置正确"
            ]
        },
        r"No executable found": {
            "diagnosis": "可执行文件不存在",
            "category": "package",
            "suggestions": [
                "检查包是否完整安装",
                "使用 ros2 pkg executables 查看可用可执行文件"
            ]
        },
        # ---- Communication failures ----
        r"timeout.*waiting for": {
            "diagnosis": "通信超时",
            "category": "communication",
            "suggestions": [
                "检查 DDS 发现机制",
                "检查网络配置和防火墙",
                "检查 RMW_IMPLEMENTATION 环境变量"
            ]
        },
        r"failed to get.*topic": {
            "diagnosis": "话题通信失败",
            "category": "communication",
            "suggestions": [
                "检查发布者节点是否运行",
                "检查 DDS 配置",
                "检查网络连通性"
            ]
        },
        r"service.*not available": {
            "diagnosis": "服务不可用",
            "category": "communication",
            "suggestions": [
                "检查服务端节点是否运行",
                "检查服务名称是否正确"
            ]
        },
        # ---- RMW/DDS failures ----
        r"RMW.*error": {
            "diagnosis": "RMW中间件错误",
            "category": "rmw",
            "suggestions": [
                "检查 RMW_IMPLEMENTATION 环境变量",
                "检查 DDS 实现是否安装 (fastrtps/connext/cyclone)"
            ]
        },
        r"participant.*failed": {
            "diagnosis": "DDS参与者创建失败",
            "category": "rmw",
            "suggestions": [
                "检查网络配置",
                "检查共享内存设置",
                "重启 DDS 守护进程"
            ]
        },
        # ---- Permission failures ----
        r"permission denied": {
            "diagnosis": "权限问题",
            "category": "permission",
            "suggestions": [
                "检查文件/目录权限",
                "使用 root 用户执行"
            ]
        },
    }

    def __init__(self, executor: SSHExecutor, skill_dir: Path):
        self.executor = executor
        self.skill_dir = skill_dir

    def analyze(self, test_case: TestCase, output: str, error: str) -> dict:
        """
        Analyze test failure and return diagnosis.

        Returns:
            {
                "diagnosis": str,
                "category": str,
                "suggestions": list[str],
                "needs_agent_analysis": bool,
                "agent_analysis_prompt": str (if needs_agent_analysis)
            }
        """
        combined_output = output + "\n" + error

        # First, check for fatal system errors (highest priority)
        fatal_patterns = [
            r"Cannot allocate memory", r"std::bad_alloc",
            r"Killed|SIGKILL", r"Segmentation fault|SIGSEGV",
            r"error while loading shared libraries",
            r"abort|SIGABRT", r"bus error|SIGBUS",
        ]
        for pattern in fatal_patterns:
            if re.search(pattern, combined_output, re.IGNORECASE):
                info = self.FAILURE_PATTERNS.get(pattern, {})
                if info:
                    return {
                        "diagnosis": info["diagnosis"],
                        "category": info["category"],
                        "suggestions": info["suggestions"],
                        "needs_agent_analysis": False
                    }

        # Second, check if test case has built-in failure info
        if test_case.on_failure:
            result = {
                "diagnosis": test_case.on_failure.get("diagnosis", "测试失败"),
                "category": test_case.category if hasattr(test_case, 'category') else "unknown",
                "suggestions": test_case.on_failure.get("suggestions", []),
                "needs_agent_analysis": False
            }
            if result["suggestions"]:
                return result

        # Third, pattern matching for known failures
        for pattern, info in self.FAILURE_PATTERNS.items():
            if re.search(pattern, combined_output, re.IGNORECASE):
                return {
                    "diagnosis": info["diagnosis"],
                    "category": info["category"],
                    "suggestions": info["suggestions"],
                    "needs_agent_analysis": False
                }

        # Unknown failure - needs agent analysis
        return {
            "diagnosis": "需要进一步分析",
            "category": "unknown",
            "suggestions": [],
            "needs_agent_analysis": True,
            "agent_analysis_prompt": self._build_agent_prompt(test_case, output, error)
        }

    def _build_agent_prompt(self, test_case: TestCase, output: str, error: str) -> str:
        """Build prompt for code agent analysis."""
        return f"""## ROS2 测试失败分析请求

### 测试用例信息
- ID: {test_case.id}
- 名称: {test_case.name}
- 描述: {test_case.description}
- 命令: `{test_case.command}`
- 预期输出包含: {test_case.expect_contains}
- 预期输出不包含: {test_case.expect_not_contains}

### 实际输出
```
{output[:2000]}
```

### 错误信息
```
{error[:1000]}
```

### 分析要求
请分析上述测试失败的原因，并提供以下信息：

1. **根本原因**: 测试失败的根本原因是什么？
2. **修复类别**: 应该采取哪种修复方式？
   - [ ] 修改测试用例 (测试命令或预期值有误)
   - [ ] 修改环境配置 (缺少包、环境变量等)
   - [ ] 修改构建内容 (spec文件、补丁等)
3. **具体修复建议**: 列出具体的修复步骤
4. **验证命令**: 修复后如何验证

请以JSON格式回复分析结果。
"""


class TestRunner:
    """Load and execute test cases with auto-retry and conditional execution."""

    def __init__(self, executor: SSHExecutor, test_cases_dir: Path, skill_dir: Path,
                 max_retries: int = 1, target_packages: Optional[set[str]] = None):
        self.executor = executor
        self.test_cases_dir = test_cases_dir
        self.skill_dir = skill_dir
        self.max_retries = max_retries
        self.target_packages = target_packages  # Set of package names from dependency list
        self.results: list[TestResult] = []
        self.analyzer = FailureAnalyzer(executor, skill_dir)
        self._uploaded_data: set[str] = set()  # Track uploaded test data

    def _load_target_packages(self, dependency_list_file: str) -> set[str]:
        """Load target package names from dependency list file."""
        packages = set()
        try:
            with open(dependency_list_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        packages.add(line)
        except FileNotFoundError:
            print(f"[WARN] Dependency list file not found: {dependency_list_file}")
        return packages

    def _check_requires_packages(self, test_case: TestCase) -> tuple[bool, str]:
        """
        Check if the test case's required packages are in the target package list.

        Returns:
            (should_run, skip_reason)
        """
        if not test_case.requires_packages:
            return True, ""  # No requirement, always run

        if self.target_packages is None:
            return True, ""  # No target list provided, run all

        for pkg in test_case.requires_packages:
            if pkg in self.target_packages:
                return True, ""

        missing = ", ".join(test_case.requires_packages)
        return False, f"目标包列表中不包含: {missing}"

    def _upload_test_data(self, yaml_data: dict) -> None:
        """Upload test_data files defined in YAML to the development board."""
        test_data_list = yaml_data.get("test_data", [])
        if not test_data_list:
            return

        for item in test_data_list:
            source = item.get("source", "")
            target = item.get("target", "")
            if not source or not target:
                continue

            # Already uploaded in this session
            if target in self._uploaded_data:
                continue

            # Resolve source path relative to test_cases_dir parent (skill root)
            local_path = self.test_cases_dir.parent / source
            if not local_path.exists():
                # Also try relative to test_cases_dir itself
                local_path = self.test_cases_dir / source
            if not local_path.exists():
                print(f"[WARN] Test data file not found: {source} (tried {local_path})")
                continue

            print(f"[INFO] Uploading test data: {source} -> {target}")
            success = self.executor.scp_upload(str(local_path), target)
            if success:
                self._uploaded_data.add(target)
                print(f"[INFO]   ✓ Uploaded successfully")
            else:
                print(f"[WARN]   ✗ Upload failed: {source}")

    def load_test_cases(self, category: str) -> list[TestCase]:
        """Load test cases from YAML files."""
        test_cases = []
        categories_loaded = set()

        if category == "all":
            yaml_files = sorted(self.test_cases_dir.glob("*.yaml"))
        else:
            categories = [c.strip() for c in category.split(",")]
            yaml_files = []
            for cat in categories:
                # Support both numbered (07_integration) and plain (integration) names
                matches = sorted(self.test_cases_dir.glob(f"*{cat}*.yaml"))
                if matches:
                    yaml_files.extend(matches)
                else:
                    yaml_file = self.test_cases_dir / f"{cat}.yaml"
                    if yaml_file.exists():
                        yaml_files.append(yaml_file)

        for yaml_file in yaml_files:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            category_name = data.get("category", yaml_file.stem)
            categories_loaded.add(category_name)

            # Upload test data for this YAML if needed
            self._upload_test_data(data)

            for tc in data.get("test_cases", []):
                test_case = TestCase(
                    id=tc["id"],
                    name=tc["name"],
                    description=tc.get("description", ""),
                    priority=tc.get("priority", "P2"),
                    command=tc["command"],
                    expect_contains=tc.get("expect_contains", []),
                    expect_not_contains=tc.get("expect_not_contains", []),
                    timeout=tc.get("timeout", 30),
                    pre_condition=tc.get("pre_condition"),
                    setup=tc.get("setup"),
                    cleanup=tc.get("cleanup"),
                    packages_required=tc.get("packages_required"),
                    requires_packages=tc.get("requires_packages"),
                    on_failure=tc.get("on_failure")
                )
                test_case.category = category_name
                test_cases.append(test_case)

        print(f"[INFO] Loaded {len(test_cases)} test cases from {len(yaml_files)} files")
        print(f"[INFO] Categories: {', '.join(categories_loaded)}")

        return test_cases

    def run_test(self, test_case: TestCase, retry_count: int = 0) -> TestResult:
        """Execute a single test case."""
        start_time = time.time()

        # Run setup commands
        if test_case.setup:
            for setup_cmd in test_case.setup:
                cmd = setup_cmd["command"]
                if setup_cmd.get("background"):
                    # 使用 nohup 和 bash -c 确保后台进程正确启动并继承环境
                    wrapped_cmd = f"nohup bash -c '{cmd}' > /dev/null 2>&1 &"
                    self.executor.exec(wrapped_cmd, timeout=5, source_ros=True)
                    wait_time = setup_cmd.get("wait", 1)
                    time.sleep(wait_time)
                else:
                    self.executor.exec(cmd, timeout=30)

        # Execute main command
        returncode, stdout, stderr = self.executor.exec(
            test_case.command,
            test_case.timeout
        )

        # Run cleanup commands
        if test_case.cleanup:
            for cleanup_cmd in test_case.cleanup:
                cmd_str = cleanup_cmd.get("command", "") if isinstance(cleanup_cmd, dict) else cleanup_cmd
                if cmd_str.strip() == "sleep 2":
                    time.sleep(2)
                elif cmd_str.strip() == "sleep 3":
                    time.sleep(3)
                else:
                    self.executor.exec(cmd_str, timeout=10)

        # Also run general cleanup
        time.sleep(0.5)
        self.executor.cleanup_all_ros_processes()

        # Check expectations
        passed = True
        combined_output = stdout + "\n" + stderr
        fail_detail = ""

        for expected in test_case.expect_contains:
            if expected not in combined_output:
                passed = False
                fail_detail = f"预期包含 '{expected}' 但未找到"
                break

        if passed:
            for not_expected in test_case.expect_not_contains:
                if not_expected in combined_output:
                    passed = False
                    fail_detail = f"发现不应出现的内容: '{not_expected}'"
                    break

        duration = time.time() - start_time

        # Analyze failure if not passed
        diagnosis = ""
        fix_applied = ""
        if not passed:
            analysis = self.analyzer.analyze(test_case, stdout, stderr)
            diagnosis = analysis["diagnosis"]
            if fail_detail:
                diagnosis = f"{fail_detail} | {diagnosis}"

            if retry_count < self.max_retries:
                # Try to auto-fix
                fix_result = self._try_auto_fix(test_case, analysis, stdout, stderr)
                if fix_result:
                    fix_applied = fix_result
                    # Re-run test after fix
                    print(f"    [RETRY] Re-running after fix: {fix_result}")
                    time.sleep(2)
                    return self.run_test(test_case, retry_count + 1)

        return TestResult(
            test_id=test_case.id,
            test_name=test_case.name,
            passed=passed,
            output=combined_output,
            error=stderr if not passed else "",
            duration=duration,
            timestamp=datetime.now().isoformat(),
            retry_count=retry_count,
            diagnosis=diagnosis,
            fix_applied=fix_applied
        )

    def _try_auto_fix(self, test_case: TestCase, analysis: dict, stdout: str, stderr: str) -> Optional[str]:
        """
        Try to automatically fix the issue based on analysis.

        Returns:
            Fix description if fix was applied, None otherwise
        """
        category = analysis.get("category", "unknown")

        if category == "environment":
            # Try to source ROS environment
            returncode, out, err = self.executor.exec("test -f /opt/ros/humble/setup.bash && echo 'OK'", source_ros=False)
            if "OK" in out:
                return "ROS environment verified"

        elif category == "package":
            # Check if we need to install packages
            if test_case.packages_required:
                installed_any = False
                for pkg in test_case.packages_required:
                    rpm_pkg = f"ros-humble-{pkg.replace('_', '-')}"
                    print(f"    [FIX] Attempting to install: {rpm_pkg}")
                    ret, out, err = self.executor.exec(
                        f"dnf install -y {rpm_pkg}",
                        timeout=120,
                        source_ros=False
                    )
                    if ret == 0 and "already installed" not in out.lower():
                        installed_any = True
                if installed_any:
                    return f"Installed missing packages"

        elif category == "communication":
            # Check DDS/RMW
            ret, out, err = self.executor.exec("echo $RMW_IMPLEMENTATION")
            if not out.strip():
                ret, out, err = self.executor.exec(
                    "export RMW_IMPLEMENTATION=rmw_fastrtps_cpp",
                    source_ros=False
                )
                return "Set RMW_IMPLEMENTATION=rmw_fastrtps_cpp"

        elif category == "system":
            # For system errors (OOM etc.), try cleaning up processes and freeing memory
            print("    [FIX] Attempting to free system resources...")
            self.executor.cleanup_all_ros_processes()
            time.sleep(3)
            ret, out, err = self.executor.exec("sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null; echo 'CACHE_DROPPED'", source_ros=False)
            if "CACHE_DROPPED" in out:
                return "Cleaned processes and dropped caches to free memory"

        return None

    def run_all(self, category: str) -> list[TestResult]:
        """Run all test cases in category, with conditional skip."""
        test_cases = self.load_test_cases(category)

        # Sort by priority (P0 > P1 > P2)
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        test_cases.sort(key=lambda tc: priority_order.get(tc.priority, 3))

        # Count how many will be skipped
        skip_count = 0
        if self.target_packages is not None:
            for tc in test_cases:
                should_run, _ = self._check_requires_packages(tc)
                if not should_run:
                    skip_count += 1

        print(f"\n{'='*60}")
        print(f"Running {len(test_cases)} test cases (max retries: {self.max_retries})")
        if self.target_packages is not None:
            print(f"Target packages: {len(self.target_packages)} packages loaded")
            if skip_count > 0:
                print(f"Will skip: {skip_count} test cases (requires_packages not in target list)")
        print(f"{'='*60}\n")

        for tc in test_cases:
            # Check conditional execution
            should_run, skip_reason = self._check_requires_packages(tc)

            if not should_run:
                print(f"[{tc.priority}] {tc.id}: {tc.name}... ⏭️ SKIP ({skip_reason})")
                self.results.append(TestResult(
                    test_id=tc.id,
                    test_name=tc.name,
                    passed=True,  # Skipped tests don't count as failures
                    output="",
                    error="",
                    duration=0.0,
                    timestamp=datetime.now().isoformat(),
                    skipped=True,
                    skip_reason=skip_reason
                ))
                continue

            print(f"[{tc.priority}] {tc.id}: {tc.name}...", end=" ", flush=True)
            result = self.run_test(tc)
            self.results.append(result)

            if result.passed:
                status = "✅ PASS"
                if result.retry_count > 0:
                    status += f" (retry #{result.retry_count})"
                print(f"{status} ({result.duration:.2f}s)")
            else:
                print(f"❌ FAIL ({result.duration:.2f}s)")
                if result.diagnosis:
                    print(f"    [诊断] {result.diagnosis}")
                if result.fix_applied:
                    print(f"    [修复] {result.fix_applied}")
                if result.error:
                    error_preview = result.error[:200].replace('\n', ' ')
                    print(f"    [错误] {error_preview}...")

        return self.results

    def generate_report(self, output_dir: Path) -> dict:
        """Generate test report with PASS/FAIL/SKIP status."""
        total = len(self.results)
        skipped = sum(1 for r in self.results if r.skipped)
        executed = total - skipped
        passed = sum(1 for r in self.results if r.passed and not r.skipped)
        failed = sum(1 for r in self.results if not r.passed)

        # Group by priority
        by_priority = {}
        for p in ["P0", "P1", "P2"]:
            by_priority[p] = {"passed": 0, "failed": 0, "skipped": 0}

        # Build priority lookup from loaded test cases
        tc_priority_map = {}
        for r in self.results:
            tc_priority_map[r.test_id] = "P2"  # default

        for yaml_file in sorted(self.test_cases_dir.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                for tc in data.get("test_cases", []):
                    tc_priority_map[tc["id"]] = tc.get("priority", "P2")
            except Exception:
                pass

        for r in self.results:
            priority = tc_priority_map.get(r.test_id, "P2")
            if r.skipped:
                by_priority[priority]["skipped"] += 1
            elif r.passed:
                by_priority[priority]["passed"] += 1
            else:
                by_priority[priority]["failed"] += 1

        pass_rate = f"{passed/executed*100:.1f}%" if executed > 0 else "N/A"

        report = {
            "summary": {
                "total": total,
                "executed": executed,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "pass_rate": pass_rate,
                "by_priority": by_priority,
                "timestamp": datetime.now().isoformat()
            },
            "results": [
                {
                    "id": r.test_id,
                    "name": r.test_name,
                    "status": "SKIP" if r.skipped else ("PASS" if r.passed else "FAIL"),
                    "passed": r.passed,
                    "skipped": r.skipped,
                    "skip_reason": r.skip_reason,
                    "duration": round(r.duration, 2),
                    "retry_count": r.retry_count,
                    "diagnosis": r.diagnosis,
                    "fix_applied": r.fix_applied,
                    "output": r.output[:500] if r.output else "",
                    "error": r.error[:200] if r.error else ""
                }
                for r in self.results
            ]
        }

        # Save JSON report
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "test_report.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Save detailed failure log
        failed_results = [r for r in self.results if not r.passed]
        if failed_results:
            failure_path = output_dir / "test_failures.json"
            with open(failure_path, "w") as f:
                json.dump([
                    {
                        "id": r.test_id,
                        "name": r.test_name,
                        "diagnosis": r.diagnosis,
                        "error": r.error,
                        "full_output": r.output
                    }
                    for r in failed_results
                ], f, indent=2, ensure_ascii=False)

        # Print summary
        print(f"\n{'='*60}")
        print("Test Summary")
        print(f"{'='*60}")
        print(f"Total: {total}, Executed: {executed}, Passed: {passed}, Failed: {failed}, Skipped: {skipped}")
        print(f"Pass Rate: {pass_rate} (of executed tests)")
        print("")
        print("By Priority:")
        for p in ["P0", "P1", "P2"]:
            stats = by_priority[p]
            parts = []
            if stats['passed']:
                parts.append(f"{stats['passed']} passed")
            if stats['failed']:
                parts.append(f"{stats['failed']} failed")
            if stats['skipped']:
                parts.append(f"{stats['skipped']} skipped")
            if parts:
                print(f"  {p}: {', '.join(parts)}")
        print("")
        print(f"Report saved to: {json_path}")
        if failed_results:
            failure_path = output_dir / "test_failures.json"
            print(f"Failure details: {failure_path}")

        return report


def main():
    parser = argparse.ArgumentParser(description="ROS2 Test Runner with Conditional Execution")
    parser.add_argument("--board-ip", required=True, help="Development board IP address")
    parser.add_argument("--board-username", required=True, help="SSH username")
    parser.add_argument("--board-password", required=True, help="SSH password")
    parser.add_argument("--test-cases-dir", required=True, help="Directory containing test YAML files")
    parser.add_argument("--category", default="all",
                        help="Test category to run (all, environment, topic, integration, etc.)")
    parser.add_argument("--output-dir", default="./test_results", help="Output directory for reports")
    parser.add_argument("--max-retries", type=int, default=1, help="Max retry count per test")
    parser.add_argument("--skill-dir", required=True, help="Skill scripts directory")
    parser.add_argument("--dependency-list", default=None,
                        help="Path to dependency_list.txt for conditional test execution. "
                             "Tests with requires_packages will be skipped if their packages "
                             "are not in this list.")

    args = parser.parse_args()

    # Load target packages for conditional execution
    target_packages = None
    if args.dependency_list:
        target_packages = set()
        try:
            with open(args.dependency_list) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        target_packages.add(line)
            print(f"[INFO] Loaded {len(target_packages)} target packages from {args.dependency_list}")
        except FileNotFoundError:
            print(f"[WARN] Dependency list not found: {args.dependency_list}")
            target_packages = None

    executor = SSHExecutor(
        host=args.board_ip,
        username=args.board_username,
        password=args.board_password
    )

    runner = TestRunner(
        executor=executor,
        test_cases_dir=Path(args.test_cases_dir),
        skill_dir=Path(args.skill_dir),
        max_retries=args.max_retries,
        target_packages=target_packages
    )

    runner.run_all(args.category)
    report = runner.generate_report(Path(args.output_dir))

    # Exit with error code if any tests failed (skipped don't count)
    if report["summary"]["failed"] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
