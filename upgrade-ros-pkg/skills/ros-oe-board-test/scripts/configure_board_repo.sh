#!/bin/bash
# EUR Repository Configuration Script for Development Board
#
# Usage:
#   ./configure_board_repo.sh <board_ip> <board_username> <board_password> <eur_project> [oe_version] [oe_arch]
#
# Example:
#   ./configure_board_repo.sh 192.168.1.100 root password "<gitcode_username>/<eur_project_name>" 24.03 aarch64
#
# This script:
#   1. Uses provided version/arch or detects them from board
#   2. Backs up existing ROS repository configuration
#   3. Creates new EUR repository configuration
#   4. Verifies the configuration

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib_ssh.sh"

# Check arguments
if [ $# -lt 4 ]; then
    echo "Usage: $0 <board_ip> <board_username> <board_password> <eur_project> [oe_version] [oe_arch]"
    echo "Example: $0 192.168.1.100 root password '<gitcode_username>/<eur_project_name>' 24.03 aarch64"
    exit 1
fi

BOARD_IP="$1"
BOARD_USERNAME="$2"
BOARD_PASSWORD="$3"
EUR_PROJECT="$4"
OE_VERSION_PROVIDED="$5"
OE_ARCH_PROVIDED="$6"

echo "============================================"
echo "EUR Repository Configuration"
echo "============================================"
echo "[INFO] Board: ${BOARD_USERNAME}@${BOARD_IP}"
echo "[INFO] EUR Project: ${EUR_PROJECT}"
echo ""

# Step 1: Verify SSH connection
echo "[Step 1] Verifying SSH connection..."
if ! verify_ssh_connection "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD"; then
    echo "[ERROR] Cannot connect to board"
    exit 1
fi

# Step 2: Get openEuler version and architecture
echo ""
echo "[Step 2] Getting openEuler version and architecture..."

if [ -n "$OE_VERSION_PROVIDED" ]; then
    OE_VERSION="$OE_VERSION_PROVIDED"
    echo "[INFO] Using provided version: $OE_VERSION"
else
    echo "[INFO] Attempting to detect version from board..."
    # Method 1: Extract version from openEuler.repo baseurl
    OE_VERSION=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
        "grep -oP 'openEuler-[0-9]+\\.[0-9]+' /etc/yum.repos.d/openEuler.repo 2>/dev/null | head -1 | sed 's/openEuler-//'")

    # Method 2: Fallback to /etc/os-release VERSION_ID
    if [ -z "$OE_VERSION" ] || [ "$OE_VERSION" == "latest" ]; then
        OE_VERSION=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
            "grep '^VERSION_ID=' /etc/os-release 2>/dev/null | cut -d'=' -f2")
    fi

    if [ -z "$OE_VERSION" ] || [ "$OE_VERSION" == "latest" ]; then
        echo "[ERROR] Failed to detect openEuler version"
        echo "[ACTION] Please specify version manually as 5th argument"
        exit 1
    fi
    echo "[INFO] Detected version: $OE_VERSION"
fi

if [ -n "$OE_ARCH_PROVIDED" ]; then
    OE_ARCH="$OE_ARCH_PROVIDED"
    echo "[INFO] Using provided architecture: $OE_ARCH"
else
    echo "[INFO] Detecting architecture from board..."
    OE_ARCH=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" "uname -m")
    echo "[INFO] Detected architecture: $OE_ARCH"
fi

# Step 3: Parse EUR project parameters
EUR_USERNAME=$(echo "$EUR_PROJECT" | cut -d'/' -f1)
EUR_PROJECT_NAME=$(echo "$EUR_PROJECT" | cut -d'/' -f2)

# EUR URL format: openeuler-24.03_LTS-aarch64 (version keeps dots, underscore before LTS)
# Step 4: Build repository URLs
EUR_BASEURL="https://eur.openeuler.openatom.cn/results/${EUR_USERNAME}/${EUR_PROJECT_NAME}/openeuler-${OE_VERSION}_LTS-\$basearch/"
OFFICIAL_BASEURL="https://eulermaker.compass-ci.openeuler.openatom.cn/api/ems1/repositories/ROS-SIG-Multi-Version_ros-humble_openEuler-${OE_VERSION}-LTS-TEST4/openEuler%3A${OE_VERSION}-LTS/\$basearch/"

