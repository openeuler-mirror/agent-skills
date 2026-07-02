#!/usr/bin/env python3
import os
import subprocess
import xml.etree.ElementTree as ET

def run_cmd(cmd):
    print(f"[*] Running local: {cmd}")
    return subprocess.run(cmd, shell=True, text=True, capture_output=True)

def ssh_cmd(cmd, ip, user, password, timeout=None):
    if password:
        ssh = f"sshpass -p '{password}' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {user}@{ip} \"{cmd}\""
    else:
        ssh = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {user}@{ip} \"{cmd}\""
    
    print(f"[*] Running remote: {cmd}")
    return subprocess.run(ssh, shell=True, text=True, capture_output=True, timeout=timeout)

def scp_dir(src, dst, ip, user, password):
    if password:
        scp = f"sshpass -p '{password}' scp -o StrictHostKeyChecking=no -r {src} {user}@{ip}:{dst}"
    else:
        scp = f"scp -o StrictHostKeyChecking=no -r {src} {user}@{ip}:{dst}"
        
    print(f"[*] SCP: {src} -> {ip}:{dst}")
    return subprocess.run(scp, shell=True, text=True, capture_output=True)

def assess_package(pkg_dir, threshold=5):
    """
    智能评估该包是否适合在 L1 (开发板增量编译) 运行。
    """
    pkg_xml = os.path.join(pkg_dir, "package.xml")
    if not os.path.exists(pkg_xml):
        return False, "No package.xml found"

    try:
        tree = ET.parse(pkg_xml)
        root = tree.getroot()
        
        build_type = "ament_cmake"
        export = root.find("export")
        if export is not None:
            bt = export.find("build_type")
            if bt is not None and bt.text:
                build_type = bt.text.strip()

        if build_type == "ament_python":
            return True, "Python package (ament_python) - 编译与执行极快"

        test_dir = os.path.join(pkg_dir, "test")
        if not os.path.exists(test_dir):
            return True, "No test directory found - 无测试代码，极快"

        cpp_files = []
        for root_dir, dirs, files in os.walk(test_dir):
            for f in files:
                if f.endswith(".cpp") or f.endswith(".cc"):
                    cpp_files.append(f)

        if len(cpp_files) <= threshold:
            return True, f"Lightweight C++ package ({len(cpp_files)} 个测试文件) - 可在板上快速编译"
        else:
            return False, f"Heavy C++ package ({len(cpp_files)} 个测试文件) - 编译超限制极易 OOM，需分配至 L2"

    except Exception as e:
        return False, f"Error parsing package.xml: {e}"
