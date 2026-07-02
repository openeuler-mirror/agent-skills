---
name: ros-oe-repo-fork
description: |
  Batch fork src-openeuler repositories to a personal GitCode account and clone them locally.

  Use Cases:
  - User provides a list of package names (e.g., foonathan_memory_vendor, rclcpp, rclpy).
  - Need to batch fork official openEuler package repositories to a personal account.
  - Need to iterate, develop, and test in personal repositories.
  - Need to prepare a local workspace for subsequent spec merging and building.

  Trigger: Use this skill when the user mentions "fork repo", "batch fork", "fork from src-openeuler", "prepare personal repo", or "get development repo".
---

# Fork Src-openEuler Repository

Batch fork src-openeuler repositories to a personal GitCode account and clone them to a local workspace.

## Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ros-oe-repo-fork Workflow                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   Input: Package name list file (packages.txt)                                │
│         e.g.: foonathan_memory_vendor, rclcpp, rclpy...             │
│                                                                     │
│   Step 1: Read Config                                                   │
│   └── From ~/.config/gitcode read username 和 token                   │
│                                                                     │
│   Step 2: Package Name → Repo Name Mapping                                          │
│   ├── Check local cache (~/.cache/fork-src-openeuler/package_repo_map.json)│
│   ├── Exact match: src-openeuler/<package_name>                        │
│   ├── Search match: GitCode API Search                                     │
│   └── Deduplication: Keep only one repo for multiple packages                                 │
│                                                                     │
│   Step 3: Iterate over repo list                                               │
│   ├── Check if forked → Skip if exists                             │
│   └── Not forked → Call GitCode API to fork                           │
│                                                                     │
│   Step 4: Clone/更新本地Repo                                         │
│   ├── Directory exists → git pull                                          │
│   ├── Directory not exists → git clone + config upstream remote                  │
│   └── Output to: <workdir>/src-openeuler-forked/<repo-name>/           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Configuration File

Config file location：`~/.config/gitcode`

Format（YAML）：
```yaml
username: your_gitcode_username
token: your_gitcode_api_token
```

**Getting GitCode Token**：
1. Login to GitCode → Settings → Access Tokens
2. Create new token, check `api` and `write_repository` permissions
3. Save token to config file

## 输入文件Format

Create a text file with one **package name** per line:

```
# packages.txt Example
foonathan_memory_vendor
rpyutils
iceoryx_hoofs
rosidl_cli
fastcdr
rclcpp
rcl
```

**Note**:
- Input must be package names, not repository names.
- Multiple packages might belong to the same repository (e.g., rclcpp and rcl are both in the rclcpp repo).
- The tool automatically deduplicates; a repository is forked only once.

## Package to Repository Mapping

### Mapping Logic

```
For each package name:
1. Check local cache
   └── Cache hit → Use cached repo name

2. 尝试Exact match
   └── Check if src-openeuler/<package_name> exists
   └── Exists → Record mapping, cache result

3. Search match
   └── 调用 GitCode API Search包名
   └── Take the first matched src-openeuler repository
   └── 记录映射，缓存结果

4. Mapping failed
   └── Record in failure list, continue to the next
```

### Cache File

Cache location：`~/.cache/fork-src-openeuler/package_repo_map.json`

Format：
```json
{
  "foonathan_memory_vendor": "foonathan_memory_vendor",
  "iceoryx_hoofs": "iceoryx",
  "rclcpp": "rclcpp",
  "rcl": "rclcpp",
  "updated_at": "2024-01-15T10:30:00"
}
```

## Usage

### Basic Usage

```bash
# Call script (Python direct execution recommended)
python3 /path/to/fork_repos.py --input packages.txt --workdir /path/to/workspace

# Or via skill invocation
ros-oe-repo-fork --input packages.txt --workdir /path/to/workspace

# Output directory structure (named after repository):
# /path/to/workspace/src-openeuler-forked/
# ├── foonathan_memory_vendor/
# ├── iceoryx/
# ├── rclcpp/        # Contains both rclcpp and rcl packages
# └── fastcdr/
```

### Parameters

| Parameter | Description | Default |
|------|------|--------|
| `--input, -i` | Package list file path | Required |
| `--workdir, -w` | Working directory | Current dir |
| `--config, -c` | GitCode config path | `~/.config/gitcode` |
| `--cache-dir` | Cache directory | `~/.cache/fork-src-openeuler` |
| `--dry-run` | Dry run, show actions without executing | `false` |
| `--verbose, -v` | Verbose output (show git commands) | `false` |
| `--force-update` | Force update all repos (git pull) | `false` |

### 完整使用Example

