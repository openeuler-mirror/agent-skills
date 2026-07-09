#!/bin/bash
# ==============================================================================
# Script: setup_l1_test_env.sh
# Description: Installs all necessary dependencies on the target openEuler
#              development board to enable Level 1 (L1) on-board incremental
#              colcon testing.
# ==============================================================================

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <board_ip> <board_user> [board_password]"
    exit 1
fi

BOARD_IP=$1
BOARD_USER=$2
BOARD_PASSWORD=${3:-""}

echo "============================================"
echo "Installing L1 Test Dependencies on $BOARD_IP"
echo "============================================"

# Helper function for remote execution
ssh_exec() {
    local cmd=$1
    if [ -n "$BOARD_PASSWORD" ]; then
        sshpass -p "$BOARD_PASSWORD" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${BOARD_USER}@${BOARD_IP}" "$cmd"
    else
        ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${BOARD_USER}@${BOARD_IP}" "$cmd"
    fi
}

echo "[1/4] Updating package cache..."
ssh_exec "dnf makecache"

echo "[2/4] Installing system dependencies (C++/Python build tools, ROS test packages)..."
PKG_LIST="python3-devel gcc gcc-c++ make cmake gtest-devel gmock-devel python3-pip ros-humble-ament-cmake ros-humble-ament-lint-auto ros-humble-test-msgs ros-humble-ament-cmake-gtest ros-humble-ament-cmake-gmock ros-humble-ament-cmake-pytest ros-humble-ament-cmake-ros ros-humble-ament-cmake-mypy ros-humble-ament-cmake-clang-format ros-humble-ament-cmake-cppcheck ros-humble-ament-cmake-cpplint ros-humble-ament-cmake-uncrustify ros-humble-ament-cmake-pep257 ros-humble-ament-cmake-flake8 ros-humble-ament-cmake-xmllint ros-humble-ament-cmake-copyright ros-humble-ament-cmake-lint-cmake ros-humble-ament-flake8 ros-humble-ament-pep257 ros-humble-ament-copyright ros-humble-ament-xmllint ros-humble-ament-lint-common ros-humble-launch-testing ros-humble-launch-testing-ros"
ssh_exec "dnf install -y $PKG_LIST"

echo "[3/4] Installing Python test dependencies via pip..."
PIP_CMD="pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple colcon-common-extensions pytest flake8 pytest-repeat pytest-rerunfailures"
ssh_exec "$PIP_CMD"

echo "[4/4] Verifying colcon installation..."
ssh_exec "which colcon || echo 'colcon not found in PATH, might be installed in /usr/local/bin'"

echo "============================================"
echo "✅ L1 dependencies setup completed."
echo "============================================"
