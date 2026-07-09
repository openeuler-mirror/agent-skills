#!/usr/bin/env python3
"""
MoveIt2 OMPL Planner Integration Test for openEuler Embedded Board.

This script performs a full end-to-end OMPL motion planning test:
1. Starts panda robot headless demo (move_group + ros2_control FakeSystem) via Python Launch API
2. Sends a plan-only request via MoveGroup action (RRTConnect planner)
3. Verifies trajectory generation succeeds without SONAME errors or crashes

Usage (on board):
    source /opt/ros/humble/setup.bash
    python3 test_moveit_ompl_integration.py

Exit codes:
    0 = PASS (OMPL planning succeeded)
    1 = FAIL (planning failed or move_group didn't start)
    2 = ERROR (unexpected exception)

Background: This test was created to verify moveit2 2.5.9 builds correctly link
against libompl.so.18 (ompl 1.7.0). A colleague's device had moveit 2.5.4 linking
libompl.so.17 which caused "cannot open shared object" and "Illegal instruction"
errors when mixed with ompl SOVERSION=18.
"""
import os
import sys
import time
import signal
import threading
import subprocess
import json


def main():
    """Run the full OMPL integration test."""
    results = {
        "test_name": "MoveIt2 OMPL Integration Test",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": {},
        "overall": "UNKNOWN",
    }

    print("=" * 60)
    print("  MoveIt2 OMPL Planner Integration Test")
    print("  openEuler Embedded + moveit2")
    print("=" * 60)
    print()

    # ============================================================
    # Step 1: Start panda headless demo via Python Launch API
    # ============================================================
    print("[STEP 1] Starting panda headless demo via Python Launch API...")
    print()

    launch_script = '''
import os, sys, signal, threading
from launch import LaunchService, LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
from moveit_configs_utils import MoveItConfigsBuilder

moveit_config = (
    MoveItConfigsBuilder("moveit_resources_panda")
    .robot_description(file_path="config/panda.urdf.xacro")
    .robot_description_semantic(file_path="config/panda.srdf")
    .trajectory_execution(file_path="config/gripper_moveit_controllers.yaml")
    .planning_pipelines(pipelines=["ompl", "chomp"])
    .to_moveit_configs()
)

ros2_controllers_path = os.path.join(
    get_package_share_directory("moveit_resources_panda_moveit_config"),
    "config", "ros2_controllers.yaml",
)

ld = LaunchDescription([
    Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="static_transform_publisher",
        output="log",
        arguments=["0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "world", "panda_link0"],
    ),
    Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="robot_state_publisher",
        output="both",
        parameters=[moveit_config.robot_description],
    ),
    Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=[moveit_config.to_dict()],
        arguments=["--ros-args", "--log-level", "info"],
    ),
    Node(
        package="controller_manager",
        executable="ros2_control_node",
        parameters=[moveit_config.robot_description, ros2_controllers_path],
        output="screen",
    ),
    Node(
        package="controller_manager",
        executable="spawner",
        arguments=["joint_state_broadcaster", "--controller-manager", "/controller_manager"],
    ),
    Node(
        package="controller_manager",
        executable="spawner",
        arguments=["panda_arm_controller", "-c", "/controller_manager"],
    ),
    Node(
        package="controller_manager",
        executable="spawner",
        arguments=["panda_hand_controller", "-c", "/controller_manager"],
    ),
])

ls = LaunchService()
ls.include_launch_description(ld)

def shutdown_handler(signum, frame):
    ls.shutdown()

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

ls.run()
'''

    # Write launch script to temp file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    launch_script_path = os.path.join(script_dir, "_tmp_launch_nodes.py")
    with open(launch_script_path, "w") as f:
        f.write(launch_script)

    # Start launch in background process
    env = os.environ.copy()
    launch_proc = subprocess.Popen(
        [sys.executable, launch_script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )

    # Capture output in a thread
    output_lines = []
    ompl_ready = threading.Event()
    ompl_failed = threading.Event()

    def read_output():
        for line in iter(launch_proc.stdout.readline, b""):
            decoded = line.decode("utf-8", errors="replace").rstrip()
            output_lines.append(decoded)
            print(f"  [LAUNCH] {decoded}")
            if "You can start planning now" in decoded:
                ompl_ready.set()
            if "Failed to load any planning pipelines" in decoded:
                ompl_failed.set()
            if "libompl.so" in decoded and "cannot open" in decoded:
                ompl_failed.set()

    reader_thread = threading.Thread(target=read_output, daemon=True)
    reader_thread.start()

    # Wait for ready or failure (timeout: 180s for slow embedded boards)
    STARTUP_TIMEOUT = 180
    print()
    print(f"[STEP 1] Waiting for move_group to become ready (timeout: {STARTUP_TIMEOUT}s)...")
    start_time = time.time()

    while not ompl_ready.is_set() and not ompl_failed.is_set():
        if time.time() - start_time > STARTUP_TIMEOUT:
            elapsed = time.time() - start_time
            print(f"  TIMEOUT: move_group did not become ready in {STARTUP_TIMEOUT}s")
            results["steps"]["step1_launch"] = {
                "status": "FAIL",
                "reason": f"Timeout after {elapsed:.1f}s",
                "last_lines": output_lines[-20:],
            }
            results["overall"] = "FAIL"
            os.killpg(os.getpgid(launch_proc.pid), signal.SIGTERM)
            launch_proc.wait(timeout=10)
            _save_results(results, script_dir)
            sys.exit(1)
        time.sleep(0.5)

    if ompl_failed.is_set():
        print("  OMPL pipeline failed to load!")
        error_lines = [l for l in output_lines if any(
            k in l.lower() for k in ["error", "fatal", "failed", "libompl", "dlopen"]
        )]
        results["steps"]["step1_launch"] = {
            "status": "FAIL",
            "reason": "OMPL pipeline load failed",
            "error_lines": error_lines,
        }
        results["overall"] = "FAIL"
        os.killpg(os.getpgid(launch_proc.pid), signal.SIGTERM)
        launch_proc.wait(timeout=10)
        _save_results(results, script_dir)
        sys.exit(1)

    elapsed = time.time() - start_time
    print(f"  move_group is READY! (took {elapsed:.1f}s)")
    results["steps"]["step1_launch"] = {
        "status": "PASS",
        "startup_time_seconds": round(elapsed, 1),
        "ompl_loaded": True,
    }
    print()

    # ============================================================
    # Step 2: Send OMPL planning request
    # ============================================================
    print("[STEP 2] Sending OMPL planning request (RRTConnect, panda_arm -> 'ready' pose)...")
    print()

    plan_script_path = os.path.join(script_dir, "_tmp_plan_request.py")
    plan_script = '''
#!/usr/bin/env python3
"""Send a plan-only request to move_group via MoveGroup action."""
import sys
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import (
    MotionPlanRequest,
    PlanningOptions,
    Constraints,
    JointConstraint,
)

class OmplPlanTester(Node):
    def __init__(self):
        super().__init__("ompl_plan_tester")
        self._action_client = ActionClient(self, MoveGroup, "move_action")
        self.get_logger().info("Waiting for move_group action server...")

    def send_plan_request(self):
        if not self._action_client.wait_for_server(timeout_sec=60.0):
            self.get_logger().error("move_group action server not available after 60s!")
            return False
        self.get_logger().info("move_group action server is available. Sending plan request...")
        goal_msg = MoveGroup.Goal()
        req = MotionPlanRequest()
        req.group_name = "panda_arm"
        req.num_planning_attempts = 5
        req.allowed_planning_time = 10.0
        req.pipeline_id = "ompl"
        req.planner_id = "RRTConnectkConfigDefault"
        ready_joints = {
            "panda_joint1": 0.0, "panda_joint2": -0.785, "panda_joint3": 0.0,
            "panda_joint4": -2.356, "panda_joint5": 0.0, "panda_joint6": 1.571,
            "panda_joint7": 0.785,
        }
        constraints = Constraints()
        for name, val in ready_joints.items():
            jc = JointConstraint()
            jc.joint_name = name
            jc.position = val
            jc.tolerance_above = 0.01
            jc.tolerance_below = 0.01
            jc.weight = 1.0
            constraints.joint_constraints.append(jc)
        req.goal_constraints.append(constraints)
        goal_msg.request = req
        goal_msg.planning_options = PlanningOptions()
        goal_msg.planning_options.plan_only = True
        goal_msg.planning_options.look_around = False
        goal_msg.planning_options.replan = False
        self.get_logger().info("Sending OMPL plan request: group=panda_arm, planner=RRTConnect, pipeline=ompl")
        future = self._action_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, future, timeout_sec=30.0)
        goal_handle = future.result()
        if not goal_handle or not goal_handle.accepted:
            self.get_logger().error("Goal was rejected!")
            return False
        self.get_logger().info("Goal accepted! Waiting for result...")
        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future, timeout_sec=60.0)
        result = result_future.result()
        if result is None:
            self.get_logger().error("No result received (timeout?)")
            return False
        error_code = result.result.error_code.val
        if error_code == 1:
            traj = result.result.planned_trajectory
            n_points = len(traj.joint_trajectory.points) if traj.joint_trajectory.points else 0
            self.get_logger().info(f"OMPL Planning SUCCEEDED! Trajectory has {n_points} waypoints.")
            if n_points > 0:
                last = traj.joint_trajectory.points[-1]
                self.get_logger().info(f"   Final positions: {[round(p, 4) for p in last.positions]}")
            return True
        else:
            self.get_logger().error(f"OMPL Planning FAILED with error code: {error_code}")
            return False

def main():
    rclpy.init()
    tester = OmplPlanTester()
    try:
        success = tester.send_plan_request()
        if success:
            tester.get_logger().info("=== TEST PASSED: OMPL planner works correctly ===")
            sys.exit(0)
        else:
            tester.get_logger().error("=== TEST FAILED: OMPL planner did not succeed ===")
            sys.exit(1)
    except Exception as e:
        tester.get_logger().error(f"Exception: {e}")
        sys.exit(2)
    finally:
        tester.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
'''
    with open(plan_script_path, "w") as f:
        f.write(plan_script)

    plan_proc = subprocess.Popen(
        [sys.executable, plan_script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )

    plan_output = []
    for line in iter(plan_proc.stdout.readline, b""):
        decoded = line.decode("utf-8", errors="replace").rstrip()
        plan_output.append(decoded)
        print(f"  [PLAN] {decoded}")

    plan_proc.wait(timeout=120)

    print()
    print("=" * 60)

    if plan_proc.returncode == 0:
        print("  OMPL INTEGRATION TEST PASSED!")
        results["steps"]["step2_planning"] = {
            "status": "PASS",
            "planner": "RRTConnect",
            "pipeline": "ompl",
            "group": "panda_arm",
        }
        results["overall"] = "PASS"
    else:
        print(f"  OMPL INTEGRATION TEST FAILED (exit code: {plan_proc.returncode})")
        results["steps"]["step2_planning"] = {
            "status": "FAIL",
            "exit_code": plan_proc.returncode,
            "output": plan_output[-10:],
        }
        results["overall"] = "FAIL"

    print("=" * 60)

    # Cleanup: shutdown launch
    print()
    print("[CLEANUP] Shutting down launch nodes...")
    try:
        os.killpg(os.getpgid(launch_proc.pid), signal.SIGTERM)
        launch_proc.wait(timeout=15)
        print("  Nodes shut down cleanly.")
    except Exception as e:
        print(f"  Cleanup warning: {e}")
        try:
            os.killpg(os.getpgid(launch_proc.pid), signal.SIGKILL)
        except Exception:
            pass

    # Cleanup temp files
    for tmp in [launch_script_path, plan_script_path]:
        try:
            os.remove(tmp)
        except Exception:
            pass

    _save_results(results, script_dir)
    sys.exit(0 if results["overall"] == "PASS" else 1)


def _save_results(results, script_dir):
    """Save test results to JSON."""
    result_path = os.path.join(script_dir, "ompl_test_result.json")
    try:
        with open(result_path, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"  Results saved to {result_path}")
    except Exception as e:
        print(f"  Warning: could not save results: {e}")


if __name__ == "__main__":
    main()
