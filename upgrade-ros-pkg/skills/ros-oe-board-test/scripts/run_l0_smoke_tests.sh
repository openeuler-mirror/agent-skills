#!/bin/bash
# ROS2 Basic Functionality Test Runner
#
# Usage:
#   ./run_l0_smoke_tests.sh <board_ip> <board_username> <board_password> \
#                        [test_category] [output_dir] [max_retries]
#
# Examples:
#   # Run all tests
#   ./run_l0_smoke_tests.sh 192.168.137.2 root password
#
#   # Run only topic tests
#   ./run_l0_smoke_tests.sh 192.168.137.2 root password topic
#
#   # Run multiple categories with custom output dir
#   ./run_l0_smoke_tests.sh 192.168.137.2 root password "environment,topic" ./results
#
#   # With auto-retry (max 3 attempts)
#   ./run_l0_smoke_tests.sh 192.168.137.2 root password all ./results 3
#
# Categories:
#   - environment: 环境验证
#   - package: 包管理
#   - node: 节点管理
#   - topic: 话题通信
#   - service: 服务通信
#   - parameter: 参数管理
#   - all: 运行所有测试 (默认)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_CASES_DIR="${SCRIPT_DIR}/../test_cases"
SKILL_DIR="${SCRIPT_DIR}/.."

# Default values
BOARD_IP=""
BOARD_USERNAME=""
BOARD_PASSWORD=""
TEST_CATEGORY="all"
OUTPUT_DIR="./test_results"
MAX_RETRIES=1

# Parse arguments
if [ $# -lt 3 ]; then
    echo "Usage: $0 <board_ip> <board_username> <board_password> [test_category] [output_dir] [max_retries]"
    echo ""
    echo "Examples:"
    echo "  $0 192.168.137.2 root password"
    echo "  $0 192.168.137.2 root password topic"
    echo "  $0 192.168.137.2 root password all ./results 3"
    exit 1
fi

BOARD_IP="$1"
BOARD_USERNAME="$2"
BOARD_PASSWORD="$3"
TEST_CATEGORY="${4:-all}"
OUTPUT_DIR="${5:-./test_results}"
MAX_RETRIES="${6:-1}"

echo "============================================"
echo "ROS2 Basic Functionality Test Runner"
echo "============================================"
echo "[INFO] Board: ${BOARD_USERNAME}@${BOARD_IP}"
echo "[INFO] Test Category: ${TEST_CATEGORY}"
echo "[INFO] Output Directory: ${OUTPUT_DIR}"
echo "[INFO] Max Retries: ${MAX_RETRIES}"
echo ""

# Create output directory
mkdir -p "${OUTPUT_DIR}"

# Check if test cases directory exists
if [ ! -d "${TEST_CASES_DIR}" ]; then
    echo "[ERROR] Test cases directory not found: ${TEST_CASES_DIR}"
    exit 1
fi

# Run Python test runner
python3 "${SCRIPT_DIR}/run_l0_custom_tests.py" \
    --board-ip "${BOARD_IP}" \
    --board-username "${BOARD_USERNAME}" \
    --board-password "${BOARD_PASSWORD}" \
    --test-cases-dir "${TEST_CASES_DIR}" \
    --category "${TEST_CATEGORY}" \
    --output-dir "${OUTPUT_DIR}" \
    --max-retries "${MAX_RETRIES}" \
    --skill-dir "${SKILL_DIR}"

exit_code=$?

echo ""
echo "============================================"
if [ $exit_code -eq 0 ]; then
    echo "[OK] All tests passed!"
else
    echo "[FAILED] Some tests failed. Check ${OUTPUT_DIR}/test_report.json for details."
fi
echo "============================================"

exit $exit_code
