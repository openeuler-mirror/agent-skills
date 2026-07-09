#!/usr/bin/env python3
"""
Fork src-openeuler repositories to personal GitCode account and clone locally.

Usage:
    python fork_repos.py --input packages.txt --workdir /path/to/workspace
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import requests
import yaml


# GitCode API configuration
GITCODE_API_BASE = "https://gitcode.com/api/v5"
GITCODE_WEB_BASE = "https://gitcode.com"

# Default paths
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "gitcode"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "fork-src-openeuler"
CACHE_FILE_NAME = "package_repo_map.json"


class Config:
    """GitCode configuration."""

    def __init__(self, config_path: Path):
        self.username: str = ""
        self.token: str = ""
        self._load(config_path)

    def _load(self, config_path: Path) -> None:
        """Load configuration from YAML file."""
        if not config_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {config_path}\n"
                "请创建配置文件，格式如下:\n"
                "username: your_username\n"
                "token: your_api_token"
            )

        with open(config_path, "r") as f:
            data = yaml.safe_load(f)

        self.username = data.get("username", "")
        self.token = data.get("token", "")

        if not self.username or not self.token:
            raise ValueError(
                f"配置文件缺少必要字段: {config_path}\n"
                "请确保包含 username 和 token"
            )


class Cache:
    """Package to repository name mapping cache."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / CACHE_FILE_NAME
        self.mapping: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load cache from file."""
        if self.cache_file.exists():
            with open(self.cache_file, "r") as f:
                data = json.load(f)
                self.mapping = data.get("mapping", {})

    def _save(self) -> None:
        """Save cache to file."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "mapping": self.mapping,
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.cache_file, "w") as f:
            json.dump(data, f, indent=2)

    def _normalize(self, name: str) -> str:
        return name.replace('_', '-')
        
    def get(self, package_name: str) -> Optional[str]:
        """Get repository name from cache (handles hyphen/underscore mismatches)."""
        # Exact match
        res = self.mapping.get(package_name)
        if res: return res
        
        # Match normalized
        norm_pkg = self._normalize(package_name)
        for k, v in self.mapping.items():
            if self._normalize(k) == norm_pkg:
                return v
        return None

    def set(self, package_name: str, repo_name: str) -> None:
        """Set repository name in cache."""
        self.mapping[package_name] = repo_name
        self._save()


