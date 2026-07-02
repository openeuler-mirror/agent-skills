#!/usr/bin/env python3
"""
ROS包依赖解析工具
用于解析ROS包的递归依赖关系并生成构建顺序
"""

import os
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict, deque


class ROSDependencyResolver:
    """ROS包依赖解析器"""

    def __init__(self, deps_dir: str):
        self.deps_dir = Path(deps_dir)
        if not self.deps_dir.exists():
            raise ValueError(f"依赖目录不存在: {deps_dir}")

        # 存储解析结果
        self.system_deps = set()  # 系统依赖
        self.ros_deps = {}  # {pkg: [dep1, dep2, ...]} ROS包依赖图
        self.ros_pkg_set = set()  # 所有ROS包
        self.dependency_levels = {}  # {pkg: level} 依赖层级

        # 初始化系统依赖白名单
        self._init_system_dep_whitelist()

    def _init_system_dep_whitelist(self):
        """初始化系统依赖白名单"""
        self.system_dep_whitelist = {
            # 构建工具
            'cmake', 'make', 'gcc', 'g++', 'ccache', 'pkg-config', 'git', 'ninja',
            # Python解释器
            'python3', 'python', 'python2',
            # Python模块（通常随python3一起安装）
            'python3-catkin-pkg-modules', 'python3-importlib-metadata',
            'python3-importlib-resources', 'python3-setuptools', 'python3-yaml',
            'python3-empy', 'python3-flake8', 'python3-pep257', 'python3-pytest',
            'python3-mock', 'python3-pytest-timeout', 'python3-nose',
            # 系统库
            'libatomic', 'libstdc++', 'libyaml', 'yaml', 'pthread', 'dl', 'm', 'rt',
            'z', 'boost_thread', 'boost_system', 'boost',
            # 其他
            'ncurses', 'readline', 'sqlite',
        }

    def _normalize_pkg_name(self, pkg_name: str) -> str:
        """标准化包名，将下划线转换为连字符"""
        return pkg_name.replace('_', '-')

    def is_system_dependency(self, pkg_name: str) -> bool:
        """判断是否为系统依赖"""
        # 1. 检查白名单
        if pkg_name in self.system_dep_whitelist:
            return True

        # 2. 检查lib开头的系统库
        if pkg_name.startswith('lib'):
            return True

        # 3. 检查是否为ROS包（通过检查PackageXml存在性）
        # 需要将下划线转换为连字符
        normalized_name = self._normalize_pkg_name(pkg_name)
        package_xml = self.deps_dir / f"{normalized_name}-PackageXml"

        if not package_xml.exists():
            # 如果PackageXml不存在，可能是系统依赖
            return True

        # 4. 有PackageXml的肯定是ROS包
        return False

    def parse_package_xml(self, pkg_name: str) -> List[str]:
        """解析PackageXml文件，返回所有依赖包列表"""
        # 将下划线转换为连字符以匹配文件名
        normalized_name = self._normalize_pkg_name(pkg_name)
        package_xml = self.deps_dir / f"{normalized_name}-PackageXml"
        if not package_xml.exists():
            return []

        deps = []
        with open(package_xml, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # 解析依赖行，格式如：depend:rcl 或 build_depend:ament_index_cpp
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()

                    # 只处理依赖相关的键
                    dep_keys = {
                        'depend', 'build_depend', 'build_export_depend',
                        'buildtool_depend', 'buildtool_export_depend',
                        'exec_depend', 'doc_depend'
                    }
                    if key in dep_keys and value:
                        deps.append(value)

        return deps

    def resolve_package(
        self,
        pkg_name: str,
        visited: Set[str] = None,
        depth: int = 0
    ):
        """
        递归解析包依赖
        """
        if visited is None:
            visited = set()

        # 已经处理过
        if pkg_name in visited:
            return

        # 检查是否为系统依赖
        if self.is_system_dependency(pkg_name):
            self.system_deps.add(pkg_name)
            return

        # 添加到已处理集合
        visited.add(pkg_name)
        self.ros_pkg_set.add(pkg_name)
        self.dependency_levels[pkg_name] = depth

        # 解析PackageXml
        deps = self.parse_package_xml(pkg_name)

        print(f"  解析: {pkg_name} (depth={depth}) -> {len(deps)} 个依赖")

        # 筛选出ROS包依赖
        ros_package_deps = [dep for dep in deps if not self.is_system_dependency(dep)]
        self.ros_deps[pkg_name] = ros_package_deps

        # 递归处理每个ROS包依赖
        for dep in ros_package_deps:
            self.resolve_package(dep, visited, depth + 1)

    def topological_sort(self) -> List[str]:
        """
        使用Kahn算法进行拓扑排序
        返回构建顺序（从被依赖的包到依赖它们的包）
        如果A依赖B，那么B应该在A之前构建
        """
        # 构建入度表和反向依赖图
        in_degree = {pkg: 0 for pkg in self.ros_pkg_set}
        reverse_deps = {pkg: [] for pkg in self.ros_pkg_set}

        for pkg, deps in self.ros_deps.items():
            for dep in deps:
                if dep in self.ros_pkg_set:
                    # dep -> pkg: 依赖dep的包是pkg
                    reverse_deps[dep].append(pkg)
                    in_degree[pkg] += 1

        # 找到入度为0的节点（没有依赖其他ROS包）
        queue = deque([pkg for pkg in self.ros_pkg_set if in_degree[pkg] == 0])
        result = []

        # 记录已处理的节点
        processed = set()

        while queue:
            pkg = queue.popleft()
            if pkg in processed:
                continue
            processed.add(pkg)
            result.append(pkg)

            # 处理依赖该包的其他包
            for dependent in reverse_deps[pkg]:
                if dependent not in processed:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        # 检查是否所有包都已处理
        if len(result) != len(self.ros_pkg_set):
            unprocessed = self.ros_pkg_set - processed
            print(f"警告: 有 {len(unprocessed)} 个包无法排序（可能存在循环依赖）", file=sys.stderr)
            for pkg in unprocessed:
                print(f"  - {pkg}", file=sys.stderr)
            # 将未处理的包添加到结果末尾
            result.extend(sorted(unprocessed))

        return result

    def resolve(self, target_pkg: str):
        """
        解析目标包的所有依赖
        """
        print(f"\n{'='*60}")
        print(f"开始解析包依赖: {target_pkg}")
        print(f"{'='*60}\n")

        # 递归解析依赖
        self.resolve_package(target_pkg)

        # 执行拓扑排序
        build_order = self.topological_sort()

        return build_order

    def categorize_system_deps(self) -> Dict[str, List[str]]:
        """分类系统依赖"""
        categories = {
            '构建工具': [],
            'Python相关': [],
            '系统库': [],
            '其他': []
        }

        for dep in sorted(self.system_deps):
            if dep in {'cmake', 'make', 'gcc', 'g++', 'ccache', 'pkg-config', 'git', 'ninja'}:
                categories['构建工具'].append(dep)
            elif dep.startswith('python'):
                categories['Python相关'].append(dep)
            elif dep.startswith('lib') or dep in {'yaml', 'pthread', 'dl', 'm', 'rt', 'z', 'boost', 'boost_thread', 'boost_system'}:
                categories['系统库'].append(dep)
            else:
                categories['其他'].append(dep)

        return categories

    def print_result(self, target_pkg: str, build_order: List[str]):
        """打印解析结果到标准输出"""
        print(f"\n{'='*60}")
        print(f"依赖树分析结果: {target_pkg}")
        print(f"{'='*60}\n")

        # 打印系统依赖
        print("📦 系统依赖（环境已内置，无需构建）：")
        system_categories = self.categorize_system_deps()
        for category, deps in system_categories.items():
            if deps:
                print(f"  {category}：{', '.join(deps)}")
        print()

        # 打印依赖层级统计
        level_counts = defaultdict(int)
        for pkg, level in self.dependency_levels.items():
            level_counts[level] += 1

        print("📊 依赖层级统计：")
        for level in sorted(level_counts.keys()):
            print(f"  Level {level}: {level_counts[level]} 个包")
        print()

        # 打印构建顺序
        print(f"📋 构建顺序（按依赖关系，共 {len(build_order)} 个ROS包）：")
        for i, pkg in enumerate(build_order, 1):
            level = self.dependency_levels.get(pkg, 0)
            is_target = pkg == target_pkg
            marker = " ★ [目标]" if is_target else ""
            print(f"  {i:2d}. {pkg:<40} (Level {level}){marker}")
        print()

    def save_to_file(self, target_pkg: str, build_order: List[str], output_file: str):
        """将构建顺序保存到文件"""
        output_path = Path(output_file)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"# ROS包构建顺序: {target_pkg}\n")
            f.write(f"# 生成时间: {self._get_current_time()}\n")
            f.write(f"# 总计: {len(build_order)} 个包\n")
            f.write(f"# 说明: 此文件中的包需要按照从上到下的顺序构建\n\n")

            for i, pkg in enumerate(build_order, 1):
                f.write(f"{pkg}\n")

        print(f"✅ 构建顺序已保存到: {output_path.absolute()}")

    def _get_current_time(self) -> str:
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='ROS包依赖解析工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 解析 rclcpp 包的依赖
  python resolve_dependencies.py rclcpp ./ros-oe-upstream-init/output/deps

  # 指定输出文件
  python resolve_dependencies.py rclcpp ./ros-oe-upstream-init/output/deps -o build_order.txt

  # 解析多个包
  python resolve_dependencies.py rclcpp rcl ./ros-oe-upstream-init/output/deps
        """
    )

    parser.add_argument(
        'package',
        help='要解析的包名'
    )

    parser.add_argument(
        'deps_dir',
        help='依赖关系目录路径'
    )

    parser.add_argument(
        '-o', '--output',
        default='build_order.txt',
        help='输出文件名 (默认: build_order.txt)'
    )

    args = parser.parse_args()

    try:
        # 创建解析器
        resolver = ROSDependencyResolver(args.deps_dir)

        # 解析依赖
        build_order = resolver.resolve(args.package)

        # 打印结果
        resolver.print_result(args.package, build_order)

        # 保存到文件
        resolver.save_to_file(args.package, build_order, args.output)

    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()