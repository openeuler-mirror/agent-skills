#!/bin/bash

. base.sh

ROS_PROJECTS_LIST=${OUTPUT}/ros-projects.list
ROS_VERSION_FIX=${ROOT}/ros/${ROS_DISTRO}/config/ros-version-fix
ROS_URL_FIX=${ROOT}/ros/${ROS_DISTRO}/config/ros-url-fix
ROS_THIRD_LIST=${ROOT}/ros/${ROS_DISTRO}/config/ros-projects-third.list
ROS_REPOS=${OUTPUT}/ros.repos
ROS_REPOS_URL=${OUTPUT}/ros.url

project_version_fix()
{
	project=$1
	org_version=$2

	if [ ! -f ${ROS_VERSION_FIX} ]
	then
		echo $org_version
		return 0
	fi

	while read p fix_version
	do
		if [ "$p" = "$project" ]
		then
			echo $fix_version
			return 0
		fi
	done < ${ROS_VERSION_FIX}

	echo $org_version
	return 0
}

project_url_fix()
{
	local pkg_name=$1
	local original_url=$2

	if [ ! -f "${ROS_URL_FIX}" ]; then
		echo "$original_url"
		return 0
	fi

	local new_url=$(grep "^$pkg_name " "${ROS_URL_FIX}" | awk '{print $2}')
	if [ -n "$new_url" ]; then
		echo "$new_url"
	else
		echo "$original_url"
	fi
}

