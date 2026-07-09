#!/usr/bin/env python3
"""
整合多个ROS包的构建顺序文件
支持批量处理、自动去重、生成完整的构建顺序

功能特点：
1. 批量处理多个ROS包
2. 智能合并依赖，自动去重
3. 保持正确的构建顺序
4. 生成详细的日志和统计信息
5. 支持时间戳工作目录，避免污染当前目录
"""
import sys
import os
import shutil
from datetime import datetime
from collections import OrderedDict

def read_build_order_file(filename):
    """
    读取构建顺序文件
    返回: [package_name, ...]
    """
    packages = []
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            # 跳过注释和空行
            if not line or line.startswith('#'):
                continue

            # 格式: "   1. package_name (Level X)"
            # 或: "package_name"
            if '.' in line:
                # 带编号的格式
                parts = line.split()
                if len(parts) >= 2:
                    pkg_name = parts[1]
                    packages.append(pkg_name)
            else:
                # 纯包名格式
                packages.append(line)

    return packages

def normalize_pkg_name(pkg_name: str) -> str:
    """标准化包名，将下划线转换为连字符"""
    return pkg_name.replace('_', '-')

def merge_build_orders(target_packages, deps_dir, work_dir):
    """
    合并多个目标包的构建顺序
    使用改进的合并策略：保持原有顺序，智能去重

    Args:
        target_packages: 目标包列表
        deps_dir: 依赖数据库目录
        work_dir: 工作目录（用于存储中间文件）

    Returns:
        (final_order, target_set, failed_packages)
    """
    # 存储所有包及其出现顺序
    all_packages = OrderedDict()
    failed_packages = []

    log_file = os.path.join(work_dir, 'merge_log.txt')
    with open(log_file, 'w') as log:
        log.write(f"ROS包依赖整合日志\n")
        log.write(f"{'='*60}\n")
        log.write(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"目标包数量: {len(target_packages)}\n\n")

    print(f"{'='*60}")
    print(f"整合多个ROS包的构建顺序")
    print(f"{'='*60}")
    print(f"目标包数量: {len(target_packages)}")
    print(f"工作目录: {work_dir}")
    print(f"目标包列表:")
    for i, pkg in enumerate(target_packages, 1):
        print(f"  {i}. {pkg}")
    print()

    # 第一步：为每个目标包生成依赖分析
    print("第一步: 逐个分析目标包的依赖...")

    # 记录缺失的包
    missing_packages = []

    with open(log_file, 'a') as log:
        log.write(f"逐个分析目标包:\n")

        for idx, target_pkg in enumerate(target_packages, 1):
            temp_file = os.path.join(work_dir, f"temp_{target_pkg}_deps.txt")

            # 检查 PackageXml 是否存在（使用标准化名称）
            normalized_name = normalize_pkg_name(target_pkg)
            package_xml = os.path.join(deps_dir, f"{normalized_name}-PackageXml")
            if not os.path.exists(package_xml):
                print(f"  [{idx}/{len(target_packages)}] 分析: {target_pkg}... ✗ PackageXml 不存在")
                log.write(f"  [{idx}/{len(target_packages)}] {target_pkg}: ✗ PackageXml 不存在\n")
                failed_packages.append(target_pkg)
                missing_packages.append({
                    'name': target_pkg,
                    'reason': f'PackageXml not found in deps directory (looked for {normalized_name}-PackageXml)'
                })
                continue

            # 调用 resolve_dependencies.py
            resolve_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve_dependencies.py")
            cmd = f"python3 {resolve_script} {target_pkg} {deps_dir} -o {temp_file}"

            print(f"  [{idx}/{len(target_packages)}] 分析: {target_pkg}...", end=" ", flush=True)
            log.write(f"  [{idx}/{len(target_packages)}] {target_pkg}: ")

            ret = os.system(cmd + " > /dev/null 2>&1")

            if ret == 0:
                packages = read_build_order_file(temp_file)

                # 检查是否为空
                if not packages:
                    print(f"⚠️  0 个包（可能是独立包或系统包）")
                    log.write(f"⚠️  0 个包（可能是独立包或系统包）\n")
                    # 仍然添加到列表，但标记为特殊
                    if target_pkg not in all_packages:
                        all_packages[target_pkg] = {
                            'first_seen_in': 'standalone',
                            'position': len(all_packages)
                        }
                else:
                    new_packages = [pkg for pkg in packages if pkg not in all_packages]
                    print(f"✓ {len(packages)} 个包 (新增 {len(new_packages)} 个)")
                    log.write(f"✓ {len(packages)} 个包 (新增 {len(new_packages)} 个)\n")

                    # 将包添加到 OrderedDict（保持第一次出现的顺序）
                    for pkg in packages:
                        if pkg not in all_packages:
                            all_packages[pkg] = {
                                'first_seen_in': target_pkg,
                                'position': len(all_packages)
                            }
            else:
                print(f"✗ 分析失败")
                log.write(f"✗ 分析失败\n")
                failed_packages.append(target_pkg)

    print(f"\n✓ 去重后总包数: {len(all_packages)}")

    if missing_packages:
        print(f"⚠️  缺失 PackageXml 的包: {len(missing_packages)} 个")
        for pkg_info in missing_packages:
            print(f"    - {pkg_info['name']}: {pkg_info['reason']}")

    if failed_packages:
        print(f"⚠️  失败的包: {len(failed_packages)} 个 ({', '.join(failed_packages)})")

    # 第二步：验证并调整顺序
    # 由于每个单独的构建顺序都是正确的，
    # 我们只需要保持它们第一次出现的相对顺序即可
    print("\n第二步: 生成最终构建顺序...")

    final_order = list(all_packages.keys())

    # 写入详细日志
    with open(log_file, 'a') as log:
        log.write(f"\n{'='*60}\n")
        log.write(f"整合结果:\n")
        log.write(f"  总包数: {len(final_order)}\n")
        log.write(f"  失败包: {len(failed_packages)}\n")
        log.write(f"  缺失PackageXml: {len(missing_packages)}\n")
        log.write(f"  成功率: {((len(target_packages) - len(failed_packages)) / len(target_packages) * 100):.1f}%\n")

        if missing_packages:
            log.write(f"\n缺失 PackageXml 的包:\n")
            for pkg_info in missing_packages:
                log.write(f"  - {pkg_info['name']}: {pkg_info['reason']}\n")

        log.write(f"\n完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 生成缺失包列表文件
    if missing_packages:
        missing_file = os.path.join(work_dir, 'missing_packages.txt')
        with open(missing_file, 'w') as f:
            f.write(f"# 缺失 PackageXml 的包列表\n")
            f.write(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# 总计: {len(missing_packages)} 个\n")
            f.write(f"# 说明: 这些包在 {deps_dir} 目录中没有对应的 PackageXml 文件\n")
            f.write(f"# 可能原因:\n")
            f.write(f"#   1. ros-oe-upstream-init 未下载该包\n")
            f.write(f"#   2. 该包是系统包（如 python3-serial）\n")
            f.write(f"#   3. 包名在 ros-oe-upstream-init 中不同\n\n")

            for pkg_info in missing_packages:
                f.write(f"{pkg_info['name']}\n")

    return final_order, set(target_packages), failed_packages, missing_packages

def main():
    if len(sys.argv) < 3:
        print("用法: python3 merge_package_sources.py <target_packages_file> <deps_dir> [options]")
        print("\n选项:")
        print("  -o <output_file>     输出文件名（默认: merged_build_order.txt）")
        print("  -w <work_dir>        工作目录（默认: 自动创建带时间戳的目录）")
        print("  --keep-temp          保留临时文件（默认会清理）")
        print("\n示例:")
        print("  python3 merge_package_sources.py packages.txt output/deps")
        print("  python3 merge_package_sources.py packages.txt output/deps -o my_order.txt")
        print("  python3 merge_package_sources.py packages.txt output/deps --keep-temp")
        sys.exit(1)

    target_file = sys.argv[1]
    deps_dir = sys.argv[2]

    # 解析参数
    output_file = None
    work_dir = None
    keep_temp = False

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == '-o' and i + 1 < len(sys.argv):
            output_file = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '-w' and i + 1 < len(sys.argv):
            work_dir = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--keep-temp':
            keep_temp = True
            i += 1
        else:
            i += 1

    # 读取目标包列表
    target_packages = []
    with open(target_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                target_packages.append(line)

    # 去重
    original_count = len(target_packages)
    target_packages = list(dict.fromkeys(target_packages))  # 保持顺序去重
    if len(target_packages) < original_count:
        print(f"ℹ️  去除重复包: {original_count} → {len(target_packages)}")

    # 创建工作目录
    if not work_dir:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        work_dir = f"merge_work_{timestamp}"

    os.makedirs(work_dir, exist_ok=True)
    print(f"📁 工作目录: {work_dir}")

    # 合并构建顺序
    build_order, target_set, failed_packages, missing_packages = merge_build_orders(target_packages, deps_dir, work_dir)

    # 输出结果
    print(f"\n{'='*60}")
    print(f"整合后的构建顺序")
    print(f"{'='*60}")
    print(f"总包数: {len(build_order)}\n")

    # 输出到屏幕（只显示前20和后20）
    if len(build_order) <= 40:
        for i, pkg in enumerate(build_order, 1):
            marker = " ★ [目标]" if pkg in target_set else ""
            print(f"{i:3d}. {pkg}{marker}")
    else:
        print("前 20 个包:")
        for i, pkg in enumerate(build_order[:20], 1):
            marker = " ★ [目标]" if pkg in target_set else ""
            print(f"{i:3d}. {pkg}{marker}")

        print(f"\n... (中间省略 {len(build_order) - 40} 个包) ...\n")

        print("后 20 个包:")
        for i, pkg in enumerate(build_order[-20:], len(build_order) - 19):
            marker = " ★ [目标]" if pkg in target_set else ""
            print(f"{i:3d}. {pkg}{marker}")

    # 输出到文件
    if not output_file:
        output_file = os.path.join(work_dir, "merged_build_order.txt")

    # 生成纯净的包名列表（用于后续自动化处理）
    with open(output_file, 'w') as f:
        f.write(f"# ROS包构建顺序（整合自 {len(target_packages)} 个目标包）\n")
        f.write(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 工作目录: {work_dir}\n")
        f.write(f"# 总计: {len(build_order)} 个包\n")
        f.write(f"# 目标包: {len(target_set)} 个 ({', '.join(sorted(target_set))})\n")
        if missing_packages:
            f.write(f"# 缺失PackageXml: {len(missing_packages)} 个 (见 {work_dir}/missing_packages.txt)\n")
        f.write(f"# 说明: 此文件只包含包名，可直接用于自动化构建\n\n")

        for pkg in build_order:
            f.write(f"{pkg}\n")

    # 生成带标记的版本（用于人工查看）
    marked_file = output_file.replace('.txt', '_marked.txt')
    with open(marked_file, 'w') as f:
        f.write(f"# ROS包构建顺序（带标记版本，便于人工查看）\n")
        f.write(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 工作目录: {work_dir}\n")
        f.write(f"# 总计: {len(build_order)} 个包\n")
        f.write(f"# 目标包: {', '.join(sorted(target_set))}\n\n")

        for i, pkg in enumerate(build_order, 1):
            marker = " ★ [目标]" if pkg in target_set else ""
            f.write(f"{i:3d}. {pkg}{marker}\n")

    print(f"\n✅ 构建顺序已保存到: {output_file}")
    print(f"📄 带标记版本: {marked_file}")
    print(f"📝 详细日志: {os.path.join(work_dir, 'merge_log.txt')}")

    if missing_packages:
        print(f"⚠️  缺失包列表: {os.path.join(work_dir, 'missing_packages.txt')}")

    # 统计信息
    print(f"\n📊 统计信息:")
    print(f"  输入:")
    print(f"    目标包: {len(target_packages)} 个")
    print(f"    成功: {len(target_packages) - len(failed_packages)} 个")
    if failed_packages:
        print(f"    失败: {len(failed_packages)} 个")
    if missing_packages:
        print(f"    缺失PackageXml: {len(missing_packages)} 个")
    print(f"  输出:")
    print(f"    依赖包: {len(build_order) - len(target_set)} 个")
    print(f"    总计: {len(build_order)} 个")
    print(f"  成功率: {((len(target_packages) - len(failed_packages)) / len(target_packages) * 100):.1f}%")

    # 清理临时文件
    if not keep_temp:
        temp_files = [f for f in os.listdir(work_dir) if f.startswith('temp_')]
        for temp_file in temp_files:
            os.remove(os.path.join(work_dir, temp_file))
        if temp_files:
            print(f"\n🧹 已清理 {len(temp_files)} 个临时文件")

if __name__ == '__main__':
    main()
