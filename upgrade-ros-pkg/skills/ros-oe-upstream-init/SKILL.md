# ros-oe-upstream-init

## Description
This skill encapsulates the `ros-oe-upstream-init` initialization process. It fetches the complete upstream ROS packages list, source code, metadata, resolves dependencies, and generates RPM spec templates for the openEuler ROS environment. 

This serves as **Phase 0** in the ROS upgrade workflow. It constructs the offline Upstream ROS Universe cache pool which is required before any single ROS package can be ported or upgraded.

*(Note: This skill is an enhanced and streamlined version of the original [openEuler ros-porting-tools](https://gitcode.com/openeuler/ros-porting-tools/tree/master), completely refactoring the directory structure, preventing `-release` repo disguises, and adding an automatic static spec bypass mechanism for 3rd-party C++ libraries.)*

**⚠️ CRITICAL WARNING - HIGH EXECUTION COST ⚠️**
Running this skill from scratch (full `vcs clone`) takes **2 to 3 hours** depending on network speed. Even if repositories are already cloned and just need updating, it takes around **30 minutes**. 
Because of this, **the Agent MUST ALWAYS ask the user** if they already have an initialized `ros-oe-upstream-init` or upstream cache directory before deciding to run this skill. 

## When to use this skill
Use this skill ONLY when:
1. The user explicitly asks you to initialize the upstream environment.
2. The user is starting a ROS package upgrade task but responds "No" when asked if they already have a local upstream cache directory available.

## How to use this skill (Agent Workflow)

### 1. The Inquiry (Agent-in-the-Loop)
Before invoking the script, you MUST ask the user:
> "Do you already have an initialized `ros-oe-upstream-init` (or upstream fetch) cache directory? If so, please provide the absolute path. This will save hours of downloading. If you don't have one, please reply 'No' and I will initialize a new one for you."

### 2. Fast Path (User provides a path)
If the user provides a path (e.g., `/path/to/user/ros-oe-upstream-init`):
- Verify that the path exists and contains an `output/` subdirectory (using `ls` or `read`).
- If valid, **DO NOT run this skill's scripts**. Simply save this path to your working context (e.g., in your `env.yaml`) as the upstream environment and proceed to the next phase of your task.

### 3. Fallback Path (Running the Skill)
If the user says "No" or provides an invalid path, you must run the initialization process.

1. **Choose a persistent cache directory**: Select a location on the host machine to serve as the persistent cache, for example, `/workspace/ros-upstream-cache` or `~/.cache/ros-upstream-cache/` (you can ask the user for a preferred location if you wish).
2. **Execute (CRITICAL STEP)**: Run the setup script, pointing it to your chosen workspace. **Since this clones hundreds of repositories, it can take 10-30 minutes or more. You MUST set the `timeout` parameter of your `bash` tool to at least `3600000` (1 hour) to ensure you wait for it to complete synchronously.** Do NOT use `nohup` or `&` to run it in the background, as you will lose track of its completion status.
   ```bash
   bash <skill_dir>/scripts/ros-upstream-setup.sh -w /path/to/chosen/cache_dir
   ```
3. **Handle Errors / Mismatches**:
   - If the script outputs `[WARNING: VERSION_MISMATCH]`, it means the upstream ROS status page points to the wrong tracking branches for some packages (e.g., `main` instead of `humble`), causing incorrect version downloads.
   - A `version_mismatch.log` file will be generated in the `output/` directory.
   - **Crucially: You do NOT need to halt or fix these immediately during initialization**, as you don't know which of these 100+ packages the user will actually need. The script will continue to completion.
   - Instead, save the location of `version_mismatch.log` to your context. Later, during the `ros-oe-pkg-update` phase, you will check if the *target packages* are in this list and fix them on-demand.
4. **Monitor & Review**: The script will print progress. Once completed, review the generated report at `output/log/<timestamp>/ros-setup-report.txt` to verify successful packages.
5. **Notify User**: Inform the user about the newly created cache directory path so they can reuse it in the future.

## Output Structure
After a successful run (or when validating a user-provided path), the `output/` directory will contain:
- `output/ros.repos`: vcs repository configuration
- `output/src/`: Cloned source code with git histories
- `output/deps/`: Dependency analysis files per package
- `output/repo/`: Generated spec files and tarballs
- `output/log/`: Execution logs and reports

## Troubleshooting & Retry Strategy (IMPORTANT)
If you encounter errors during initialization, missing URLs, or need to intervene in the process:
1. **Read the Reference Guide**: ALWAYS consult `<skill_dir>/references/pipeline_and_configs.md` first to understand the pipeline flow, configurations, and where to inject fixes.
2. **DO NOT RE-RUN FULL VCS**: If an intermediate step fails, or you add a `custom.spec` / URL fix, **DO NOT run `./ros-upstream-setup.sh` again**. This will trigger a full VCS clone and waste hours. 
3. **Resume from Breakpoint**: Apply the fix (in `config/` or `package_fix/`), and run the specific intermediate scripts needed (e.g., just `./get-pkg-src.sh && ./gen-pkg-spec.sh`). See the reference guide for detailed instructions.