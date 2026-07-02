---
name: ros-oe-board-test
description: "ROS package dev board automated testing skill. Used to configure EUR repos, install ROS packages, execute functional tests, and handle failures on the development board. Trigger scenarios: user mentions 'on-board testing', 'board test', 'ROS package testing', 'EUR repo configuration', 'functional validation', or needs to verify ROS package functionality on a dev board. Supports a complete iterative loop of intelligent test case generation (via code agent), smart failure analysis, spec/patch modification, and rebuilding."
compatibility:
  - "bash"
  - "python3"
  - "ssh"
  - "scp"
---

# ROS Package On-Board Test

An automated workflow for testing ROS packages on a development board, including repository configuration, package installation, functional testing, and failure handling.

## Core Features

1. **Tiered Testing Architecture**: 
   - **L0**: Basic core functionality smoke testing (verifying package commands and node availability after installation).
   - **L1**: On-board incremental single-package compilation testing (`colcon test`), using EUR RPMs as an Underlay for rapid testing to expose minor OS-level differences.
   - **L2**: (Planned) Heavy C++ packages are automatically intercepted and routed to a Host PC QEMU Docker for full compilation to prevent Out-Of-Memory (OOM) on the dev board.
2. **Intelligent Test Case Generation**: Uses code agent capabilities to dynamically generate L0 test cases, avoiding hardcoded scripts.
3. **Smart Routing Mechanism**: Intelligently evaluates and assigns packages to the appropriate test tier based on `package.xml` (`ament_python` vs `ament_cmake`) and the number of `.cpp` test files.
4. **Intelligent Failure Analysis**: Uses the code agent to analyze failure causes, potentially involving inspecting ROS source code, git history, etc.
5. **Automated Iterative Repair**: Modifies specs/patches, runs git push, and rebuilds EUR packages.

## Tiered Testing Architecture Details

### Level 0 (L0): Basic Core Functionality Validation
Verifies that pre-compiled binary RPM packages installed via EUR work correctly. Executes predefined, intelligently generated scenarios (e.g., launching `ros2 run`, checking `ros2 topic`, etc.). The entry script is `run_l0_smoke_tests.sh`.

### Level 1 (L1): Ultra-fast Incremental On-board Testing
Uses EUR packages already installed on the dev board as underlying dependencies (Underlay Workspace), pushes only the source code of the target package to an isolated workspace (Overlay Workspace) on the board, and executes `colcon build` and `colcon test`.
- **Target Audience**: Pure Python packages (like `ros2topic`) or lightweight C++ packages (like `angles`, with <= 5 test files).
- **Advantages**: Extremely fast (typically < 20s), perfectly exposes minor differences specific to aarch64 and openEuler environments (like Python flake8 standards, default dependency paths, etc.).
- **Dependency Prep**: L1 testing requires additional test dependencies to be installed on the target board. A one-click environment setup script `setup_l1_test_env.sh` is provided.

### Level 2 (L2): Deep Containerized Full Testing (Planned)
For heavy C++ packages (like `nav2_util`) that contain many GTests and are highly prone to OOM and excessive time consumption during template expansion on aarch64 boards, they are intercepted and assigned to an aarch64 QEMU Docker environment on a Host PC (x86) for testing.

---

### Configuration File Format

Add the following fields to `openeuler_ros_upgrader_env.yaml`:

```yaml
# ============================================
# Development Board Configuration
# ============================================
board_ip: 192.168.1.100
board_username: root
board_password: your_password_here  # or use SSH key authentication

# Board system information (for EUR repo URL generation)
oe_version: 24.03
oe_arch: aarch64

# ============================================
# ROS Package Upgrade Configuration
# ============================================
# ... (other configuration items remain unchanged)
```

### Reading Configuration

```bash
board_ip=$(grep "^board_ip:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
board_username=$(grep "^board_username:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
board_password=$(grep "^board_password:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
eur_project=$(grep "^current_active_eur_project:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
dependency_list_file=$(grep "^dependency_list_file:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
oe_version=$(grep "^oe_version:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
oe_arch=$(grep "^oe_arch:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
```

**If the configuration is incomplete, immediately prompt the user for the missing information.**

## Complete Workflow

### Phase 0: Environment Preparation

#### Step 1: Verify SSH Connection

```bash
# Use sshpass for password authentication (if configured)
if [ -n "$board_password" ]; then
  sshpass -p "$board_password" ssh -o ConnectTimeout=5 \
    ${board_username}@${board_ip} "echo 'Connection successful'"
else
  # Use SSH key authentication
  ssh -o ConnectTimeout=5 \
    ${board_username}@${board_ip} "echo 'Connection successful'"
fi

if [ $? -ne 0 ]; then
  echo "[ERROR] Cannot connect to dev board: ${board_username}@${board_ip}"
  echo "[ACTION] Please check the board IP, username, password, or SSH key configuration"
  exit 1
fi

echo "[✓] SSH connection successful"
```

### Phase 1: EUR Repo Configuration

#### Step 1: Check Current Repo Configuration

```bash
echo "============================================"
echo "Checking current ROS repo configuration"
echo "============================================"

current_repo=$(ssh ${board_username}@${board_ip} "cat /etc/yum.repos.d/openEulerROS.repo 2>/dev/null" || echo "")

if [ -z "$current_repo" ]; then
  echo "[INFO] openEulerROS.repo file not found"
else
  echo "[INFO] Current repo configuration:"
  echo "$current_repo"
fi
```

#### Step 2: Backup Original Configuration

```bash
timestamp=$(date +%Y%m%d_%H%M%S)
backup_file="/root/openEulerROS.repo.${timestamp}"

if [ -n "$current_repo" ]; then
  ssh ${board_username}@${board_ip} "cp /etc/yum.repos.d/openEulerROS.repo $backup_file"
  echo "[✓] Original configuration backed up to: $backup_file"
fi
```

#### Step 3: Generate and Configure New EUR Repo

**Parse EUR project parameters**:
```bash
# eur_project format: <gitcode_username>/<eur_project_name>
eur_username=$(echo "$eur_project" | cut -d'/' -f1)
eur_project_name=$(echo "$eur_project" | cut -d'/' -f2)

# Get openEuler version
oe_version=$(ssh ${board_username}@${board_ip} \
  "cat /etc/os-release | grep VERSION_ID | cut -d'\"' -f2")

# EUR repo URL (dots in version number replaced with underscores)
eur_version=$(echo "$oe_version" | sed 's/\./_/g')
eur_baseurl="https://eur.openeuler.openatom.cn/results/${eur_username}/${eur_project_name}/openeuler-${eur_version}_LTS-\$basearch/"

# Official repo URL
official_baseurl="https://eulermaker.compass-ci.openeuler.openatom.cn/api/ems1/repositories/ROS-SIG-Multi-Version_ros-humble_openEuler-${oe_version}-LTS-TEST4/openEuler%3A${oe_version}-LTS/\$basearch/"

echo "[INFO] EUR repo URL: $eur_baseurl"
echo "[INFO] Official repo URL: $official_baseurl"
```

