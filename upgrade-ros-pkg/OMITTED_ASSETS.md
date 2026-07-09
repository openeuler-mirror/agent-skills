# Omitted Assets

The following source archives were intentionally not committed because they
exceed GitCode's 10 MiB per-file limit and this repository does not currently
use Git LFS.

| Source path in ros-porting-tools | Size | Reason |
| --- | ---: | --- |
| `.agents/skills/ros-oe-upstream-init/scripts/ros/humble/package_fix/rviz-ogre-vendor/ogre-rm-Media-1.12.1.tar.gz` | 12 MiB | Exceeds 10 MiB file limit |
| `.agents/skills/ros-oe-upstream-init/scripts/ros/humble/package_fix/zenoh-bridge-dds/zenoh-bridge-dds-cargo-vendor.tar.gz` | 31 MiB | Exceeds 10 MiB file limit |

Future cleanup options:

1. Replace committed source archives with download metadata and fetch scripts.
2. Move large archives to an approved artifact storage location.
3. Enable Git LFS if the target hosting policy allows it.
