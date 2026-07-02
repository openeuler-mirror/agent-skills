#!/bin/bash

# 检查所有仓库的 Git 状态
# 用法: ./check_workspace_status.sh

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
echo "检查所有仓库的 Git 状态"
echo "========================================"

for repo in "${REPOS[@]}"; do
  repo_dir="$BASE_DIR/$repo"
  if [ -d "$repo_dir" ]; then
    cd "$repo_dir"
    staged=$(git diff --staged --name-only 2>/dev/null | wc -l)
    unstaged=$(git diff --name-only 2>/dev/null | wc -l)
    untracked=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l)

    echo "$repo:"
    echo "  Staged: $staged, Unstaged: $unstaged, Untracked: $untracked"
  else
    echo "$repo: 目录不存在"
  fi
done
