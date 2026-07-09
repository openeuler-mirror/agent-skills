#!/bin/bash

. base.sh

ROS_PKG_SRC=${OUTPUT}/ros-pkg-src.list

prepare()
{
	if [ "${ROS_SRC_BASE}" = "" ]
	then
		error_log "Please give the source repo path of ros"
		exit 1
	fi

	if [ ! -d ${ROS_SRC_BASE} ]
	then
		error_log "Please give the source repo path of ros"
		exit 1
	fi

	if [ ! -f ${ROS_PKG_LIST} ]
	then
		error_log "Can not find ${ROS_PKG_LIST}, you can use get-repo-list.sh to create it"
		exit 1
	fi

	>${ROS_PKG_SRC}
	rm -f ${OUTPUT}/version_mismatch.log
}


find_pkg_src_path_by_package_xml()
{
	pkg_org_name=$1
	base_path=$2

	package_xml=`find ${ROS_SRC_BASE}/${base_path} -name package.xml`

	for i in $package_xml
	do
		grep -Fq "<name>$pkg_org_name</name>" $i
		if [ $? -eq 0 ]
		then
			pkg_src_path=`echo $i | sed "s#${ROS_SRC_BASE}/##g" | sed "s#/package.xml##g"`
			echo $pkg_src_path
			return 0
		fi
	done

	return 0
}

main()
{
	prepare

	info_log "Start to analyse ros-pkg."

	# ros-pkg.list 格式: pkg \t repo_name \t version \t git_url \t tree \t src_dir_name
	while read pkg repo_name version git_url tree src_dir_name
	do
		if [ "$pkg" = "" -o "$src_dir_name" = "" ]
		then
			continue
		fi

		pkg_org_name=`echo $pkg | sed "s/-/_/g"`
		pkg_src_path=""

		if [ -f ${ROS_SRC_BASE}/${src_dir_name}/${pkg_org_name}/package.xml ]
		then
			pkg_src_path=${src_dir_name}/${pkg_org_name}
		fi

		if [ -f ${ROS_SRC_BASE}/${src_dir_name}/package.xml ]
		then
			pkg_src_path=${src_dir_name}
		fi

		if [ "$pkg_src_path" = "" ]
		then
			pkg_src_path=`find_pkg_src_path_by_package_xml $pkg_org_name ${src_dir_name}`
		fi

		# [New Logic] Fallback to 3rdparty path fix
		if [ "$pkg_src_path" = "" ] && [ -f "${ROOT}/ros/${ROS_DISTRO}/config/ros-3rdparty-path-fix" ]; then
			custom_path=$(grep "^$pkg " "${ROOT}/ros/${ROS_DISTRO}/config/ros-3rdparty-path-fix" | awk '{print $2}')
			if [ "$custom_path" = "." ] || [ "$custom_path" = "" ]; then
				if grep -q "^$pkg " "${ROOT}/ros/${ROS_DISTRO}/config/ros-3rdparty-path-fix"; then
					pkg_src_path=${src_dir_name}
					info_log "Used 3rdparty path fix for $pkg: $pkg_src_path"
				fi
			elif [ -n "$custom_path" ]; then
				pkg_src_path=${src_dir_name}/${custom_path}
				info_log "Used 3rdparty path fix for $pkg: $pkg_src_path"
			fi
		fi

		if [ "$pkg_src_path" = "" ]
		then
			error_log "Can not find src path for package $pkg"
			pkg_src_path="-"  # 使用占位符，避免 read 时字段错位
		else
			# === Version Validation Logic ===
			actual_version=$(grep -m 1 "<version>" "${ROS_SRC_BASE}/${pkg_src_path}/package.xml" 2>/dev/null | sed -e 's/.*<version>//' -e 's/<\/version>.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
			expected_base_version=$(echo "$version" | cut -d'-' -f1)

			if [ "$actual_version" != "" ] && [ "$actual_version" != "$expected_base_version" ]; then
				# 判断是否为跨代大版本不匹配（主版本不同，或次版本不同但差异过大）
				# 这里我们用一个极其稳健且兼容的 Python 单行脚本来处理 Semantic Version
				is_fatal=$(python3 -c "
try:
    v1 = [int(x) for x in '$expected_base_version'.split('.')[:2]]
    v2 = [int(x) for x in '$actual_version'.split('.')[:2]]
    # Major differs OR Minor differs -> Fatal Mismatch (Requires Agent fix)
    print('1' if v1[0] != v2[0] or v1[1] != v2[1] else '0')
except:
    print('1') # parse error = fallback to fatal
" 2>/dev/null || echo "1")

				if [ "$is_fatal" == "1" ]; then
					error_log "FATAL Version mismatch for '$pkg' (Repo: $repo_name, Branch: $tree). Expected: $expected_base_version, Found: $actual_version. Marked for agent fix."
					echo "MISMATCH:$pkg:$repo_name:$tree:$expected_base_version:$actual_version" >> "${OUTPUT}/version_mismatch.log"
				else
					# 只是补丁号（Patch）升级 (例如 2.4.2 -> 2.4.3)
					info_log "Patch version advancement for '$pkg'. Expected: $expected_base_version, Found: $actual_version. Automatically accepting the newer version."
					# 覆写变量！传给下游的打包脚本，保持 -1 为 release number
					version="${actual_version}-1" 
				fi
			fi
			# ================================
		fi
		# ros-pkg-src.list 格式: pkg \t src_path \t version \t git_url \t tree \t repo_name
		echo -e "$pkg\t$pkg_src_path\t$version\t$git_url\t$tree\t$repo_name" >> ${ROS_PKG_SRC}
	done < ${ROS_PKG_LIST}

	if [ -f "${OUTPUT}/version_mismatch.log" ]; then
		error_log "Detected version mismatches!"
	fi
	info_log "Gen ros-pkg-src.list done, you can find it in ${ROS_PKG_SRC}"
}

main $*