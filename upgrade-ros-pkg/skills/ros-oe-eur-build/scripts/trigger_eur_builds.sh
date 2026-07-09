#!/bin/bash
# Trigger ROS package builds on openEuler EUR (COPR) platform

set -e

# Default values
COPR_PROJECT="${EUR_PROJECT:-your-gitcode-username/your-eur-project}"
MODE="official"
USERNAME="${GITCODE_USERNAME:-your-gitcode-username}"
BRANCH="humble"
WORKSPACE_DIR=""
PACKAGE_LIST_FILE=""
POLL_INTERVAL=60  # seconds
MAX_WAIT=240      # minutes per package (4 hours)
FORCE_REBUILD=0
BUILD_LOG_DIR=""
DEFAULT_MAPPING_FILE="${ROS_UPSTREAM_WORKSPACE:-$PWD}/output/ros-projects.list"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --workspace-dir)
            WORKSPACE_DIR="$2"
            shift 2
            ;;
        --package-list-file)
            PACKAGE_LIST_FILE="$2"
            shift 2
            ;;
        --copr-project)
            COPR_PROJECT="$2"
            shift 2
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --mode)
            MODE="$2"
            shift 2
            ;;
        --username)
            USERNAME="$2"
            shift 2
            ;;
        --poll-interval)
            POLL_INTERVAL="$2"
            shift 2
            ;;
        --force)
            FORCE_REBUILD=1
            shift 1
            ;;
        --max-wait)
            MAX_WAIT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 --workspace-dir <dir> --package-list-file <file> [--copr-project <project>] [--mode <mode>] [--username <username>] [--branch <branch>] [--poll-interval <seconds>] [--max-wait <minutes>]"
            exit 1
            ;;
    esac
done

# Validate required parameters
if [ -z "$WORKSPACE_DIR" ]; then
    echo -e "${RED}Error: --workspace-dir is required${NC}"
    exit 1
fi

if [ -z "$PACKAGE_LIST_FILE" ]; then
    echo -e "${RED}Error: --package-list-file is required${NC}"
    exit 1
fi

# Convert workspace to absolute path
WORKSPACE_DIR=$(cd "$WORKSPACE_DIR" && pwd)

# Validate workspace directory
if [ ! -d "$WORKSPACE_DIR" ]; then
    echo -e "${RED}Error: Workspace directory does not exist: $WORKSPACE_DIR${NC}"
    exit 1
fi

# Validate package list file
PACKAGE_LIST_PATH="$WORKSPACE_DIR/$PACKAGE_LIST_FILE"
if [ ! -f "$PACKAGE_LIST_PATH" ]; then
    echo -e "${RED}Error: Package list file does not exist: $PACKAGE_LIST_PATH${NC}"
    exit 1
fi

# Create build log directory
BUILD_LOG_DIR="$WORKSPACE_DIR/eur_build_logs"
mkdir -p "$BUILD_LOG_DIR"

# State file to track successful builds
SUCCESS_STATE_FILE="$WORKSPACE_DIR/eur_build_success.list"

# Read package list and parse layers
LAYERS=()
CURRENT_LAYER=""
TOTAL_PACKAGES=0