```bash
# 1. Create package list file
cat > /tmp/packages.txt << EOF
foonathan_memory_vendor
rpyutils
iceoryx_hoofs
fastcdr
rclcpp
rcl
EOF

# 2. Execute fork and clone (with verbose output)
python3 <skill_dir>/ros-oe-repo-fork/scripts/fork_repos.py \\
  --input /tmp/packages.txt \\
  --workdir /tmp/fork-test \\
  --verbose

# 3. Check results
cd /tmp/fork-test/src-openeuler-forked
ls -la
```

## Fork Processing Logic

### Repo Already Forked

If the target repository already exists in the personal account:

1. Skip fork operation
2. 本地Directory exists → 执行 `git pull`
3. 本地Directory not exists → clone 并配置 upstream

### Repo Not Forked

1. 调用 GitCode API 创建 fork
2. Wait for fork completion (poll, max 30 seconds)
3. Clone to local directory
4. Configure git remote

## Git Remote Configuration

Remote configuration for each repo after cloning:

```bash
# origin 指向个人 fork（用于推送）- 使用 SSH Format
git remote add origin git@gitcode.com:<username>/<repo-name>.git

# upstream 指向官方Repo（用于同步上游）- 使用 SSH Format
git remote add upstream git@gitcode.com:src-openeuler/<repo-name>.git
```

**Note**:
- 使用 SSH URL Format，需要提前配置好 SSH Key
- 避免使用 HTTPS Format，否则需要频繁输入用户名和 Token

## Git Branch Handling

The script automatically checks out the `humble` branch and pulls the latest code:

```bash
# For newly cloned repositories
git clone git@gitcode.com:<username>/<repo-name>.git
git checkout humble                    # Checkout humble branch
git pull upstream humble               # From upstream 拉取 humble 分支代码

# For existing repositories
git checkout humble                    # Ensure on humble branch
git pull upstream humble               # Pull latest code
```

**Branch Notes**:
- Defaults to pulling `humble` branch (ROS2 Humble release).
- A local `humble` branch is created tracking `upstream/humble`.
- To change the target branch, modify the branch name in the script.

## SSH Configuration Requirements

SSH keys must be configured before using this script:

```bash
# 1. Generate SSH key (if not already)
ssh-keygen -t ed25519 -C "your_email@example.com"

# 2. View public key content
cat ~/.ssh/id_ed25519.pub

# 3. Add to GitCode
# Login GitCode → Settings → SSH Keys → Add Key
# Paste public key and save

# 4. Test connection
ssh -T git@gitcode.com
# Should see: Hi <username>! You've successfully authenticated...

# 5. Configure SSH agent (optional, avoids password prompt)
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

**SSH Verification**:
```bash
# Test GitCode connection
ssh -T git@gitcode.com

# Test repository cloning (replace with your username/repo)
git clone git@gitcode.com:<username>/test-repo.git /tmp/test-clone
```

## Important Notes

### API Rate Limits

GitCode limits fork operations: **Maximum 1 fork per minute**

- The script automatically waits 61 seconds between consecutive forks.
- Allocate sufficient time when forking many repositories.
- Recommend using `--dry-run` mode first to check operations.

### Git Operation Timeout

- Git commands have a 300-second (5 minute) timeout.
- Clone + pull might take long for large repositories.
- If timeout occurs, manually enter the directory and execute `git pull upstream humble`

### Caching Mechanism

- Package to repo name mapping is cached at `~/.cache/fork-src-openeuler/package_repo_map.json`
- If a repository name changes, delete the cache file and rerun.
- 缓存可以避免重复调用 API Search

### Error Handling

Suggestions when encountering failures:
1. Check error messages to confirm root cause.
2. Manually inspect the failed repository directory.
3. Verify network connectivity and SSH config.
4. Retry later or manually process failed items.

## Repo Search & Branch Strategy (Core Experience)

Adopt different strategies based on package type (ROS / Non-ROS) and its presence in `src-openeuler`. Strictly follow these guidelines when executing scripts or processing repos:

### 1. Existing ROS packages in `src-openeuler`
- **Search Strategy**: Search and fork normally under the `src-openeuler` organization.
- **Target Branch**: Usually checkout and pull the `humble` branch.
- **Notes**: These packages are managed via spec files. The current automation script handles this by default.

### 2. Existing Non-ROS packages in `src-openeuler`
- **Search Strategy**: Search and fork normally under the `src-openeuler` organization.
- **Target Branch**: **Select appropriate branch based on context**. Non-ROS packages usually lack a `humble` branch. The target branch might be `master`, `main`, or a specific LTS branch (e.g., `openEuler-24.03-LTS`).
- **Processing Advice**: If branch checkout (`git checkout humble`) fails, do not give up. Use `git branch -a` to inspect available upstream branches and switch accordingly.

### 3. Packages Not Found in `src-openeuler` (ROS or Non-ROS)
- **Search Strategy**: If not found in `src-openeuler`, search the **`gh_mirrors`** organization (or other source mirrors) on GitCode for the upstream source repo. Fork it to your personal workspace once found.
- **Target Branch**: Create an empty spec-dedicated branch, recommended name: **`openEuler-24.03-spec`**.
- **Execution Steps**:
  ```bash
  # After cloning the forked source repo in personal workspace
  git checkout --orphan openEuler-24.03-spec
  git rm -rf .
  # The branch is now entirely clean/empty, used solely for storing merged .spec files and source tarballs for EUR builds.
  ```
- **Core Logic**: This occurs when ROS packages depend on non-ROS packages not yet in openEuler, or users explicitly provide unintroduced dependencies. We must configure them against `openEuler-24.03-spec` in personal GitCode repos, verify builds via EUR, and consider community submission only after successful validation.

## Multi-Package Repo Handling

```
Input Package List:
  - rclcpp
  - rcl
  - rcl_yaml_param_parser

