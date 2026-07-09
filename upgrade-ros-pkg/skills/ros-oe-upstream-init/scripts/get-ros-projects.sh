#!/bin/bash
#
# get-ros-projects.sh
# Fetch ROS package information from upstream status page
#
# Usage: get-ros-projects.sh [ros_distro] [output_file]
#   ros_distro: ROS distribution (default: humble)
#   output_file: Output file path (default: output/ros-projects.list)
#

set -e

# Configuration
ROS_DISTRO="${1:-humble}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
OUTPUT_FILE="${2:-${ROS_UPSTREAM_WORKSPACE:-$SCRIPT_DIR}/output/ros-projects.list}"

# Upstream status page URL
STATUS_PAGE_URL="https://repo.ros2.org/status_page/ros_${ROS_DISTRO}_default.html"

echo "Fetching ROS ${ROS_DISTRO} package information from:"
echo "  ${STATUS_PAGE_URL}"
echo

# Create output directory if needed
OUTPUT_DIR="$(dirname "$OUTPUT_FILE")"
mkdir -p "$OUTPUT_DIR"

# Temporary file for HTML content
TMP_HTML=$(mktemp)

# Fetch the HTML page
if ! curl -s -f "$STATUS_PAGE_URL" -o "$TMP_HTML"; then
    echo "Error: Failed to fetch status page from $STATUS_PAGE_URL" >&2
    rm -f "$TMP_HTML"
    exit 1
fi

echo "Parsing package information..."
echo

# Temporary file for output
TMP_OUTPUT=$(mktemp)

# Parse HTML and extract package information
# Extract each table row and parse package name, repo URL, version, and status
grep -o '<tr[^>]*>.*</tr>' "$TMP_HTML" | \
    grep 'index.ros.org/p/' | \
    while IFS= read -r row; do
        # Extract package name from index.ros.org link
        pkg_name=$(echo "$row" | grep -oP "index\.ros\.org/p/\K[^#\"]+(?=#${ROS_DISTRO})" | head -1)

        # Convert underscores to hyphens for package name (ROS naming convention)
        pkg_name=$(echo "$pkg_name" | tr '_' '-')

        # Extract repository URL and remove .git suffix if present
        repo_url=$(echo "$row" | grep -oP '<div class="repo"><a href="\K[^"]+(?=")' | sed 's/"$//' | sed 's/\.git$//' | head -1)

        # Extract version number (from <span> tag before status class)
        version=$(echo "$row" | grep -oP '<span>\K[0-9]+\.[^<]+(?=</span>)' | head -1)

        # Extract status (maintained, developed, end-of-life, or unknown)
        status=$(echo "$row" | grep -oP '<span class="\K(maintained|developed|end-of-life|unknown)(?=")' | head -1)

        # Default to unknown if status not found
        status="${status:-unknown}"

        # Only output if we have the required fields
        if [ -n "$pkg_name" ] && [ -n "$repo_url" ] && [ -n "$version" ]; then
            printf '%s\t%s\t%s\t%s\n' "$pkg_name" "$repo_url" "$status" "$version"
        fi
    done > "$TMP_OUTPUT"

# Count packages
pkg_count=$(wc -l < "$TMP_OUTPUT")

# Write to output file
mv "$TMP_OUTPUT" "$OUTPUT_FILE"

# Cleanup
rm -f "$TMP_HTML"

echo "Done! Generated ${pkg_count} package entries."
echo "Output written to: $OUTPUT_FILE"
echo
echo "Format: package_name<TAB>repository_url<TAB>status<TAB>version"
echo "Status values: maintained | developed | end-of-life | unknown"