while IFS= read -r line || [ -n "$line" ]; do
    line=$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [ -z "$line" ]; then continue; fi
    
    if [[ "$line" =~ ^###[[:space:]]*Layer[[:space:]]+([0-9]+)[[:space:]]*###$ ]]; then
        if [ -n "$CURRENT_LAYER" ]; then
            LAYERS+=("$CURRENT_LAYER")
            CURRENT_LAYER=""
        fi
        continue
    fi
    
    if [[ "$line" =~ ^# ]]; then continue; fi
    
    if [ -z "$CURRENT_LAYER" ]; then
        CURRENT_LAYER="$line"
    else
        CURRENT_LAYER="$CURRENT_LAYER $line"
    fi
    TOTAL_PACKAGES=$((TOTAL_PACKAGES + 1))
done < "$PACKAGE_LIST_PATH"

if [ -n "$CURRENT_LAYER" ]; then
    LAYERS+=("$CURRENT_LAYER")
fi

# If no layers were found but packages exist, treat as a single layer
if [ ${#LAYERS[@]} -eq 0 ] && [ $TOTAL_PACKAGES -gt 0 ]; then
    LAYERS+=("$CURRENT_LAYER")
fi

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}EUR Build Trigger (Layered)${NC}"
echo -e "${BLUE}==========================================${NC}"
echo "Workspace: $WORKSPACE_DIR"
echo "Package list file: $PACKAGE_LIST_FILE"
echo "COPR Project: $COPR_PROJECT"
echo "Mode: $MODE"
echo "Username: $USERNAME"
echo "Branch: $BRANCH"
echo "Total layers: ${#LAYERS[@]}"
echo "Total packages to build: $TOTAL_PACKAGES"
echo -e "${BLUE}==========================================${NC}"
echo ""

# Function to get repo name from mapping file
get_repo_name() {
    local pkg_name="$1"
    local cache_file="$HOME/.cache/fork-src-openeuler/package_repo_map.json"
    
    if [ -f "$cache_file" ]; then
        local repo=$(python3 -c "import json,sys; data=json.load(open('$cache_file')); print(data.get('mapping', {}).get('$pkg_name', ''))" 2>/dev/null)
        if [ -n "$repo" ]; then
            echo "$repo"
            return
        fi
    fi

    # 降级逻辑
    echo "$pkg_name" | sed 's/_/-/g'
}

# Function to check environment
check_environment() {
    echo -e "${BLUE}Checking environment...${NC}"

    # Check if copr-cli is installed
    if ! command -v copr-cli &> /dev/null; then
        echo -e "${RED}Error: copr-cli is not installed${NC}"
        echo "Install it with: dnf install copr-cli"
        exit 1
    fi

    # Check if copr-cli is configured
    if ! copr-cli whoami &> /dev/null; then
        echo -e "${RED}Error: copr-cli is not configured${NC}"
        echo "Please configure it first:"
        echo "  mkdir -p ~/.config"
        echo "  cat > ~/.config/copr << EOF"
        echo "  [copr-cli]"
        echo "  login = <your_login>"
        echo "  token = <your_token>"
        echo "  copr_url = https://eur.openeuler.openatom.cn"
        echo "  EOF"
        echo "  chmod 600 ~/.config/copr"
        echo ""
        echo "Get your API token from: https://eur.openeuler.openatom.cn/coprs/<user>/api/"
        exit 1
    fi

    local USER=$(copr-cli whoami 2>/dev/null)
    echo -e "${GREEN}✓ copr-cli configured for user: $USER${NC}"
}

# Function to trigger build for a package
trigger_build() {
    local package_name=$1
    local attempt=0
    local max_attempts=3

    echo -e "${BLUE}Triggering build for: $package_name${NC}" >&2
    echo "  COPR Project: $COPR_PROJECT" >&2

    while [ $attempt -lt $max_attempts ]; do
        attempt=$((attempt + 1))

        local BUILD_OUTPUT
        BUILD_OUTPUT=$(copr-cli build-package \
            --name "$package_name" \
            --nowait \
            "$COPR_PROJECT" 2>&1)

        # Handle EUR package renaming
        if echo "$BUILD_OUTPUT" | grep -q "No package with name"; then
            echo -e "\033[0;33m  Package '$package_name' not found. EUR might have renamed it. Trying 'ros-humble-${package_name}'...\033[0m" >&2
            BUILD_OUTPUT=$(copr-cli build-package \
                --name "ros-humble-$package_name" \
                --nowait \
                "$COPR_PROJECT" 2>&1)
        fi

        local BUILD_ID=$(echo "$BUILD_OUTPUT" | grep "Created builds:" | awk '{print $3}')

        if [ -n "$BUILD_ID" ]; then
            echo -e "${GREEN}✓ Build triggered successfully${NC}" >&2
            echo "  Build ID: $BUILD_ID" >&2
            echo "$BUILD_ID"
            return 0
        else
            echo -e "${YELLOW}Attempt $attempt failed, retrying...${NC}" >&2
            sleep 2
        fi
    done

    echo -e "${RED}✗ Failed to trigger build for $package_name after $max_attempts attempts${NC}" >&2
    echo "" >&2
    return 1
}

get_build_status() {
    local build_id=$1
    copr-cli status "$build_id" 2>/dev/null
}

# Function to download build logs
download_build_log() {
    local build_id=$1
    local package_name=$2
    local output_dir="$BUILD_LOG_DIR"
    local formatted_id=$(printf "%08d" $build_id)
    local project_path="${COPR_PROJECT//\//\/}"

    # Possible architectures/chroots
    local chroot="openeuler-24.03_LTS-aarch64"
    local chroot_alt="openEuler-24.03-LTS-aarch64"

    # Possible package names (original or EUR-renamed)
    local pkg_variations=("$package_name" "ros-humble-$package_name")

    # Define all possible log paths to check
    local candidate_urls=()
    
    # 1. SRPM phase
    candidate_urls+=("https://eur.openeuler.openatom.cn/results/${project_path}/srpm-builds/${formatted_id}/builder-live.log.gz")
    candidate_urls+=("https://eur.openeuler.openatom.cn/results/${project_path}/srpm-builds/${formatted_id}/builder-live.log")

    # 2. RPM phase (with different package name variations and chroots)
    for pkg in "${pkg_variations[@]}"; do
        candidate_urls+=("https://eur.openeuler.openatom.cn/results/${project_path}/${chroot}/${formatted_id}-${pkg}/builder-live.log.gz")
        candidate_urls+=("https://eur.openeuler.openatom.cn/results/${project_path}/${chroot}/${formatted_id}-${pkg}/builder-live.log")
        
        candidate_urls+=("https://eur.openeuler.openatom.cn/results/${project_path}/${chroot_alt}/${formatted_id}-${pkg}/builder-live.log.gz")
        candidate_urls+=("https://eur.openeuler.openatom.cn/results/${project_path}/${chroot_alt}/${formatted_id}-${pkg}/builder-live.log")
    done

    mkdir -p "$output_dir"
    echo -e "${BLUE}Downloading build log for $package_name (Build $build_id)...${NC}"

    local success=0
    local log_file="${output_dir}/${package_name}_build_${build_id}.log"
    local tmp_file="${log_file}.tmp"

    for url in "${candidate_urls[@]}"; do
        # Try downloading the file quietly
        if curl -s -f "$url" -o "$tmp_file"; then
            # We found a valid log file!
            if [[ "$url" == *.gz ]]; then
                gunzip -c "$tmp_file" > "$log_file" 2>/dev/null
                rm -f "$tmp_file"
            else
                mv "$tmp_file" "$log_file"
            fi
            
            echo -e "${GREEN}✓ Build log found and saved to: ${log_file}${NC}" >&2
            echo "$log_file"
            success=1
            break
        fi
    done

    if [ $success -eq 0 ]; then
        echo -e "${YELLOW}⚠ Failed to download build log from any known location.${NC}" >&2
        rm -f "$tmp_file"
        return 1
    fi
    return 0
}

# Function to poll build status
poll_status() {
    local build_id=$1
    local package_name=$2
    local start_time=$(date +%s)
    local max_wait_seconds=$((MAX_WAIT * 60))

    echo -e "${BLUE}Polling build status for: $package_name (Build ID: $build_id)${NC}"

    while true; do
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))

        if [ $elapsed -ge $max_wait_seconds ]; then
            echo -e "${YELLOW}⚠ Build timeout after ${MAX_WAIT} minutes${NC}"
            echo "timeout"
            return
        fi

        local STATUS=$(get_build_status "$build_id")

        case $STATUS in
            "pending")
                echo -e "  [$((elapsed / 60))m ${elapsed}s] Status: ${YELLOW}pending${NC} - Waiting in queue..."
                ;;
            "running")
                echo -e "  [$((elapsed / 60))m ${elapsed}s] Status: ${BLUE}running${NC} - Building..."
                ;;
            "succeeded")
                echo -e "  [$((elapsed / 60))m ${elapsed}s] Status: ${GREEN}succeeded${NC} ✓"
                return 0
                ;;
            "failed")
                echo -e "  [$((elapsed / 60))m ${elapsed}s] Status: ${RED}failed${NC} ✗"
                return 1
                ;;
            "canceled")
                echo -e "  [$((elapsed / 60))m ${elapsed}s] Status: ${YELLOW}canceled${NC}"
                return 2
                ;;
            "skipped")
                echo -e "  [$((elapsed / 60))m ${elapsed}s] Status: ${YELLOW}skipped${NC}"
                return 3
                ;;
            *)
                echo -e "  [$((elapsed / 60))m ${elapsed}s] Status: ${YELLOW}unknown ($STATUS)${NC}"
                ;;
        esac

        sleep $POLL_INTERVAL
    done
}

