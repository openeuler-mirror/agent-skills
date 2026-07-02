#!/bin/bash
#
# ros-upstream-setup.sh
# 一键完成 ROS 包移植准备工作
#
# 功能：
#   1. 获取 ROS 包信息 (get-ros-projects.sh)
#   2. 生成仓库列表 (get-repo-list.sh)
#   3. 克隆源代码 (vcs import)
#   4. 获取包源路径 (get-pkg-src.sh)
#   5. 获取包依赖 (get-pkg-deps.sh)
#   6. 生成 spec 文件 (gen-pkg-spec.sh)
#
# Usage: ./ros-upstream-setup.sh [ros_distro]
#   ros_distro: ROS 发行版 (默认: humble)
#
# 注意: 推荐使用 bash xxx.sh 执行，不建议用 source/. 执行
#

# 阻止 git 操作时在终端挂起要求输入密码（如遇到私有库或需认证的错误 URL，直接报错退出）
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=echo
export SSH_ASKPASS=echo

# 检测是否被 source 执行（用于安全退出）
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    _SOURCED=0
else
    _SOURCED=1
fi

# 安全退出函数：source 时用 return，否则用 exit
safe_exit() {
    local code=$1
    if [[ $_SOURCED -eq 1 ]]; then
        return $code
    else
        exit $code
    fi
}

# 只在非 source 模式下启用 set -e（避免 source 时退出终端）
if [[ $_SOURCED -eq 0 ]]; then
    set -e
fi

# 获取脚本所在目录（兼容 source 和直接执行）
if [ -n "${BASH_SOURCE[0]:-}" ]; then
    SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
elif [ -n "${0:-}" ]; then
    SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
else
    SCRIPT_DIR=$PWD
fi

# 获取参数
ROS_DISTRO="humble"
WORKSPACE_DIR="$PWD"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -w|--workspace) WORKSPACE_DIR="$(realpath "$2")"; shift ;;
        -d|--distro) ROS_DISTRO="$2"; shift ;;
        -h|--help) echo "Usage: $0 [-w <workspace_dir>] [-d <ros_distro>]"; exit 0 ;;
        *) ROS_DISTRO="$1" ;; # 兼容旧版本的只传 distro 模式
    esac
    shift
done

export ROS_UPSTREAM_WORKSPACE="$WORKSPACE_DIR"

# 日志文件 - 组织到 output/log/<timestamp>/ 目录下
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_DIR="$WORKSPACE_DIR/output"
LOG_DIR="$OUTPUT_DIR/log/$TIMESTAMP"
LOG_FILE="$LOG_DIR/ros-upstream-setup.log"
REPORT_FILE="$LOG_DIR/ros-setup-report.txt"
mkdir -p "$LOG_DIR"

# 打印函数
print_step() {
    echo ""
    printf "\033[0;32m=== %s ===\033[0m\n" "$1"
}

print_info() {
    printf "\033[1;33m[INFO]\033[0m %s\n" "$1"
}

print_error() {
    printf "\033[0;31m[ERROR]\033[0m %s\n" "$1"
}

# 运行脚本，详细日志只写入文件
run_script() {
    _script_name="$1"
    _step_desc="$2"
    _extra_args="$3"

    print_step "$_step_desc"

    if [ ! -f "$SCRIPT_DIR/$_script_name" ]; then
        print_error "找不到 $_script_name 脚本"
        safe_exit 1
    fi

    # 详细日志只写入文件
    if (cd "$SCRIPT_DIR" && bash "./$_script_name" $_extra_args) >> "$LOG_FILE" 2>&1; then
        if [ "$_script_name" = "get-pkg-src.sh" ] && [ -f "$OUTPUT_DIR/version_mismatch.log" ]; then
            echo ""
            echo -e "\033[1;33m[WARNING: VERSION_MISMATCH]\033[0m Detected upstream status page branch/version errors!"
            echo "Some packages downloaded incorrect versions because official tracking branches are wrong."
            echo "The errors are recorded in: $OUTPUT_DIR/version_mismatch.log"
            echo "Agent Note: You do NOT need to fix all of them now. Only fix them during the 'pkg-update' phase if the packages you are explicitly upgrading are listed in this log."
            echo ""
        fi
        return 0
    else
        print_error "$_step_desc 失败"
        echo "=== 查看日志获取详细信息: $LOG_FILE ==="
        safe_exit 1
    fi
}

echo "========================================"
echo "ROS 包移植一键准备工具"
echo "========================================"
echo "ROS 发行版: $ROS_DISTRO"
echo "工作目录: $SCRIPT_DIR"
echo "日志文件: $LOG_FILE"
echo ""

