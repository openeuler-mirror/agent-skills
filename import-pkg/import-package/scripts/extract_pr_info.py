#!/usr/bin/env python3
"""
GitCode PR 信息提取脚本
用于提取 GitCode 上 Pull Request 的修改信息
基于 GitCode API v5 文档: https://docs.gitcode.com/docs/apis/
"""

import json
import requests
import sys
from typing import Dict, List, Optional


class GitCodeClient:
    """GitCode API 客户端"""

    def __init__(self, config_path: str = "config.json"):
        """初始化客户端

        Args:
            config_path: 配置文件路径
        """
        self.config = self._load_config(config_path)
        self.token = self.config["gitcode"]["token"]
        self.base_url = self.config["gitcode"]["base_url"]
        # 使用 PRIVATE-TOKEN 认证方式
        self.headers = {
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json"
        }

    def _load_config(self, config_path: str) -> Dict:
        """加载配置文件"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"错误: 配置文件 {config_path} 不存在")
            sys.exit(1)

    def get_pull_requests(self, owner: str, repo: str, state: str = "open") -> List[Dict]:
        """获取仓库的 Pull Request 列表

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            state: PR 状态 (open, closed, merged, all)

        Returns:
            PR 列表
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
        params = {"state": state}

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"错误: 获取 PR 列表失败 - {e}")
            if hasattr(e.response, 'text'):
                print(f"响应内容: {e.response.text[:500]}")
            return []

    def get_pull_request_details(self, owner: str, repo: str, number: int) -> Optional[Dict]:
        """获取特定 Pull Request 的详细信息

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            number: PR 编号

        Returns:
            PR 详细信息
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}"

        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"错误: 获取 PR 详情失败 - {e}")
            if hasattr(e.response, 'text'):
                print(f"响应内容: {e.response.text[:500]}")
            return None

    def get_pull_request_files(self, owner: str, repo: str, number: int) -> Optional[List[Dict]]:
        """获取 Pull Request 的文件变更列表

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            number: PR 编号

        Returns:
            文件变更列表
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}/files"

        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"错误: 获取 PR 文件变更失败 - {e}")
            if hasattr(e.response, 'text'):
                print(f"响应内容: {e.response.text[:500]}")
            return None

    def get_pull_request_commits(self, owner: str, repo: str, number: int) -> Optional[List[Dict]]:
        """获取 Pull Request 的提交列表

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            number: PR 编号

        Returns:
            提交列表
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}/commits"

        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"错误: 获取 PR 提交列表失败 - {e}")
            if hasattr(e.response, 'text'):
                print(f"响应内容: {e.response.text[:500]}")
            return None


def print_pr_info(pr: Dict):
    """打印 PR 基本信息"""
    print(f"\n{'='*60}")
    print(f"标题: {pr.get('title', 'N/A')}")
    print(f"编号: #{pr.get('number', 'N/A')}")
    print(f"状态: {pr.get('state', 'N/A')}")
    print(f"作者: {pr.get('user', {}).get('login', 'N/A')}")
    print(f"源分支: {pr.get('head', {}).get('ref', 'N/A')}")
    print(f"目标分支: {pr.get('base', {}).get('ref', 'N/A')}")
    print(f"创建时间: {pr.get('created_at', 'N/A')}")
    print(f"更新时间: {pr.get('updated_at', 'N/A')}")
    print(f"合并状态: {'已合并' if pr.get('merged') else '未合并'}")
    print(f"{'='*60}")


def print_pr_files(files: List[Dict], show_diff: bool = True):
    """打印 PR 文件变更信息

    Args:
        files: 文件变更列表
        show_diff: 是否显示详细的 diff 内容
    """
    if not files:
        return

    print(f"\n变更文件数: {len(files)}")
    print(f"\n文件列表:")
    for idx, file in enumerate(files, 1):
        filename = file.get('filename', 'N/A')
        status = file.get('status', 'N/A')
        additions = file.get('additions', 0)
        deletions = file.get('deletions', 0)
        changes = file.get('changes', 0)

        print(f"\n[{idx}] {filename}")
        print(f"    状态: {status}, 变更: +{additions}/-{deletions} (共{changes}行)")

        # 显示详细的 diff 内容
        if show_diff:
            patch_content = None
            # 处理不同的 patch 数据格式
            if 'patch' in file:
                patch_data = file['patch']
                if isinstance(patch_data, dict) and 'diff' in patch_data:
                    patch_content = patch_data['diff']
                elif isinstance(patch_data, str):
                    patch_content = patch_data

            if patch_content:
                print(f"\n    详细修改内容:")
                print("    " + "=" * 70)
                patch_lines = patch_content.split('\n')
                for line in patch_lines:
                    # 为不同类型的行添加标记
                    if line.startswith('+') and not line.startswith('+++'):
                        print(f"    + {line[1:]}")  # 新增行（绿色标记）
                    elif line.startswith('-') and not line.startswith('---'):
                        print(f"    - {line[1:]}")  # 删除行（红色标记）
                    elif line.startswith('@@'):
                        print(f"    {line}")  # 位置信息
                    else:
                        print(f"      {line}")  # 上下文行
                print("    " + "=" * 70)
            else:
                print(f"    (无 diff 内容)")
                # 如果是新增文件，尝试获取完整内容
                if status == 'added' and 'raw_url' in file:
                    print(f"    文件 URL: {file['raw_url']}")


def print_pr_commits(commits: List[Dict]):
    """打印 PR 提交信息"""
    if not commits:
        return

    print(f"\n提交数量: {len(commits)}")
    print(f"\n提交列表:")
    for commit in commits:
        sha = commit.get('sha', 'N/A')[:7]
        message = commit.get('commit', {}).get('message', 'N/A').split('\n')[0]
        author = commit.get('commit', {}).get('author', {}).get('name', 'N/A')
        date = commit.get('commit', {}).get('author', {}).get('date', 'N/A')

        print(f"  - {sha}: {message}")
        print(f"    作者: {author}, 时间: {date}")


def main():
    """主函数"""
    if len(sys.argv) < 4:
        print("用法: python extract_pr_info.py <owner> <repo> <pr_number> [--no-diff]")
        print("示例: python extract_pr_info.py openeuler community 7083")
        print("选项:")
        print("  --no-diff  不显示详细的 diff 内容")
        sys.exit(1)

    owner = sys.argv[1]
    repo = sys.argv[2]
    pr_number = int(sys.argv[3])
    show_diff = '--no-diff' not in sys.argv

    client = GitCodeClient()

    print(f"正在获取 {owner}/{repo} 的 PR #{pr_number} 信息...")

    # 获取 PR 详情
    pr_details = client.get_pull_request_details(owner, repo, pr_number)
    if pr_details:
        print_pr_info(pr_details)

    # 获取 PR 文件变更
    pr_files = client.get_pull_request_files(owner, repo, pr_number)
    if pr_files:
        print_pr_files(pr_files, show_diff=show_diff)

    # 获取 PR 提交列表
    pr_commits = client.get_pull_request_commits(owner, repo, pr_number)
    if pr_commits:
        print_pr_commits(pr_commits)

    # 保存完整信息到文件
    if pr_details or pr_files or pr_commits:
        output_data = {
            "details": pr_details,
            "files": pr_files,
            "commits": pr_commits
        }
        output_file = f"pr_{pr_number}_info.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n完整 PR 信息已保存到: {output_file}")


if __name__ == "__main__":
    main()
