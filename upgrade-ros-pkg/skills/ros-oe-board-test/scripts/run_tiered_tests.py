#!/usr/bin/env python3
"""
Master Test Orchestrator for ROS openEuler Board Tests
Executes testing levels (L0, L1) sequentially and standardizes failures
into [HALT: XXX] blocks for Agent interception and diagnosis.
"""
import os
import sys
import argparse
import subprocess

def run_cmd(cmd, step_name):
    print(f"\n[{step_name}] Running: {cmd}")
    res = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    return res

def halt_for_agent(halt_code, phase, summary, log_content, action_items):
    print("\n" + "="*60)
    print(f"[HALT: {halt_code}]")
    print(f"Phase: {phase}")
    print(f"Summary: {summary}")
    print("\n--- Log Snippet ---")
    lines = log_content.strip().split('\n')
    print('\n'.join(lines[-30:] if len(lines) > 30 else lines))
    print("-------------------")
    print("\n【Agent Action Directive】")
    for action in action_items:
        print(f"- {action}")
    print("="*60 + "\n")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Master Tiered Test Orchestrator")
    parser.add_argument("--board-ip", required=True)
    parser.add_argument("--board-user", required=True)
    parser.add_argument("--board-password", default="")
    parser.add_argument("--levels", default="L0,L1", help="Comma separated levels, e.g. L0,L1")
    parser.add_argument("--package-path", help="Path to local ROS package source (Required for L1)")
    parser.add_argument("--eur-project", help="EUR project for environment setup")
    parser.add_argument("--dependency-list", help="Path to dependency_list.txt for filtering L0 custom tests")
    
    args = parser.parse_args()
    levels = [l.strip().upper() for l in args.levels.split(",")]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    test_cases_dir = os.path.join(os.path.dirname(script_dir), "test_cases")

    print("============================================")
    print(f"  Starting Tiered Tests: {levels}")
    print("============================================")

    # ------------------ L0 Phase ------------------
    if "L0" in levels:
        print("\n>>> Phase: L0 Smoke & Custom Tests <<<")
        
        # 1. L0 Smoke Tests (Basic CLI tests)
        l0_smoke = os.path.join(script_dir, "run_l0_smoke_tests.sh")
        if os.path.exists(l0_smoke):
            cmd = f"{l0_smoke} {args.board_ip} {args.board_user} '{args.board_password}'"
            res = run_cmd(cmd, "L0 Smoke Tests")
            if res.returncode != 0:
                halt_for_agent("L0_SMOKE_FAILED", "L0 Smoke Tests", "Basic ROS commands failed.", res.stderr or res.stdout, [
                    "Analyze logs to find missing dependencies (.so files) or core node crashes.",
                    "Update installation lists or patch the ROS package source code."
                ])
        
        # 2. L0 Custom YAML Tests (Functional tests with conditional filtering)
        l0_custom = os.path.join(script_dir, "run_l0_custom_tests.py")
        if os.path.exists(l0_custom):
            cmd = f"python3 -u {l0_custom} --board-ip {args.board_ip} --board-user {args.board_user} --board-password '{args.board_password}' --test-cases-dir {test_cases_dir} --skill-dir {os.path.dirname(script_dir)} --category all"
            if args.dependency_list:
                cmd += f" --dependency-list {args.dependency_list}"
            
            res = run_cmd(cmd, "L0 Custom YAML Tests")
            if res.returncode != 0:
                halt_for_agent("L0_CUSTOM_FAILED", "L0 Custom Tests", "One or more YAML test cases failed.", res.stderr or res.stdout, [
                    "Read the test output to see which specific YAML case failed.",
                    "Use Code Agent to inspect the source code of the failing test.",
                    "Fix any openEuler specific bugs and generate patches."
                ])
        print("✅ L0 Phase Passed.")

    # ------------------ L1 Phase ------------------
    if "L1" in levels:
        if not args.package_path:
            print("Error: --package-path is required for L1 testing.")
            sys.exit(1)

        print("\n>>> Phase: L1 On-board Incremental Build & Test <<<")
        
        # 1. L1 Env Setup
        l1_env = os.path.join(script_dir, "setup_l1_test_env.sh")
        if os.path.exists(l1_env):
            cmd = f"{l1_env} {args.board_ip} {args.board_user} '{args.board_password}'"
            res = run_cmd(cmd, "L1 Test Env Setup")
            if res.returncode != 0:
                halt_for_agent("L1_ENV_SETUP_FAILED", "L1 Env Setup", "Failed to install L1 compiling dependencies.", res.stderr or res.stdout, [
                    "Check network on board or missing packages in openEuler repos."
                ])

        # 2. L1 Colcon Build & Test
        l1_test = os.path.join(script_dir, "run_l1_colcon_tests.py")
        if os.path.exists(l1_test):
            cmd = f"python3 -u {l1_test} --board-ip {args.board_ip} --board-user {args.board_user} --board-password '{args.board_password}' --package-path '{args.package_path}'"
            res = run_cmd(cmd, "L1 Colcon Build & Test")
            
            if res.returncode == 2:
                print("⏭️ Package heavy. Redirected to L2 (skipped L1).")
            elif res.returncode != 0:
                is_oom = "killed" in (res.stderr or "").lower() or "killed" in (res.stdout or "").lower()
                halt_code = "L1_RESOURCE_OOM" if is_oom else "L1_BUILD_OR_TEST_FAILED"
                summary = "OOM killed compiler." if is_oom else "colcon build/test failed."
                halt_for_agent(halt_code, "L1 Colcon Build & Test", summary, res.stderr or res.stdout, [
                    "If OOM: Recommend L2 QEMU testing or reduce -j parallelism.",
                    "If compile error: Check CMakeLists.txt or .cpp files, missing .d dependency files usually mean concurrent build IO issues. Force sequential build.",
                    "If test failed: Skip inherited upstream issues or patch openEuler specific bugs."
                ])
        print("✅ L1 Phase Passed.")

    print("\n🎉 All requested testing levels completed successfully!")

if __name__ == '__main__':
    main()