Mapping Results:
  - rclcpp → rclcpp Repo
  - rcl → rclcpp Repo (Same repo)
  - rcl_yaml_param_parser → rcl_yaml_param_parser Repo

Actual Fork:
  - rclcpp (Once)
  - rcl_yaml_param_parser (Once)

Local Directory:
  src-openeuler-forked/
  ├── rclcpp/           # 包含 rclcpp.spec 和 rcl.spec
  └── rcl_yaml_param_parser/
```

## 错误处理

| Error Condition | Handling Method |
|----------|----------|
| Config file missing | Prompt user to create config and exit |
| Invalid Token | Prompt token permission issue and exit |
| 包名无法映射到Repo | 记录失败，继续处理下一个 |
| Fork API 失败 | 记录错误，继续处理下一个Repo |
| Clone 失败 | 记录错误，继续处理下一个Repo |
| Network Timeout | Retry 3 times, 5-second interval |

Outputs a final success/failure/skipped statistical report.

## 输出Example

```
================================================================================
                         Fork Src-openEuler Repository
================================================================================
Config file: /home/user/.config/gitcode
Username: your-gitcode-username
Input file: packages.txt (10 packages)
Work dir: /home/user/workspace

--------------------------------------------------------------------------------
Step 1: Package Name → Repo Name Mapping
--------------------------------------------------------------------------------
  foonathan_memory_vendor → foonathan_memory_vendor (Exact match)
  rpyutils → rpyutils (Exact match)
  iceoryx_hoofs → iceoryx (Search match)
  rosidl_cli → rosidl_cli (Exact match)
  fastcdr → fastcdr (Exact match)
  rclcpp → rclcpp (Exact match)
  rcl → rclcpp (缓存命中: 同一Repo)

  去重后Repo数量: 6

--------------------------------------------------------------------------------
Step 2: Fork and Clone
--------------------------------------------------------------------------------
[1/6] foonathan_memory_vendor
      ✓ Fork exists, skipped
      ✓ Local dir exists, git pull done
      ✓ Remote config: origin → your-gitcode-username/foonathan_memory_vendor

[2/6] rpyutils
      ✓ Fork created successfully
      ✓ Clone successful
      ✓ Remote config: origin, upstream

[3/6] iceoryx
      ✓ Fork exists, skipped
      ✓ Local dir exists, git pull done
      ✓ Remote config: origin → your-gitcode-username/iceoryx

[4/6] rosidl_cli
      ✗ Fork failed: API rate limit exceeded

[5/6] fastcdr
      ✓ Fork exists, skipped
      ✓ Local dir exists, git pull done

[6/6] rclcpp
      ✓ Fork exists, skipped
      ✓ Local dir exists, git pull done

================================================================================
                              Processing Complete
================================================================================
Success: 5
Failed: 1 (rosidl_cli)
Skipped: 0

Output dir: /home/user/workspace/src-openeuler-forked/
Failure list: rosidl_cli (Fork failed: API rate limit exceeded)

Recommendation: Manually check failed items or retry later
================================================================================
```

## Dependencies

- Python 3.8+
- requests library
- PyYAML library
- Git CLI tools

## Related Skills

- `ros-oe-pkg-prep`: Generate dependency package list
- `ros-oe-pkg-update`: Execute spec merge and upgrade


---
## Output Language Rules

- **Internal Reasoning & Tools**: Use English for all internal thinking, tool inputs, and terminal commands.
- **User-Facing Reports**: **CRITICAL** - Whenever you generate a summary, log file, or report meant for the user to read based on the fork/clone results, you MUST write the content entirely in **Simplified Chinese**.