class GitCodeAPI:
    """GitCode API client."""

    def __init__(self, config: Config, verbose: bool = False, repo_dir: Path = None):
        self.config = config
        self.verbose = verbose
        self.repo_dir = repo_dir
        self.session = requests.Session()
        self.session.params = {"access_token": config.token}

    def _request(
        self, method: str, endpoint: str, **kwargs
    ) -> Optional[requests.Response]:
        """Make API request with retry logic."""
        url = f"{GITCODE_API_BASE}{endpoint}"
        params = kwargs.pop("params", {})
        params["access_token"] = self.config.token

        for attempt in range(3):
            try:
                response = self.session.request(
                    method, url, params=params, timeout=30, **kwargs)
                # Don't raise for 4xx errors, let caller handle them
                if response.status_code >= 500:
                    response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                if attempt < 2:
                    if self.verbose:
                        print(f"    重试 {attempt + 1}/3: {e}")
                    time.sleep(5)
                else:
                    raise
        return None

    def check_repo_exists(self, owner: str, repo_name: str) -> bool:
        """Check if repository exists."""
        response = self._request("GET", f"/repos/{owner}/{repo_name}")
        if response is None:
            return False
        # 200 = exists, 404/400 = not found
        return response.status_code == 200

    def search_repo(self, package_name: str) -> Optional[str]:
        """Search for repository by package name in src-openeuler using central skill.py"""
        import subprocess
        import os
        


        # Try ground truth from local output/repo/ first
        if self.repo_dir and self.repo_dir.exists():
            import glob
            # check if <repo_dir>/*/<pkg>.spec exists
            spec_files = glob.glob(str(self.repo_dir / "*" / f"{package_name}.spec"))
            if not spec_files:
                spec_files = glob.glob(str(self.repo_dir / "*" / f"{package_name.replace('-', '_')}.spec"))
            if not spec_files:
                spec_files = glob.glob(str(self.repo_dir / "*" / f"{package_name.replace('_', '-')}.spec"))
                
            if spec_files:
                repo_name = Path(spec_files[0]).parent.name
                # verify it actually exists on src-openeuler
                if self.check_repo_exists("src-openeuler", repo_name):
                    return repo_name

        # Try exact match first
        if self.check_repo_exists("src-openeuler", package_name):
            return package_name

        # Try exact match with underscores instead of hyphens
        alt_pkg_name = package_name.replace('-', '_')
        if alt_pkg_name != package_name and self.check_repo_exists("src-openeuler", alt_pkg_name):
            return alt_pkg_name

        # Search API
        response = self._request(
            "GET", "/search/repositories", params={"q": package_name, "owner": "src-openeuler"}
        )
        if response and response.status_code == 200:
            results = response.json()
            for repo in results:
                full_name = repo.get("full_name", "")
                if full_name.startswith("src-openeuler/"):
                    repo_name = repo.get("path", "")
                    
                    # Verify this repo contains the corresponding .spec file
                    spec_exists = False
                    for branch in ["humble", "master", "main"]:
                        url = f"/repos/src-openeuler/{repo_name}/contents/{package_name}.spec"
                        check_res = self._request("GET", url, params={"ref": branch})
                        if check_res and check_res.status_code == 200:
                            spec_exists = True
                            break
                        
                        # Fallback: check underscore version
                        if alt_pkg_name != package_name:
                            url = f"/repos/src-openeuler/{repo_name}/contents/{alt_pkg_name}.spec"
                            check_res = self._request("GET", url, params={"ref": branch})
                            if check_res and check_res.status_code == 200:
                                spec_exists = True
                                break
                                
                    if spec_exists:
                        return repo_name
                        
            # If search returns results but none pass verification, return None to prevent wrong mappings
            
        return None

    def check_fork_exists(self, repo_name: str) -> bool:
        """Check if fork already exists in user's account."""
        return self.check_repo_exists(self.config.username, repo_name)

    def create_fork(self, repo_name: str) -> Tuple[bool, str]:
        """Create fork of src-openeuler repository."""
        endpoint = f"/repos/src-openeuler/{repo_name}/forks"
        response = self._request("POST", endpoint)

        if response is None:
            return False, "API 请求失败"

        # Success responses: 200 (returns repo info), 201 (created)
        if response.status_code in (200, 201):
            try:
                data = response.json()
                full_name = data.get("full_name", "")
                return True, f"Fork 创建成功: {full_name}"
            except:
                return True, "Fork 创建成功"

        # Already exists
        if response.status_code == 409:
            return True, "Fork 已存在"

        # Rate limit
        if response.status_code == 429:
            try:
                error_data = response.json()
                error_msg = error_data.get("error_message", response.text)
            except:
                error_msg = response.text
            return False, f"API 速率限制: {error_msg}"

        # Other errors
        try:
            error_data = response.json()
            error_msg = error_data.get("message", error_data.get("error_message", response.text))
        except:
            error_msg = response.text

        return False, f"Fork 失败: {error_msg}"

    def wait_for_fork(self, repo_name: str, timeout: int = 30) -> bool:
        """Wait for fork to be ready."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.check_fork_exists(repo_name):
                return True
            time.sleep(2)
        return False


class GitOperations:
    """Git operations for cloning and managing repositories."""

    def __init__(self, config: Config, workdir: Path, verbose: bool = False):
        self.config = config
        # Convert to absolute path to avoid nested directory issues
        self.workdir = workdir.resolve()
        self.forked_dir = self.workdir / "src-openeuler-forked"
        self.verbose = verbose

    def _run_git(self, args: List[str], cwd: Optional[Path] = None) -> Tuple[bool, str]:
        """Run git command."""
        cmd = ["git"] + args
        if self.verbose:
            print(f"    执行: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            output = result.stdout.strip() if result.stdout.strip() else result.stderr.strip()

            if result.returncode != 0:
                # Combine stdout and stderr for better error message
                error_output = output if output else result.stderr.strip()
                return False, error_output

            return True, output
        except subprocess.TimeoutExpired:
            return False, f"git 命令超时"
        except Exception as e:
            return False, f"git 命令执行失败: {str(e)}"

    def clone_or_pull(self, repo_name: str) -> Tuple[bool, str]:
        """Clone repository or pull if already exists."""
        import time
        repo_path = self.forked_dir / repo_name
        origin_url = f"git@gitcode.com:{self.config.username}/{repo_name}.git"
        upstream_url = f"git@gitcode.com:src-openeuler/{repo_name}.git"

        # Determine branches to try
        branches_to_try = ["humble", "master", "main"]

        if repo_path.exists():
            # Already exists
            self._run_git(["fetch", "upstream"], cwd=repo_path)
            
            # Find which branch we are currently on or should be on
            current_branch = None
            success, output = self._run_git(["branch", "--show-current"], cwd=repo_path)
            if success and output:
                current_branch = output
            else:
                current_branch = "humble" # fallback
                
            # Try checking out preferred branches if we are not on one of them
            if current_branch not in branches_to_try:
                for b in branches_to_try:
                    succ, _ = self._run_git(["checkout", b], cwd=repo_path)
                    if succ:
                        current_branch = b
                        break

            # Now pull latest from upstream for the current branch
            success, output = self._run_git(["pull", "upstream", current_branch], cwd=repo_path)
            if success:
                return True, f"git pull 完成 (分支: {current_branch})"
            else:
                return False, f"git pull {current_branch} 失败: {output}"

        else:
            # Need to clone
            self.forked_dir.mkdir(parents=True, exist_ok=True)

            # Retry clone up to 5 times (GitCode forks take time to become cloneable)
            max_clone_retries = 5
            clone_success = False
            clone_output = ""
            for i in range(max_clone_retries):
                success, output = self._run_git(
                    ["clone", origin_url, str(repo_path)],
                    cwd=self.forked_dir,
                )
                if success:
                    clone_success = True
                    break
                else:
                    clone_output = output
                    if i < max_clone_retries - 1:
                        if self.verbose:
                            print(f"      clone 尚未准备好，等待 5 秒后重试 ({i+1}/{max_clone_retries})...")
                        time.sleep(5)

            if not clone_success:
                return False, f"git clone 失败 (尝试了 {max_clone_retries} 次): {clone_output}"

            # Configure remotes safely
            self._run_git(["remote", "rename", "origin", "upstream"], cwd=repo_path)
            self._run_git(["remote", "add", "origin", origin_url], cwd=repo_path)
            self._run_git(["remote", "set-url", "upstream", upstream_url], cwd=repo_path)
            
            # Fetch upstream to get all refs
            self._run_git(["fetch", "upstream"], cwd=repo_path)

            # Try checking out preferred branches
            checked_out = False
            final_branch = "未知"
            for b in branches_to_try:
                # Check if branch exists on upstream
                succ_check, _ = self._run_git(["ls-remote", "--heads", "upstream", b], cwd=repo_path)
                if succ_check:
                    # Try checkout existing local branch
                    succ_co, _ = self._run_git(["checkout", b], cwd=repo_path)
                    if not succ_co:
                        # Try creating local branch tracking upstream
                        succ_co, _ = self._run_git(["checkout", "-b", b, f"upstream/{b}"], cwd=repo_path)
                    
                    if succ_co:
                        checked_out = True
                        final_branch = b
                        break

            if checked_out:
                # We are on a valid branch, try to pull to be sure
                self._run_git(["pull", "upstream", final_branch], cwd=repo_path)
                return True, f"clone 完成，已切换并同步 {final_branch} 分支"
            else:
                # Could not checkout any preferred branch, fallback to whatever default branch it is
                succ, default_branch = self._run_git(["branch", "--show-current"], cwd=repo_path)
                if succ and default_branch:
                    return True, f"clone 完成，但未能找到 humble/master/main，保留在默认分支 {default_branch}"
                return False, "clone 完成，但所有分支切换失败且无默认分支"


def print_header(title: str) -> None:
    """Print section header."""
    width = 80
    print()
    print("=" * width)
    print(f"{title:^{width}}")
    print("=" * width)


def print_subheader(title: str) -> None:
    """Print subsection header."""
    width = 80
    print()
    print("-" * width)
    print(f"  {title}")
    print("-" * width)


def main():
    parser = argparse.ArgumentParser(
        description="Fork src-openeuler repositories to personal GitCode account"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input file containing package names (one per line)",
    )
    parser.add_argument(
        "--workdir", "-w",
        default=".",
        help="Working directory (default: current directory)",
    )
    parser.add_argument(
        "--config", "-c",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"GitCode config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help=f"Cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually doing it",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show verbose output",
    )
    parser.add_argument(
        "--force-update",
        action="store_true",
        help="Force update all repositories (git pull)",
    )

    parser.add_argument(
        "--upstream-dir",
        help="Path to ros-oe-upstream-init output/repo directory",
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = Config(Path(args.config))
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}")
        sys.exit(1)

    # Load cache
    cache = Cache(Path(args.cache_dir))

    # Initialize API and Git operations
    repo_dir = Path(args.upstream_dir) if args.upstream_dir else None
    api = GitCodeAPI(config, args.verbose, repo_dir=repo_dir)
    git_ops = GitOperations(config, Path(args.workdir), args.verbose)

    # Read package list
    input_file = Path(args.input)
    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        sys.exit(1)

    with open(input_file, "r") as f:
        packages = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    # Print header
    print_header("Fork Src-openEuler Repository")
    print(f"配置文件: {args.config}")
    print(f"用户名: {config.username}")
    print(f"输入文件: {args.input} ({len(packages)} 个包)")
    print(f"工作目录: {Path(args.workdir).absolute()}")

    if args.dry_run:
        print("\n*** DRY RUN 模式 - 不会执行实际操作 ***")

    # Step 1: Map package names to repository names
    print_subheader("Step 1: 包名 → 仓库名映射")

    package_to_repo: Dict[str, str] = {}
    failed_mappings: List[str] = []

    for pkg in packages:
        # Check cache first
        cached = cache.get(pkg)
        if cached:
            package_to_repo[pkg] = cached
            if args.verbose:
                print(f"  {pkg} → {cached} (缓存命中)")
            continue

        # Search for repository
        repo_name = api.search_repo(pkg)
        if repo_name:
            package_to_repo[pkg] = repo_name
            cache.set(pkg, repo_name)
            print(f"  {pkg} → {repo_name} (搜索匹配)")
        else:
            failed_mappings.append(pkg)
            print(f"  {pkg} → 未找到对应仓库")

    # De-duplicate repositories
    repos: Set[str] = set(package_to_repo.values())
    print(f"\n  去重后仓库数量: {len(repos)}")

    if failed_mappings:
        print(f"\n  警告: {len(failed_mappings)} 个包无法映射到仓库:")
        for pkg in failed_mappings:
            print(f"    - {pkg}")

    # Step 2: Fork and Clone
    print_subheader("Step 2: Fork 和 Clone")

    success_count = 0
    fail_count = 0
    skip_count = 0
    results: List[Dict] = []

    for i, repo_name in enumerate(sorted(repos), 1):
        print(f"\n[{i}/{len(repos)}] {repo_name}")

        if args.dry_run:
            print("      [DRY RUN] 将检查 fork 并 clone")
            continue

        # Check if fork exists
        fork_exists = api.check_fork_exists(repo_name)

        if fork_exists:
            print("      ✓ Fork 已存在，跳过")
            skip_count += 1
        else:
            # Create fork (with rate limit handling)
            # GitCode limits: 1 fork per minute
            # Track successful forks to add delay between them
            if success_count > 0 and not args.dry_run:
                wait_time = 61  # Wait 61 seconds to be safe
                print(f"      等待 {wait_time} 秒以符合 API 速率限制...")
                time.sleep(wait_time)

            success, message = api.create_fork(repo_name)
            if success:
                print(f"      ✓ {message}")
                # Wait for fork to be ready
                if not api.wait_for_fork(repo_name):
                    print("      ⚠ Fork 创建中，可能需要稍后重试 clone")
            else:
                print(f"      ✗ {message}")
                fail_count += 1
                results.append({"repo": repo_name, "status": "fail", "message": message})
                continue

        # Clone or pull
        success, message = git_ops.clone_or_pull(repo_name)
        if success:
            print(f"      ✓ {message}")
            success_count += 1
            results.append({"repo": repo_name, "status": "success", "message": message})
        else:
            print(f"      ✗ {message}")
            fail_count += 1
            results.append({"repo": repo_name, "status": "fail", "message": message})

    # Print summary
    print_header("处理完成")
    print(f"成功: {success_count}")
    print(f"失败: {fail_count}")
    print(f"跳过 (已 fork): {skip_count}")
    print(f"\n输出目录: {git_ops.forked_dir}")

    if fail_count > 0:
        print("\n失败列表:")
        for r in results:
            if r["status"] == "fail":
                print(f"  - {r['repo']}: {r['message']}")
        print("\n建议: 检查失败的项目并手动处理，或稍后重试")

    print("=" * 80)

    # Exit with error code if any failures
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
