#!/usr/bin/env python3
"""
Generate EUR Build Layers
Reads a list of ROS packages and their dependencies, then outputs them in deterministic topological layers.
Packages in Layer N only depend on packages in Layer 0 to Layer N-1 (or system dependencies).
This allows for maximum parallel building in EUR.
"""

import os
import sys
import argparse
import json
from collections import defaultdict
from pathlib import Path

# Try to import ROSDependencyResolver from the same directory
try:
    from resolve_dependencies import ROSDependencyResolver
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        from resolve_dependencies import ROSDependencyResolver
    except ImportError:
        print("Error: Could not import ROSDependencyResolver. Make sure resolve_dependencies.py is in the same directory.", file=sys.stderr)
        sys.exit(1)

def generate_layers(packages_list, deps_dir):
    """
    Generate build layers from a list of target packages.
    """
    resolver = ROSDependencyResolver(deps_dir)
    
    # 1. Normalize target packages mapping
    # normalized_name -> original_name
    norm_to_orig = {}
    for p in packages_list:
        norm = resolver._normalize_pkg_name(p)
        norm_to_orig[norm] = p
        
    target_set = set(norm_to_orig.keys())
    
    # 2. Build the subgraph restricted to target_set
    # adj_list[u] = [v1, v2] means v1 and v2 depend on u (so u must be built before v1 and v2)
    adj_list = defaultdict(list)
    in_degree = {p: 0 for p in target_set}
    
    print(f"[INFO] Analyzing dependencies for {len(target_set)} target packages...")
    missing_xmls = []
    
    for norm_pkg in target_set:
        orig_pkg = norm_to_orig[norm_pkg]
        package_xml = resolver.deps_dir / f"{norm_pkg}-PackageXml"
        
        if not package_xml.exists():
            missing_xmls.append(orig_pkg)
            
        deps = resolver.parse_package_xml(orig_pkg)
        
        for dep in deps:
            norm_dep = resolver._normalize_pkg_name(dep)
            if norm_dep in target_set and norm_dep != norm_pkg:
                # norm_pkg depends on norm_dep
                adj_list[norm_dep].append(norm_pkg)
                in_degree[norm_pkg] += 1
                
    if missing_xmls:
        print(f"[WARNING] ⚠️  Found {len(missing_xmls)} packages missing their -PackageXml file in {deps_dir}.", file=sys.stderr)
        print(f"[WARNING] These packages will be assumed to have NO DEPENDENCIES and will be forced into Layer 0:", file=sys.stderr)
        for i, pkg in enumerate(missing_xmls[:10]):
            print(f"  - {pkg}", file=sys.stderr)
        if len(missing_xmls) > 10:
            print(f"  - ... and {len(missing_xmls) - 10} more.", file=sys.stderr)
                
    # 3. Perform layered topological sort
    layers = []
    assigned = set()
    
    while len(assigned) < len(target_set):
        # Find nodes with in_degree 0 that haven't been assigned yet
        current_layer = [p for p in target_set if in_degree[p] == 0 and p not in assigned]
        
        if not current_layer:
            # Cycle detected
            unassigned = target_set - assigned
            print(f"[WARNING] Dependency cycle detected or missing dependency among packages: {unassigned}", file=sys.stderr)
            print("[WARNING] These packages will be dumped into the final layer together.", file=sys.stderr)
            current_layer = list(unassigned)
            layers.append([norm_to_orig[p] for p in sorted(current_layer)])
            assigned.update(current_layer)
            break
            
        # Add current layer to results (using original names, sorted for determinism)
        layers.append([norm_to_orig[p] for p in sorted(current_layer)])
        assigned.update(current_layer)
        
        # Decrease in_degree for neighbors of the current layer
        for p in current_layer:
            for neighbor in adj_list[p]:
                in_degree[neighbor] -= 1
                
    return layers

def main():
    parser = argparse.ArgumentParser(description="Generate parallel build layers for a list of ROS packages.")
    parser.add_argument("input_file", help="Input file containing list of ROS packages (one per line)")
    parser.add_argument("deps_dir", help="Path to ros-oe-upstream-init output/deps directory containing PackageXml files")
    parser.add_argument("-o", "--output", default="build_layers.txt", help="Output text file path (default: build_layers.txt)")
    parser.add_argument("--json", help="Optional output JSON file path for script parsing")
    
    args = parser.parse_args()
    
    # Read packages
    packages = []
    if not os.path.exists(args.input_file):
        print(f"[ERROR] Input file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)
        
    with open(args.input_file, 'r') as f:
        for line in f:
            line = line.strip()
            # Handle comments and empty lines
            if line and not line.startswith('#'):
                # Handle possible numbering (e.g. "  1. pkg_name") from merged files
                if '.' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        packages.append(parts[1])
                else:
                    packages.append(line)
                    
    packages = list(dict.fromkeys(packages)) # Deduplicate maintaining order
    print(f"[INFO] Loaded {len(packages)} packages from {args.input_file}")
    
    layers = generate_layers(packages, args.deps_dir)
    
    # Write text output
    with open(args.output, 'w') as f:
        f.write("# EUR Parallel Build Layers\n")
        f.write("# Packages in Layer N only depend on packages in Layer 0 to N-1.\n")
        f.write("# Therefore, all packages within the same layer can be built in parallel.\n\n")
        
        for i, layer in enumerate(layers):
            f.write(f"### Layer {i} ###\n")
            for pkg in layer:
                f.write(f"{pkg}\n")
            f.write("\n")
            
    print(f"[INFO] Successfully wrote {len(layers)} layers to {args.output}")
    
    # Write JSON output if requested
    if args.json:
        layer_dict = {f"Layer_{i}": layer for i, layer in enumerate(layers)}
        with open(args.json, 'w') as f:
            json.dump(layer_dict, f, indent=2)
        print(f"[INFO] Successfully wrote JSON layers to {args.json}")
        
    # Print summary to stdout
    print("\n--- Layers Summary ---")
    for i, layer in enumerate(layers):
        print(f"Layer {i}: {len(layer)} packages")

if __name__ == "__main__":
    main()