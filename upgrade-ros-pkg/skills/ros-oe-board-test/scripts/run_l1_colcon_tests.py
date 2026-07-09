#!/usr/bin/env python3
"""
Executes Level 1 (L1) on-board incremental `colcon test` for a specific package.
It uses lib_test_framework.py for SSH and package assessment.
"""
import os
import sys
import argparse
from lib_test_framework import ssh_cmd, scp_dir, assess_package

def run_l1_test(args):
    pkg_dir = args.package_path
    if not os.path.exists(pkg_dir):
        print(f"[ERROR] Package directory not found: {pkg_dir}", file=sys.stderr)
        sys.exit(1)
        
    pkg_name = os.path.basename(os.path.abspath(pkg_dir))
    print(f"\n{'='*60}")
    print(f"🚀 开始评估包: {pkg_name}")
    print(f"{'='*60}")

    eligible, reason = assess_package(pkg_dir, args.cpp_test_threshold)
    print(f"评估结果: {'✅ [L1 通过]' if eligible else '⏭️ [分配至 L2]'} -> {reason}")

    if not eligible:
        print(f"\n[INFO] 由于性能限制，跳过 {pkg_name} 的 L1 开发板测试。")
        print(f"[INFO] 后续应在 L2 (QEMU/Docker Host PC) 中进行全量编译测试。")
        # Exit with code 2 to indicate skipped/redirected
        sys.exit(2)

    print(f"\n--- 执行 Level 1 开发板增量编译与测试: {pkg_name} ---")

    # 1. 准备清理工作空间
    print("\n[1/5] 准备测试工作空间...")
    ssh_cmd("rm -rf ~/l1_test_ws && mkdir -p ~/l1_test_ws/src", args.board_ip, args.board_user, args.board_password)
    
    # 2. 推送源码
    print("\n[2/5] 推送源码至开发板...")
    res = scp_dir(pkg_dir, "~/l1_test_ws/src/", args.board_ip, args.board_user, args.board_password)
    if res.returncode != 0:
        print(f"❌ 源码推送失败: {res.stderr}", file=sys.stderr)
        sys.exit(1)

    # 3. 构建 (开启测试)
    print("\n[3/5] 正在开发板上编译测试代码 (Underlay 模式)...")
    # 支持默认的 colcon 或者 /usr/local/bin/colcon
    colcon_check = ssh_cmd("which colcon", args.board_ip, args.board_user, args.board_password)
    colcon_bin = "colcon" if colcon_check.returncode == 0 else "/usr/local/bin/colcon"

    build_cmd = f"cd ~/l1_test_ws && source /opt/ros/humble/setup.bash && {colcon_bin} build --cmake-args -DBUILD_TESTING=ON"
    build_res = ssh_cmd(build_cmd, args.board_ip, args.board_user, args.board_password)
    
    if build_res.returncode != 0:
        print("❌ 构建失败!", file=sys.stderr)
        print(build_res.stdout, file=sys.stderr)
        print(build_res.stderr, file=sys.stderr)
        sys.exit(1)

    # 4. 运行测试
    print("\n[4/5] 正在运行测试用例 (colcon test)...")
    test_cmd = f"cd ~/l1_test_ws && source /opt/ros/humble/setup.bash && timeout 600 {colcon_bin} test --event-handlers console_direct+"
    test_res = ssh_cmd(test_cmd, args.board_ip, args.board_user, args.board_password)
    
    if test_res.returncode == 124: # timeout exit code
        print(f"❌ [L1 Error] '{pkg_name}' 测试执行超时 (10分钟/600秒).", file=sys.stderr)
        sys.exit(1)

    print("\n[--- Colcon Test Output ---]")
    print(test_res.stdout)
    if test_res.stderr:
        print("[--- STDERR ---]", file=sys.stderr)
        print(test_res.stderr, file=sys.stderr)
    print("[--------------------------]\n")

    # 5. 获取结果
    print("\n[5/5] 测试结果汇总:")
    res = ssh_cmd(f"cd ~/l1_test_ws && source /opt/ros/humble/setup.bash && {colcon_bin} test-result --all", args.board_ip, args.board_user, args.board_password)
    print(res.stdout.strip())
    
    if res.returncode == 0:
        print(f"\n✅ 包 {pkg_name} L1 测试完美通过！")
        sys.exit(0)
    else:
        print(f"\n❌ 包 {pkg_name} L1 测试存在失败项，请检查日志！", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Run L1 Tiered Test: On-board Incremental Testing")
    parser.add_argument("--board-ip", required=True, help="Target board IP address")
    parser.add_argument("--board-user", required=True, help="Target board SSH user")
    parser.add_argument("--board-password", required=False, default="", help="Target board SSH password")
    parser.add_argument("--package-path", required=True, help="Local path to the ROS package source code")
    parser.add_argument("--cpp-test-threshold", type=int, default=5, help="Threshold for .cpp test files to route to L1 vs L2 (default: 5)")
    
    args = parser.parse_args()
    run_l1_test(args)
