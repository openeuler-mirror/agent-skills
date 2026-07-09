#!/usr/bin/env python3
"""
ROS Package Local Update Script

用于在本地将 upstream ROS 包的 spec 文件、补丁、tar 包更新到 openEuler 仓库中。

输入参数：
- openeuler_dir: openEuler 仓库目录
- upstream_dir: upstream tar 包目录
- package_list_file: ROS 包列表文件路径

输出文件：
- local_update.log: 详细操作日志
- local_update_success.txt: 成功更新的包列表 (生成到 .tmp/state/)
- local_update_failure.txt: 失败的包列表 (生成到 .tmp/state/)
- agent_analysis_request.json: 需要 agent 分析的包列表（生成到 .tmp/state/）
"""

import os
import re
import json
import argparse
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SpecDiff:
    """存储 spec 文件差异信息"""
    package: str
    diff_type: str
    old_value: str
    new_value: str
    old_patches: List[str]
    new_patches: List[str]
    git_diff_output: str
    reason: str


@dataclass
class PackageUpdateResult:
    """包更新结果"""
    package: str
    success: bool
    error_message: Optional[str]
    changes: Optional[Dict]


class Logger:
    """统一的日志系统"""

    def __init__(self, log_file_path: Path):
        self.log_file_path = log_file_path
        self.setup_logging()

    def setup_logging(self):
        """设置日志系统"""
        self.logger = logging.getLogger('ros_package_update')
        self.logger.setLevel(logging.INFO)

        # 创建文件处理器
        file_handler = logging.FileHandler(self.log_file_path, mode='w', encoding='utf-8')
        file_handler.setLevel(logging.INFO)

        # 创建控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # 创建格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # 添加处理器
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def info(self, msg: str):
        self.logger.info(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def success(self, msg: str):
        self.logger.info(f"✅ {msg}")


class ROSPackageUpdater:
    """ROS 包更新器"""

    def __init__(self, openeuler_dir: Path, upstream_dir: Path, log_file_path: Path):
        self.openeuler_dir = openeuler_dir
        self.upstream_dir = upstream_dir
        self.logger = Logger(log_file_path)
        self.success_list = []
        self.failure_list = []
        self.agent_requests = []
        self.changes_log = []

    def get_repo_name(self, package: str) -> str:
        """Get downstream GitCode repository name using the mapping cache."""
        import json
        import os
        cache_path = os.path.expanduser("~/.cache/fork-src-openeuler/package_repo_map.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    cache_data = json.load(f)
                    mapping = cache_data.get("mapping", {})
                    # Exact match
                    if package in mapping:
                        return mapping[package]
                    # Normalized match
                    norm_pkg = package.replace('_', '-')
                    for k, v in mapping.items():
                        if k.replace('_', '-') == norm_pkg:
                            return v
            except Exception as e:
                self.logger.warning(f"Failed to read repo mapping cache: {e}")
                
        # Try ground truth from local output/repo/ first (same logic as fork_repos)
        if self.upstream_dir and self.upstream_dir.exists():
            import glob
            # check if <repo_dir>/*/<pkg>.spec exists
            spec_files = glob.glob(str(self.upstream_dir / "*" / f"{package}.spec"))
            if not spec_files:
                spec_files = glob.glob(str(self.upstream_dir / "*" / f"{package.replace('-', '_')}.spec"))
            if not spec_files:
                spec_files = glob.glob(str(self.upstream_dir / "*" / f"{package.replace('_', '-')}.spec"))
                
            if spec_files:
                return Path(spec_files[0]).parent.name
                
        return package

    def get_upstream_name(self, package: str) -> str:
        """Get upstream tarball directory name using the same logic"""
        return self.get_repo_name(package)


    def validate_directories(self, package_list_file: Path) -> bool:
        """验证输入参数的合理性"""

        self.logger.info("开始验证输入参数...")

        # 1. 检查 openEuler 目录
        if not self.openeuler_dir.exists():
            self.logger.error(f"openEuler 目录不存在: {self.openeuler_dir}")
            return False

        if not self.openeuler_dir.is_dir():
            self.logger.error(f"openEuler 路径不是目录: {self.openeuler_dir}")
            return False

        # 注意: openEuler 目录是多仓库结构，每个子目录是独立的 git 仓库
        # 不检查父目录是否是 git 仓库，而是在处理每个包时检查

        # 2. 检查 upstream 目录
        if not self.upstream_dir.exists():
            self.logger.error(f"upstream 目录不存在: {self.upstream_dir}")
            return False

        if not self.upstream_dir.is_dir():
            self.logger.error(f"upstream 路径不是目录: {self.upstream_dir}")
            return False

        # 3. 检查包列表文件
        if not package_list_file.exists():
            self.logger.error(f"包列表文件不存在: {package_list_file}")
            return False

        # 4. 检查 openEuler 目录下是否有包
        openeuler_packages = [d for d in self.openeuler_dir.iterdir()
                            if d.is_dir() and not d.name.startswith('.')]
        if not openeuler_packages:
            self.logger.error(f"openEuler 目录下没有包: {self.openeuler_dir}")
            return False

        self.logger.info(f"✅ 找到 {len(openeuler_packages)} 个 openEuler 包")
        self.logger.info(f"✅ upstream 目录存在")
        self.logger.info(f"✅ 包列表文件存在: {package_list_file}")
        self.logger.info(f"ℹ️  多仓库结构: 每个包子目录是独立的 git 仓库")

        return True

    def read_package_list(self, package_list_file: Path) -> List[str]:
        """读取包列表文件"""
        packages = []

        with open(package_list_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    packages.append(line)

        self.logger.info(f"📋 从文件读取到 {len(packages)} 个包")
        return packages

    def find_spec_file(self, package_dir: Path) -> Optional[Path]:
        """查找包目录中的 spec 文件"""
        spec_files = list(package_dir.glob("*.spec"))
        if spec_files:
            return spec_files[0]  # 返回第一个找到的 spec 文件
        return None

    def find_spec_file_for_package(self, package_dir: Path, package: str) -> Optional[Path]:
        """在目录中查找特定包的 spec 文件（支持 multibuild 仓库）

        匹配策略：
        1. 优先精确匹配：rosidl_cli -> rosidl-cli.spec (下划线转连字符)
        2. 次优匹配：rosidl_cli -> rosidl_cli.spec (原名匹配)
        3. 回退匹配：如果只有一个 spec 文件，直接返回
        """
        # 生成精确的 spec 文件名变体
        spec_variants = []

        # 1. 下划线转连字符（最常见的 ROS 命名约定）
        if '_' in package:
            spec_variants.append(f"{package.replace('_', '-')}.spec")

        # 2. 原名匹配
        spec_variants.append(f"{package}.spec")

        # 3. 连字符转下划线
        if '-' in package:
            spec_variants.append(f"{package.replace('-', '_')}.spec")

        # 尝试精确匹配
        for spec_name in spec_variants:
            spec_path = package_dir / spec_name
            if spec_path.exists():
                self.logger.info(f"  ✅ 精确匹配 spec: {spec_name}")
                return spec_path

        # 如果目录中只有一个 spec 文件，直接返回（适用于单包仓库）
        spec_files = list(package_dir.glob("*.spec"))
        if len(spec_files) == 1:
            self.logger.info(f"  ℹ️ 目录中只有一个 spec 文件，直接使用: {spec_files[0].name}")
            return spec_files[0]

        # 多个 spec 文件但都无法精确匹配，报错
        if len(spec_files) > 1:
            self.logger.error(f"  ❌ Multibuild 仓库中找到多个 spec 文件但无法精确匹配:")
            self.logger.error(f"     包名: {package}")
            self.logger.error(f"     尝试的变体: {spec_variants}")
            self.logger.error(f"     目录中的 spec 文件: {[f.name for f in spec_files]}")
            return None

        return None

    def generate_package_variants(self, package: str) -> List[str]:
        """生成包名的多种变体形式，用于匹配不同命名风格"""
        variants = [package]  # 原始名称

        # 下划线 <-> 连字符 转换
        if '_' in package:
            variants.append(package.replace('_', '-'))
        if '-' in package:
            variants.append(package.replace('-', '_'))

        # 尝试常见的命名风格
        # 例如: fastcdr -> Fast-CDR, FastCDR, fast-cdr
        name = package

        # 尝试首字母大写
        variants.append(name.capitalize())
        variants.append(name.title())

        # 处理常见的缩写词
        # 例如: fastcdr -> Fast-CDR
        parts = []
        if '_' in name:
            parts = name.split('_')
        elif '-' in name:
            parts = name.split('-')
        else:
            # 尝试按常见缩写拆分 (cdr, cli, utils 等)
            import re
            # 匹配常见模式: fastcdr, rosidl 等
            match = re.match(r'^([a-z]+)(cdr|cli|idl|utils|vendor|cpp|py|hpps|hoofs|dds|rtps|rmw|rcl|ros)$', name)
            if match:
                parts = [match.group(1), match.group(2)]

        if parts:
            # 连字符形式
            variants.append('-'.join(parts))
            # 下划线形式
            variants.append('_'.join(parts))
            # 首字母大写连字符形式
            variants.append('-'.join(p.capitalize() for p in parts))
            # 首字母大写无分隔符形式
            variants.append(''.join(p.capitalize() for p in parts))

        # 去重并返回
        return list(dict.fromkeys(variants))

    def find_upstream_pkg_dir(self, package: str) -> Optional[Path]:
        """在 upstream 目录中查找包目录，支持多种命名风格和 multibuild 仓库"""
        # 生成包名变体
        variants = self.generate_package_variants(package)

        self.logger.info(f"  查找 upstream 包目录，尝试变体: {variants}")

        # 首先尝试直接匹配目录名
        for variant in variants:
            pkg_dir = self.upstream_dir / variant
            if pkg_dir.exists() and pkg_dir.is_dir():
                spec_file = self.find_spec_file(pkg_dir)
                if spec_file:
                    self.logger.info(f"  ✅ 找到 upstream 包目录: {variant}")
                    return pkg_dir

        # 如果直接匹配失败，搜索所有子目录（支持 multibuild 仓库）
        # multibuild 仓库：一个目录包含多个 spec 文件，如 iceoryx 目录包含 iceoryx_hoofs.spec 等
        for subdir in self.upstream_dir.iterdir():
            if not subdir.is_dir():
                continue

            # 检查目录内是否有匹配的 spec 文件
            for spec_file in subdir.glob("*.spec"):
                spec_name = spec_file.stem.lower()
                # 移除常见的 ROS 包前缀（如 ros-humble-）
                spec_name_clean = spec_name
                for prefix in ['ros-humble-', 'ros-', 'humble-']:
                    if spec_name_clean.startswith(prefix):
                        spec_name_clean = spec_name_clean[len(prefix):]
                        break

                # 检查 spec 文件名是否匹配包名或其变体（只使用精确匹配）
                for variant in variants:
                    variant_lower = variant.lower().replace('-', '').replace('_', '')
                    spec_name_normalized = spec_name_clean.replace('-', '').replace('_', '')
                    if variant_lower == spec_name_normalized:
                        self.logger.info(f"  ✅ 在 multibuild 仓库 {subdir.name} 中找到 spec: {spec_file.name}")
                        return subdir

        # 如果上述方法都失败，使用全局查找作为最后的回退策略
        # 这种情况适用于包名和目录名差异很大的情况，如 tracetools -> ros2_tracing
        self.logger.info(f"  尝试全局查找 spec 文件...")

        # 生成可能的 spec 文件名
        spec_names = [
            f"{package}.spec",
            f"{package.replace('_', '-')}.spec",
            f"{package.replace('-', '_')}.spec"
        ]

        for spec_name in spec_names:
            # 在 upstream 目录树中查找 spec 文件
            for spec_file in self.upstream_dir.rglob(spec_name):
                if spec_file.is_file():
                    pkg_dir = spec_file.parent
                    self.logger.info(f"  ✅ 通过全局查找找到 spec: {spec_file.name} (目录: {pkg_dir.name})")
                    return pkg_dir

        return None

    def _parse_macros(self, content: str) -> dict:
        macros = {}
        for line in content.split('\n'):
            line = line.strip()
            define_match = re.match(r'^%(?:define|global)\s+(\w+)\s+(.+)$', line)
            if define_match:
                macros[define_match.group(1)] = define_match.group(2).strip()
        return macros

    def _expand_macros(self, text: str, macros: dict, content: str, max_depth: int = 10) -> str:
        if max_depth <= 0 or '%' not in text:
            return text
            
        result = text
        for match in re.finditer(r'%\{\?(\w+):([^}]*)\}', result):
            macro_name = match.group(1)
            default_val = match.group(2)
            result = result.replace(match.group(0), macros.get(macro_name, default_val))

        for match in re.finditer(r'%\{(\w+)\}', result):
            macro_name = match.group(1)
            if macro_name in macros:
                macro_value = self._expand_macros(macros[macro_name], macros, content, max_depth - 1)
                result = result.replace(match.group(0), macro_value)
            elif macro_name in ('name', 'version'):
                for l in content.split('\n'):
                    if l.startswith(f"{macro_name.capitalize()}:"):
                        result = result.replace(match.group(0), l.split(':', 1)[1].strip())
                        break
        return result

    def extract_all_sources(self, spec_file: Path) -> list:
        sources = []
        try:
            with open(spec_file, 'r', encoding='utf-8') as f:
                content = f.read()
            macros = self._parse_macros(content)
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('Source') and ':' in line:
                    source_val = line.split(':', 1)[1].strip().split('/')[-1]
                    expanded = self._expand_macros(source_val, macros, content)
                    sources.append(expanded)
        except Exception as e:
            self.logger.warning(f"提取 sources 失败: {e}")
        return list(set(sources))

    def extract_tar_name(self, spec_file: Path) -> str:
        try:
            with open(spec_file, 'r', encoding='utf-8') as f:
                content = f.read()
            macros = self._parse_macros(content)
            for line in content.split('\n'):
                if line.startswith('Source0:'):
                    source = line.split(':', 1)[1].strip().split('/')[-1]
                    return self._expand_macros(source, macros, content)
        except Exception:
            pass
        return None

    def try_fetch_real_source(self, package: str, version: str) -> Optional[str]:
        """寻找真实上游源码 URL 的尝试机制"""
        import requests
        
        # 尝试一些常见的上游组织和仓库名组合
        base_name = package.replace('-release', '').replace('_vendor', '')
        
        candidates = [
            f"{base_name}/{base_name}",
            f"{package}/{package}",
            f"ros/{base_name}",
            f"ros2/{base_name}",
            f"ros-planning/{base_name}",
            f"eProsima/{package}",
            f"eProsima/{base_name}"
        ]
        
        for repo in candidates:
            try:
                # 设置超时，防止卡死
                resp = requests.get(f"https://api.github.com/repos/{repo}/tags", timeout=5)
                if resp.status_code == 200:
                    tags = [t['name'] for t in resp.json()]
                    
                    # 寻找匹配版本号的 tag
                    match_tag = None
                    for tag in tags:
                        if tag == version or tag == f"v{version}" or tag == version.replace('.', '-'):
                            match_tag = tag
                            break
                    
                    if match_tag:
                        tarball_url = f"https://github.com/{repo}/archive/refs/tags/{match_tag}.tar.gz"
                        return tarball_url
            except Exception:
                continue
                
        return None

    def extract_patches(self, spec_file: Path) -> List[str]:
        """从 spec 文件提取补丁列表"""
        patches = []
        try:
            with open(spec_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('Patch'):
                        patches.append(line.strip())
        except Exception as e:
            self.logger.warning(f"提取补丁列表失败: {e}")
        return patches

    def get_source0_url(self, spec_file: Path) -> Optional[str]:
        """提取 Source0 的完整 URL"""
        try:
            with open(spec_file, 'r', encoding='utf-8') as f:
                content = f.read()
            macros = self._parse_macros(content)
            for line in content.split('\n'):
                if line.startswith('Source0:') or (line.startswith('Source:') and not any(l.startswith('Source0:') for l in content.split('\n'))):
                    source = line.split(':', 1)[1].strip()
                    return self._expand_macros(source, macros, content)
        except Exception:
            pass
        return None

    def verify_tarball(self, tar_path: Path) -> bool:
        """检查 tar 包是否包含真实的源码（CMakeLists.txt, package.xml 等）"""
        import subprocess
        try:
            # 只列出文件，不解压，检查是否包含源码特征文件
            result = subprocess.run(['tar', '-tf', str(tar_path)], capture_output=True, text=True, timeout=15)
            output = result.stdout.lower()
            if 'cmakelists.txt' in output or 'package.xml' in output or 'setup.py' in output:
                return True
            return False
        except Exception as e:
            self.logger.warning(f"验证 tar 包失败: {e}")
            return False

    def download_tarball(self, url: str, dest_path: Path) -> bool:
        """使用 curl 下载 tar 包"""
        import subprocess
        try:
            self.logger.info(f"    ⬇️ 正在从 URL 下载真实的源码包: {url}")
            result = subprocess.run(['curl', '-L', '-s', '-o', str(dest_path), url], capture_output=True, check=True)
            if dest_path.exists() and dest_path.stat().st_size > 100:
                return True
        except Exception as e:
            self.logger.error(f"下载失败: {e}")
        return False

    def strip_source_url_in_spec(self, spec_file: Path):
        """将 spec 文件中的 Source0 的 URL 替换为纯文件名，防止 EUR 云端构建时由于网络问题下载失败"""
        with open(spec_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        new_lines = []
        for line in lines:
            if line.startswith('Source0:') or (line.startswith('Source:') and 'http' in line):
                parts = line.split(':', 1)
                val = parts[1].strip()
                if val.startswith('http://') or val.startswith('https://'):
                    filename = val.split('/')[-1]
                    new_lines.append(f"{parts[0]}:        {filename}\n")
                    continue
            new_lines.append(line)
            
        with open(spec_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    def get_tarball_top_dir(self, tar_path: Path) -> Optional[str]:
        """探测 tar 包内的顶层目录名称"""
        import subprocess
        try:
            result = subprocess.run(['tar', '-tf', str(tar_path)], capture_output=True, text=True, timeout=15)
            lines = result.stdout.strip().split('\n')
            if not lines or not lines[0]:
                return None
            
            # 找到所有文件路径的第一级目录
            top_dirs = set()
            for line in lines:
                parts = line.split('/')
                if parts[0]:
                    top_dirs.add(parts[0])
            
            # 如果只有一个顶层目录，说明打包规范，返回它
            if len(top_dirs) == 1:
                return list(top_dirs)[0]
            else:
                self.logger.warning(f"⚠️ Tar 包内包含多个顶层文件或目录，无法确定单一的根目录: {list(top_dirs)[:3]}...")
                return None
        except Exception as e:
            self.logger.warning(f"探测 tar 包目录结构失败: {e}")
            return None

    def fix_autosetup_dir(self, spec_file: Path, actual_dir: str):
        """修改 spec 文件中的 %autosetup 或 %setup 以匹配真实的解压目录"""
        import re
        with open(spec_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        new_lines = []
        for line in lines:
            if line.startswith('%autosetup') or line.startswith('%setup'):
                # 如果已经存在 -n 参数，将其替换
                if '-n' in line:
                    line = re.sub(r'-n\s+([^\s]+)', f'-n {actual_dir}', line)
                else:
                    # 追加 -n 参数
                    line = line.rstrip() + f' -n {actual_dir}\n'
            new_lines.append(line)
            
        with open(spec_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    def run_git_diff(self, package_dir: Path, package: str) -> str:
        """运行 git diff 并返回输出"""
        try:
            # 保存当前工作目录
            original_cwd = os.getcwd()

            os.chdir(package_dir)
            # 使用精确匹配找到正确的 spec 文件（支持 multibuild 仓库）
            spec_file = self.find_spec_file_for_package(package_dir, package)
            if not spec_file:
                os.chdir(original_cwd)
                return ""

            # 运行 git diff
            result = os.popen(f"git diff {spec_file.name}")
            diff_output = result.read()

            # 恢复原工作目录
            os.chdir(original_cwd)

            return diff_output
        except Exception as e:
            self.logger.warning(f"Git diff 失败: {e}")
            # 确保恢复工作目录
            if 'original_cwd' in locals():
                os.chdir(original_cwd)
            return ""

    def make_diff_decision(self, package: str, old_spec: Path, new_spec: Path,
                          git_diff_output: str) -> dict:
        """根据差异类型决定是否自动处理"""

        old_patches = self.extract_patches(old_spec) if old_spec else []
        new_patches = self.extract_patches(new_spec)

        # 构建逻辑怀疑机制检查
        critical_sections = ["%prep", "%build", "%install", "%autosetup", "cmake", "%cmake"]
        suspicious_build_change = False
        suspicious_reason = ""
        
        diff_lines = git_diff_output.splitlines()
        for line in diff_lines:
            if line.startswith("-") and not line.startswith("---"):
                for sec in critical_sections:
                    if sec in line and "Source" not in line and "Patch" not in line and "Version" not in line and "Release" not in line:
                        suspicious_build_change = True
                        suspicious_reason = f"检测到核心构建指令或宏 ({sec}) 被移除或大幅修改"
                        break
            if suspicious_build_change:
                break
                
        if suspicious_build_change:
             return {
                 "auto_handle": False,
                 "diff_type": "build_logic_changed",
                 "old_value": "",
                 "new_value": "",
                 "old_patches": old_patches,
                 "new_patches": new_patches,
                 "git_diff_output": git_diff_output,
                 "reason": suspicious_reason + "，需人工或 Agent 确认是否为合理修改或丢失了 openEuler 的定制化配置"
             }

        # 预期差异：自动处理
        if "Version:" in git_diff_output and not suspicious_build_change and old_patches == new_patches:
            return {
                "auto_handle": True,
                "diff_type": "version",
                "old_value": "提取版本号",
                "new_value": "提取版本号",
                "old_patches": old_patches,
                "new_patches": new_patches,
                "git_diff_output": git_diff_output,
                "reason": "版本号变更，预期差异"
            }

        # 补丁差异分析
        if not old_patches and not new_patches:
            return {
                "auto_handle": True,
                "diff_type": "no_patch_change",
                "old_patches": [],
                "new_patches": [],
                "git_diff_output": git_diff_output,
                "reason": "无补丁变化"
            }
        elif old_patches == new_patches:
            return {
                "auto_handle": True,
                "diff_type": "patch_unchanged",
                "old_patches": old_patches,
                "new_patches": new_patches,
                "git_diff_output": git_diff_output,
                "reason": "补丁未变化"
            }
        elif old_patches and not new_patches:
            return {
                "auto_handle": False, # 强制拦截分析
                "diff_type": "patch_dropped",
                "old_value": "",
                "new_value": "",
                "old_patches": old_patches,
                "new_patches": [],
                "git_diff_output": git_diff_output,
                "reason": "新 spec 丢弃了老补丁，需要分析确认是否上游已修复该 Bug/CVE，或是否仍需保留(可用 patch --dry-run 验证)"
            }
        elif not old_patches and new_patches:
            return {
                "auto_handle": True,
                "diff_type": "patch_copy_new",
                "old_patches": [],
                "new_patches": new_patches,
                "git_diff_output": git_diff_output,
                "reason": "需要拷贝新补丁"
            }
        else:
            return {
                "auto_handle": False,
                "diff_type": "patch_diff",
                "old_value": "",
                "new_value": "",
                "old_patches": old_patches,
                "new_patches": new_patches,
                "git_diff_output": git_diff_output,
                "reason": "补丁内容或数量变更，需要深入分析补丁有效性(可用 patch --dry-run 验证)"
            }

    def handle_large_tarball(self, tar_path: Path, spec_file: Path) -> list:
        """处理大 tar 包，超过 10MB 则切分"""
        if not tar_path.exists():
            return None
            
        tar_size_mb = tar_path.stat().st_size / (1024 * 1024)
        
        if tar_size_mb > 10:
            self.logger.warning(f"⚠️ 检测到大 tar 包 ({tar_size_mb:.1f}MB)，开始前置自动切分...")
            split_prefix = f"{tar_path.name}."
            import subprocess
            try:
                subprocess.run([
                    "split", "-b", "8M", tar_path.name, split_prefix
                ], check=True, cwd=str(tar_path.parent))
                tar_path.unlink()
                splits = sorted([f.name for f in tar_path.parent.glob(f"{split_prefix}*")])
                self.modify_spec_for_splits(spec_file, tar_path.name, splits)
                self.logger.info(f"✅ 已成功切分为 {len(splits)} 个分片文件并修改了 spec 文件")
                return splits
            except Exception as e:
                self.logger.error(f"❌ 切分 tar 包失败: {e}")
                return None
        return None

    def modify_spec_for_splits(self, spec_file: Path, orig_tar_name: str, splits: list):
        """修改 spec 文件以支持切分文件"""
        with open(spec_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        new_lines = []
        source_replaced = False
        prep_modified = False
        
        for line in lines:
            if (line.startswith('Source0:') or (line.startswith('Source:') and not any(l.startswith('Source0:') for l in lines))) and not source_replaced:
                for idx, split_file in enumerate(splits):
                    new_lines.append(f"Source1{idx:02d}:        {split_file}\n")
                source_replaced = True
                continue
                
            if line.startswith('%prep') and not prep_modified:
                new_lines.append(line)
                new_lines.append(f"cat %{{SOURCE100}}")
                for idx in range(1, len(splits)):
                    new_lines.append(f" %{{SOURCE1{idx:02d}}}")
                new_lines.append(f" > {orig_tar_name}\n")
                new_lines.append(f"tar -xzf {orig_tar_name}\n")
                prep_modified = True
                continue
                
            if line.strip().startswith('%autosetup') and prep_modified:
                import re
                n_param = re.search(r'-n\s+(\S+)', line)
                dir_name = n_param.group(1) if n_param else orig_tar_name.replace('.tar.gz', '').replace('.tgz', '').replace('.tar.bz2', '')
                new_lines.append(f"%autosetup -T -D -p1 -n {dir_name}\n")
                continue
                
            new_lines.append(line)
            
        with open(spec_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    def update_single_package(self, package: str) -> PackageUpdateResult:
        """处理单个包的完整更新流程"""

        self.logger.info(f"开始处理包: {package}")

        # 步骤 1: 路径检查
        repo_name = self.get_repo_name(package)
        upstream_name = self.get_upstream_name(package)
        pkg_dir = self.openeuler_dir / repo_name
        
        if repo_name != package:
            self.logger.info(f"  ℹ️ 包 {package} 映射到仓库 {repo_name}")
            
        if not pkg_dir.exists():
            return PackageUpdateResult(package, False, "找不到 openEuler 包目录", None)

        # 检查包目录是否是 git 仓库（多仓库结构）
        pkg_git_dir = pkg_dir / ".git"
        if not pkg_git_dir.exists():
            return PackageUpdateResult(package, False, f"包目录不是 git 仓库: {pkg_dir}", None)

        # 查找 openEuler 中的目标 spec 文件（支持 multibuild 仓库）
        old_spec = self.find_spec_file_for_package(pkg_dir, package)
        if not old_spec and pkg_dir.exists():
            self.logger.warning(f"在 openEuler 目录中找不到匹配的 spec 文件: {package}，可能是个全新的包。")

        upstream_pkg_dir = self.find_upstream_pkg_dir(upstream_name)
        if not upstream_pkg_dir:
            # 如果 ros-oe-upstream-init 没有生成该包（通常是因为它是非 ROS 包的底层依赖）
            # 我们不应该直接报错退出，而是把它标记为需要 Agent 介入处理的特殊任务
            self.agent_requests.append({
                "package": package,
                "diff_type": "non_ros_manual_update",
                "old_value": "",
                "new_value": "",
                "old_patches": [],
                "new_patches": [],
                "spec_file": str(old_spec) if old_spec else "",
                "git_diff": "",
                "reason": "ros-oe-upstream-init 未生成该包(可能是非ROS系统依赖)。需要 Agent 介入：1. 检查 src-openeuler 是否有此仓；2. 若有，则智能 bump version 并下载最新 tar 包；3. 若无，则从头手写 spec 并打包最新源码。"
            })
            self.logger.warning(f"📝 {package} 未在 upstream 找到，已标记为非 ROS 包，交由 Agent 后续智能分析处理")
            return PackageUpdateResult(package, False, "非 ROS 包或 ros-oe-upstream-init skill 未生成，已交由 Agent 处理", None)

        upstream_spec = self.find_spec_file_for_package(upstream_pkg_dir, package)
        if not upstream_spec:
            return PackageUpdateResult(package, False, "找不到 upstream spec 文件", None)

        if not old_spec:
            self.logger.info(f"🚀 {package} 是全新的包，执行首次导入流程...")
            target_spec = pkg_dir / f"{package}.spec"
            shutil.copy2(upstream_spec, target_spec)
            self.logger.info(f"✅ 导入全新 spec: {target_spec}")
            
            tar_name = self.extract_tar_name(target_spec)
            if tar_name:
                upstream_tar = list(upstream_pkg_dir.glob(tar_name))[0] if list(upstream_pkg_dir.glob(tar_name)) else None
                if upstream_tar:
                    shutil.copy2(upstream_tar, pkg_dir / tar_name)
                    self.logger.info(f"✅ 拷贝新 tar 包: {tar_name}")
                else:
                    self.logger.warning(f"⚠️ 找不到对应的 tar 包: {tar_name}")
            
            result = PackageUpdateResult(package, True, "全新包导入", [])
            result.changes.append({
                "type": "new_package",
                "old_value": "无",
                "new_value": str(target_spec.name),
                "reason": "openEuler 首次导入该包"
            })
            return result

        # 步骤 2: 备份 spec 文件
        if old_spec:
            backup_spec = old_spec.with_suffix('.backup')
            shutil.copy2(old_spec, backup_spec)
            self.logger.info(f"✅ 备份 spec: {old_spec} → {backup_spec}")
        else:
            backup_spec = None
            self.logger.info(f"⚠️ 新包 {package}，无旧 spec 文件，无需备份。")

        try:
            # 步骤 3: 精准清理旧的物理源文件 (tar包等)
            old_sources = self.extract_all_sources(old_spec) if old_spec else []
            for src in old_sources:
                if src:
                    src_path = pkg_dir / Path(src).name
                    if src_path.exists():
                        src_path.unlink()
                        self.logger.info(f"🗑️ 删除旧源文件/tar包: {src_path.name}")

            # 步骤 4: 覆盖 spec 文件
            target_spec = old_spec if old_spec else pkg_dir / f"{package}.spec"
            shutil.copy2(upstream_spec, target_spec)
            self.logger.info(f"✅ 用 upstream spec 覆盖: {target_spec}")
            self.logger.info(f"✅ 覆盖 spec: {upstream_spec} → {old_spec}")

            # 步骤 5: Git diff 分析
            git_diff_output = self.run_git_diff(pkg_dir, package)

            # 步骤 6: 根据差异类型处理决策
            decision = self.make_diff_decision(package, backup_spec, old_spec, git_diff_output)

            if not decision["auto_handle"]:
                # 需要 agent 分析：只记录，不处理
                self.agent_requests.append({
                    "package": package,
                    "diff_type": decision["diff_type"],
                    "old_value": decision["old_value"],
                    "new_value": decision["new_value"],
                    "old_patches": decision["old_patches"],
                    "new_patches": decision["new_patches"],
                    "spec_file": str(old_spec),
                    "git_diff": decision["git_diff_output"],
                    "reason": decision["reason"]
                })
                self.logger.warning(f"📝 {package} 需要 agent 分析，跳过此包")
                return PackageUpdateResult(package, False, f"需要 agent 分析: {decision['reason']}", None)

            # 自动处理补丁
            if decision["diff_type"] == "patch_restore":
                # 恢复补丁
                with open(old_spec, 'a', encoding='utf-8') as f:
                    f.write('\n')
                    for patch in decision["old_patches"]:
                        f.write(patch + '\n')
                self.logger.info(f"✅ 恢复补丁: {len(decision['old_patches'])} 个")

            elif decision["diff_type"] == "patch_copy_new":
                # 拷贝新补丁
                new_patch_files = []
                for patch_name in decision["new_patches"]:
                    # 查找上游补丁文件
                    upstream_patches = list(upstream_pkg_dir.glob(patch_name))
                    if upstream_patches:
                        shutil.copy2(upstream_patches[0], pkg_dir / patch_name)
                        new_patch_files.append(patch_name)
                self.logger.info(f"✅ 拷贝新补丁: {len(new_patch_files)} 个")

            # 步骤 7: 获取真实的源码 tar 包
            # 只拷贝 Source0 指定的 tar 包，避免拷贝 multibuild 仓库中的其他 tar 包
            tar_name = self.extract_tar_name(old_spec)
            source_url = self.get_source0_url(old_spec)
            version = None
            
            # 从 spec 提取版本号，用于 fallback 的上游探测
            try:
                with open(old_spec, 'r') as f:
                    for line in f:
                        if line.startswith("Version:"):
                            version = line.split(':', 1)[1].strip()
                            break
            except Exception:
                pass
            
            tar_downloaded = False
            new_tar_path = pkg_dir / tar_name if tar_name else None

            # 情况A: 如果 Source0 是硬编码的本地名字 (如 %{RosPkgName}-%{version}.tar.gz) 且缺少 URL，但它是 release 仓库
            if source_url and not (source_url.startswith('http://') or source_url.startswith('https://')):
                if '-release' in upstream_name or '_vendor' in upstream_name or package == 'ompl':
                    self.logger.warning(f"⚠️ 检测到 Source0 缺少 URL 且可能是 GBP(release) 仓库，尝试智能推断真实源码的 URL...")
                    if version:
                        guessed_url = self.try_fetch_real_source(package, version)
                        if guessed_url:
                            self.logger.info(f"🎯 智能推断找到上游源码 URL: {guessed_url}")
                            source_url = guessed_url

            # 优先使用 URL 下载真实的源码包
            if source_url and (source_url.startswith('http://') or source_url.startswith('https://')) and new_tar_path:
                self.logger.info(f"🌐 检测到源码 URL: {source_url}")
                if self.download_tarball(source_url, new_tar_path):
                    if self.verify_tarball(new_tar_path):
                        self.logger.info(f"✅ 成功下载并验证了真实的源码 tar 包: {tar_name}")
                        tar_downloaded = True
                        
                        # 核心修改：剥离 URL，只保留文件名，强制走本地 tar 包
                        self.strip_source_url_in_spec(old_spec)
                        self.logger.info(f"✅ 已将 spec 文件中的 Source0 URL 替换为本地文件名，防止 EUR 联网下载失败")
                    else:
                        self.logger.warning(f"⚠️ 下载的 tar 包似乎不包含源码 (缺少 CMakeLists.txt / package.xml)！可能是空包或 GBP 仓库！")
                        # 即使缺少，我们也留作备用，可能打包结构特殊
                        tar_downloaded = True
                else:
                    self.logger.warning(f"⚠️ URL 下载源码失败，将回退到拷贝本地打包的 tar 包")

            # 回退逻辑：如果下载失败、无 URL，则从 upstream_pkg_dir 拷贝
            if not tar_downloaded and tar_name:
                tar_path = upstream_pkg_dir / tar_name
                if tar_path.exists():
                    shutil.copy2(tar_path, new_tar_path)
                    self.logger.info(f"✅ 拷贝本地由 ros-oe-upstream-init 打包的备用 tar 包: {tar_name}")
                    
                    if not self.verify_tarball(new_tar_path):
                        self.logger.warning(f"⚠️ 本地 tar 包内似乎没有 CMakeLists.txt 等源码文件，这可能是因为该包是 GBP (xxx-release) 仓库！传到 EUR 可能会构建失败！")
                else:
                    self.logger.error(f"❌ upstream 目录中未找到 tar 包: {tar_path}")
                    self.logger.error(f"   目录中的文件: {[f.name for f in upstream_pkg_dir.iterdir() if f.is_file()]}")
            elif not tar_name:
                self.logger.warning(f"⚠️ 无法从 spec 文件提取 tar 包名，跳过获取")

            if new_tar_path and new_tar_path.exists():
                # 动态探测并修复解压目录 (-n 参数)
                actual_top_dir = self.get_tarball_top_dir(new_tar_path)
                if actual_top_dir:
                    self.logger.info(f"🔍 探测到 tar 包真实的解压根目录为: {actual_top_dir}")
                    self.fix_autosetup_dir(old_spec, actual_top_dir)
                    self.logger.info(f"✅ 已自动修正 spec 文件中的 %autosetup -n 参数")

                # ⚠️ 立即检测大文件并自动前置切分
                self.handle_large_tarball(new_tar_path, old_spec)

            # 步骤 8: 物理清理不再使用的旧补丁文件 (仅在 auto_handle=True 时执行)
            if decision["auto_handle"]:
                current_patches_in_dir = list(pkg_dir.glob("*.patch"))
                expected_new_patches = [p.split()[1].strip() if len(p.split())>1 else p for p in decision["new_patches"]]
                for p_file in current_patches_in_dir:
                    if p_file.name not in expected_new_patches:
                        # [BUGFIX for Monorepos]: Do NOT blindly delete patches, they might be used by other specs in the same repo.
                        self.logger.info(f"⏭️ 发现未在当前 spec 中使用的补丁文件，但由于是 monorepo 结构保留不删除: {p_file.name}")

            # 步骤 9: 删除备份文件
            backup_spec.unlink()
            self.logger.info(f"🗑️ 清理备份: {backup_spec}")

            # 步骤 9: 记录差异点
            changes = {
                "package": package,
                "changes": [
                    {
                        "type": decision["diff_type"],
                        "reason": decision["reason"]
                    }
                ],
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.changes_log.append(changes)

            self.logger.success(f"{package} 更新成功")
            return PackageUpdateResult(package, True, None, changes)

        except Exception as e:
            # 发生错误时恢复备份
            if backup_spec and backup_spec.exists():
                shutil.copy2(backup_spec, old_spec)
            self.logger.error(f"❌ {package} 更新失败: {e}")
            import traceback
            self.logger.error(f"发生异常: {traceback.format_exc()}")
            return PackageUpdateResult(package, False, str(e), None)

    def update_all_packages(self, package_list_file: Path):
        """更新所有包"""

        # 验证目录
        if not self.validate_directories(package_list_file):
            self.logger.error("目录验证失败，退出")
            return

        # 读取包列表
        packages = self.read_package_list(package_list_file)

        self.logger.info(f"\n{'='*60}")
        self.logger.info("开始批量更新包")
        self.logger.info(f"{'='*60}")

        # 逐个处理包
        for i, package in enumerate(packages, 1):
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"正在处理包: {package} [{i}/{len(packages)}]")
            self.logger.info(f"{'='*60}")

            # 处理单个包
            result = self.update_single_package(package)

            if result.success:
                self.success_list.append(result.package)
                self.logger.success(f"✅ {result.package} 更新成功")
            else:
                self.failure_list.append({
                    "package": result.package,
                    "error": result.error_message
                })
                self.logger.error(f"❌ {result.package} 更新失败: {result.error_message}")

        # 生成汇总报告
        self.generate_summary_report()
        self.generate_agent_analysis_request()

        self.logger.info(f"\n{'='*60}")
        self.logger.info("📊 更新结果摘要")
        self.logger.info(f"{'='*60}")
        self.logger.info(f"总处理包数: {len(packages)}")
        self.logger.success(f"成功: {len(self.success_list)}")
        if self.failure_list:
            self.logger.error(f"失败: {len(self.failure_list)}")
            for fail in self.failure_list:
                self.logger.info(f"  - {fail['package']}: {fail['error']}")

        if self.agent_requests:
            self.logger.warning(f"需要 agent 分析: {len(self.agent_requests)}")
            for req in self.agent_requests:
                self.logger.info(f"  - {req['package']}: {req['reason']}")

        self.logger.success(f"所有输出文件已生成到: {self.openeuler_dir}")

    def generate_summary_report(self):
        """生成汇总报告"""

        state_dir = self.openeuler_dir.parent / ".tmp" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        # 生成成功列表
        if self.success_list:
            success_file = state_dir / "local_update_success.txt"
            with open(success_file, 'w', encoding='utf-8') as f:
                for pkg in self.success_list:
                    f.write(f"{pkg}\n")

        # 生成失败列表
        if self.failure_list:
            failure_file = state_dir / "local_update_failure.txt"
            with open(failure_file, 'w', encoding='utf-8') as f:
                for fail in self.failure_list:
                    f.write(f"{fail['package']}: {fail['error']}\n")

    def generate_agent_analysis_request(self):
        """生成需要 agent 分析的请求"""

        if not self.agent_requests:
            return

        state_dir = self.openeuler_dir.parent / ".tmp" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        request_file = state_dir / "agent_analysis_request.json"
        analysis_request = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_packages": len(self.success_list) + len(self.failure_list),
            "auto_handled": len(self.success_list),
            "need_analysis": len(self.agent_requests),
            "analysis_requests": self.agent_requests
        }

        with open(request_file, 'w', encoding='utf-8') as f:
            json.dump(analysis_request, f, indent=2, ensure_ascii=False)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='ROS Package Local Update Script')
    parser.add_argument('--openeuler-dir', type=str, required=False,
                       help='openEuler 仓库目录')
    parser.add_argument('--upstream-dir', type=str, required=False,
                       help='upstream tar 包目录')
    parser.add_argument('--package-list-file', type=str, required=False,
                       help='ROS 包列表文件路径')
                       
    # 允许 Agent 单独调用切分功能
    parser.add_argument('--split-tarball', type=str, required=False, help='独立执行: 切分指定的巨型 tar 包文件路径')
    parser.add_argument('--spec', type=str, required=False, help='独立执行: 指定需要修改的 spec 文件路径')

    args = parser.parse_args()

    if args.split_tarball and args.spec:
        tar_path = Path(args.split_tarball)
        spec_path = Path(args.spec)
        if tar_path.exists() and spec_path.exists():
            updater = ROSPackageUpdater(Path('.'), Path('.'), Path('/dev/null'))
            updater.logger.info(f"单独执行大文件切分任务: {tar_path}")
            updater.handle_large_tarball(tar_path, spec_path)
        else:
            print("Error: tarball or spec file does not exist.")
        return

    if not args.openeuler_dir or not args.upstream_dir or not args.package_list_file:
        parser.error("执行批量更新时，必须提供 --openeuler-dir, --upstream-dir 和 --package-list-file")

    # 转换为 Path 对象
    openeuler_dir = Path(args.openeuler_dir)
    upstream_dir = Path(args.upstream_dir)
    package_list_file = Path(args.package_list_file)

    # 创建日志文件路径
    log_dir = openeuler_dir.parent / ".tmp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "local_update.log"

    # 创建更新器并执行
    updater = ROSPackageUpdater(openeuler_dir, upstream_dir, log_file)
    updater.update_all_packages(package_list_file)


if __name__ == '__main__':
    main()
