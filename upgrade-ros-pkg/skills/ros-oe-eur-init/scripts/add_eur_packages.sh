#!/bin/bash
# EUR ROS Repo Init - 批量在 openEuler EUR (COPR) 项目中添加 ROS 软件包

# 不要使用 set -e，因为我们需要继续处理其他包即使某个包失败

# 默认配置
DEFAULT_MODE="official"
DEFAULT_USERNAME="${GITCODE_USERNAME:-your-gitcode-username}"
DEFAULT_BRANCH="humble"
DEFAULT_METHOD="rpkg"
DEFAULT_TYPE="git"
DEFAULT_CHROOT="openEuler-24.03-LTS-aarch64"
DEFAULT_MAPPING_FILE="${ROS_UPSTREAM_WORKSPACE:-$PWD}/output/ros-projects.list"

# Spec文件命名规范: ROS 2的spec文件名使用连字符（hyphen）而非下划线（underscore）
# 例如: iceoryx_hoofs包的spec文件是 iceoryx-hoofs.spec
DEFAULT_SPEC="{pkg}"  # {pkg}会被替换为包名（下划线转连字符）

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印帮助信息
print_help() {
    cat << EOF
用法: $0 [选项]

选项:
  --project PROJECT           COPR 项目名 (格式: username/projectname) [必需]
  --packages PACKAGES         包列表,空格分隔 [与 --package-list-file 二选一]
  --package-list-file FILE    包列表文件路径,每行一个包名 [与 --packages 二选一]
  --mode MODE                 仓库模式: personal 或 official (默认: $DEFAULT_MODE)
  --username USERNAME         个人仓用户名 (默认: $DEFAULT_USERNAME)
  --branch BRANCH             Git 分支 (默认: $DEFAULT_BRANCH)
  --spec SPEC                 spec 文件路径模板 (默认: {pkg}.spec)
  --method METHOD             构建方法 (默认: $DEFAULT_METHOD)
  --type TYPE                 SCM 类型 (默认: $DEFAULT_TYPE)
  --chroot CHROOT             目标 chroot (默认: $DEFAULT_CHROOT)
  --mapping-file FILE         包名映射文件路径 (默认: ${ROS_UPSTREAM_WORKSPACE:-$PWD}/output/ros-projects.list)
  --skip-existing             跳过已存在的包
  --dry-run                   只预览命令,不实际执行
  --list-packages             列出项目中的包
  --delete                    删除包而不是添加
  --build                     添加包后触发构建
  -h, --help                  显示此帮助信息

示例:
  $0 --project your-gitcode-username/your-eur-project --packages rclcpp rclpy
  $0 --project your-gitcode-username/my-project --package-list-file packages.txt --mode personal
  $0 --project your-gitcode-username/test --package-list-file packages.txt --dry-run
EOF
}

# 打印彩色消息
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 获取包对应的仓库名 (从缓存优先读取)
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

# 转换包名为spec文件名（下划线转连字符）
get_spec_name() {
    local pkg_name="$1"
    # ROS 2规范：spec文件名使用连字符
    echo "$pkg_name" | sed 's/_/-/g'
}

# 检查环境
check_environment() {
    if ! command -v copr-cli &> /dev/null; then
        log_error "copr-cli 未安装,请先安装: pip install copr"
        exit 1
    fi

    if ! copr-cli whoami &> /dev/null; then
        log_error "copr-cli 未配置 API token,请先配置 ~/.config/copr"
        exit 1
    fi

    log_success "copr-cli 环境正常: $(copr-cli whoami)"
}

# 列出项目中的包
list_packages() {
    local project="$1"
    log_info "列出项目 $project 中的包..."
    copr-cli list-packages "$project"
}

# 添加包
add_package() {
    local project="$1"
    local pkg_name="$2"
    local repo_name="$3"
    local clone_url="$4"
    local branch="$5"
    local spec="$6"
    local method="$7"
    local type="$8"
    local dry_run="$9"
    local build="${10}"
    local chroot="${11}"

    local cmd="copr-cli add-package-scm"
    cmd+=" --name ${pkg_name}"
    cmd+=" --clone-url ${clone_url}"
    cmd+=" --commit ${branch}"
    cmd+=" --spec ${spec}"
    cmd+=" --method ${method}"
    cmd+=" --type ${type}"
    cmd+=" ${project}"

    if [[ "$dry_run" == "true" ]]; then
        echo "$cmd"
        return 0
    fi

    if $cmd 2>&1; then
        log_success "添加成功: $pkg_name"
        if [[ "$build" == "true" ]]; then
            log_info "触发构建: $pkg_name"
            copr-cli build-package "$project" "$pkg_name" --chroot "$chroot" || true
        fi
        return 0
    else
        log_error "添加失败: $pkg_name"
        return 1
    fi
}

# 删除包
delete_package() {
    local project="$1"
    local pkg_name="$2"
    local dry_run="$3"

    local cmd="copr-cli delete-package $project $pkg_name"

    if [[ "$dry_run" == "true" ]]; then
        echo "$cmd"
        return 0
    fi

    if $cmd 2>&1; then
        log_success "删除成功: $pkg_name"
        return 0
    else
        log_error "删除失败: $pkg_name"
        return 1
    fi
}