# 步骤 1: 获取 ROS 包信息
print_step "步骤 1/6: 获取 ROS 包信息"
if [ ! -f "$SCRIPT_DIR/get-ros-projects.sh" ]; then
    print_error "找不到 get-ros-projects.sh 脚本"
    safe_exit 1
fi
# 详细日志只写入文件
if (cd "$SCRIPT_DIR" && bash "./get-ros-projects.sh" "$ROS_DISTRO") >> "$LOG_FILE" 2>&1; then
    pkg_count=$(wc -l < "$OUTPUT_DIR/ros-projects.list" 2>/dev/null || echo 0)
    print_info "获取完成 ($pkg_count 个包)"
else
    print_error "步骤 1/6: 获取 ROS 包信息 失败"
    echo "=== 查看日志获取详细信息: $LOG_FILE ==="
    safe_exit 1
fi

# 步骤 2: 生成仓库列表
run_script "get-repo-list.sh" "步骤 2/6: 生成仓库列表"

# 步骤 3: 克隆源代码
print_step "步骤 3/6: 克隆源代码 (这可能需要较长时间)..."

if [ ! -f "$OUTPUT_DIR/ros.repos" ]; then
    print_error "找不到 $OUTPUT_DIR/ros.repos 文件"
    safe_exit 1
fi

mkdir -p "$OUTPUT_DIR/src"

if ! command -v vcs >/dev/null 2>&1; then
    print_error "找不到 vcs 命令，请先安装:"
    echo "  pip3 install vcstool"
    safe_exit 1
fi

# 计算仓库数量
repo_count=$(grep "^  [a-z]" "$OUTPUT_DIR/ros.repos" 2>/dev/null | wc -l)
print_info "发现 $repo_count 个仓库..."

# 检查已存在的仓库并执行 git pull
existing_repo_count=$(find "$OUTPUT_DIR/src" -maxdepth 2 -name ".git" -type d 2>/dev/null | wc -l)
if [ "$existing_repo_count" -gt 0 ]; then
    print_info "检测到 $existing_repo_count 个已存在的仓库，正在更新..."
    cd "$OUTPUT_DIR"
    # 使用 vcs pull 更新所有已存在的仓库
    if vcs pull src >> "$LOG_FILE" 2>&1; then
        print_info "已更新 $existing_repo_count 个仓库"
    else
        print_info "部分仓库更新存在告警，继续..."
    fi
    cd "$SCRIPT_DIR"
fi

print_info "开始克隆新仓库..."

# 使用 vcs import，输出到日志
cd "$OUTPUT_DIR"
if vcs import src < ros.repos >> "$LOG_FILE" 2>&1; then
    print_info "克隆完成 ($repo_count 个仓库)"
else
    print_error "克隆存在失败或告警，查看日志: $LOG_FILE"
fi
cd "$SCRIPT_DIR"

# 步骤 4: 获取包源路径
run_script "get-pkg-src.sh" "步骤 4/6: 获取包源路径"

# 步骤 5: 获取包依赖
print_step "步骤 5/6: 获取包依赖..."

# 详细日志只写入文件
if (cd "$SCRIPT_DIR" && bash "./get-pkg-deps.sh") >> "$LOG_FILE" 2>&1; then
    # 统计生成的依赖文件数量
    if [ -d "$OUTPUT_DIR/deps" ]; then
        dep_count=$(ls -1 "$OUTPUT_DIR/deps" 2>/dev/null | wc -l)
        print_info "依赖分析完成 (生成 $dep_count 个依赖文件)"
    fi
else
    print_error "获取包依赖失败"
    echo "=== 查看日志获取详细信息: $LOG_FILE ==="
    safe_exit 1
fi

# 步骤 6: 生成 spec 文件
print_step "步骤 6/6: 生成 spec 文件..."

# 统计要处理的仓库数量
total_repos=$(cat "$OUTPUT_DIR/ros-pkg.list" 2>/dev/null | awk '{print $2}' | sort -u | wc -l)
print_info "开始生成 spec 文件 ($total_repos 个仓库)..."

# 详细日志只写入文件，使用 bash 执行避免 exit 影响父进程
(cd "$SCRIPT_DIR" && bash "./gen-pkg-spec.sh") >> "$LOG_FILE" 2>&1 &
_gen_pid=$!

