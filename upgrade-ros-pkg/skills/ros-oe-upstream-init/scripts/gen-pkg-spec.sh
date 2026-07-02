#!/bin/bash

. base.sh

GEN_ONE=$1

ROS_PKG_SRC=${OUTPUT}/ros-pkg-src.list
ROS_PACKAGE_FIX=${ROOT}/ros/${ROS_DISTRO}/package_fix
ROS_PKG_REMAP=${ROOT}/global_config/pkg.remap

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

	if [ ! -f ${ROS_PKG_SRC} -o ! -f ${ROS_PKG_LIST} ]
	then
		error_log "Can not find ${ROS_PKG_SRC}, you can use get-pkg-src.sh to create it"
		exit 1
	fi

	if [ "$GEN_ONE" == "" ]
	then
		rm -rf ${ROS_REPO_BASE}
	fi
	mkdir -p ${ROS_REPO_BASE}
}

spec_fix()
{
	pkg=$1
	deps_suffix=$2
	require_type=$3
	key_word=$4
	require_file=$5

	spec_fix_file=${ROS_PACKAGE_FIX}/$pkg/${deps_suffix}

	[ ! -f ${spec_fix_file} ] && return

	if [ -f ${require_file} ]
	then
		for dep in `grep "^\-" ${spec_fix_file} | sed -e "s|^\-||g"`
		do
			sed -i "/^$require_type: *$dep\$/d" $require_file
		done
	fi

	grep -q "^\+" $spec_fix_file
	[ $? -ne 0 ] && return

	grep "^\+" $spec_fix_file | sed -e "s|^\+|$require_type: |g" >> ${require_file}
}

rename_requires()
{
	require_file=$1

	while read deb_pkg rpm_pkg
	do
		sed -i "s|Requires: ${deb_pkg}\$|Requires: ${rpm_pkg}|g" $require_file
	done <${ROS_PKG_REMAP}
}

replace_key_word()
{
	key_word=$1
	input_file=$2
	replace_file=$3

	if [ ! -f ${input_file} ]
	then
		sed -i "/${key_word}/d" $replace_file
		return
	fi

	desc_wc=`cat ${input_file} | wc -l`
	if [ "$desc_wc" = "1" ]
	then
		desc=`cat ${input_file}`
		sed -i "s|${key_word}|$desc|g" $replace_file
	else
		desc=`cat ${input_file} | sed ":a;N;s/\n/ROS_DESC_CRLF/g;ta"`
		sed -i "s|${key_word}|$desc|g" $replace_file
		sed -i ":a;N;s/ROS_DESC_CRLF/\n/g;ta" $replace_file
	fi
}

gen_requires()
{
	pkg=$1
	deps_suffix=$2
	require_type=$3
	key_word=$4
	spec=$5

	debug_log "gen ${deps_suffix}"

	package_xml_deps=${ROS_DEPS_BASE}/$pkg-${deps_suffix}
	require_file=${OUTPUT}/.tempRequires

	rm -f ${require_file}

	[ -f ${package_xml_deps} ] && cp ${package_xml_deps} ${require_file}
	spec_fix $pkg $deps_suffix $require_type $key_word $require_file

	if [ ! -f ${require_file} ]
	then
		sed -i "/${key_word}/d" $spec
		return
	fi

	rename_requires $require_file

	replace_key_word ${key_word} ${require_file} $spec
}