**Generate configuration file**:
```bash
cat > /tmp/openEulerROS.repo << EOF
[openEuler-Embedded-ROS-humble]
name=openEuler-Embedded-ROS-humble
baseurl=${eur_baseurl}
skip_if_unavailable=True
enabled=1
gpgcheck=0
priority=1

[openEulerROS-humble]
name=openEulerROS-humble
baseurl=${official_baseurl}
enabled=1
gpgcheck=0
priority=2
EOF

# Transfer to dev board
scp /tmp/openEulerROS.repo ${board_username}@${board_ip}:/etc/yum.repos.d/openEulerROS.repo

# Clear cache
ssh ${board_username}@${board_ip} "dnf clean all && dnf makecache"

echo "[✓] EUR repo configuration complete"
```

#### Step 4: Verify Repo Configuration

```bash
repolist=$(ssh ${board_username}@${board_ip} "dnf repolist")

echo "$repolist" | grep -q "openEuler-Embedded-ROS-humble"
if [ $? -eq 0 ]; then
  echo "[✓] EUR repo enabled"
else
  echo "[ERROR] EUR repo not configured correctly"
  exit 1
fi

echo "$repolist" | grep -q "openEulerROS-humble"
if [ $? -eq 0 ]; then
  echo "[✓] Official repo enabled"
fi
```

### Phase 2: ROS Package Cleanup and Installation

#### Step 1: Clean Existing ROS Packages

```bash
echo "============================================"
echo "Cleaning existing ROS packages"
echo "============================================"

ssh ${board_username}@${board_ip} "dnf remove ros-humble-* -y"
echo "[✓] All ros-humble packages cleaned up"
```

#### Step 2: Read Target Package List

```bash
packages=$(grep -v '^#' "$dependency_list_file" | grep -v '^$')
total_packages=$(echo "$packages" | wc -l)
current=0

echo "============================================"
echo "Installing ROS packages (Total $total_packages)"
echo "============================================"
```

#### Step 3: Install and Verify Sources Individually

**CRITICAL**: Core ROS packages MUST be installed from the EUR repo.

```bash
failed_packages=()
eur_source_packages=()
official_source_packages=()

while IFS= read -r package; do
  [[ "$package" =~ ^# ]] && continue
  [ -z "$package" ] && continue

  current=$((current + 1))
  echo ""
  echo "[$current/$total_packages] Installing: ros-humble-$package"
  echo "----------------------------------------"

  # Install package and capture output
  install_output=$(ssh ${board_username}@${board_ip} \
    "dnf install ros-humble-$package -y 2>&1")

  # Check if installation was successful
  if echo "$install_output" | grep -q "Already installed"; then
    echo "[!] Package already installed: ros-humble-$package"
    source_repo="unknown"
  elif echo "$install_output" | grep -q "Error"; then
    echo "[✗] Installation failed: ros-humble-$package"
    failed_packages+=("$package")
    continue
  else
    # Extract installation source
    source_repo=$(echo "$install_output" | \
      grep -oP 'from \K[^ ]+' | tail -1 || echo "unknown")
  fi

  # Verify source
  if [ "$source_repo" == "openEuler-Embedded-ROS-humble" ]; then
    echo "[✓] Installed from EUR repo: ros-humble-$package"
    eur_source_packages+=("$package")
  elif [ "$source_repo" == "openEulerROS-humble" ]; then
    echo "[⚠] Installed from Official repo: ros-humble-$package"
    official_source_packages+=("$package")
  else
    echo "[?] Source unknown: ros-humble-$package (Source: $source_repo)"
  fi

done <<< "$packages"
```

#### Step 4: Multi-Dimensional RPM Provenance Verification

**CRITICAL**: After installation, verify that each package actually came from the expected EUR repository. This is a universal verification step for ALL package upgrades — not specific to any single package.

The `install_target_packages.sh` script's Step 6 performs this automatically using two independent verification dimensions:

| Dimension | Command | Field | Purpose |
|-----------|---------|-------|---------|
| 1. From repo | `dnf repoquery --installed --info <pkg>` | `From repo` | Identifies which repo the package was installed from |
| 2. Vendor | `rpm -qi <pkg>` | `Vendor` | Confirms the builder identity (personal vs official EUR) |

**Why multi-dimensional?**
- `dnf list --installed` output can be unreliable due to metadata caching and column truncation.
- `From repo` from `repoquery` is the ground truth for installation source.
- `Vendor` provides a secondary signal: `openEuler Copr - user openEuler_Embedded` (official EUR) vs `openEuler Copr - user <your_username>` (personal EUR).

**Provenance Categories**:
- ✅ **EUR_OK**: Package installed from the expected EUR repo — the ideal state.
- ⚠️ **OTHER_REPO**: Package installed from a different repo (e.g., official repo as fallback) — acceptable for base dependencies, but warrants investigation for upgrade targets.
- ❌ **VERIFY_FAILED**: Package not installed or repoquery returned empty — indicates installation failure.

**The `expected_eur_repo` parameter**: The script accepts an optional 6th argument `expected_eur_repo` (default: `openEuler-Embedded-ROS-humble`). This allows the same script to validate against personal EUR repos during development or official EUR repos during release validation. The repo name must match the `[section]` name in `openEulerROS.repo`.

```bash
# Personal EUR validation (default)
./install_target_packages.sh $ip $user $pass ./packages.txt humble

# Official EUR validation
./install_target_packages.sh $ip $user $pass ./packages.txt humble openEuler-Embedded-ROS-humble
```

#### Step 5: Installation Result Analysis

