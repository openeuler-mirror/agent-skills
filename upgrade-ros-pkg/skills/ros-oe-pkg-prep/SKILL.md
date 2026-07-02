---
name: ros-oe-pkg-prep
description: "ROS package dependency resolution skill for analyzing recursive dependencies and generating build order"
compatibility:
  - "python3"
  - "bash"
---

## Usage Instructions

This skill resolves ROS package dependencies and generates build order. It will:

1. Validate the upstream-dir path exists
2. Automatically convert upstream-dir to correct deps path (upstream-dir/output/deps)
3. Execute the dependency resolution script
4. Generate a build order file

### Parameter Format

This skill supports multiple operating modes depending on the provided parameters:

```bash
# Mode 1: Single Package Resolution (Find all recursive dependencies)
/ros-oe-pkg-prep --target-package <package-name> --upstream-dir /path/to/ros-oe-upstream-init

# Mode 2: Explicit Batch Prep (Auto-expand siblings + Topological sort + Generate Parallel Layers)
/ros-oe-pkg-prep --explicit-list <path/to/input.txt> --upstream-dir /path/to/ros-oe-upstream-init

# Mode 3: Standalone Layer Generation (Convert existing linear list to parallel layers)
/ros-oe-pkg-prep --generate-layers <path/to/dependency_list.txt> --upstream-dir /path/to/ros-oe-upstream-init
```

### Usage Examples

```bash
# Resolve dependencies for rclcpp package
/ros-oe-pkg-prep --target-package rclcpp --upstream-dir /path/to/ros-oe-upstream-init

# Prepare explicit batch for EUR (expands siblings and outputs build_layers.txt)
/ros-oe-pkg-prep --explicit-list ./packages.txt --upstream-dir ./ros-oe-upstream-init

# Convert an existing dependency list into EUR parallel build layers
/ros-oe-pkg-prep --generate-layers release/20260402/dependency_list.txt --upstream-dir ./src-openeuler-forked/ros-oe-upstream-init
```

### Implementation Steps

When this skill is invoked, you should determine the mode based on the user's request:

**Mode 1 (Single Package):**
1. Extract `<target-package>` and `<upstream-dir>`
2. Execute:
   ```bash
   python3 <skill_dir>/ros-oe-pkg-prep/scripts/resolve_dependencies.py <target-package> <upstream-dir>/output/deps -o <target-package>_build_order.txt
   ```

**Mode 2 (Explicit Batch Prep - Recommended for EUR):**
1. Extract `<explicit-list>` and `<upstream-dir>`
2. Execute `parse_package_list.py` which will automatically call `generate_build_layers.py`:
   ```bash
   python3 <skill_dir>/ros-oe-pkg-prep/scripts/parse_package_list.py <explicit-list> <upstream-dir> -o dependency_list.txt --layers-output build_layers.txt
   ```
3. Report the path to the generated `build_layers.txt`.

**Mode 3 (Standalone Layer Generation):**
1. Extract `<generate-layers>` (path to linear list) and `<upstream-dir>`
2. Execute:
   ```bash
   python3 <skill_dir>/ros-oe-pkg-prep/scripts/generate_build_layers.py <generate-layers> <upstream-dir>/output/deps -o build_layers.txt --json build_layers.json
   ```
3. Show the Layer distribution summary and path to the output.

### Function Description

This skill will:
1. Recursively analyze all dependencies of the specified ROS package
2. Distinguish between system dependencies and ROS package dependencies
3. Generate correct build order using topological sorting algorithm
4. Output detailed dependency analysis report
5. Generate build order file for future use

### Output

The script will output:
- Dependency tree analysis
- System dependencies (filtered out)
- Dependency level statistics
- Build order list (topological sort)

A file `<target-package>_build_order.txt` will be generated containing the ordered package list.

### Notes

- The deps directory should contain corresponding `-PackageXml` files
- System dependencies are automatically filtered and don't need to be built
- If circular dependencies exist, warning messages will be displayed
- The build order includes both build-time and runtime dependencies

---

## Explicit Upgrade Mode (parse_package_list.py)

### Use Case

When the user specifies an `explicit` upgrade mode, they only want to upgrade the packages explicitly listed in their input file. However, simply using the input list directly causes two problems:
1. **Build Order**: The packages must still be topologically sorted to build successfully on EUR.
2. **Multi-Repo Consistency**: Some ROS repositories contain multiple packages (e.g., `rclcpp`, `rclcpp_components`, `rclcpp_action` all live in the `rclcpp` repo). EUR builds the entire repository at once, so all sibling packages must be included in the upgrade list.

### Usage

The script `parse_package_list.py` solves this by expanding the explicit list to include sibling packages from the same repository, topologically sorting the full dependency graph for these packages, and then filtering the result to *only* include the requested packages and their siblings (without pulling in unrequested lower-level dependencies).

```bash
python3 <skill_dir>/ros-oe-pkg-prep/scripts/parse_package_list.py \
    input_packages.txt \
    /path/to/ros-oe-upstream-init \
    -o dependency_list.txt \
    --layers-output build_layers.txt
```