echo ""
echo "[INFO] EUR repository URL: $EUR_BASEURL"
echo "[INFO] Official repository URL: $OFFICIAL_BASEURL"

# Step 5: Backup existing configuration
echo ""
echo "[Step 3] Backing up existing ROS repository configuration..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/root/openEulerROS.repo.${TIMESTAMP}"

EXISTING_REPO=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
    "cat /etc/yum.repos.d/openEulerROS.repo 2>/dev/null" || echo "")

if [ -n "$EXISTING_REPO" ]; then
    ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
        "cp /etc/yum.repos.d/openEulerROS.repo $BACKUP_FILE"
    echo "[OK] Existing configuration backed up to: $BACKUP_FILE"
else
    echo "[INFO] No existing openEulerROS.repo found"
fi

# Step 6: Create new repository configuration
echo ""
echo "[Step 4] Creating new EUR repository configuration..."

cat > /tmp/openEulerROS.repo.$$ << EOF
[openEuler-Embedded-ROS-humble]
name=openEuler-Embedded-ROS-humble
baseurl=${EUR_BASEURL}
skip_if_unavailable=True
enabled=1
gpgcheck=0
priority=1

[openEulerROS-humble]
name=openEulerROS-humble
baseurl=${OFFICIAL_BASEURL}
enabled=1
gpgcheck=0
priority=2
EOF

# Step 7: Transfer configuration to board
ssh_copy "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
    /tmp/openEulerROS.repo.$$ /etc/yum.repos.d/openEulerROS.repo

rm -f /tmp/openEulerROS.repo.$$

echo "[OK] Repository configuration file created"

# Step 8: Clean cache and rebuild (only ROS-related repos to save time)
echo ""
echo "[Step 5] Cleaning ROS-related repository cache..."

# Only clean metadata/packages for ROS-related repos instead of all repos
# This avoids the expensive full makecache for system repos (everything, update, EPOL, etc.)
ROS_REPO_IDS=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
    "dnf repolist --all 2>/dev/null | grep -iE 'ROS|eur|copr|Embedded' | awk '{print \$1}'" || echo "")

if [ -n "$ROS_REPO_IDS" ]; then
    CLEAN_CMD=""
    for repo_id in $ROS_REPO_IDS; do
        CLEAN_CMD="${CLEAN_CMD}dnf clean metadata --repo=${repo_id} 2>/dev/null; "
    done
    CLEAN_CMD="${CLEAN_CMD}dnf makecache --repo=openEuler-Embedded-ROS-humble 2>/dev/null; dnf makecache --repo=openEulerROS-humble 2>/dev/null"
    ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" "$CLEAN_CMD"
    echo "[OK] ROS-related repository cache cleaned and rebuilt"
else
    # Fallback: if we can't identify repo IDs, clean the two known ones
    ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
        "dnf clean metadata --repo=openEuler-Embedded-ROS-humble 2>/dev/null; dnf clean metadata --repo=openEulerROS-humble 2>/dev/null; dnf makecache --repo=openEuler-Embedded-ROS-humble 2>/dev/null; dnf makecache --repo=openEulerROS-humble 2>/dev/null"
    echo "[OK] Repository cache cleaned (fallback mode)"
fi

# Step 9: Verify configuration
echo ""
echo "[Step 6] Verifying repository configuration..."
REPOLIST=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" "dnf repolist")

if echo "$REPOLIST" | grep -q "openEuler-Embedded-ROS-humble"; then
    echo "[OK] EUR repository (openEuler-Embedded-ROS-humble) is enabled"
else
    echo "[ERROR] EUR repository not found in repolist"
    exit 1
fi

if echo "$REPOLIST" | grep -q "openEulerROS-humble"; then
    echo "[OK] Official repository (openEulerROS-humble) is enabled"
fi

echo ""
echo "============================================"
echo "[OK] EUR Repository Configuration Complete"
echo "============================================"
echo ""
echo "Repository priorities:"
echo "  - EUR source (priority 1): For upgraded packages"
echo "  - Official source (priority 2): Fallback for unchanged packages"
echo ""
echo "To restore original configuration:"
echo "  ssh ${BOARD_USERNAME}@${BOARD_IP} 'cp ${BACKUP_FILE} /etc/yum.repos.d/openEulerROS.repo'"

exit 0