```bash
echo ""
echo "============================================"
echo "Installation Results Summary"
echo "============================================"
echo "EUR repo installs: ${#eur_source_packages[@]}"
echo "Official repo installs: ${#official_source_packages[@]}"
echo "Failed installs: ${#failed_packages[@]}"
echo ""

# Handle failed packages
if [ ${#failed_packages[@]} -gt 0 ]; then
  echo "[ERROR] The following packages failed to install:"
  printf '  - %s\n' "${failed_packages[@]}"
  echo ""
  echo "[ACTION] Need to investigate failure reasons"
  echo "[INFO] Possible causes:"
  echo "  1. Packages are not in the EUR repo"
  echo "  2. Incorrect package names (check dependency_list.txt)"
  echo "  3. Dependency issues"
  echo ""
  read -p "Continue testing installed packages? (y/n): " continue_test
  if [ "$continue_test" != "y" ]; then
    exit 1
  fi
fi

# Handle official repo packages
if [ ${#official_source_packages[@]} -gt 0 ]; then
  echo "[WARNING] The following packages were installed from the official repo (not EUR):"
  printf '  - %s\n' "${official_source_packages[@]}"
  echo ""
  echo "[INFO] This might mean:"
  echo "  1. Latest builds for these packages are not yet in the EUR repo"
  echo "  2. These are base dependencies, normally downloaded from official repo"
  echo ""
  read -p "Continue testing? (y/n): " continue_test
  if [ "$continue_test" != "y" ]; then
    echo "[ACTION] Please check the EUR repo or rebuild missing packages"
    exit 1
  fi
fi

echo "[✓] Package installation complete, ready to start functional testing"
```

### Phase 3: Basic Functionality Test Framework

Use the standardized test framework to verify ROS2 core functionalities.

#### Step 1: Execute Basic Functional Tests

```bash
SCRIPTS_DIR="$HOME/.claude/skills/ros-oe-board-test/scripts"
OUTPUT_DIR="./test_results/$(date +%Y%m%d_%H%M%S)"

# Run all basic tests
"$SCRIPTS_DIR/run_l0_smoke_tests.sh" \
    "$board_ip" "$board_username" "$board_password" \
    "all" \
    "$OUTPUT_DIR" \
    "3"  # max_retries
```

#### Step 2: Test Category Details

| Category | Description | Cases | Priority |
|-----|------|-------|-------|
| environment | Env validation | 5 | P0 (Must pass) |
| package | Package management | 6 | P0 |
| node | Node management | 4 | P0 |
| topic | Topic communication | 7 | P0-P1 |
| service | Service communication | 3 | P1 |
| parameter | Parameter management | 3 | P1-P2 |

**Execute specific categories**:
```bash
# Run only environment and topic tests
"$SCRIPTS_DIR/run_l0_smoke_tests.sh" \
    "$board_ip" "$board_username" "$board_password" \
    "environment,topic" \
    "$OUTPUT_DIR"
```

#### Step 3: Smart Failure Analysis and Auto-Iteration

**Framework built-in smart analysis**:
- Auto-identifies common failure patterns (env variables, missing packages, DDS configs, etc.)
- Attempts auto-repair (install missing packages, set env vars)
- Supports retry mechanism (default 3 times)

**Failure Analysis Flow**:
```
Test Fails → Pattern Match → Auto Diagnose → Try Repair → Retest
    ↓           ↓
Unknown Fail → Gen Analysis Request → Call Code Agent → Manual Confirm
```

**Auto-repair Capabilities**:

| Failure Type | Auto-repair Strategy |
|---------|------------|
| Env not loaded | Verify and prompt source |
| Package not installed | Auto dnf install |
| DDS/RMW issues | Set default RMW_IMPLEMENTATION |
| Permission issues | Prompt to use root |
| Unknown issues | Generate code agent analysis request |

#### Step 4: Test Results Output

**JSON Report** (`test_report.json`):
```json
{
  "summary": {
    "total": 28,
    "passed": 26,
    "failed": 2,
    "pass_rate": "92.9%",
    "by_priority": {
      "P0": {"passed": 15, "failed": 0},
      "P1": {"passed": 8, "failed": 1},
      "P2": {"passed": 3, "failed": 1}
    }
  },
  "results": [
    {
      "id": "topic_002",
      "name": "C++ Talker publishes message",
      "passed": false,
      "diagnosis": "Communication timeout",
      "fix_applied": "Set RMW_IMPLEMENTATION=rmw_fastrtps_cpp"
    }
  ]
}
```

**Failure Details** (`test_failures.json`):
```json
[
  {
    "id": "topic_002",
    "name": "C++ Talker publishes message",
    "diagnosis": "Needs further analysis",
    "error": "timeout waiting for message...",
    "full_output": "..."
  }
]
```

#### Step 5: Unknown Failure Handling

If the test framework cannot auto-diagnose, it generates `agent_analysis_request.json`:

```json
{
  "test_case": {
    "id": "topic_002",
    "name": "C++ Talker publishes message",
    "command": "timeout 5 ros2 topic echo /chatter --once"
  },
  "output": "...",
  "error": "...",
  "analysis_prompt": "Please analyze the reason for the following test failure..."
}
```

**At this point, invoke the code agent**:
```
Please analyze the failure causes in test_failures.json to determine if we should:
1. Modify the test case
2. Modify the environment configuration
3. Modify the build contents (spec/patch)

Provide specific repair solutions after analysis.
```

### Phase 4: Advanced Test Case Generation (Smart Agent Mode)

#### Step 1: Check Existing Test Cases

```bash
# Create working directory
timestamp=$(date +%Y%m%d_%H%M%S)
work_test_dir="./on-board-testcases"
mkdir -p "$work_test_dir"

# Check if ready-made cases exist under references/
skill_ref_dir="$HOME/.claude/skills/ros-oe-board-test/references"
existing_testcases=$(find "$skill_ref_dir/test_cases" -name "*.sh" 2>/dev/null || true)

if [ -n "$existing_testcases" ]; then
  echo "============================================"
  echo "Existing test cases found"
  echo "============================================"
  echo "$existing_testcases"
  echo ""
  read -p "Reuse existing cases? (y/n): " reuse_cases

  if [ "$reuse_cases" == "y" ]; then
    # Copy existing cases to working directory
    cp -r "$skill_ref_dir/test_cases"/* "$work_test_dir/" 2>/dev/null || true
    echo "[✓] Existing test cases reused"
  fi
fi
```

#### Step 2: Use Code Agent to Generate Test Cases

**IMPORTANT**: Do not use hardcoded scripts here; invoke the code agent for smart generation.