### Features

1. **Hyphen/Underscore Normalization**: Automatically treats `-` and `_` as equivalent to handle package naming inconsistencies gracefully when looking up spec files.
2. **Multi-Build Expansion**: Finds the spec file for a requested package, checks if its directory contains other `.spec` files, and automatically adds those sibling packages to the requested list.
3. **Strict Topological Sorting**: Uses the core dependency resolution logic to sort the expanded list so that base packages are built before dependent packages, ensuring EUR build success.
4. **Parallel Build Layers Generation (New)**: Automatically groups the packages into deterministic topological layers (`build_layers.txt`). Packages in `Layer N` only depend on packages in `Layer 0` to `Layer N-1`. This guarantees that all packages within the same layer can be safely triggered for parallel builds in EUR without dependency races.

---

## Parallel Build Layers (generate_build_layers.py)

### Use Case

When building packages on the openEuler EUR (COPR) cloud platform, submitting packages one by one is extremely slow. Submitting all of them at once causes build failures and race conditions because EUR doesn't always automatically wait for missing dependencies to appear in the repo. 

To maximize EUR's parallel build capabilities safely, we group the target packages into **Topological Layers**.

### Usage

This script is automatically called by `parse_package_list.py`, but can also be used standalone on any flat list of packages:

```bash
python3 <skill_dir>/ros-oe-pkg-prep/scripts/generate_build_layers.py \
    dependency_list.txt \
    /path/to/ros-oe-upstream-init/output/deps \
    -o build_layers.txt \
    --json build_layers.json
```

### How it works

1. It reads the flat target list and builds a strictly constrained dependency subgraph containing *only* those packages.
2. It calculates the in-degree (number of dependencies) for each package within that subgraph.
3. Packages with an `in-degree == 0` are assigned to **Layer 0** and removed from the graph.
4. This unblocks the next wave of packages (`in-degree` drops to 0), which become **Layer 1**, and so on.
5. The result is a mathematically perfect schedule where `Layer X` can be fully parallelized once `Layer X-1` is complete.

---

## Advanced Feature: Batch Merge Multiple ROS Packages

### Use Case

When you need to upgrade or build multiple ROS packages simultaneously, their dependencies may overlap. This feature allows you to:

1. **Merge dependencies** from multiple target packages
2. **Automatically deduplicate** common dependencies
3. **Generate a unified build order** that respects all dependency relationships
4. **Avoid redundant builds** by sharing common dependencies

### Scenario Examples

**Scenario 1: Robot System Upgrade**
```
You need to upgrade: joint_state_publisher, robot_state_publisher, tf2_ros, urdf
These packages share many common dependencies like rclcpp, geometry_msgs, etc.

Using merge_package_sources.py:
- Input: 4 target packages
- Output: ~120 unique packages (not 300+)
- Build order: correctly sorted with all dependencies
```

**Scenario 2: Perception Stack**
```
Target: cv_bridge, perception_pcl, realsense2_camera, rtabmap_ros
These packages share: sensor_msgs, vision_opencv, pcl_msgs, etc.

Merge script will:
- Analyze each package's dependency tree
- Combine and deduplicate
- Generate single build order
```

### Usage

#### Method 1: Direct Script Execution

```bash
# Basic usage
python3 <skill_dir>/ros-oe-pkg-prep/scripts/merge_package_sources.py packages.txt output/deps

# With custom output file
python3 <skill_dir>/ros-oe-pkg-prep/scripts/merge_package_sources.py packages.txt output/deps -o my_build_order.txt

# Keep temporary files for debugging
python3 <skill_dir>/ros-oe-pkg-prep/scripts/merge_package_sources.py packages.txt output/deps --keep-temp

# Specify custom work directory
python3 <skill_dir>/ros-oe-pkg-prep/scripts/merge_package_sources.py packages.txt output/deps -w my_workspace
```

#### Method 2: Invoke via Skill

When you need to merge multiple packages, tell the skill:

```
"I need to merge dependencies for these packages: rclcpp, geometry_msgs, sensor_msgs"
```

The skill will:
1. Create a package list file
2. Call merge_package_sources.py
3. Show results and statistics

### Input File Format

Create a text file with one package name per line:

```text
# packages.txt
rclcpp
geometry_msgs
sensor_msgs
joint_state_publisher
nlohmann_json
```

- Lines starting with `#` are comments
- Empty lines are ignored
- Duplicate packages are automatically removed

### Output

The script generates:

1. **Build Order File** (`merged_build_order.txt` by default)
   - **Pure package names only** (no markers, no line numbers)
   - Ready for automated processing
   - Can be directly used by other skills/scripts
   - Format: One package name per line

2. **Marked Build Order File** (`merged_build_order_marked.txt`)
   - Human-readable version with line numbers
   - Target packages marked with `★ [目标]`
   - Useful for manual review and verification

3. **Detailed Log** (`merge_log.txt`)
   - Analysis progress for each package
   - Statistics and timing information
   - Success/failure status

4. **Work Directory** (with timestamp)
   - All intermediate files
   - Temporary dependency files (cleaned by default)
   - Logs and statistics

