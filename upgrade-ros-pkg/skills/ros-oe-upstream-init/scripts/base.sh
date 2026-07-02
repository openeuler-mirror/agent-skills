#!/bin/bash

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT="$SCRIPT_DIR"
WORKSPACE_DIR="${ROS_UPSTREAM_WORKSPACE:-$ROOT}"

ROS_DISTRO=`grep ROS_DISTRO "$ROOT/config" | cut -d'=' -f2`
DEBUG=`grep DEBUG "$ROOT/config" | cut -d'=' -f2`
DOWNLOAD_EOL_REPO=`grep DOWNLOAD_EOL_REPO "$ROOT/config" | cut -d'=' -f2`
DOWNLOAD_UNKNOWN_REPO=`grep DOWNLOAD_UNKNOWN_REPO "$ROOT/config" | cut -d'=' -f2`

# 默认值处理
DOWNLOAD_EOL_REPO=${DOWNLOAD_EOL_REPO:-off}
DOWNLOAD_UNKNOWN_REPO=${DOWNLOAD_UNKNOWN_REPO:-off}

OUTPUT=${WORKSPACE_DIR}/output
ROS_OUTPUT_TMP=${OUTPUT}/.tmp
ROS_SRC_BASE=${OUTPUT}/src
ROS_DEPS_BASE=${OUTPUT}/deps
ROS_REPO_BASE=${OUTPUT}/repo
LOG=${OUTPUT}/ros-tools.log

ROS_PKG_LIST=${OUTPUT}/ros-pkg.list
ROS_PROJECTS_NAME=${OUTPUT}/ros-projects-name.list

mkdir -p ${OUTPUT}
mkdir -p ${ROS_OUTPUT_TMP}

error_log()
{
        echo "`date` [Error] $*"
        echo "`date` [Error] $*" >>${LOG}
}

info_log()
{
        echo "`date` [Info ] $*"
        echo "`date` [Info ] $*" >> ${LOG}
}

debug_log()
{
	if [ "$DEBUG" != "yes" ]
	then
	        return
	fi
        echo "`date` [Debug] $*"
        echo "`date` [Debug] $*" >> ${LOG}
}

if [ "${ROS_DISTRO}" = "" ]
then
        error_log "ROS_DISTRO not defined"
        exit 1
fi