```
Please generate functional test cases for the following ROS packages:

Package List:
$(cat "$dependency_list_file")

Requirements:
1. Generate at least one basic functional test case per package.
2. Use bash script format for test cases.
3. Test cases should verify the core functionality of the package.
4. For communication packages, test topics/services.
5. For tool packages, test basic commands.

Generation Strategy and Strict Rules (Must be strictly followed):
- **Anti-interference Isolation**: Must generate a random `ROS_DOMAIN_ID=$((RANDOM % 100 + 1))` and export it at the start of the script for absolute isolation from residual nodes.
- **Process-level Assertions**: When starting a background node, you must capture the PID (`PID=$!`) and verify success using OS process probes (`kill -0 $PID`). Never rely solely on `ros2 node list` for verification (to prevent false positives and residual ghost nodes).
- **Log-level Assertions**: If a node is expected to exit due to missing hardware (e.g., cameras), you must use `grep` to capture specific error logs (e.g., "no cameras available") to reverse-prove that underlying dynamic libraries loaded successfully.
- If the package has an executable node, test node startup and basic functionality.
- If the package provides libraries, test library loading and basic invocation.
- If the package defines messages/services, test message generation and service invocation.

Output Location: $work_test_dir/

File Naming: <package_name>_test.sh
```

**The Code Agent will**:
1. Analyze the functionality of each ROS package.
2. View the package's package.xml and CMakeLists.txt.
3. View the package's executables and libraries.
4. Generate targeted test cases.
5. Save them to the working directory.

#### Step 3: User Reviews Test Cases

```bash
echo "============================================"
echo "Test cases generated"
echo "============================================"
echo "Location: $work_test_dir/"
echo ""
ls -lh "$work_test_dir/"*.sh 2>/dev/null || echo "No test case files"
echo ""
read -p "Please review the test cases. Press (y/n) to continue after confirmation: " review_cases

if [ "$review_cases" != "y" ]; then
  echo "[ACTION] Please manually modify test cases and continue"
  read -p "Press Enter to continue after modifying..."
fi
```

#### Step 4: Archive Test Cases (Optional)

```bash
read -p "Archive test cases to skill references/ directory? (y/n): " archive_cases

if [ "$archive_cases" == "y" ]; then
  mkdir -p "$skill_ref_dir/test_cases"
  cp "$work_test_dir/"*.sh "$skill_ref_dir/test_cases/" 2>/dev/null || true
  echo "[✓] Test cases archived, ready for reuse in future upgrades"
fi
```

#### Step 5: Transfer Test Cases to Dev Board

```bash
# Create test directory on dev board
remote_test_dir="/tmp/ros_test_${timestamp}"
ssh ${board_username}@${board_ip} "mkdir -p $remote_test_dir"

# Transfer test cases
scp -r "$work_test_dir/"*.sh ${board_username}@${board_ip}:${remote_test_dir}/

echo "[✓] Test cases transferred to dev board: $remote_test_dir"
```

#### Step 6: Execute Test Cases

```bash
echo "============================================"
echo "Executing Test Cases"
echo "============================================"

# Create results directory
mkdir -p ./test_results/$timestamp

# Execute test cases one by one
test_results=()
for test_script in "$work_test_dir/"*.sh; do
  [ -f "$test_script" ] || continue

  test_name=$(basename "$test_script")
  echo ""
  echo "Executing: $test_name"
  echo "----------------------------------------"

  # Execute test on dev board
  test_output=$(ssh ${board_username}@${board_ip} \
    "cd $remote_test_dir && bash $test_name 2>&1")

  test_exit_code=$?

  # Save output
  echo "$test_output" > "./test_results/$timestamp/${test_name}.log"

  if [ $test_exit_code -eq 0 ]; then
    echo "[✓] Test Passed: $test_name"
    test_results+=("$test_name: PASS")
  else
    echo "[✗] Test Failed: $test_name"
    echo "$test_output"
    test_results+=("$test_name: FAIL")
  fi
done

echo ""
echo "============================================"
echo "Test Execution Complete"
echo "============================================"
printf '%s\n' "${test_results[@]}"
```

### Phase 5: Smart Failure Analysis and Iterative Repair

#### Step 1: Identify Failed Tests

```bash
failed_tests=()
for result in "${test_results[@]}"; do
  if [[ "$result" == *": FAIL" ]]; then
    failed_tests+=("${result%: FAIL}")
  fi
done

if [ ${#failed_tests[@]} -eq 0 ]; then
  echo "[✓] All tests passed"
  # Proceed to Phase 6
else
  echo "[!] Found ${#failed_tests[@]} failed tests"
  printf '  - %s\n' "${failed_tests[@]}"
fi
```

#### Step 2: Use Code Agent for Smart Failure Analysis

**IMPORTANT**: Do not use hardcoded scripts; invoke the code agent for deep analysis.

```
Please analyze the causes of the following ROS package test failures:

Failed tests:
$(printf '%s\n' "${failed_tests[@]}")

Test logs location: ./test_results/$timestamp/

Analysis Requirements:
1. Read the log files for each failed test.
2. Identify the root cause of the failure.
3. If code issues are involved, check the ROS source code and git history.
4. Provide repair recommendations.

Possible failure types:
- Environment config issue (ROS env not sourced)
- Missing dependencies (runtime dependencies)
- Compilation issues (incorrect build options)
- Code issues (requires modifying spec or adding patches)
- Test case issues (the test case itself is flawed)

Output Format:
For each failed test, provide:
1. Failure reason
2. Error log summary
3. Repair recommendation
4. Whether spec/patch modification is required
```

**The Code Agent will**:
1. Read test logs.
2. Inspect ROS package source code.
3. Check git history.
4. Review spec files.
5. Intelligently analyze failure causes.
6. Provide repair recommendations.

#### Step 3: Handle Based on Analysis Results

