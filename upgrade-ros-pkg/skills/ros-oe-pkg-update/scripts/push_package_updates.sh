#!/bin/bash

# 批量提交并推送所有仓库（英文 commit 信息，自动执行）
# 支持 LFS 和 force push
# 用法: ./push_package_updates.sh <base_dir>

BASE_DIR="${1:-/path/to/src-openeuler-forked}"
PKG_LIST_FILE="$2"

if [ -z "$PKG_LIST_FILE" ] || [ ! -f "$PKG_LIST_FILE" ]; then
    echo "使用方法: $0 <base_dir> <package_list_file>"
    echo "注意: 现在必须提供包名列表文件，脚本会自动从 ~/.cache/fork-src-openeuler/package_repo_map.json 读取正确的映射。"
    exit 1
fi

echo "========================================"
echo "读取仓库映射列表..."
REPOS=()
while IFS= read -r pkg || [[ -n "$pkg" ]]; do
    if [[ -z "$pkg" ]] || [[ "$pkg" == \#* ]]; then
        continue
    fi
    
    # 从缓存中查找仓库名
    repo_name=$(python3 -c '
import json, os, sys
pkg = sys.argv[1]
cache_file = os.path.expanduser("~/.cache/fork-src-openeuler/package_repo_map.json")
repo = pkg
if os.path.exists(cache_file):
    try:
        with open(cache_file, "r") as f:
            data = json.load(f).get("mapping", {})
            if pkg in data:
                repo = data[pkg]
            else:
                norm_pkg = pkg.replace("_", "-")
                for k, v in data.items():
                    if k.replace("_", "-") == norm_pkg:
                        repo = v
                        break
    except:
        pass
print(repo)
' "$pkg")
    
    # 去重
    if [[ ! " ${REPOS[@]} " =~ " ${repo_name} " ]]; then
        REPOS+=("$repo_name")
    fi
done < "$PKG_LIST_FILE"

echo "目标仓库数量: ${#REPOS[@]}"
echo "========================================"
echo "Batch Commit and Push All Repositories"
echo "========================================"

success_count=0
failed_count=0
failed_repos=()

for repo in "${REPOS[@]}"; do
  repo_dir="$BASE_DIR/$repo"

  echo ""
  echo "----------------------------------------"
  echo "Processing: $repo"
  echo "----------------------------------------"

  cd "$repo_dir" || {
    echo "✗ Cannot enter directory: $repo_dir"
    failed_count=$((failed_count + 1))
    failed_repos+=("$repo (directory not found)")
    continue
  }

  # 1. Add all changes
  echo "1. Adding all changes..."
  git add -A

  # 2. Extract version information
  main_spec=$(ls *.spec 2>/dev/null | head -1)
  if [ -n "$main_spec" ]; then
    version=$(grep "^Version:" "$main_spec" | awk '{print $2}' | head -1)

    # Generate commit message in English
    commit_msg="Upgrade $repo to version $version

- Update spec files to latest upstream version
- Synchronize tarballs
- Update patches if any

ROS2 Humble version upgrade"
  else
    commit_msg="Upgrade $repo

- Update spec files
- Synchronize tarballs

ROS2 Humble version upgrade"
  fi

  # 3. Commit
  echo "2. Committing changes..."
  echo "Version: $version"

  if git commit -m "$commit_msg"; then
    echo "✓ Commit successful"

    # 4. Push
    echo "3. Pushing to origin humble..."
    if git push origin humble; then
      echo "✓ Push successful"
      success_count=$((success_count + 1))
    else
      # 5. Try force push if normal push fails
      echo "⚠ Push failed, trying with --force..."
      if git push origin humble --force; then
        echo "✓ Force push successful"
        success_count=$((success_count + 1))
      else
        echo "✗ Force push failed"
        failed_count=$((failed_count + 1))
        failed_repos+=("$repo (push failed)")
      fi
    fi
  else
    echo "✗ Commit failed"
    failed_count=$((failed_count + 1))
    failed_repos+=("$repo (commit failed)")
  fi
done

echo ""
echo "========================================"
echo "Batch Commit and Push Complete"
echo "========================================"
echo "Success: $success_count"
echo "Failed: $failed_count"

if [ $failed_count -gt 0 ]; then
  echo ""
  echo "Failed repositories:"
  printf '  - %s\n' "${failed_repos[@]}"
fi
