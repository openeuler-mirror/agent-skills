import os
import sys
import argparse

def get_package_repo(spec_dir, pkg):
    """Find which repo the package belongs to. In ros-upstream-fetch, it's often the same name, or we can look at the Source url or package_name."""
    # Since we are asked to find siblings, maybe they are in the same folder, or maybe they share a repo name.
    # Actually, in ros-upstream-fetch/output/repo, each directory is a repo.
    # The folders are named after the *repository*, not necessarily the package.
    # Let's find the repo directory that contains this package's spec file.
    pkg_hyphen = pkg.replace('_', '-')
    pkg_under = pkg.replace('-', '_')
    
    # Check if a directory exists matching the pkg name
    for repo_name in os.listdir(spec_dir):
        if repo_name in [pkg, pkg_hyphen, pkg_under]:
            return repo_name
        
        # Or if the repo contains a spec file matching the pkg
        repo_path = os.path.join(spec_dir, repo_name)
        if os.path.isdir(repo_path):
            spec_names = [f"{pkg}.spec", f"{pkg_hyphen}.spec", f"{pkg_under}.spec"]
            for spec_name in spec_names:
                if os.path.exists(os.path.join(repo_path, spec_name)):
                    return repo_name
    return None

def get_packages_in_repo(spec_dir, repo_name):
    """Return all packages in a given repo directory based on .spec files."""
    repo_path = os.path.join(spec_dir, repo_name)
    pkgs = []
    if os.path.isdir(repo_path):
        for f in os.listdir(repo_path):
            if f.endswith('.spec'):
                pkgs.append(f[:-5])
    return pkgs

def topological_sort(pkgs, spec_dir):
    """Simple topological sort of the requested packages based on BuildRequires/Requires in their spec files."""
    # Build a dependency graph among the selected packages only
    graph = {pkg: [] for pkg in pkgs}
    
    for pkg in pkgs:
        # Find spec file
        pkg_hyphen = pkg.replace('_', '-')
        pkg_under = pkg.replace('-', '_')
        repo_name = get_package_repo(spec_dir, pkg)
        
        if not repo_name:
            continue
            
        repo_path = os.path.join(spec_dir, repo_name)
        spec_file = None
        for name in [pkg, pkg_hyphen, pkg_under]:
            if os.path.exists(os.path.join(repo_path, f"{name}.spec")):
                spec_file = os.path.join(repo_path, f"{name}.spec")
                break
                
        if spec_file:
            with open(spec_file, 'r') as f:
                for line in f:
                    if line.startswith('BuildRequires:') or line.startswith('Requires:'):
                        dep = line.split(':')[1].strip()
                        # Very simple heuristic: ROS packages often start with ros-humble-
                        if dep.startswith('ros-humble-'):
                            dep_pkg = dep[len('ros-humble-'):]
                            dep_pkg_hyphen = dep_pkg.replace('_', '-')
                            dep_pkg_under = dep_pkg.replace('-', '_')
                            # Check if this dep is in our selected pkgs
                            for p in pkgs:
                                if p in [dep_pkg, dep_pkg_hyphen, dep_pkg_under]:
                                    graph[pkg].append(p)
                                    break

    # Topo sort
    visited = set()
    temp_mark = set()
    order = []
    
    def visit(n):
        if n in temp_mark:
            return # cycle
        if n not in visited:
            temp_mark.add(n)
            for m in graph.get(n, []):
                visit(m)
            temp_mark.remove(n)
            visited.add(n)
            order.append(n)
            
    for pkg in pkgs:
        visit(pkg)
        
    return order

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-file', required=True)
    parser.add_argument('--output-list', required=True)
    parser.add_argument('--output-layers', required=True)
    parser.add_argument('--repo-dir', required=True)
    args = parser.parse_args()

    # Read explicitly requested packages
    requested_pkgs = []
    with open(args.input_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                requested_pkgs.append(line)

    expanded_pkgs = set()
    for pkg in requested_pkgs:
        repo_name = get_package_repo(args.repo_dir, pkg)
        if repo_name:
            # Add all packages in this repo
            repo_pkgs = get_packages_in_repo(args.repo_dir, repo_name)
            expanded_pkgs.update(repo_pkgs)
        else:
            expanded_pkgs.add(pkg) # Fallback

    # Topologically sort the expanded packages
    sorted_pkgs = topological_sort(list(expanded_pkgs), args.repo_dir)

    # Write outputs
    with open(args.output_list, 'w') as f:
        for pkg in sorted_pkgs:
            f.write(f"{pkg}\n")
            
    with open(args.output_layers, 'w') as f:
        # Just put them in a single layer for now, one per line or separated by space
        # Or layer by layer if we want to be fancy. For simple topo sort, we can just output line by line
        for pkg in sorted_pkgs:
            f.write(f"{pkg}\n")
            
    print(f"Expanded and sorted {len(sorted_pkgs)} packages.")

if __name__ == '__main__':
    main()