```bash
max_retries=3
retry_count=0

while [ ${#failed_tests[@]} -gt 0 ] && [ $retry_count -lt $max_retries ]; do
  retry_count=$((retry_count + 1))
  echo ""
  echo "============================================"
  echo "Failure Handling Iteration $retry_count/$max_retries"
  echo "============================================"

  # Code Agent has analyzed the failure causes
  # Now take action based on the analysis results

  # User confirmation required here
  echo "[INFO] Failure analysis completed"
  echo ""
  read -p "After reviewing the analysis results, choose an action:
  1. Auto-repair spec/patch and rebuild
  2. Only modify environment configuration
  3. Handle manually and continue
  4. Abandon currently failed tests
  Please select (1/2/3/4): " fix_choice

  case "$fix_choice" in
    1)
      echo "[INFO] Auto-repairing spec/patch..."

      # Invoke code agent to modify spec/patch
      # (This part is handled by the code agent)

      # Git push to personal repo
      personal_repos_dir=$(grep "^personal_repos_dir:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')
      gitcode_username=$(grep "^gitcode_username:" openeuler_ros_upgrader_env.yaml | awk '{print $2}')

      for package in "${failed_packages[@]}"; do
        echo "[INFO] Pushing changes for: $package"
        cd "$personal_repos_dir/$package"
        git add .
        git commit -m "Fix: $(date +%Y%m%d_%H%M%S) test failure fix"
        git push origin humble
        cd -
      done

      # Retrigger EUR build
      skill: "ros-oe-eur-build", args: \
        --workspace-dir . \
        --package-list-file "$dependency_list_file" \
        --mode personal \
        --username "$gitcode_username" \
        --max-wait 240

      # Wait for build completion and reconfigure repo
      echo "[INFO] Waiting for EUR build to complete..."
      sleep 300  # Wait 5 mins for EUR to update repos

      # Reconfigure repo
      ssh ${board_username}@${board_ip} "dnf clean all && dnf makecache"

      # Reinstall packages
      # (Repeat Phase 2 installation steps)

      # Rerun failed tests
      # (Repeat Phase 3 testing steps)
      ;;

    2)
      echo "[INFO] Modifying environment configuration..."
      # Handled manually by user or via specific commands
      read -p "Enter the environment configuration to modify: " env_changes
      ssh ${board_username}@${board_ip} "$env_changes"

      # Rerun failed tests
      ;;

    3)
      echo "[ACTION] Please manually handle the failure and continue"
      read -p "Press Enter to continue after handling..."
      # Rerun failed tests
      ;;

    4)
      echo "[WARNING] Abandoning currently failed tests"
      failed_tests=()
      ;;

    *)
      echo "[ERROR] Invalid choice"
      exit 1
      ;;
  esac
done

if [ ${#failed_tests[@]} -gt 0 ] && [ $retry_count -ge $max_retries ]; then
  echo ""
  echo "============================================"
  echo "[ERROR] Maximum retries reached, tests still failing"
  echo "============================================"
  echo "[ACTION] User manual inspection required"
  exit 1
fi
```

### Phase 6: Result Recording and Reporting

#### Step 1: Generate Test Report

```bash
cat > ./test_results/$timestamp/test_summary.md << EOF
# ROS Package On-Board Test Report

## Test Environment
- Dev Board: ${board_username}@${board_ip}
- EUR Project: $eur_project
- Test Time: $(date)
- openEuler Version: $oe_version

## Installation Statistics
- Total Packages: $total_packages
- EUR Repo Installs: ${#eur_source_packages[@]}
- Official Repo Installs: ${#official_source_packages[@]}
- Failed Installs: ${#failed_packages[@]}

## Test Statistics
- Total Tests: $(echo "${test_results[@]}" | wc -w)
- Passed: $(echo "${test_results[@]}" | grep -c ": PASS" || echo 0)
- Failed: $(echo "${test_results[@]}" | grep -c ": FAIL" || echo 0)

## Failure Handling Record
$(if [ $retry_count -gt 0 ]; then
  echo "Iteration count: $retry_count/$max_retries"
else
  echo "No failure handling needed"
fi)

## Test Result Details
$(printf '%s\n' "${test_results[@]}")

## Log Locations
- Test Logs: ./test_results/$timestamp/*.log
- Test Cases: $work_test_dir/

## Conclusion
$(if [ ${#failed_tests[@]} -eq 0 ]; then
  echo "✅ All tests passed"
else
  echo "❌ Some tests failed"
fi)
EOF

echo "[✓] Test report generated: ./test_results/$timestamp/test_summary.md"
```

#### Step 2: Update Environment State

```bash
sed -i 's/^current_phase:.*/current_phase: on_board_test_complete/' openeuler_ros_upgrader_env.yaml
echo "[✓] Environment state updated"
```

## Test Case Management Strategy

### references/ Directory Structure

```text
<skill_dir>/ros-oe-board-test/references/
├── test_cases/           # Archived test cases
│   ├── rclcpp_test.sh
│   ├── rclpy_test.sh
│   ├── fastcdr_test.sh
│   └── ...
└── test_patterns.md      # Test case writing guide
```

### Case Reuse Flow

1. **Check Existing Cases**: Look up in references/test_cases/
2. **Smart Generate New Cases**: Use code agent to generate
3. **User Review**: Confirm case correctness
4. **Archive**: Copy to references/test_cases/ for future use

### Test Case Writing Guide

Test cases should:
- Use bash script format
- Contain clear test goal comments
- Return 0 for success, non-zero for failure
- Output detailed testing processes and results

Example:
```bash
#!/bin/bash
# Test: rclcpp basic functionality
# Package: rclcpp

# Source ROS environment
source /opt/ros/humble/setup.bash

# Test 1: Check if rclcpp library exists
if [ -f /opt/ros/humble/lib/librclcpp.so ]; then
  echo "[✓] rclcpp library found"
else
  echo "[✗] rclcpp library not found"
  exit 1
fi

# Test 2: Test basic node creation
ros2 run demo_nodes_cpp talker &
talker_pid=$!
sleep 2

if ps -p $talker_pid > /dev/null; then
  echo "[✓] Node started successfully"
  kill $talker_pid
else
  echo "[✗] Node failed to start"
  exit 1
fi

echo "[✓] All tests passed"
exit 0
```

## Auxiliary Scripts

This skill contains the following auxiliary scripts located in the `scripts/` directory:

### Script List

| Script | Purpose | Usage |
|-----|------|---------|
| `lib_ssh.sh` | SSH connection utils | `source lib_ssh.sh` |
| `configure_board_repo.sh` | EUR repo config | `./configure_board_repo.sh <board_ip> <board_username> <board_password> <eur_project> [oe_version] [oe_arch]` |
| `install_target_packages.sh` | ROS package install + provenance verify | `./install_target_packages.sh <board_ip> <board_username> <board_password> <package_list_file> [ros_distro] [expected_eur_repo]` |
| `run_l0_smoke_tests.sh` | Basic func test entry (L0) | `./run_l0_smoke_tests.sh <board_ip> <board_username> <board_password> [category] [output_dir] [max_retries]` |
| `run_l0_custom_tests.py` | Python test runner (L0) | Called by run_l0_smoke_tests.sh |
| `setup_l1_test_env.sh`| L1 test env 1-click config | `./setup_l1_test_env.sh <board_ip> <board_username> [board_password]` |
| `run_l1_colcon_tests.py`| L1 smart routing & inc test | `python3 scripts/run_l1_colcon_tests.py --board-ip <IP> --board-user root --package-path <LOCAL_PKG_SRC>` |

### setup_l1_test_env.sh - L1 Test Env 1-Click Config

Installs required test dependencies for L1 testing (`colcon test`) on the dev board (including compiler toolchains, Python pytest extensions, ament_lint, etc.). Must be executed **at least once** before official L1 testing.