modify_spec()
{
	pkg=$1
	spec=$2
	pkg_dir_name=$3
	base_version=$4
	release_version=$5

	info_log "gen spec for $pkg"

	debug_log "gen version"

	csrc=`find $pkg_dir_name -name "*.c" | grep -v /test/ | wc -l`
	cppsrc=`find $pkg_dir_name -name "*.cpp" | grep -v /test/ | wc -l`
	no_debug=`grep -P "^${pkg}\$" ${ROOT}/ros/${ROS_DISTRO}/config/no-debuginfo`

	if [ "$csrc" == "0" -a "$cppsrc" == "0" ] || [ "$no_debug" != "" ]
	then
		sed -i "s|ROS_PACKAGE_NO_DEBUGINFO|%global debug_package %{nil}|g" $spec
	else
		sed -i '/ROS_PACKAGE_NO_DEBUGINFO/d' $spec
	fi

	sed -i "s|ROS_PACKAGE_NAME|$pkg|g" $spec
	sed -i "s|ROS_DISTRO|${ROS_DISTRO}|g" $spec
	sed -i "s|ROS_PACKAGE_VERSION|$base_version|g" $spec
	sed -i "s|ROS_PACKAGE_RELEASE|$release_version|g" $spec

	debug_log "gen desc"
	desc_wc=`cat ${ROS_DEPS_BASE}/$pkg-PackageXml-description | wc -l`
	if [ "$desc_wc" = "1" ]
	then
		desc=`cat ${ROS_DEPS_BASE}/$pkg-PackageXml-description`
		sed -i "s|ROS_PACKAGE_SUMMARY|$desc|g" $spec
		sed -i "s|ROS_PACKAGE_DESCRIPTION|$desc|g" $spec
	else
		desc=`cat ${ROS_DEPS_BASE}/$pkg-PackageXml-description | sed ":a;N;s/\n/ROS_DESC_CRLF/g;ta"`
		sed -i "s|ROS_PACKAGE_SUMMARY|ROS $pkg package|g" $spec
		sed -i "s|ROS_PACKAGE_DESCRIPTION|$desc|g" $spec
		sed -i ":a;N;s/ROS_DESC_CRLF/\n/g;ta" $spec
	fi

	debug_log "gen license"
	license_wc=`grep license: ${ROS_DEPS_BASE}/$pkg-PackageXml | awk -F"license:" '{print $2}' | wc -l`
	if [ "$license_wc" = "1" ]
	then
		license=`grep license: ${ROS_DEPS_BASE}/$pkg-PackageXml | awk -F"license:" '{print $2}'`
		sed -i "s|ROS_PACKAGE_LICENSE|$license|g" $spec
	else
		license=`grep license: ${ROS_DEPS_BASE}/$pkg-PackageXml | awk -F"license:" '{print $2}' | sed ":a;N;s/\n/ and /g;ta"`
		sed -i "s|ROS_PACKAGE_LICENSE|$license|g" $spec
	fi

	debug_log "gen url"
	url=`grep url: ${ROS_DEPS_BASE}/$pkg-PackageXml | awk -F"url:" '{print $2}' | sed -n '1p'`
	if [ "$url" = "" ]
	then
		sed -i '/ROS_PACKAGE_URL/d' $spec
	else
		sed -i "s|ROS_PACKAGE_URL|$url|g" $spec
	fi

	gen_requires $pkg Requires Requires ROS_PACKAGE_REQUIRES $spec
	gen_requires $pkg BuildRequires BuildRequires ROS_PACKAGE_BUILDREQUIRES $spec
	gen_requires $pkg test-BuildRequires BuildRequires ROS_TEST_BUILDREQUIRES $spec
	replace_key_word ROS_PROVIDES_FIX ${ROS_PACKAGE_FIX}/$pkg/Provides $spec

	if [ "$pkg" == "ament-cmake-core" -o "$pkg" == "ament-package" -o "$pkg" == "ros-workspace" ]
	then
		sed -i '/ROS_ALL_FIX_REQUIRES/d' $spec
	else
		sed -i "s|ROS_ALL_FIX_REQUIRES||g" $spec
	fi

	debug_log "gen changelog"
	maintainer=`grep maintainer: ${ROS_DEPS_BASE}/$pkg-PackageXml | awk -F"maintainer:" '{print $2}'`
	changetime=`LC_TIME=en_US.UTF-8 date +"%a %b %d %Y"`
	changelog="$changetime $maintainer - $base_version-$release_version"
	sed -i "s|ROS_PACKAGE_CHANGELOG|$changelog|g" $spec

	debug_log "gen spec ok"
}

spec_type_fix()
{
	pkg=$1

	spec_type=`cat ${ROS_PACKAGE_FIX}/${pkg}/spec_type 2>/dev/null`
	if [ "$spec_type" == "" ]
	then
		return
	fi

	spec_tplt=${spec_type}.spec
	cp ${ROOT}/template/${spec_tplt} $pkg.spec
}

