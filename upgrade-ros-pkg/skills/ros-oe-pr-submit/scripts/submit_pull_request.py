#!/usr/bin/env python3
"""
openEuler ROS PR Manager (GitCode V5 API Edition)

This script provides a clean, robust interface for interacting with the GitCode API V5
specifically tailored for the openEuler ROS upgrade workflow.
It replaces the older, error-prone V4 API implementation and abstracts away the
GitCode platform quirks into a lightweight SDK.
"""

import os
import json
import urllib.request
import urllib.parse
from pathlib import Path
import re
import argparse
import sys

def get_gitcode_token():
    """Retrieve GitCode API token from ~/.config/gitcode"""
    token_file = Path(os.path.expanduser("~/.config/gitcode"))
    if not token_file.exists():
        print(f"Error: Token file not found at {token_file}")
        sys.exit(1)
        
    with open(token_file, "r") as f:
        content = f.read()
        match = re.search(r"token:\s*(.+)", content)
        if not match:
            print("Error: Could not parse token from config file")
            sys.exit(1)
        return match.group(1).strip()

class GitCodeV5Client:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://gitcode.com/api/v5"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "ros-oe-pr-submit/1.0"
        }

    def _request(self, endpoint, method="GET", data=None):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        req_data = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=req_data, headers=self.headers, method=method)
        
        try:
            with urllib.request.urlopen(req) as response:
                content = response.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            error_content = e.read().decode("utf-8")
            try:
                err_json = json.loads(error_content)
                err_msg = err_json.get("message") or err_json.get("error_message") or error_content
            except:
                err_msg = error_content
            raise Exception(f"HTTP {e.code}: {err_msg}")
        except Exception as e:
            raise Exception(f"Request failed: {str(e)}")

    def find_open_pr(self, upstream_owner, repo, author, branch):
        """Find an open PR from the author's fork branch to the upstream repo"""
        try:
            prs = self._request(f"repos/{upstream_owner}/{repo}/pulls?state=open")
            for pr in prs:
                head_user = pr.get("head", {}).get("user", {}).get("login", "")
                head_label = pr.get("head", {}).get("label", "")
                
                # Check either the user matches exactly or the label is author:branch
                if head_user == author or head_label == f"{author}:{branch}":
                    return pr
            return None
        except Exception as e:
            print(f"Warning: Failed to fetch PRs: {e}")
            return None

    def create_pr(self, upstream_owner, repo, title, body, head, base="humble"):
        """Create a new Pull Request"""
        data = {
            "title": title,
            "body": body,
            "head": head,
            "base": base
        }
        return self._request(f"repos/{upstream_owner}/{repo}/pulls", method="POST", data=data)

    def update_pr(self, upstream_owner, repo, pr_number, title=None, body=None):
        """Update an existing Pull Request"""
        data = {}
        if title:
            data["title"] = title
        if body:
            data["body"] = body
        if not data:
            return None
        return self._request(f"repos/{upstream_owner}/{repo}/pulls/{pr_number}", method="PATCH", data=data)


def main():
    parser = argparse.ArgumentParser(description="GitCode PR Manager for openEuler ROS Upgrades (V5)")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")
    
    # Create PR Subcommand
    create_parser = subparsers.add_parser("create", help="Create a new PR")
    create_parser.add_argument("--pkg", required=True, help="Package name (e.g., perception_pcl)")
    create_parser.add_argument("--author", required=True, help="Your GitCode username")
    create_parser.add_argument("--title", required=True, help="PR Title")
    create_parser.add_argument("--desc", required=True, help="PR Description")
    create_parser.add_argument("--branch", default="humble", help="Target/Source branch (default: humble)")
    
    # Update PR Subcommand
    update_parser = subparsers.add_parser("update", help="Update an existing PR")
    update_parser.add_argument("--pkg", required=True, help="Package name")
    update_parser.add_argument("--author", required=True, help="Your GitCode username")
    update_parser.add_argument("--title", help="New PR Title")
    update_parser.add_argument("--desc", help="New PR Description")
    update_parser.add_argument("--branch", default="humble", help="Target/Source branch (default: humble)")
    
    args = parser.parse_args()
    
    if not args.action:
        parser.print_help()
        sys.exit(1)
        
    api = GitCodeV5Client(get_gitcode_token())
    upstream_owner = "src-openeuler"
    repo = args.pkg
    
    print(f"🔍 Checking open PRs for {upstream_owner}/{repo} from {args.author}...")
    existing_pr = api.find_open_pr(upstream_owner, repo, args.author, args.branch)
    
    if args.action == "create":
        if existing_pr:
            pr_number = existing_pr.get("number") or existing_pr.get("iid")
            pr_url = existing_pr.get("html_url") or existing_pr.get("web_url")
            print(f"⚠️ An open PR already exists: {pr_url}")
            print("🚀 Switching to update mode...")
            
            try:
                api.update_pr(upstream_owner, repo, pr_number, title=args.title, body=args.desc)
                print(f"🎉 Successfully updated existing PR #{pr_number}")
            except Exception as e:
                print(f"❌ Failed to update PR: {e}")
                sys.exit(1)
            sys.exit(0)
            
        print("🚀 Creating Pull Request...")
        head = f"{args.author}:{args.branch}"
        try:
            pr = api.create_pr(upstream_owner, repo, args.title, args.desc, head, args.branch)
            pr_url = pr.get("html_url") or pr.get("web_url")
            print(f"🎉 Successfully created PR: {pr_url}")
        except Exception as e:
            print(f"❌ Failed to create PR: {e}")
            sys.exit(1)
            
    elif args.action == "update":
        if not args.title and not args.desc:
            print("❌ Must provide --title or --desc to update")
            sys.exit(1)
            
        if not existing_pr:
            print(f"❌ Could not find any open PRs for {repo} authored by {args.author}")
            sys.exit(1)
            
        pr_number = existing_pr.get("number") or existing_pr.get("iid")
        pr_url = existing_pr.get("html_url") or existing_pr.get("web_url")
        print(f"✅ Found PR #{pr_number}: {pr_url}")
        
        print("🚀 Updating Pull Request...")
        try:
            api.update_pr(upstream_owner, repo, pr_number, title=args.title, body=args.desc)
            print(f"🎉 Successfully updated PR #{pr_number}")
        except Exception as e:
            print(f"❌ Failed to update PR: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