```bash
./scripts/setup_l1_test_env.sh 192.168.137.2 root password
```

### lib_test_framework.py - L1 Smart Routing & Incremental Testing

Intelligently evaluates local ROS package source directories to determine if they are suitable for running L1 tests on the board. If suitable, pushes code to the board via SSH/SCP (Overlay mode), utilizes installed EUR packages as Underlay, compiles, and executes `colcon test`.

**Command line arguments**:
```bash
python3 scripts/run_l1_colcon_tests.py \
  --board-ip 192.168.137.2 \
  --board-user root \
  --board-password password \
  --package-path /tmp/ros2_test_src/angles \
  --cpp-test-threshold 5
```

**Functions**:
1. Parses `package.xml` for `build_type`.
2. Counts `.cpp` files in the `test/` directory to evaluate test weight.
3. Automatically routes to L1 (Execute) or L2 (Skip and return exit code 2) based on `threshold` (default 5).
4. Executes incremental on-board build (if evaluation passes).
5. Automatically runs `colcon test` and `colcon test-result` and parses output.

### lib_ssh.sh - SSH Connection Utils

Provides SSH connection, remote command execution, and file transfer functions.

```bash
# Load utility library
source lib_ssh.sh

# Verify SSH connection
verify_ssh_connection <board_ip> <board_username> [board_password]

# Execute remote command
ssh_exec <board_ip> <board_username> <board_password> "<command>"

# Copy file to remote
ssh_copy <board_ip> <board_username> <board_password> <local_file> <remote_path>

# Copy directory to remote
ssh_copy_dir <board_ip> <board_username> <board_password> <local_dir> <remote_path>
```

**Dependencies**: `sshpass` (for password authentication)

```bash
dnf install sshpass  # if not installed
```

### configure_board_repo.sh - EUR Repo Configuration

Configures the EUR repo on the dev board with priority 1, using official repo as fallback with priority 2.

```bash
./configure_board_repo.sh 192.168.1.100 root password "<gitcode_username>/<eur_project_name>" 24.03 aarch64
```

**Functions**:
1. Verifies SSH connection
2. Gets/detects openEuler version and architecture
3. Backs up existing ROS repo config
4. Creates new EUR repo config file
5. Cleans repo cache
6. Verifies config correctness

**EUR Repo URL Format**:
```
https://eur.openeuler.openatom.cn/results/<username>/<project>/openeuler-<version>_LTS-$basearch/
```

**Note**: Version format is `24_03` not `24.03` (dots become underscores)

### install_target_packages.sh - ROS Package Installation
Installs ROS packages from the EUR repo and performs multi-dimensional provenance verification.

```bash
./install_target_packages.sh 192.168.1.100 root password ./dependency_list.txt humble
./install_target_packages.sh 192.168.1.100 root password ./dependency_list.txt humble openEuler-Embedded-ROS-humble
```

**Functions**:
1. Verifies SSH connection
2. Checks currently installed ROS packages
3. **Removes existing packages** (ensures re-installation from EUR repo)
4. Installs packages from EUR repo
5. Verifies installation results
6. **Multi-dimensional provenance verification** (From repo + Vendor double check)

**Package Name Conversion**: Underscores to hyphens (e.g., `iceoryx_hoofs` → `ros-humble-iceoryx-hoofs`)

**Provenance Verification (Step 6)**:
- Uses `dnf repoquery --installed --info` to get the `From repo` field (Dimension 1).
- Uses `rpm -qi` to get the `Vendor` field (Dimension 2).
- Accepts an optional `expected_eur_repo` parameter (default: `openEuler-Embedded-ROS-humble`).
- Reports three categories: ✅ EUR_OK, ⚠️ OTHER_REPO, ❌ VERIFY_FAILED.
- Installation failures are hard errors (exit 1); provenance mismatches are warnings (exit 0).

### run_l0_smoke_tests.sh - Basic Functional Test Entry

Executes ROS2 basic functional tests, supporting smart failure analysis and auto-retry.

```bash
# Run all tests
./run_l0_smoke_tests.sh 192.168.137.2 root password

# Run specific category
./run_l0_smoke_tests.sh 192.168.137.2 root password topic

# Run multiple categories, custom output dir and max retries
./run_l0_smoke_tests.sh 192.168.137.2 root password "environment,topic" ./results 3
```

**Test Categories**:
- `environment`: Env validation (5 cases)
- `package`: Package management (6 cases)
- `node`: Node management (4 cases)
- `topic`: Topic communication (7 cases)
- `service`: Service communication (3 cases)
- `parameter`: Parameter management (3 cases)
- `all`: Run all tests (Default)

**Output Files**:
- `test_report.json`: Test report summary
- `test_failures.json`: Failure details (if any)

### run_l0_custom_tests.py - Python Test Runner

Core test execution engine, called by `run_l0_smoke_tests.sh`.

**Command Line Arguments**:
```bash
python3 scripts/run_l0_custom_tests.py \
  --board-ip 192.168.137.2 \
  --board-username root \
  --board-password 'password' \
  --test-cases-dir test_cases \
  --skill-dir . \
  --category all \
  --output-dir ./test_results \
  --max-retries 1
```

**Functions**:
1. Loads YAML format test cases
2. Executes test commands remotely via SSH
3. Auto-sources ROS env (`/opt/ros/humble/setup.bash`)
4. Matches expected outputs, judges test results
5. Auto-analyzes and diagnoses upon failure
6. Supports auto-retry and repair

**Smart Failure Analysis**:
- Built-in recognition for common failure patterns
- Auto-generates repair recommendations
- Generates code agent analysis requests for unknown issues

**Test Case Format** (YAML):
```yaml
test_cases:
  - id: "topic_001"
    name: "List Topics"
    priority: "P0"
    command: "ros2 topic list"
    expect_contains:
      - "/chatter"
    timeout: 15
    on_failure:
      diagnose: "Failed to list topics"
      suggestions:
        - "Check if the node is running normally"
```

---

## Practical Validation Report (2026-03-25)

### Test Environment

- **Dev Board**: HiEulerPi1 (aarch64)
- **OS**: openEuler 24.03 LTS
- **ROS Version**: ROS2 Humble
- **Test Results**: 28/28 Passed (100%)

### Test Coverage

| Category | Cases | Passed | Details |
|-----|-------|-----|------|
| environment | 5 | 5 | ROS env vars, install dirs, command availability |
| package | 6 | 6 | rclcpp, rclpy, demo_nodes, etc. |
| node | 4 | 4 | C++/Python talker node startup & management |
| topic | 7 | 7 | Topic list, msg pub, cross-language comms |
| service | 3 | 3 | Service list, type queries, service calls |
| parameter | 3 | 3 | Parameter list, get, set |

