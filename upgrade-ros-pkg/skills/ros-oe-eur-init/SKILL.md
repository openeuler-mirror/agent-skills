> **[CRITICAL WARNING FOR AI AGENT]**
> - **NEVER** pass literal strings like `<your_username>` or `<your_project>` to the scripts.
> - **ALWAYS** extract the real `gitcode_username` and `personal_eur_project` from the `openeuler_ros_upgrader_env.yaml` file and pass them explicitly using `--username` and `--project`.
> - **BRANCH NAMING**: When specifying a branch for a personal/forked repository, NEVER use legacy names like `Multi-Version_ros-humble_...`. ALWAYS use `openEuler-<version>-spec` (e.g., `openEuler-24.03-spec`).


---
name: ros-oe-eur-init
description: "Batch add ROS packages to an openEuler EUR (COPR) project. Supports two modes: personal repository iteration validation and official repository building. Uses the copr-cli tool. Use this skill when the user mentions 'batch add EUR packages', 'batch create COPR packages', 'ROS repository initialization', 'copr add-package-scm batch operation', or 'ros_oe_eur_init'. Make sure to use this skill whenever the user mentions 'batch add packages to EUR', 'add packages to COPR project', 'initialize ROS repository', or 'batch create COPR packages'."
compatibility:
  - copr-cli
  - bash
---

# EUR ROS Repo Init

Batch add ROS packages to an openEuler EUR (COPR) project.

## Description
This skill is used to batch add ROS packages into an EUR (openEuler COPR) project. It utilizes the `copr-cli` command-line tool to interact with the EUR API.

## Mode Specifications
- **personal**: Personal repository mode, used for iterative validation and testing. URL pattern: `https://gitcode.com/{username}/{repo}.git`
- **official**: Official repository mode, used for formal builds. URL pattern: `https://gitcode.com/src-openeuler/{repo}.git`

## Package Name and Spec File Naming Conventions

**IMPORTANT**: ROS 2 RPM spec file naming strictly follows these conventions:

1. **Spec filenames use hyphens**: Even if the ROS package name uses underscores, the spec filename MUST use hyphens.
   - Package: `iceoryx_hoofs` → Spec: `iceoryx-hoofs.spec`
   - Package: `rosidl_cli` → Spec: `rosidl-cli.spec`
   - Package: `foonathan_memory_vendor` → Spec: `foonathan-memory-vendor.spec`

2. **Package name format conversion**: The skill automatically attempts underscore↔hyphen conversion to look up mappings in `ros-projects.list`.
   - The package `iceoryx_hoofs` from the input list will automatically match `iceoryx-hoofs` in `ros-projects.list`.

3. **Repository name mapping**: The repository name might differ from the package name. Use `ros-projects.list` to establish the correct mapping.
   - `iceoryx_hoofs` package → `iceoryx` repository
   - `rosidl_cli` package → `rosidl` repository
   - `fastcdr` package → `Fast-CDR` repository

## Command Line Parameters
```text
--project PROJECT           COPR project name (format: username/projectname) [Required]
--packages PACKAGES         Space-separated list of packages or file path [Required]
--mode MODE                 Repository mode: personal or official (Default: official)
--username USERNAME         Personal repository username (Default: your-gitcode-username)
--branch BRANCH             Git branch (Default: humble)
--spec SPEC                 Spec file path template (Default: {pkg}.spec)
--method METHOD             Build method (Default: rpkg)
--type TYPE                 SCM type (Default: git)
--chroot CHROOT             Target chroot (Default: openEuler-24.03-LTS-aarch64)
--help, -h                  Show help message
--skip-existing             Skip packages that already exist in the project
--dry-run                   Preview commands only, do not execute
--list-packages             List packages currently in the project
--delete                    Delete packages
--build                     Trigger a build immediately after adding packages
```

## Package Name to Repository Mapping
Mapping is performed via the `ros-projects.list` file, located at:
`<ros_upstream_workspace>/output/ros-projects.list`

**Format**: Package Name followed by Upstream Repository Link. The link contains the repository name. For example, the repository name for `ros-base` is `variants`, and for `ros-environment` it is `ros_environment`.
Example:
```text
ros-base	https://github.com/ros2/variants/tree/master	maintained	0.10.0-1
ros-core	https://github.com/ros2/variants/tree/master	maintained	0.10.0-1
ros-environment	https://github.com/ros/ros_environment/tree/humble	maintained	3.2.2-1
ros-gz	https://github.com/gazebosim/ros_gz/tree/humble	maintained	0.244.9-1
```
If no mapping is found, it defaults to assuming the package name is identical to the repository name.

## Execution Flow
1. Check environment: Run `copr-cli whoami` to verify configuration.
2. Parse package list: Read from parameters or file.
3. Lookup repository mapping: Search via `ros-projects.list`.
4. Generate and execute commands: Add each package sequentially, displaying progress.
5. Generate report: Output mapping relationship file and execution summary.

## Examples
```bash
# Add a single package (using default config)
/ros-oe-eur-init --project your-gitcode-username/your-eur-project --packages rclcpp

# Batch add from file (personal repo mode)
/ros-oe-eur-init --project your-gitcode-username/my-project --package-list-file packages.txt --mode personal --username myuser

# Batch add from file, custom branch
/ros-oe-eur-init --project your-gitcode-username/your-eur-project --package-list-file packages.txt --branch Multi-Version_ros-humble_openEuler-24.03-LTS

# List packages in the project
/ros-oe-eur-init --project your-gitcode-username/your-eur-project --list-packages

# Script preview mode (Dry run)
/ros-oe-eur-init --project your-gitcode-username/your-eur-project --package-list-file packages.txt --dry-run
```

## Notes
1. The `{pkg}` placeholder in templates will be replaced with the actual package name before looking up the corresponding repository name.
2. The `{pkg}` placeholder in the spec file path will be replaced with the actual package name.
- When using the `--spec` parameter, specify the relative path of the spec file within the repository.
- When using the `--subdir` parameter, specify the repository subdirectory.

---
## Output Language Rules

- **Internal Reasoning & Tools**: Use English for all internal thinking, tool inputs, and terminal commands.
- **User-Facing Reports**: **CRITICAL** - Whenever you generate a summary, log file, or report meant for the user to read based on the EUR initialization results (e.g., reporting which packages were successfully added to COPR, which failed, or the mapping summaries), you MUST write the content entirely in **Simplified Chinese**.