# 主函数
main() {
    local project=""
    local packages=()
    local package_list_file=""
    local mode="$DEFAULT_MODE"
    local username="$DEFAULT_USERNAME"
    local branch="$DEFAULT_BRANCH"
    local spec="{pkg}.spec"
    local method="$DEFAULT_METHOD"
    local type="$DEFAULT_TYPE"
    local chroot="$DEFAULT_CHROOT"
    local mapping_file="$DEFAULT_MAPPING_FILE"
    local skip_existing="false"
    local dry_run="false"
    local list_only="false"
    local delete_mode="false"
    local build="false"

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project)
                project="$2"
                shift 2
                ;;
            --packages)
                shift
                while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                    packages+=("$1")
                    shift
                done
                ;;
            --package-list-file)
                package_list_file="$2"
                shift 2
                ;;
            --mode)
                mode="$2"
                shift 2
                ;;
            --username)
                username="$2"
                shift 2
                ;;
            --branch)
                branch="$2"
                shift 2
                ;;
            --spec)
                spec="$2"
                shift 2
                ;;
            --method)
                method="$2"
                shift 2
                ;;
            --type)
                type="$2"
                shift 2
                ;;
            --chroot)
                chroot="$2"
                shift 2
                ;;
            --mapping-file)
                mapping_file="$2"
                shift 2
                ;;
            --skip-existing)
                skip_existing="true"
                shift
                ;;
            --dry-run)
                dry_run="true"
                shift
                ;;
            --list-packages)
                list_only="true"
                shift
                ;;
            --delete)
                delete_mode="true"
                shift
                ;;
            --build)
                build="true"
                shift
                ;;
            -h|--help)
                print_help
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                print_help
                exit 1
                ;;
        esac
    done

    # 检查必需参数
    if [[ -z "$project" ]]; then
        log_error "必须指定 --project 参数"
        print_help
        exit 1
    fi

    # 检查环境
    if [[ "$dry_run" == "false" ]]; then
        check_environment
    fi

    # 如果只是列出包
    if [[ "$list_only" == "true" ]]; then
        list_packages "$project"
        exit 0
    fi

    # 从文件读取包列表
    if [[ -n "$package_list_file" ]]; then
        if [[ ! -f "$package_list_file" ]]; then
            log_error "包列表文件不存在: $package_list_file"
            exit 1
        fi
        while IFS= read -r line || [[ -n "$line" ]]; do
            # 跳过空行和注释
            [[ -z "$line" || "$line" =~ ^# ]] && continue
            packages+=("$line")
        done < "$package_list_file"
    fi

    # 检查是否有包要处理
    if [[ ${#packages[@]} -eq 0 ]]; then
        log_error "没有指定要处理的包"
        print_help
        exit 1
    fi

    # 显示配置
    echo "============================================================"
    echo "EUR ROS Repo Init"
    echo "============================================================"
    echo "项目: $project"
    echo "模式: $mode"
    echo "分支: $branch"
    echo "包数量: ${#packages[@]}"
    echo "映射文件: $mapping_file"
    [[ "$dry_run" == "true" ]] && echo "模式: DRY-RUN (预览)"
    echo "============================================================"
    echo ""

    # 统计
    local success_count=0
    local failed_count=0
    local skipped_count=0
    local failed_packages=()

    # 处理每个包
    for pkg in "${packages[@]}"; do
        # 获取仓库名 (无需再传 mapping_file)
        local repo_name=$(get_repo_name "$pkg")

        # 生成 clone URL
        local clone_url=""
        if [[ "$mode" == "personal" ]]; then
            clone_url="https://gitcode.com/${username}/${repo_name}.git"
        else
            clone_url="https://gitcode.com/src-openeuler/${repo_name}.git"
        fi

        # 生成spec文件名（下划线转连字符，符合ROS 2规范）
        local pkg_spec=$(get_spec_name "$pkg").spec

        echo "------------------------------------------------------------"
        echo "处理包: $pkg (仓库: $repo_name)"
        echo "  Clone URL: $clone_url"
        echo "  Spec: $pkg_spec"

        # 检查是否已存在
        if [[ "$skip_existing" == "true" ]] && copr-cli get-package --name "$pkg" "$project" &> /dev/null; then
            log_warning "包已存在,跳过: $pkg"
            ((skipped_count++))
            continue
        fi

        # 执行操作
        if [[ "$delete_mode" == "true" ]]; then
            if delete_package "$project" "$pkg" "$dry_run"; then
                ((success_count++))
            else
                ((failed_count++))
                failed_packages+=("$pkg")
            fi
        else
            if add_package "$project" "$pkg" "$repo_name" "$clone_url" "$branch" "$pkg_spec" "$method" "$type" "$dry_run" "$build" "$chroot"; then
                ((success_count++))
            else
                ((failed_count++))
                failed_packages+=("$pkg")
            fi
        fi
    done

    # 输出摘要
    echo ""
    echo "============================================================"
    echo "执行摘要"
    echo "============================================================"
    echo "成功: $success_count"
    echo "失败: $failed_count"
    echo "跳过: $skipped_count"

    if [[ ${#failed_packages[@]} -gt 0 ]]; then
        echo ""
        echo "失败的包:"
        for pkg in "${failed_packages[@]}"; do
            echo "  - $pkg"
        done
    fi
    echo "============================================================"
}

main "$@"
