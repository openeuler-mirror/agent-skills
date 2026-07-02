# ROS Porting Agent Skills

This directory contains the AI agent and skills for upgrading openEuler ROS packages.

## Contents

- `agents/openeuler-ros-upgrader.md`: Main orchestration agent for openEuler ROS package upgrade workflows.
- `skills/ros-oe-upstream-init`: Initialize upstream ROS metadata and packaging inputs.
- `skills/ros-oe-pkg-prep`: Analyze package dependencies and generate build layers.
- `skills/ros-oe-repo-fork`: Fork and clone src-openeuler package repositories.
- `skills/ros-oe-eur-init`: Initialize EUR build projects and package entries.
- `skills/ros-oe-eur-build`: Trigger EUR builds and analyze build results.
- `skills/ros-oe-pkg-update`: Update local specs, patches, tarballs, and package repositories.
- `skills/ros-oe-board-test`: Install and validate ROS packages on development boards.
- `skills/ros-oe-pr-submit`: Submit verified package changes as pull requests.

## Workflow Scope

`openeuler-ros-upgrader` covers the full ROS package upgrade flow:

1. Prepare workspace configuration and upstream package metadata.
2. Resolve recursive dependencies and build order.
3. Fork and clone target openEuler package repositories.
4. Merge generated specs with existing downstream patches and package assets.
5. Trigger EUR builds and diagnose build failures.
6. Run board-level smoke and functional tests.
7. Generate reviewable commits and pull requests for openEuler community submission.

## Notes

- Runtime outputs, logs, and Python bytecode caches are intentionally ignored.
- Large binary source archives should not be added unless the target repository has a clear storage policy.
- Sensitive credentials must stay in local user configuration and must not be committed.
