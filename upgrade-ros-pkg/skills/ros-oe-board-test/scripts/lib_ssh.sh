#!/bin/bash
# SSH Connection Utilities for ROS Package On-Board Test
#
# Usage:
#   source lib_ssh.sh
#   verify_ssh_connection <board_ip> <board_username> [board_password]
#
# Return:
#   0 - Connection successful
#   1 - Connection failed

# Verify SSH connection to development board
verify_ssh_connection() {
    local board_ip="$1"
    local board_username="$2"
    local board_password="$3"
    local timeout="${4:-5}"

    echo "[INFO] Testing SSH connection to ${board_username}@${board_ip}..."

    if [ -n "$board_password" ]; then
        # Use sshpass for password authentication
        if ! command -v sshpass &> /dev/null; then
            echo "[ERROR] sshpass not installed. Install with: dnf install sshpass"
            return 1
        fi

        sshpass -p "$board_password" ssh -o ConnectTimeout="$timeout" \
            -o StrictHostKeyChecking=no \
            "${board_username}@${board_ip}" "echo 'Connection successful'" 2>/dev/null
    else
        # Use SSH key authentication
        ssh -o ConnectTimeout="$timeout" \
            -o StrictHostKeyChecking=no \
            "${board_username}@${board_ip}" "echo 'Connection successful'" 2>/dev/null
    fi

    if [ $? -eq 0 ]; then
        echo "[✓] SSH connection verified"
        return 0
    else
        echo "[✗] SSH connection failed"
        return 1
    fi
}

# Execute command on remote board
ssh_exec() {
    local board_ip="$1"
    local board_username="$2"
    local board_password="$3"
    shift 3
    local cmd="$@"

    if [ -n "$board_password" ]; then
        sshpass -p "$board_password" ssh -o StrictHostKeyChecking=no \
            "${board_username}@${board_ip}" "$cmd"
    else
        ssh -o StrictHostKeyChecking=no \
            "${board_username}@${board_ip}" "$cmd"
    fi
}

# Copy file to remote board
ssh_copy() {
    local board_ip="$1"
    local board_username="$2"
    local board_password="$3"
    local local_file="$4"
    local remote_path="$5"

    if [ -n "$board_password" ]; then
        sshpass -p "$board_password" scp -o StrictHostKeyChecking=no \
            "$local_file" "${board_username}@${board_ip}:${remote_path}"
    else
        scp -o StrictHostKeyChecking=no \
            "$local_file" "${board_username}@${board_ip}:${remote_path}"
    fi
}

# Copy directory recursively to remote board
ssh_copy_dir() {
    local board_ip="$1"
    local board_username="$2"
    local board_password="$3"
    local local_dir="$4"
    local remote_path="$5"

    if [ -n "$board_password" ]; then
        sshpass -p "$board_password" scp -r -o StrictHostKeyChecking=no \
            "$local_dir" "${board_username}@${board_ip}:${remote_path}"
    else
        scp -r -o StrictHostKeyChecking=no \
            "$local_dir" "${board_username}@${board_ip}:${remote_path}"
    fi
}