# 显示进度指示器
_progress=""
while kill -0 $_gen_pid 2>/dev/null; do
    # 检查当前生成的 spec 数量
    if [ -d "$OUTPUT_DIR/repo" ]; then
        _current_specs=$(find "$OUTPUT_DIR/repo" -name "*.spec" 2>/dev/null | wc -l)
        printf "\r%s" "已生成 $_current_specs 个 spec 文件..."
    fi
    sleep 2
done
echo ""

# 检查退出状态
wait $_gen_pid
_gen_status=$?

if [ $_gen_status -eq 0 ]; then
    # 统计生成的 spec 文件数量
    if [ -d "$OUTPUT_DIR/repo" ]; then
        spec_count=$(find "$OUTPUT_DIR/repo" -name "*.spec" 2>/dev/null | wc -l)
        repo_dirs=$(ls -1d "$OUTPUT_DIR/repo"/*/ 2>/dev/null | wc -l)
        print_info "Spec 生成完成 ($repo_dirs 个仓库, $spec_count 个 spec 文件)"
    fi
else
    print_error "生成 spec 失败"
    echo "=== 查看日志获取详细信息: $LOG_FILE ==="
    safe_exit 1
fi

echo ""
echo "========================================"
echo "完成！所有步骤已执行完毕"
echo "========================================"
echo "日志文件: $LOG_FILE"
echo "报告文件: $REPORT_FILE"
echo ""
echo "生成的文件位置:"
echo "  - output/ros-projects.list          : ROS 包信息"
echo "  - output/ros.repos                   : 仓库配置"
echo "  - output/src/                        : 源代码"
echo "  - output/ros-pkg.list                : 包列表"
echo "  - output/ros-pkg-src.list            : 包源路径"
echo "  - output/deps/                       : 依赖分析"
echo "  - output/repo/                       : spec 文件"
echo "  - output/log/                        : 日志和报告"
echo ""

# ========================================
# 生成执行报告
# ========================================

generate_report() {
    echo "========================================"
    echo "ROS 包移植准备 - 执行报告"
    echo "========================================"
    echo "生成时间: $(date)"
    echo "ROS 发行版: $ROS_DISTRO"
    echo "日志文件: $LOG_FILE"
    echo ""

    # 统计信息
    total_pkgs=$(wc -l < "$OUTPUT_DIR/ros-projects.list" 2>/dev/null || echo 0)
    total_repos=$(ls -1d "$OUTPUT_DIR/src"/*/ 2>/dev/null | wc -l)
    spec_count=$(find "$OUTPUT_DIR/repo" -name "*.spec" 2>/dev/null | wc -l)
    repo_dirs=$(ls -1d "$OUTPUT_DIR/repo"/*/ 2>/dev/null | wc -l)


    # 统计失败仓库数（从日志中动态提取）
    failed_url_count=$(grep "repository.*not found" "$LOG_FILE" 2>/dev/null | wc -l)
    failed_checkout_count=$(grep "Could not checkout" "$LOG_FILE" 2>/dev/null | grep -v "ref 'None'" | wc -l)
    failed_repos=$((failed_url_count + failed_checkout_count))

    # 成功克隆的仓库数 = 总目录数 - 失败数
    if [ "$failed_repos" -gt "$total_repos" ]; then
        failed_repos=0
    fi
    success_repos=$((total_repos - failed_repos))

    echo "=== 执行摘要 ==="
    echo "上游包总数: $total_pkgs"
    echo "克隆仓库数: $success_repos / $total_repos (成功/总数)"
    echo "  - 失败: $failed_repos (URL 错误: $failed_url_count, checkout 失败: $failed_checkout_count)"
    echo "生成仓库数: $repo_dirs"
    echo "生成 spec 数: $spec_count"
    echo ""

    # ========================================
    # 1. 仓库 URL 错误 (repository not found)
    # ========================================
    repo_not_found=$(grep "repository.*not found" "$LOG_FILE" 2>/dev/null || true)
    if [ -n "$repo_not_found" ]; then
        repo_nf_count=$(echo "$repo_not_found" | wc -l)
        echo "========================================"
        echo "【1】仓库 URL 错误 ($repo_nf_count 个)"
        echo "========================================"
        echo "原因: GitHub 仓库不存在或 URL 错误"
        echo ""
        echo "$repo_not_found" | sed "s/.*\/\([^/]*\)\.git.*/  - \1/" | sort -u
        echo ""
        echo "影响: 这些仓库的所有包都无法生成 spec"
        echo "建议: 检查 ros-projects.list 中的 URL 是否正确"
        echo ""
    fi

    # ========================================
    # 2. 仓库 checkout 失败 (分支不存在)
    # ========================================
    # 提取 checkout 失败的仓库名和分支
    checkout_failures=$(grep -B1 "Could not checkout" "$LOG_FILE" 2>/dev/null | grep -A1 "^=== src/" || true)
    if [ -n "$checkout_failures" ]; then
        # 统计各分支失败数
        echo "========================================"
        echo "【2】仓库 checkout 失败"
        echo "========================================"
        echo "原因: 指定的分支不存在，可能是分支废弃、拼写错误、仓库不存在该分支"
        echo ""

        # 动态提取所有出错的分支名（排除 None）
        failed_branches=$(grep "Could not checkout ref '" "$LOG_FILE" 2>/dev/null | sed "s/.*ref '\([^']*\)'.*/\1/" | grep -v "^None$" | sort -u || true)

        # 按分支分类显示
        for branch in $failed_branches; do
            repos=$(grep -B1 "Could not checkout ref '$branch'" "$LOG_FILE" 2>/dev/null | grep "^=== src/" | sed 's/=== src\/\(.*\) (git) ===/\1/') || true || true
            if [ -n "$repos" ]; then
                count=$(echo "$repos" | wc -l)
                echo "--- 分支 '$branch' 不存在 ($count 个仓库) ---"
                echo "$repos" | while read repo; do
                    echo "  - $repo"
                done
                echo ""
            fi
        done

        echo "影响: 这些仓库只能使用默认分支，分支不匹配可能导致部分包无法生成 spec"
        echo "建议: 在 ros/$ROS_DISTRO/ros-version-fix 文件中指定正确的分支"
        echo ""
    fi

    # ========================================
    # 2.5 分支未指定警告 (None) - 通常是可忽略的
    # ========================================
    none_branch_repos=$(grep -B1 "Could not checkout ref 'None'" "$LOG_FILE" 2>/dev/null | grep "^=== src/" | sed 's/=== src\/\(.*\) (git) ===/\1/') || true || true
    if [ -n "$none_branch_repos" ]; then
        none_count=$(echo "$none_branch_repos" | wc -l)
        echo "========================================"
        echo "【2.5】分支未指定警告 ($none_count 个) [可忽略]"
        echo "========================================"
        echo "说明: 这些仓库在 http://repo.ros2.org/status_page/ros_humble_default.html 中没有指定分支"
        echo "      vcs import 会自动使用默认分支，分支不匹配可能导致部分包无法生成 spec"
        echo ""

        # 检查每个仓库的实际状态
        success_count=0
        for repo in $none_branch_repos; do
            if [ -d "$OUTPUT_DIR/src/$repo" ]; then
                repo_spec_count=$(find "$OUTPUT_DIR/repo" -path "*$repo*" -name "*.spec" 2>/dev/null | wc -l)
                if [ "$repo_spec_count" -gt 0 ]; then
                    echo "  ✓ $repo (已生成 $repo_spec_count 个spec)"
                    success_count=$((success_count + 1))
                else
                    echo "  ? $repo (仓库存在，但无spec - 可能缺少package.xml)"
                fi
            else
                echo "  ✗ $repo (仓库不存在)"
            fi
        done
        echo ""
        echo "总结: $success_count/$none_count 个仓库成功生成了spec"
        echo ""
    fi

    # ========================================
    # 3. 找不到源码路径的包
    # ========================================
    src_path_errors=$(grep "Can not find src path for package" "$LOG_FILE" 2>/dev/null || true)
    if [ -n "$src_path_errors" ]; then
        src_error_count=$(echo "$src_path_errors" | wc -l)
        echo "========================================"
        echo "【3】找不到源码路径 ($src_error_count 个包)"
        echo "========================================"
        echo "原因: 仓库 checkout 失败、包名与仓库内路径不匹配"
        echo ""
        # 提取所有包名（不超过 500 个则全部显示）
        failed_pkgs=$(grep "Can not find src path for package" "$LOG_FILE" 2>/dev/null | sed 's/.*package \(.*\)/\1/' | sort -u || true)
        failed_count=$(echo "$failed_pkgs" | wc -l)
        if [ $failed_count -le 500 ]; then
            echo "$failed_pkgs" | while read pkg; do
                echo "  - $pkg"
            done
        else
            echo "$failed_pkgs" | head -500 | while read pkg; do
                echo "  - $pkg"
            done
            echo "  ... 还有 $((failed_count - 500)) 个包"
        fi
        echo ""
        echo "影响: 这些包无法解析依赖和生成 spec"
        echo "建议: 检查这些包的源码结构是否正确"
        echo ""
    fi

    # ========================================
    # 4. 找不到 package.xml
    # ========================================
    pkg_xml_errors=$(grep "can not find package.xml" "$LOG_FILE" 2>/dev/null || true)
    if [ -n "$pkg_xml_errors" ]; then
        xml_error_count=$(echo "$pkg_xml_errors" | wc -l)
        echo "========================================"
        echo "【4】找不到 package.xml ($xml_error_count 个)"
        echo "========================================"
        echo "原因: 包的源码目录下没有 package.xml 文件"
        echo ""

        # 提取包名（新格式: "can not find package.xml for package 'xxx' in ..."）
        failed_pkgs=$(grep "can not find package.xml for package" "$LOG_FILE" 2>/dev/null | sed "s/.*package '\([^']*\)'.*/\1/" | sort -u || true)

        # 如果新格式没匹配到，尝试旧格式（版本号目录）
        if [ -z "$failed_pkgs" ]; then
            echo "  以下版本目录缺少 package.xml:"
            echo "$pkg_xml_errors" | sed 's|.*/src/\([^/]*\)/.*|  - \1|' | sort | uniq -c | sort -rn | head -20
        else
            failed_count=$(echo "$failed_pkgs" | wc -l)
            if [ "$failed_count" -le 500 ]; then
                echo "$failed_pkgs" | while read pkg; do echo "  - $pkg"; done
            else
                echo "$failed_pkgs" | head -500 | while read pkg; do echo "  - $pkg"; done
                echo "  ... 还有 $((failed_count - 500)) 个包"
            fi
        fi
        echo ""
        echo "影响: 这些包无法解析依赖和生成 spec"
        echo "建议: 检查这些包的源码结构是否正确"
        echo ""
    fi

    # ========================================
    # 5. Python 脚本错误
    # ========================================
    py_errors=$(grep -E "IndexError|KeyError|TypeError|ValueError|AttributeError" "$LOG_FILE" 2>/dev/null || true)
    if [ -n "$py_errors" ]; then
        py_error_count=$(echo "$py_errors" | wc -l)
        echo "========================================"
        echo "【5】Python 脚本错误 ($py_error_count 个)"
        echo "========================================"
        echo "原因: 依赖分析脚本处理某些包时出错"
        echo ""
        echo "$py_errors" | sort -u
        echo ""
        echo "建议: 检查对应包的 package.xml 格式是否正确"
        echo ""
    fi

    # ========================================
    # 6. 其他错误 (No such file 等)
    # ========================================
    other_errors=$(grep -E "No such file or directory|Permission denied" "$LOG_FILE" 2>/dev/null | grep -v "package.xml" | head -50 || true)
    if [ -n "$other_errors" ]; then
        echo "========================================"
        echo "【6】文件系统错误"
        echo "========================================"
        echo "$other_errors" | sort -u | head -20
        echo ""
    fi

    # ========================================
    # 总结
    # ========================================
    echo "========================================"
    echo "失败原因关联分析"
    echo "========================================"

    # 统计各类错误数量
    repo_nf=$(grep "repository.*not found" "$LOG_FILE" 2>/dev/null | wc -l)
    # 排除 None 分支（这些是警告而非错误）
    checkout=$(grep "Could not checkout" "$LOG_FILE" 2>/dev/null | grep -v "ref 'None'" | wc -l)
    none_branch=$(grep "Could not checkout ref 'None'" "$LOG_FILE" 2>/dev/null | wc -l)
    src_path=$(grep "Can not find src path" "$LOG_FILE" 2>/dev/null | wc -l)
    pkg_xml=$(grep "can not find package.xml" "$LOG_FILE" 2>/dev/null | wc -l)

    echo ""
    echo "错误类型统计:"
    echo "  仓库 URL 错误:           $repo_nf"
    echo "  checkout 分支失败:       $checkout"
    echo "  分支未指定警告(可忽略):  $none_branch"
    echo "  找不到源码路径:          $src_path"
    echo "  找不到 package.xml:      $pkg_xml"
    echo ""

    # 计算成功率
    if [ "$total_pkgs" -gt 0 ]; then
        success_rate=$(echo "scale=1; $spec_count * 100 / $total_pkgs" | bc 2>/dev/null || echo "N/A")
        echo "成功率: $spec_count / $total_pkgs = ${success_rate}%"
    fi

    echo ""
    echo "========================================"
    echo "报告结束"
    echo "========================================"
}

generate_report | tee "$REPORT_FILE"
print_info "报告已保存到: $REPORT_FILE"