# Function to collect build logs
collect_logs() {
    local build_id=$1
    local package_name=$2
    local status=$3

    # Always download the full build log
    download_build_log "$build_id" "$package_name"

    local log_file="$BUILD_LOG_DIR/${package_name}_build_${build_id}.log"

    # Save build metadata
    local metadata_file="$BUILD_LOG_DIR/${package_name}_metadata_${build_id}.txt"
    echo "Build ID: $build_id" > "$metadata_file"
    echo "Package: $package_name" >> "$metadata_file"
    echo "Status: $status" >> "$metadata_file"
    echo "Build URL: https://eur.openeuler.openatom.cn/results/${COPR_PROJECT//\//\/}/srpm-builds/$(printf "%07d" $build_id)/" >> "$metadata_file"
    echo "Log URL: https://eur.openeuler.openatom.cn/results/${COPR_PROJECT//\//\/}/srpm-builds/$(printf "%07d" $build_id)/builder-live.log.gz" >> "$metadata_file"
}

# Function to analyze failed build
analyze_failure() {
    local package_name=$1
    local build_id=$2
    local log_file="$BUILD_LOG_DIR/${package_name}_build_${build_id}.log"

    echo -e "${RED}==========================================${NC}"
    echo -e "${RED}Analyzing failure: $package_name (Build $build_id)${NC}"
    echo -e "${RED}==========================================${NC}"

    if [ ! -f "$log_file" ]; then
        echo "No log file found at: $log_file"
        return
    fi

    echo ""
    echo -e "${BLUE}Last 50 lines of build log:${NC}"
    echo "----------------------------------------"
    tail -50 "$log_file"
    echo "----------------------------------------"
    echo ""

    # Extract and categorize errors
    echo -e "${YELLOW}Error Analysis:${NC}"

    # Check for git/clone errors
    if grep -qi "unable to access\|could not read\|fatal:.*git" "$log_file"; then
        echo -e "${RED}❌ Git Repository Access Error${NC}"
        echo "   The build failed to clone the git repository."
        echo "   Possible causes:"
        echo "   - Repository URL is incorrect"
        echo "   - Repository does not exist or is private"
        echo "   - Network/firewall issues"
        echo ""
        grep -i "fatal:\|error:.*unable to access" "$log_file" | head -3 | sed 's/^/   /'
        echo ""
    fi

    # Check for spec file errors
    if grep -qi "error:.*spec\|rpmbuild.*error" "$log_file"; then
        echo -e "${RED}❌ Spec File Error${NC}"
        echo "   There is an error in the RPM spec file."
        echo ""
        grep -i "error:.*spec" "$log_file" | head -5 | sed 's/^/   /'
        echo ""
    fi

    # Check for dependency errors
    if grep -qi "error:.*dependency\|required.*not found\|is needed" "$log_file"; then
        echo -e "${RED}❌ Missing Dependency${NC}"
        echo "   Required packages or dependencies are missing."
        echo ""
        grep -i "is needed\|required.*not found" "$log_file" | head -5 | sed 's/^/   /'
        echo ""
    fi

    # Check for compilation errors
    if grep -qi "error:.*undefined reference\|compilation error\|make.*\*\*\*.*error" "$log_file"; then
        echo -e "${RED}❌ Compilation Error${NC}"
        echo "   Source code compilation failed."
        echo ""
        grep -i "error:" "$log_file" | grep -v "error: Spec" | head -5 | sed 's/^/   /'
        echo ""
    fi

    # Suggest next steps
    echo ""
    echo -e "${BLUE}Suggested Actions:${NC}"
    echo "1. Check the full log: $log_file"
    echo "2. Verify repository configuration: copr-cli get-package --name $package_name $COPR_PROJECT"
    echo "3. If repository URL is wrong, re-add the package with correct URL"
    echo "4. Review the build log for specific error messages"
}