package_fix()
{
	pkg=$1
	pkg_repo=$2

	if [ -d ${ROS_PACKAGE_FIX}/$pkg ]
	then
		find ${ROS_PACKAGE_FIX}/$pkg -type f | grep -v "\.fix" | xargs -i cp {} $pkg_repo/
		replace_key_word ROS_SOURCE_FIX ${ROS_PACKAGE_FIX}/$pkg/source.fix $spec
		replace_key_word ROS_PREP_FIX ${ROS_PACKAGE_FIX}/$pkg/prep.fix $spec
			replace_key_word ROS_CMAKE_ARGS_FIX ${ROS_PACKAGE_FIX}/$pkg/cmake_args.fix $spec
	else
		sed -i '/ROS_SOURCE_FIX/d' $spec
		sed -i '/ROS_PREP_FIX/d' $spec
			sed -i '/ROS_CMAKE_ARGS_FIX/d' $spec
	fi
}

main()
{
	prepare

	info_log "Start to analyse ros-pkg."

	for repo in `cat ${ROS_PKG_LIST} | awk '{print $2}' | sort | uniq`
	do
		[ "$GEN_ONE" == "" ] && info_log "start to gen $repo"

		mkdir -p ${ROS_REPO_BASE}/${repo}/
		cd ${ROS_REPO_BASE}/${repo}/

		# ros-pkg-src.list 格式: pkg \t src_path \t version \t git_url \t tree \t repo_name
		# 使用最后一列 repo_name 匹配
		awk -F'\t' -v repo="$repo" '$6 == repo {print $1"\t"$2"\t"$3}' ${ROS_PKG_SRC} >${OUTPUT}/.repo_pkgs

		if [ "$GEN_ONE" == "" ]
		then
			echo "<multibuild>" >_multibuild
		fi

		pkg_num=0
		while read pkg path version
		do
			if [ "$GEN_ONE" != "" -a "$pkg" != "$GEN_ONE" ]
			then
				continue
			fi

			if [ "$pkg" = "" -o "$path" = "" -o "$version" = "" ]
			then
				error_log "Wrong package $pkg"
				exit 1
			fi

			base_version=`echo $version | awk -F"-" '{print $1}'`
			release_version=`echo $version | awk -F"-" '{print $2}'`

			if [ -f ${ROS_PACKAGE_FIX}/${pkg}/custom.spec ]
			then
				info_log "Found custom static spec for $pkg, skipping auto-generation."
				cp ${ROS_PACKAGE_FIX}/${pkg}/custom.spec ${pkg}.spec
				
				# set spec variable so package_fix can find it
				spec="${pkg}.spec"

				# Apply basic macros if they exist in custom spec
				sed -i "s|ROS_PACKAGE_NAME|$pkg|g" $spec
				sed -i "s|ROS_DISTRO|${ROS_DISTRO}|g" $spec
				sed -i "s|ROS_PACKAGE_VERSION|$base_version|g" $spec
				sed -i "s|ROS_PACKAGE_RELEASE|$release_version|g" $spec
				
				package_fix $pkg ${ROS_REPO_BASE}/${repo}
				
				if [ "$GEN_ONE" == "" ]
				then
					echo -e "\t<flavor>$pkg</flavor>" >>_multibuild
				fi
				pkg_num=`expr $pkg_num + 1`
				continue
			fi

			pkg_dir_name=$pkg-$base_version
			mkdir -p $pkg_dir_name
			cp -r ${ROS_SRC_BASE}/$path/* $pkg-$base_version/
			tar -czf $pkg-$base_version.tar.gz $pkg-$base_version

			if [ -f $pkg-$base_version/setup.py ]
			then
				cp ${ROOT}/template/py.spec $pkg.spec
			else
				cp ${ROOT}/template/cmake.spec $pkg.spec
			fi

			spec_type_fix $pkg

			modify_spec $pkg $pkg.spec $pkg_dir_name $base_version $release_version

			package_fix $pkg ${ROS_REPO_BASE}/${repo}

			if [ "$GEN_ONE" == "" ]
			then
				echo -e "\t<flavor>$pkg</flavor>" >>_multibuild
			fi
			pkg_num=`expr $pkg_num + 1`
		done < ${OUTPUT}/.repo_pkgs

		if [ "$GEN_ONE" == "" ]
		then
			echo "</multibuild>" >>_multibuild
			[ $pkg_num -lt 2 ] && rm _multibuild
		fi
	done

	info_log "Gen ros-pkg-src.list done, you can find it in ${ROS_PKG_SRC}"
}

main $*