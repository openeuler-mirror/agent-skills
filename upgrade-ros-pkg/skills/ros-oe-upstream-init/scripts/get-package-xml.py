#!/usr/bin/python3

from xml.dom.minidom import parse
import xml.dom.minidom
import sys

PackageXMLTree = xml.dom.minidom.parse(sys.argv[2])
collection = PackageXMLTree.documentElement

def get_text_content(element):
    """安全获取元素的文本内容，如果为空则返回空字符串"""
    if element.childNodes and len(element.childNodes) > 0:
        return element.childNodes[0].data.strip()
    return ""

def get_depend(depend_name, org_depend_file_name):

    deps = collection.getElementsByTagName(depend_name)

    f = open(org_depend_file_name, 'a+', encoding='utf-8')

    for dep in deps:
        if dep.hasAttribute("ROS_VERSION"):
            ros_version = dep.getAttribute("ROS_VERSION")
            if ros_version == "1":
                continue
        if dep.hasAttribute("condition"):
            condition = dep.getAttribute("condition")
            if condition == "$ROS_VERSION == 1":
                continue
            if condition == "$ROS_PYTHON_VERSION == 2":
                continue

        if dep.hasAttribute("type"):
            url_type = dep.getAttribute("type")
            if url_type != "website":
                continue

        # 获取文本内容，如果为空则跳过
        text_content = get_text_content(dep)
        if not text_content and depend_name not in ["maintainer"]:
            continue

        if depend_name == "maintainer" and dep.hasAttribute("email"):
            email = dep.getAttribute("email")
            if text_content:
                f.write(depend_name + ":" + text_content + " " + email + "\n")
                f.close()
                return

        if depend_name == "description":
            if text_content:
                f.write(text_content + "\n")
        else:
            if text_content:
                f.write(depend_name + ":" + text_content + "\n")


    f.close()

get_depend("name", sys.argv[1])
get_depend("depend", sys.argv[1])
get_depend("build_depend", sys.argv[1])
get_depend("build_export_depend", sys.argv[1])
get_depend("exec_depend", sys.argv[1])
get_depend("test_depend", sys.argv[1])
get_depend("buildtool_depend", sys.argv[1])
get_depend("buildtool_export_depend", sys.argv[1])
get_depend("run_depend", sys.argv[1])
get_depend("license", sys.argv[1])
get_depend("url", sys.argv[1])
get_depend("maintainer", sys.argv[1])
get_depend("description", sys.argv[1] + "-description")
