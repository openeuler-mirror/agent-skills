#!/bin/bash
# ROS Package Installation Script for Development Board
#
# Usage:
#   ./install_target_packages.sh <board_ip> <board_username> <board_password> <package_list_file> [ros_distro] [expected_eur_repo]
#
# Example:
#   ./install_target_packages.sh 192.168.1.100 root password ./dependency_list.txt humble
#   ./install_target_packages.sh 192.168.1.100 root password ./dependency_list.txt humble openEuler-Embedded-ROS-humble
#
# This script:
#   1. Reads package list from file
#   2. Checks currently installed ROS packages
#   3. Removes existing packages (to force reinstall from EUR)
#   4. Installs packages from EUR repository
#   5. Verifies installation
#   6. Multi-dimensional RPM provenance verification (From repo + Vendor)

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib_ssh.sh"

# Check arguments
if [ $# -lt 4 ]; then
    echo "Usage: $0 <board_ip> <board_username> <board_password> <package_list_file> [ros_distro] [expected_eur_repo]"
    echo "Example: $0 192.168.1.100 root password ./dependency_list.txt humble"
    echo "Example: $0 192.168.1.100 root password ./dependency_list.txt humble openEuler-Embedded-ROS-humble"
    exit 1
fi

BOARD_IP="$1"
BOARD_USERNAME="$2"
BOARD_PASSWORD="$3"
PACKAGE_LIST_FILE="$4"
ROS_DISTRO="${5:-humble}"
# Expected EUR repo section name (matches [section] in openEulerROS.repo)
# Default: openEuler-Embedded-ROS-humble (the EUR repo configured by configure_board_repo.sh)
EXPECTED_EUR_REPO="${6:-openEuler-Embedded-ROS-humble}"

echo "============================================"
echo "ROS Package Installation"
echo "============================================"
echo "[INFO] Board: ${BOARD_USERNAME}@${BOARD_IP}"
echo "[INFO] ROS Distribution: ${ROS_DISTRO}"
echo "[INFO] Package list file: ${PACKAGE_LIST_FILE}"
echo "[INFO] Expected EUR repo: ${EXPECTED_EUR_REPO}"
echo ""

# Verify package list file exists
if [ ! -f "$PACKAGE_LIST_FILE" ]; then
    echo "[ERROR] Package list file not found: $PACKAGE_LIST_FILE"
    exit 1
fi

# Read package list (filter comments and empty lines)
PACKAGES=()
while IFS= read -r line || [ -n "$line" ]; do
    # Skip comments and empty lines
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    PACKAGES+=("$line")
done < "$PACKAGE_LIST_FILE"

if [ ${#PACKAGES[@]} -eq 0 ]; then
    echo "[ERROR] No packages found in package list file"
    exit 1
fi

echo "[INFO] Packages to install (${#PACKAGES[@]}):"
for pkg in "${PACKAGES[@]}"; do
    # Convert underscores to hyphens for RPM naming convention
    # e.g., iceoryx_hoofs -> iceoryx-hoofs
    RPM_PKG_NAME="${pkg//_/-}"
    ROS_PKG="ros-${ROS_DISTRO}-${RPM_PKG_NAME}"
    echo "  - ${ROS_PKG}"
done
echo ""

# Step 1: Verify SSH connection
echo ""
echo "[Step 1] Verifying SSH connection..."
if ! verify_ssh_connection "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD"; then
    echo "[ERROR] Cannot connect to board"
    exit 1
fi

echo ""
echo "[OK] SSH connection verified"

# Step 2: Check currently installed ROS packages
echo ""
echo "[Step 2] Checking currently installed ROS packages..."
INSTALLED_BEFORE=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
    "rpm -qa | grep 'ros-${ROS_DISTRO}-' | sort" || echo "")

if [ -n "$INSTALLED_BEFORE" ]; then
    echo "[INFO] Currently installed ROS packages:"
    echo "$INSTALLED_BEFORE" | head -20
    TOTAL_INSTALLED=$(echo "$INSTALLED_BEFORE" | wc -l)
    if [ "$TOTAL_INSTALLED" -gt 20 ]; then
        echo "  ... and $((TOTAL_INSTALLED - 20)) more packages"
    fi
else
    echo "[INFO] No ROS packages currently installed"
fi

echo ""
echo "[Step 3] Checking existing packages and preparing to remove..."
PACKAGES_TO_REMOVE=()
for pkg in "${PACKAGES[@]}"; do
    RPM_PKG_NAME="${pkg//_/-}"
    ROS_PKG="ros-${ROS_DISTRO}-${RPM_PKG_NAME}"

    # Check if package is installed
    IS_INSTALLED=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
        "rpm -q ${ROS_PKG} 2>/dev/null" || echo "")

    if [ -n "$IS_INSTALLED" ] && ! echo "$IS_INSTALLED" | grep -q "not installed"; then
        PACKAGES_TO_REMOVE+=("$ROS_PKG")
        echo "[INFO] Package installed, will remove: ${ROS_PKG}"
    fi
done

if [ ${#PACKAGES_TO_REMOVE[@]} -gt 0 ]; then
    echo ""
    echo "[INFO] Found ${#PACKAGES_TO_REMOVE[@]} packages to remove:"
    for p in "${PACKAGES_TO_REMOVE[@]}"; do
        echo "  - ${p}"
    done

    echo ""
    echo "[INFO] Removing packages..."
    for ROS_PKG in "${PACKAGES_TO_REMOVE[@]}"; do
        ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
            "dnf remove -y ${ROS_PKG} 2>&1" || true
    done
    echo "[OK] Packages removed"
else
    echo "[INFO] No existing packages to remove"
fi
echo ""
echo "[Step 4] Installing packages from EUR repository..."
echo ""

INSTALL_LOG=""
for pkg in "${PACKAGES[@]}"; do
    # Convert underscores to hyphens for RPM naming convention
    # e.g., iceoryx_hoofs -> iceoryx-hoofs
    RPM_PKG_NAME="${pkg//_/-}"
    ROS_PKG="ros-${ROS_DISTRO}-${RPM_PKG_NAME}"
    echo "[INFO] Installing: ${ROS_PKG}"

    # Install package with dnf
    INSTALL_OUTPUT=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
        "dnf install -y ${ROS_PKG} 2>&1" || echo "INSTALL_FAILED")

    if echo "$INSTALL_OUTPUT" | grep -q "INSTALL_FAILED\|Error\|No match for argument"; then
        echo "[ERROR] Failed to install: ${ROS_PKG}"
        echo "$INSTALL_OUTPUT" | grep -E "Error|No match" | head -5
        INSTALL_LOG="${INSTALL_LOG}FAILED: ${ROS_PKG}\n"
    else
        # Check if package was actually installed
        VERIFY=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
            "rpm -q ${ROS_PKG} 2>/dev/null" || echo "")

        if [ -n "$VERIFY" ] && ! echo "$VERIFY" | grep -q "not installed"; then
            echo "[OK] Installed: ${ROS_PKG} -> ${VERIFY}"
            INSTALL_LOG="${INSTALL_LOG}OK: ${ROS_PKG} -> ${VERIFY}\n"
        else
            echo "[WARNING] Installation status unclear for: ${ROS_PKG}"
            INSTALL_LOG="${INSTALL_LOG}UNCLEAR: ${ROS_PKG}\n"
        fi
    fi
done

echo ""
echo "[Step 5] Verifying installation..."
echo "[INFO] Installation results:"
# Use echo -e and piped to grep -c for accurate counting
SUCCESS_COUNT=$(echo -e "$INSTALL_LOG" | grep -c "^OK:" || true)
FAILED_COUNT=$(echo -e "$INSTALL_LOG" | grep -c "^FAILED:" || true)
echo "  Successfully installed: ${SUCCESS_COUNT} packages"
echo "  Failed: ${FAILED_COUNT} packages"

echo ""
echo "[Step 6] Multi-dimensional RPM provenance verification..."
echo "[INFO] Expected EUR repo: ${EXPECTED_EUR_REPO}"
echo ""

EUR_COUNT=0
OTHER_REPO_COUNT=0
VERIFY_FAILED_COUNT=0
PROVENANCE_LOG=""

for pkg in "${PACKAGES[@]}"; do
    RPM_PKG_NAME="${pkg//_/-}"
    ROS_PKG="ros-${ROS_DISTRO}-${RPM_PKG_NAME}"

    # --- Dimension 1: "From repo" via dnf repoquery --installed --info ---
    # This is the most reliable method to determine which repo a package was installed from.
    FROM_REPO=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
        "dnf repoquery --installed --info ${ROS_PKG} 2>/dev/null | grep -i '^From repo' | awk -F': ' '{print \$2}' | tr -d '[:space:]'" || echo "")

    # --- Dimension 2: "Vendor" via rpm -qi ---
    # Secondary check: confirms the builder identity (e.g., "openEuler Copr - user openEuler_Embedded"
    # vs "openEuler Copr - user <personal_username>"). Useful for distinguishing personal vs official EUR builds.
    VENDOR=$(ssh_exec "$BOARD_IP" "$BOARD_USERNAME" "$BOARD_PASSWORD" \
        "rpm -qi ${ROS_PKG} 2>/dev/null | grep -i '^Vendor' | sed 's/^Vendor[[:space:]]*:[[:space:]]*//' | sed 's/[[:space:]]*$//' " || echo "")

    # --- Evaluate provenance ---
    if [ -z "$FROM_REPO" ]; then
        echo "  ❌ ${ROS_PKG}: verification failed (package not installed or repoquery returned empty)"
        VERIFY_FAILED_COUNT=$((VERIFY_FAILED_COUNT + 1))
        PROVENANCE_LOG="${PROVENANCE_LOG}VERIFY_FAILED: ${ROS_PKG} | From repo: (empty) | Vendor: ${VENDOR:-unknown}\n"
    elif [ "$FROM_REPO" = "$EXPECTED_EUR_REPO" ]; then
        echo "  ✅ ${ROS_PKG}: From repo=${FROM_REPO} | Vendor=${VENDOR}"
        EUR_COUNT=$((EUR_COUNT + 1))
        PROVENANCE_LOG="${PROVENANCE_LOG}EUR_OK: ${ROS_PKG} | From repo: ${FROM_REPO} | Vendor: ${VENDOR}\n"
    else
        echo "  ⚠️  ${ROS_PKG}: From repo=${FROM_REPO} (expected: ${EXPECTED_EUR_REPO}) | Vendor=${VENDOR}"
        OTHER_REPO_COUNT=$((OTHER_REPO_COUNT + 1))
        PROVENANCE_LOG="${PROVENANCE_LOG}OTHER_REPO: ${ROS_PKG} | From repo: ${FROM_REPO} | Vendor: ${VENDOR}\n"
    fi