### Key Discoveries & Solutions

#### 1. ROS2 CLI Extension Package Installation

**Issue**: After basic installation, subcommands like `ros2 pkg/node/topic` are unavailable.

```
ros2: error: argument ... invalid choice: 'pkg' (choose from 'daemon', 'extension_points', 'extensions')
```

**Solution**: Need to install CLI extension packages separately:

```bash
dnf install -y \
  ros-humble-ros2pkg \
  ros-humble-ros2node \
  ros-humble-ros2topic \
  ros-humble-ros2service \
  ros-humble-ros2param \
  ros-humble-ros2run \
  ros-humble-demo-nodes-cpp \
  ros-humble-demo-nodes-py
```

#### 2. ros2 pkg list Output Format

**Issue**: Test case expects RPM package name (e.g. `ros-humble-rclcpp`), but `ros2 pkg list` actually outputs ROS package name (e.g. `rclcpp`).

**Solution**: Test case `expect_contains` should use the ROS package name:

```yaml
# Incorrect example
expect_contains:
  - "ros-humble-rclcpp"  # Wrong!

# Correct example
expect_contains:
  - "rclcpp"  # Correct!
```

#### 3. Background Process Environment Inheritance

**Issue**: When using `&` to start background processes, ROS environment is not inherited properly.

```
bash: line 1: ros2: command not found
```

**Solution**: Use `nohup bash -c` format to ensure background processes inherit the environment:

```python
# Correct implementation in run_l0_custom_tests.py
if setup_cmd.get("background"):
    wrapped_cmd = f"nohup bash -c '{cmd}' > /dev/null 2>&1 &"
    self.executor.exec(wrapped_cmd, timeout=5, source_ros=True)
```

#### 4. YAML Test Case Format

**Issue**: Test cases missing `id` and `name` fields cause parsing errors.

**Solution**: Each test case must have a complete definition:

```yaml
# Incorrect example (missing id and name)
- command: "ros2 param get /talker use_sim_time"
  expect_contains:
    - "Boolean value"

# Correct example
- id: "param_002"
  name: "Get Parameter Value"
  description: "Verify ros2 param get command is normal"
  priority: "P1"
  command: "ros2 param get /talker use_sim_time"
  expect_contains:
    - "Boolean value"
    - "False"  # Note case sensitivity
```

#### 5. Output Format Case Sensitivity

**Issue**: ROS2 outputs `Boolean value is: False` (capital F), but expects `false` (lowercase).

**Solution**: Test case expected value must match actual output casing:

```yaml
# Incorrect example
expect_contains:
  - "false"  # Lowercase, won't match

# Correct example
expect_contains:
  - "False"  # Matches actual ROS2 output
```

### Best Practices

1. **Pre-test Check**: Ensure ROS2 CLI extension packages are installed.
2. **Package Name Format**: `ros2 pkg list` outputs ROS package names, not RPM package names.
3. **Background Processes**: Use `nohup bash -c` format for startup.
4. **YAML Format**: Each test case MUST have `id`, `name`, `priority`.
5. **Output Matching**: Pay attention to the case formatting of ROS2 output.
6. **RPM Provenance Verification**: After installing packages from EUR, ALWAYS verify the installation source using `dnf repoquery --installed --info` (`From repo` field) and `rpm -qi` (`Vendor` field). Never rely solely on `dnf list --installed` for source verification — it can be unreliable due to metadata caching. The `install_target_packages.sh` script performs this automatically in Step 6.

## Practical Pitfalls & Best Practices (L1 Incremental Testing Troubleshooting Special)

In L1 dev board isolated testing, we summarized the following core troubleshooting strategies and pitfalls. This is the key methodology to ensure the pipeline is "not blocked by historical defects":

### 1. Strict Baseline A/B Testing (Baseline Regression Validation)
**Never assume a test failure is a code regression introduced by the upgrade!**
When a package fails L1 `colcon test` on the dev board:
1. **Find True Baseline**: Do not rely on local Git history. Directly query the real version from the system's official repo via the dev board's package manager (e.g., `dnf info ros-humble-ros2interface --showduplicates` to find baseline version `0.18.6`).
2. **Download Original Source**: Download the original source of that baseline version, without attaching any new patches.
3. **Retest in Same Env**: Push it to the same dev board L1 test environment for a side-by-side comparison.
4. **Decision Routing**:
   - 🔴 **Old Version Passes -> New Version Fails**: Clear upgrade regression. Highest priority to investigate Spec/Patch/API changes.
   - 🟡 **Old Version Fails -> New Version Fails**: Inherited Defect. This is a current board environment or architecture limitation. Record in the report and let it pass.

### 2. Infrastructure Dependencies for C++ Tests (CMake Test Dependencies)
When using `colcon build --cmake-args -DBUILD_TESTING=ON`, even if the package only contains pure logic tests, CMake requires an extremely massive test dependency chain.
**Issue**: Directly fails with errors like missing `ament_cmake_auto`, `ament_cmake_mypy`, `ament_cmake_gtest`, etc.
**Solution**: You must pre-install all `ros-humble-ament-cmake-*` and their derivative packages (including lint, cppcheck, flake8, etc.) onto the target board using `setup_l1_test_env.sh`. Otherwise, lightweight C++ packages will fail before even reaching the build phase.

### 3. Python Coroutines and `launch_testing` Deadlocks/Timeouts
**Issue**: Some pure Python packages (e.g., `ros2topic`, `ros2param`, `ros2lifecycle`) cause the framework to hang/deadlock for tens of minutes during `colcon test`.
**Cause**: Test cases use `launch_testing` to mock daemon interactions. On physical dev boards without perfect network loopback config or with restricted multicast routing, the underlying `FastDDS`/`RMW` easily deadlocks; or due to board compute latency, a 2-second hard timeout for coroutines (`wait_for_shutdown(timeout=2)`) is triggered, resulting in an error.
**Solution**: Add a hard timeout mechanism (e.g., `timeout 600`) for `colcon test` in `run_l1_colcon_tests.py`. Once the `124` exit code is triggered, combine with A/B baseline testing to confirm if it's an inherited defect. If it is, skip it so as not to block the pipeline.

### 4. Flake8 Version Conflicts Breaking `ament_flake8`
**Issue**: If the dev board has installed the latest `flake8` (>= 5.0.0) via pip, ROS's `ament_flake8` parsing will error out.
**Solution**: Force downgrade in the environment init script: `pip3 install 'flake8<5.0.0'`.

