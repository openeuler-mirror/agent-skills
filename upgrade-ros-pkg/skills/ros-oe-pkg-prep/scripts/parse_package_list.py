#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import tempfile

def normalize(name):
    """Normalize package names by treating _ and - as equivalent."""
    return name.replace('_', '-')

def main():
    parser = argparse.ArgumentParser(description="Process explicit ROS package list: auto-expand multi-build repos and sort topologically.")
    parser.add_argument("input_file", help="Input file with list of packages")
    parser.add_argument("ros_upstream_init_dir", help="Path to ros-oe-upstream-init directory")
    parser.add_argument("-o", "--output", default="dependency_list.txt", help="Output file path (linear list)")
    parser.add_argument("--layers-output", default="build_layers.txt", help="Output file path for parallel build layers")
    args = parser.parse_args()

    repo_dir = os.path.join(args.ros_upstream_init_dir, "output", "repo")
    deps_dir = os.path.join(args.ros_upstream_init_dir, "output", "deps")

    if not os.path.exists(repo_dir):
        print(f"[ERROR] Repo dir not found: {repo_dir}")
        sys.exit(1)

    # 1. Read input packages
    targets = set()
    with open(args.input_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                targets.add(line)
    
    print(f"[INFO] Initial target packages: {len(targets)}")

    # 2. Auto-expand multi-build repos
    dir_to_pkgs = {}
    pkg_to_dir = {}
    
    # Scan repo dir for .spec files
    for root, dirs, files in os.walk(repo_dir):
        specs = [f for f in files if f.endswith('.spec')]
        if specs:
            pkgs_in_dir = []
            for spec in specs:
                pkg_name = spec[:-5] # remove .spec
                if pkg_name.startswith('ros-humble-'):
                    pkg_name = pkg_name[11:]
                # Store multiple variants to be safe
                pkgs_in_dir.append(pkg_name)
                pkgs_in_dir.append(pkg_name.replace('-', '_'))
            
            for p in set(pkgs_in_dir):
                pkg_to_dir[normalize(p)] = root
                if root not in dir_to_pkgs:
                    dir_to_pkgs[root] = set()
                dir_to_pkgs[root].add(p)

    expanded_targets = set()
    for target in targets:
        norm_target = normalize(target)
        if norm_target in pkg_to_dir:
            d = pkg_to_dir[norm_target]
            expanded_targets.update(dir_to_pkgs[d])
        else:
            print(f"[WARNING] Could not find spec directory for {target}, adding as is.")
            expanded_targets.add(target)
    
    # Convert expanded_targets to a format matching the deps directory
    final_expanded = set()
    for pkg in expanded_targets:
        if os.path.exists(os.path.join(deps_dir, f"{pkg}-PackageXml")):
            final_expanded.add(pkg)
        elif os.path.exists(os.path.join(deps_dir, f"{pkg.replace('-', '_')}-PackageXml")):
            final_expanded.add(pkg.replace('-', '_'))
        elif os.path.exists(os.path.join(deps_dir, f"{pkg.replace('_', '-')}-PackageXml")):
            final_expanded.add(pkg.replace('_', '-'))
        else:
            final_expanded.add(pkg) # fallback

    print(f"[INFO] Expanded target packages (including repo siblings): {len(final_expanded)}")

    # 3. Topological Sort
    # We use merge_package_sources.py to get a full sorted list, then filter it.
    merge_script = os.path.join(os.path.dirname(__file__), "merge_package_sources.py")
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_in:
        for p in final_expanded:
            temp_in.write(f"{p}\n")
        temp_in_path = temp_in.name
        
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_out:
        temp_out_path = temp_out.name
        
    try:
        print(f"[INFO] Resolving dependencies to establish build order...")
        subprocess.run([
            sys.executable, merge_script, 
            temp_in_path, deps_dir, 
            "-o", temp_out_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Read the full sorted list
        full_sorted = []
        with open(temp_out_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    full_sorted.append(line)
        
        # Filter the list to only keep explicitly requested + expanded sibling packages
        normalized_expanded = {normalize(p) for p in final_expanded}
        final_list = []
        for pkg in full_sorted:
            if normalize(pkg) in normalized_expanded:
                final_list.append(pkg)
                normalized_expanded.remove(normalize(pkg))
                
        # Add any packages that missed dependency resolution
        missed = [p for p in final_expanded if normalize(p) in normalized_expanded]
        final_list.extend(missed)
        
    except Exception as e:
        print(f"[WARNING] Dependency resolution encountered an error. Falling back to unsorted expanded list.")
        final_list = list(final_expanded)
        
    finally:
        if os.path.exists(temp_in_path):
            os.remove(temp_in_path)
        if os.path.exists(temp_out_path):
            os.remove(temp_out_path)

    # Write output
    with open(args.output, 'w') as f:
        for pkg in final_list:
            f.write(f"{pkg}\n")
            
    print(f"[INFO] Successfully generated explicit list: {args.output}")
    print(f"[INFO] Final explicit package count: {len(final_list)}")

    # Generate build layers
    generate_layers_script = os.path.join(os.path.dirname(__file__), "generate_build_layers.py")
    if os.path.exists(generate_layers_script):
        try:
            print(f"[INFO] Generating parallel build layers...")
            subprocess.run([
                sys.executable, generate_layers_script,
                args.output, deps_dir,
                "-o", args.layers_output
            ], check=True)
            print(f"[INFO] Successfully generated build layers: {args.layers_output}")
        except subprocess.CalledProcessError:
            print(f"[WARNING] Failed to generate build layers. Continuing anyway.")
    else:
        print(f"[WARNING] Layer generation script not found: {generate_layers_script}")

if __name__ == "__main__":
    main()