done

echo ""
echo "============================================"
echo "Installation & Provenance Summary"
echo "============================================"
echo "Installation:"
echo "  Successfully installed: ${SUCCESS_COUNT} packages"
echo "  Failed to install: ${FAILED_COUNT} packages"
echo ""
echo "Provenance verification (expected repo: ${EXPECTED_EUR_REPO}):"
echo "  ✅ Verified from EUR repo: ${EUR_COUNT}"
echo "  ⚠️  From other repo: ${OTHER_REPO_COUNT}"
echo "  ❌ Verification failed: ${VERIFY_FAILED_COUNT}"
echo ""

# Detailed provenance log for non-EUR packages
if [ "$OTHER_REPO_COUNT" -gt 0 ]; then
    echo "[WARNING] The following packages were NOT installed from the expected EUR repo:"
    echo -e "$PROVENANCE_LOG" | grep "^OTHER_REPO:" | sed 's/OTHER_REPO: /  - /'
    echo ""
fi

if [ "$VERIFY_FAILED_COUNT" -gt 0 ]; then
    echo "[WARNING] Provenance verification failed for the following packages:"
    echo -e "$PROVENANCE_LOG" | grep "^VERIFY_FAILED:" | sed 's/VERIFY_FAILED: /  - /'
    echo ""
fi

# Exit code logic:
# - Installation failures are hard errors (exit 1)
# - Provenance mismatches are warnings (exit 0 but with warning messages)
if [ "$FAILED_COUNT" -gt 0 ]; then
    echo "[ERROR] Some packages failed to install"
    echo ""
    echo "Failed packages:"
    echo -e "$INSTALL_LOG" | grep "^FAILED:" | sed 's/FAILED: /  - /'
    exit 1
elif [ "$EUR_COUNT" -eq "${#PACKAGES[@]}" ]; then
    echo "[OK] All ${EUR_COUNT} packages installed and verified from EUR repo (${EXPECTED_EUR_REPO})"
    exit 0
else
    echo "[WARNING] All packages installed, but ${OTHER_REPO_COUNT} package(s) came from a different repo"
    echo "[INFO] This may be expected if some packages are base dependencies from the official repo"
    exit 0
fi