### 5. Isolated Dependency Faults (Inter-Suite Dependencies)
**Issue**: Packages like `rosapi` can compile successfully but tests fail with `ModuleNotFoundError` for `rosbridge_library` (which is in the same Git repo).
**Cause**: L1 is an "isolated extraction test" for single packages. The dev board's Underlay (system dir) does not pre-install other components of the same suite.
**Solution**: Recognize the traits of such packages. If no system pre-install exists, mark them as unsuitable for L1 testing and hand them over to L2 for overall Workspace build validation.

### 6. Massive Dependencies for Heavy C++ Hardware Driver Packages
**Issue**: Packages like `usb_cam` fail during compilation due to missing native system headers like `libavutil/pixfmt.h`.
**Cause**: They heavily depend not only on ROS but also on OS-level audio/video/graphics dev libraries (e.g., `ffmpeg-devel`, `libv4l`).
**Solution**: Their `build_export_depend` is extremely heavy, and the streamlined dev board filesystem cannot support them. Do not attempt to install these dependencies on the board ad-hoc. Directly trigger the routing rule to hand over to L2 (Host full image) for build testing.

## Coordination with Other Skills

- **Prerequisite**: `ros-oe-eur-build` - EUR building and publishing
- **Subsequent Flow**: Community PR Submission (next skill: `ros-oe-pr-submit`)
- **Shared Config**: `openeuler_ros_upgrader_env.yaml`
- **Failure Repair**: May invoke `ros-oe-pkg-update` to modify spec/patches

## Output Files

All outputs are saved in:

- `./on-board-testcases/` - Working directory for test cases
- `./test_results/$timestamp/` - Test results and logs
- `<skill_dir>/ros-oe-board-test/references/test_cases/` - Archived test cases

## Usage Examples

```bash
# Complete flow (default)
skill: "ros-oe-board-test"

# Setup repo and install only
skill: "ros-oe-board-test", args: "--phase setup"

# Run tests only
skill: "ros-oe-board-test", args: "--phase test"

# Analyze failure only
skill: "ros-oe-board-test", args: "--phase analyze"
```

## Output Language Rules

1.  **INTERNAL REASONING & TOOL USAGE**: All internal thinking, reasoning, and tool calls (like reading files, searching, executing bash commands, reading logs) MUST be conducted exclusively in **English**.
2.  **GIT COMMIT MESSAGES**: All git commit messages must be in **English**, clear, descriptive, and accurately reflect the changes. You MUST include the `-s` flag in your git commits to add the `Signed-off-by` line required by openEuler DCO.
3.  **USER-FACING OUTPUT (FINAL DELIVERABLES)**: Only the final, user-facing output MUST be entirely in **Simplified Chinese (简体中文)**. This includes:
    *   Summaries or reports provided to the user in the chat interface.
    *   Markdown log files (e.g., `test_summary.md`, `test_report.json` descriptions, `agent_analysis_request.json` prompts).
    *   Test case comments or failure analysis explanations.
    *   Pull Request titles and descriptions.

When interpreting failures, performing A/B baseline testing, or generating test cases, conduct all your system interaction and internal thought processes in English, but deliver the final diagnosis, generated scripts, and chat response to the user in Chinese.

## Troubleshooting & Agent Workflow (Agent-in-the-Loop)

The `run_tiered_tests.py` orchestrator implements a **Standardized Halting Protocol**. Instead of just failing silently, it will stop the pipeline and output a `[HALT: CODE]` block with context and instructions. When you (the Code Agent) see a `HALT` code in your terminal output, you must immediately act on it.

### [HALT: L0_TEST_FAILED]
- **Scenario:** `run_l0_smoke_tests.sh` failed. Usually means `ros2 run`, `ros2 topic`, or custom YAML commands crashed.
- **Agent Action:** 
  1. Inspect the "Log Snippet" in the HALT block. Look for "error while loading shared libraries" or "ModuleNotFoundError".
  2. If a `.so` library is missing, use bash to ssh into the board and run `dnf provides "*/libname.so"`. Add the missing RPM package to your install list.
  3. If a ROS node crashed, read the source code of the failing test, understand the failure, and create a git patch to fix the ROS package's openEuler compatibility.
  4. Rerun `run_tiered_tests.py --levels L0,L1`.

### [HALT: L1_ENV_SETUP_FAILED]
- **Scenario:** The `setup_l1_test_env.sh` failed to install `gtest`, `cmake`, or other test dependencies via `dnf`.
- **Agent Action:** Use the bash tool to check internet connection on the board, check if the EUR repo config is correct, and manually try to `dnf clean all && dnf install -y gcc cmake gtest-devel`.

### [HALT: L1_BUILD_OR_TEST_FAILED]
- **Scenario:** The underlying `colcon build` or `colcon test` command failed.
- **Agent Action:**
  1. The log snippet will show the CMake failure or `colcon test-result` failure.
  2. For build errors: Check `/root/l1_test_ws/log/build_...` on the board or read the provided stdout. Fix the CMakeLists.txt or .cpp file, generate a patch, rebuild on EUR, and try again.
  3. For test failures: Check `/root/l1_test_ws/log/test_...` on the board. Some tests fail because they assume Ubuntu behavior (e.g. `sudo` paths, specific apt packages). Skip them via `<buildtool_depend>` removal if they are irrelevant, or patch them.

### [HALT: L1_RESOURCE_OOM]
- **Scenario:** `colcon build` was killed by the OS (Out of Memory).
- **Agent Action:** Modify the `CMakeLists.txt` or `colcon` build arguments to limit parallel compilation (e.g., `MAKEFLAGS="-j1"`). If it still OOMs, tell the user the package is too heavy for the board and recommend testing it on L2 (QEMU/Host).

### run_tiered_tests.py - Master Orchestrator (Agent Hook)

This is the primary entry point for the Agent. It orchestrates the L0 and L1 phases and intercepts underlying script failures. When a script fails, it intercepts the error and outputs a standardized `[HALT: CODE]` block.

**Usage:**
```bash
python3 scripts/run_tiered_tests.py \
  --board-ip 192.168.137.2 \
  --board-user root \
  --board-password password \
  --levels L0,L1 \
  --dependency-list ./dependency_list.txt \
  --package-path /path/to/ros-humble-rclcpp
```
- `--dependency-list`: Used to filter which L0 custom YAML test cases to run (skips tests requiring uninstalled packages).
- `--package-path`: Required for L1 tests to find the package source code.