#### File Format Examples

**Pure version (for automation):**
```text
# ROS包构建顺序（整合自 5 个目标包）
# 生成时间: 2026-03-17 15:19:21
# 总计: 89 个包
# 目标包: ament_cmake, geometry_msgs, joint_state_publisher

ament_package
ament_cmake_core
ament_cmake_libraries
...
rclcpp
geometry_msgs
...
joint_state_publisher
```

**Marked version (for human review):**
```text
# ROS包构建顺序（带标记版本，便于人工查看）
# 总计: 89 个包

  1. ament_package
  2. ament_cmake_core
  ...
 17. ament_cmake ★ [目标]
 ...
 78. rclcpp ★ [目标]
 80. geometry_msgs ★ [目标]
 ...
 89. joint_state_publisher ★ [目标]
```

### Example Output

```
============================================================
整合后的构建顺序
============================================================
总包数: 89

前 20 个包:
  1. ament_package
  2. ament_cmake_core
  ...

... (中间省略 49 个包) ...

后 20 个包:
 70. rmw_fastrtps_cpp
 71. rmw_implementation
 72. rcl
 73. rclcpp ★ [目标]
 74. rclpy
 75. joint_state_publisher ★ [目标]

✅ 构建顺序已保存到: merge_work_20260317_150830/merged_build_order.txt
📝 详细日志: merge_work_20260317_150830/merge_log.txt

📊 统计信息:
  输入:
    目标包: 5 个
    成功: 5 个
  输出:
    依赖包: 84 个
    总计: 89 个

🧹 已清理 5 个临时文件
```

### Algorithm Details

The merge script uses a **first-seen ordering** strategy:

1. **Individual Analysis**: For each target package, call `resolve_dependencies.py` to get its complete dependency tree
2. **Ordered Merge**: Combine all packages in the order they first appear
3. **Dependency Preservation**: Since each individual tree is correctly ordered, the merged result maintains valid build order
4. **Smart Deduplication**: Common dependencies are kept once, in their earliest position

This approach ensures:
- ✅ Correct build order (dependencies before dependents)
- ✅ No missing dependencies
- ✅ Minimal redundant builds
- ✅ Efficient processing

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `-o <file>` | Output build order file | `merged_build_order.txt` in work dir |
| `-w <dir>` | Work directory for intermediate files | `merge_work_YYYYMMDD_HHMMSS` |
| `--keep-temp` | Keep temporary files (for debugging) | Clean up automatically |

### Best Practices

1. **Use time-stamped work directories** (default behavior) to avoid file conflicts
2. **Review the log file** to identify failed packages
3. **Check the build order** before starting compilation
4. **Use `--keep-temp`** when debugging dependency issues
5. **Clean up old work directories** periodically

### Troubleshooting

**Q: Some packages failed to analyze**
```
A: Check if the package exists in deps directory
   Verify PackageXml file is present
   Review merge_log.txt for error details
```

**Q: Build order seems incorrect**
```
A: Verify individual package dependencies first
   Use resolve_dependencies.py separately
   Check for version conflicts or missing packages
```

**Q: Too many work directories accumulated**
```
A: Safe to delete old directories after verification
   Only keep: merged_build_order.txt and merge_log.txt
   Temporary files are cleaned by default
```

### Performance

- **Small batches** (< 10 packages): < 1 minute
- **Medium batches** (10-50 packages): 2-5 minutes
- **Large batches** (50+ packages): 5-15 minutes

Time depends on:
- Number of target packages
- Complexity of dependency trees
- Disk I/O speed

### Integration with Build Workflow

After generating merged build order:

1. **Verify the order**
   ```bash
   # Check human-readable version
   head -20 merge_work_*/merged_build_order_marked.txt
   tail -20 merge_work_*/merged_build_order_marked.txt
   ```

2. **Use for batch building** (simplified, no need to parse markers)
   ```bash
   # The pure version can be used directly
   while read pkg; do
       # Skip comments
       [[ "$pkg" =~ ^# ]] && continue
       [ -z "$pkg" ] && continue

       echo "Building: $pkg"
       build_package "$pkg"
   done < merge_work_*/merged_build_order.txt
   ```

3. **Use with other skills**
   ```bash
   # The pure version is skill-friendly
   packages=$(grep -v '^#' merge_work_*/merged_build_order.txt)

   # Can be easily parsed by other scripts
   cat merge_work_*/merged_build_order.txt | \
       grep -v '^#' | \
       grep -v '^$' | \
       while read pkg; do
           process_package "$pkg"
       done
   ```

4. **Track progress**
   - Mark packages as built
   - Resume from failures
   - Generate build reports


---
## Output Language Rules

- **Internal Reasoning & Tools**: Use English for all internal thinking, tool inputs, and terminal commands.
- **User-Facing Reports**: **CRITICAL** - Whenever you generate a summary, log file, or report meant for the user to read based on the dependency analysis (e.g., explaining why a package is missing, reporting the final layer structure, or summarizing the dependency tree), you MUST write the content entirely in **Simplified Chinese**.