# Function to generate final report
generate_report() {
    local success_count=$1
    local failure_count=$2
    local total_count=$3

    echo ""
    echo -e "${BLUE}==========================================${NC}"
    echo -e "${BLUE}EUR Build Summary${NC}"
    echo -e "${BLUE}==========================================${NC}"
    echo "Total packages: $total_count"
    echo -e "Successful: ${GREEN}$success_count${NC}"
    echo -e "Failed: ${RED}$failure_count${NC}"
    echo ""
    echo "Build logs saved to: $BUILD_LOG_DIR"
    echo -e "${BLUE}==========================================${NC}"

    if [ $failure_count -gt 0 ]; then
        echo ""
        echo -e "${RED}==========================================${NC}"
        echo -e "${RED}Failed Builds${NC}"
        echo -e "${RED}==========================================${NC}"

        for package in "${FAILED_PACKAGES[@]}"; do
            echo "  ✗ $package"
        done

        echo ""
        echo "Check build logs for details:"
        echo "  ls -la $BUILD_LOG_DIR/"
    fi
}

# Function to publish repositories
publish_repositories() {
    local success_count=$1
    local failure_count=$2

    echo ""
    echo -e "${BLUE}==========================================${NC}"
    echo -e "${BLUE}Publishing Repository${NC}"
    echo -e "${BLUE}==========================================${NC}"

    # 只在有成功构建时发布
    if [ $success_count -gt 0 ]; then
        echo "Publishing repository metadata for $COPR_PROJECT..."
        echo "This will make the built packages available for installation."

        if copr-cli regenerate-repos "$COPR_PROJECT" 2>&1; then
            echo -e "${GREEN}✓ Repository published successfully${NC}"
            echo ""
            echo "Available packages:"
            copr-cli list-package-names "$COPR_PROJECT" 2>&1
            echo ""
            echo "Repository URL:"
            echo "  https://eur.openeuler.openatom.cn/coprs/$COPR_PROJECT/"
        else
            echo -e "${YELLOW}⚠ Failed to publish repository${NC}"
            echo "You can manually publish later with:"
            echo "  copr-cli regenerate-repos $COPR_PROJECT"
        fi
    else
        echo "No successful builds to publish"
    fi

    echo -e "${BLUE}==========================================${NC}"
}