main()
{
	if [ ! -f ${ROS_PROJECTS_LIST}  ]
	then
		error_log "can not find ${ROS_PROJECTS_LIST}"
		exit 1
	fi

	# 把第三方包的清单追加进去，参与统一下载和生成流程
	if [ -f "${ROS_THIRD_LIST}" ]; then
		info_log "Appending 3rd-party projects list to main processing pipeline..."
		cat "${ROS_THIRD_LIST}" >> "${ROS_PROJECTS_LIST}"
	fi

	info_log "find ${ROS_PROJECTS_LIST}"

	> ${ROS_PKG_LIST}
	> ${ROS_REPOS_URL}
	> ${ROS_PROJECTS_NAME}
	echo "repositories:" >${ROS_REPOS}
	> ${OUTPUT}/bloom_release_repos.log

	while read pkg url status version
	do
		# 重置变量，避免上一轮循环残留值干扰
		project_name=""
		tree=""
		new_url=""
		git_url=""
		if [ "$pkg" = "" ]
		then
			continue
		fi

		# URL 拦截：读取 ros-url-fix 覆盖
		url=$(project_url_fix "$pkg" "$url")

		# 预警：检测 -release 命名的 Bloom 仓库（精确定位 URL 中的仓库名部分）
		if echo "$url" | grep -qE "^https?://[^/]+/[^/]+/[^/]+-release($|\.git|/)"; then
			# Check if there's already a fix for this pkg
			if ! grep -q "^$pkg " "${ROOT}/ros/${ROS_DISTRO}/config/ros-url-fix" 2>/dev/null; then
				error_log "Warning: Detected potential Bloom release repo for $pkg: $url"
				echo "WARNING_RELEASE:$pkg:$url" >> "${OUTPUT}/bloom_release_repos.log"
			fi
		fi

		if [ "$status" = "end-of-life" ]
		then
			if [ "$DOWNLOAD_EOL_REPO" != "on" ]
			then
				info_log "$pkg status is end-of-life, ignore"
				continue
			else
				info_log "$pkg status is end-of-life, but will be downloaded"
			fi
		fi

		if [ "$status" = "unknown" ]
		then
			if [ "$DOWNLOAD_UNKNOWN_REPO" != "on" ]
			then
				info_log "$pkg status is unknown, ignore"
				continue
			else
				info_log "$pkg status is unknown, but will be downloaded"
			fi
		fi

		echo $url | grep -qE "^https://github.com|^https://gitee.com"
		if [ "$?" -eq 0 ]
		then
			echo $url | grep -q ".git$"
			if [ "$?" -eq 0 ]
			then
				project_name=`echo $url | awk 'BEGIN {FS="\\\.git"} {print $1}' | awk -F"/" '{print $NF}'`
				tree=master
				new_url=$url
			else
				project_name=`echo $url | awk -F"/tree/" '{print $1}' | awk -F"/" '{print $NF}'`
				tree=`echo $url | awk -F"/tree/" '{print $2}'`
				new_url=`echo $url | awk -F"/tree/" '{print $1}'`.git
			fi
		fi

		echo $url | grep -q "^https://gitlab.com"
		if [ "$?" -eq 0 ]
		then
			echo $url | grep -q ".git$"
			if [ "$?" -eq 0 ]
			then
				project_name=`echo $url | awk 'BEGIN {FS="\\\.git"} {print $1}' | awk -F"/" '{print $NF}'`
				tree=main
				new_url=$url
			else
				echo $url | grep -q "/tree"
				if [ "$?" -ne 0 ]
				then
					project_name=`echo $url | awk -F"/" '{print $NF}'`
					tree=main
					new_url=${url}.git
				fi
			fi
		fi

		echo $url | grep -q "^https://gitcode.com"
		if [ "$?" -eq 0 ]
		then
			echo $url | grep -q ".git$"
			if [ "$?" -eq 0 ]
			then
				project_name=`echo $url | awk 'BEGIN {FS="\\\.git"} {print $1}' | awk -F"/" '{print $NF}'`
				tree=master
				new_url=$url
			else
				project_name=`echo $url | awk -F"/tree/" '{print $1}' | awk -F"/" '{print $NF}'`
				tree=`echo $url | awk -F"/tree/" '{print $2}'`
				new_url=`echo $url | awk -F"/tree/" '{print $1}'`.git
				git_url=`echo $new_url | sed -e "s#/${project_name}.git##g"`
			fi
		fi

		echo $url | grep -q "^https://bitbucket.org"
		if [ "$?" -eq 0 ]
		then
			echo $url | grep -q ".git$"
			if [ "$?" -eq 0 ]
			then
				project_name=`echo $url | awk 'BEGIN {FS="\\\.git"} {print $1}' | awk -F"/" '{print $NF}'`
				tree=master
				new_url=$url
			fi
		fi

		# 通用 fallback：处理非 GitHub/GitLab/Gitee/Bitbucket 的 git URL
		# 当前面的平台检查都没有匹配时，new_url 为空，说明 URL 不属于已知平台
		if [ "$new_url" = "" ] && echo "$url" | grep -qE "^https?://"
		then
			project_name=`echo $url | awk -F"/" '{print $NF}'`
			tree=master
			new_url=$url
			
			# 特殊处理：如果不是 .git 结尾，强制加上 .git
			# (修复 libcamera 等自建 git 服务器不带 .git 访问时报 401 提示输入密码的问题)
			if ! echo "$new_url" | grep -q "\.git$"; then
				new_url="${new_url}.git"
			fi
		fi

		if [ "$project_name" = "" -o "${new_url}" = "" -o "$version" = "" ]
		then
			error_log "Failed to analyse $pkg $url $new_url $version"
			exit 1
		fi

		[ "$git_url" == "" ] && git_url="None"
		[ "$tree" == "" ] && tree="None"

		# 保存原始仓库名，用于 repo 目录
		original_project_name=$project_name

		# 为源码目录创建带分支后缀的唯一名称
		# 分支名中的 / 替换为 -，避免路径问题
		safe_tree=$(echo "$tree" | sed 's/\//-/g')
		src_dir_name="${original_project_name}-${safe_tree}"

		# ros-pkg.list 格式: pkg \t repo_name \t version \t git_url \t tree \t src_dir_name
		# 注意：所有包都要写入 ros-pkg.list，不能跳过
		echo -e "$pkg\t$original_project_name\t$version\t$git_url\t$tree\t$src_dir_name" >> ${ROS_PKG_LIST}

		# 去重检查：URL + 分支组合（仅影响 ros.repos 和 ros.url）
		repo_branch_key="${new_url}|${tree}"
		grep -Fq "$repo_branch_key" ${ROS_REPOS_URL}
		if [ $? -eq 0 ]
		then
			# 该 URL+分支 已处理过，跳过 ros.repos 写入，但 ros-pkg.list 已写入
			echo -n "."
			continue
		fi

		# 记录已处理的 URL+分支
		echo "$repo_branch_key" >> ${ROS_REPOS_URL}

		echo "$original_project_name" >> ${ROS_PROJECTS_NAME}

		fix_version=`project_version_fix $original_project_name $tree`

		echo "  $src_dir_name:" >> ${ROS_REPOS}
		echo "    type: git" >> ${ROS_REPOS}
		echo "    url: $new_url" >> ${ROS_REPOS}
		echo "    version: $fix_version" >> ${ROS_REPOS}

		echo -n "."
	done < ${ROS_PROJECTS_LIST}

	info_log "Gen ros.repos done, you can find it in ${ROS_REPOS}"
}

main