# Main execution
main() {
    # Check environment
    check_environment
    echo ""

    # Track results
    declare -gA BUILD_IDS
    declare -gA BUILD_STATUSES
    declare -gA BUILD_TIMES
    SUCCESS_COUNT=0
    FAILURE_COUNT=0
    FAILED_PACKAGES=()

    for layer_idx in "${!LAYERS[@]}"; do
        echo -e "${BLUE}==========================================${NC}"
        echo -e "${BLUE}Building Layer $layer_idx${NC}"
        echo -e "${BLUE}==========================================${NC}"
        echo ""

        layer_packages=(${LAYERS[$layer_idx]})
        local layer_failure=0

        # Step 1: Trigger builds for current layer
        for pkg in "${layer_packages[@]}"; do
            package="$pkg"

            # Check if package already succeeded in previous run
            if [ $FORCE_REBUILD -eq 0 ] && grep -q "^${package}$" "$SUCCESS_STATE_FILE" 2>/dev/null; then
                echo -e "${YELLOW}[SKIP] Package $package already built successfully in previous run.${NC}"
                BUILD_STATUSES[$package]="already_succeeded"
                SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
                echo ""
                continue
            fi

            BUILD_ID=$(trigger_build "$package")

            if [ $? -eq 0 ]; then
                BUILD_IDS[$package]=$BUILD_ID
                BUILD_STATUSES[$package]="triggered"
                echo ""
            else
                BUILD_STATUSES[$package]="trigger_failed"
                FAILURE_COUNT=$((FAILURE_COUNT + 1))
                FAILED_PACKAGES+=("$package")
                layer_failure=1
            fi
        done
        
        # Step 2: Poll build status for current layer
        for pkg in "${layer_packages[@]}"; do
            package="$pkg"
            if [ "${BUILD_STATUSES[$package]}" = "trigger_failed" ] || [ "${BUILD_STATUSES[$package]}" = "already_succeeded" ]; then
                continue
            fi

            local build_id=${BUILD_IDS[$package]}
            local start_time=$(date +%s)

            poll_status "$build_id" "$package"
            local status=$?

            local end_time=$(date +%s)
            local duration=$((end_time - start_time))
            BUILD_TIMES[$package]="${duration}s"

            # Collect logs
            case $status in
                0)
                    BUILD_STATUSES[$package]="succeeded"
                    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
                    echo "$package" >> "$SUCCESS_STATE_FILE"
                    collect_logs "$build_id" "$package" "succeeded"
                    ;;
                1)
                    BUILD_STATUSES[$package]="failed"
                    FAILURE_COUNT=$((FAILURE_COUNT + 1))
                    FAILED_PACKAGES+=("$package")
                    collect_logs "$build_id" "$package" "failed"
                    analyze_failure "$package" "$build_id"
                    layer_failure=1
                    ;;
                2)
                    BUILD_STATUSES[$package]="canceled"
                    collect_logs "$build_id" "$package" "canceled"
                    layer_failure=1
                    ;;
                3)
                    BUILD_STATUSES[$package]="skipped"
                    ;;
                *)
                    BUILD_STATUSES[$package]="timeout"
                    FAILURE_COUNT=$((FAILURE_COUNT + 1))
                    FAILED_PACKAGES+=("$package")
                    collect_logs "$build_id" "$package" "timeout"
                    layer_failure=1
                    ;;
            esac
            echo ""
        done

        if [ $layer_failure -eq 1 ]; then
            echo -e "${RED}==========================================${NC}"
            echo -e "${RED}Layer $layer_idx failed! Stopping batch build.${NC}"
            echo -e "${RED}==========================================${NC}"
            break
        fi

        # Step 3: Regenerate repositories before moving to next layer
        if [ $layer_idx -lt $((${#LAYERS[@]} - 1)) ]; then
            echo -e "${BLUE}Layer $layer_idx complete. Regenerating repos for next layer...${NC}"
            copr-cli regenerate-repos "$COPR_PROJECT" > /dev/null
            echo -e "Waiting 15 seconds for repository metadata to update..."
            sleep 15
            echo ""
        fi
    done

    # Step 4: Generate report
    generate_report $SUCCESS_COUNT $FAILURE_COUNT $TOTAL_PACKAGES

    # Step 5: Publish repositories (Final)
    publish_repositories $SUCCESS_COUNT $FAILURE_COUNT

    # Exit with appropriate code
    if [ $FAILURE_COUNT -gt 0 ]; then
        exit 1
    else
        exit 0
    fi
}

# Run main function
main
