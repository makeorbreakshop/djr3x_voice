#!/usr/bin/env python3
"""
Naming Inconsistency Scanner for CantinaOS

This script scans the CantinaOS codebase for potential naming inconsistencies
and architectural violations based on our standards.
"""

import os
import re
import sys
import ast
from typing import List, Dict, Tuple, Set, Optional

# Define patterns to search for
PATTERNS = {
    "attribute_without_underscore": r"self\.([a-z][a-zA-Z0-9_]+)\s*=",  # Matching self.attribute = ...
    "attribute_with_underscore": r"self\._([a-z][a-zA-Z0-9_]+)\s*=",   # Matching self._attribute = ...
    "missing_await": r"(?<!await\s)(?<!asyncio\.create_task\()self\.subscribe\(", # subscribe without await
    "direct_status_modification": r"self\._status\s*=",  # Direct status modification
    "override_start_stop": r"async\s+def\s+(start|stop)\(self",  # Overriding start/stop
    "event_bus_direct_access": r"self\._event_bus\.emit\(",  # Direct event bus access
}

# Allowlist of files to ignore
IGNORE_FILES = {
    "base_service.py",  # BaseService is allowed to define these methods
    "service_template.py",  # The template itself
    "find_naming_inconsistencies.py",  # This script
}

# Paths to scan
PATHS_TO_SCAN = [
    "cantina_os",
]

def parse_python_file(file_path: str) -> Optional[ast.Module]:
    """Parse a Python file using AST.
    
    Args:
        file_path: Path to the Python file
        
    Returns:
        The AST module or None if parsing failed
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        return ast.parse(content, filename=file_path)
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return None

def find_class_methods(node: ast.Module) -> Dict[str, List[str]]:
    """Find all classes and their method names.
    
    Args:
        node: AST module node
        
    Returns:
        Dictionary mapping class names to lists of method names
    """
    classes = {}
    
    for item in node.body:
        if isinstance(item, ast.ClassDef):
            class_name = item.name
            method_names = []
            
            for child in item.body:
                if isinstance(child, ast.FunctionDef) or isinstance(child, ast.AsyncFunctionDef):
                    method_names.append(child.name)
                    
            classes[class_name] = method_names
            
    return classes

def check_service_methods(file_path: str) -> List[str]:
    """Check if a service is properly implementing _start/_stop instead of start/stop.
    
    Args:
        file_path: Path to the Python file
        
    Returns:
        List of issues found
    """
    issues = []
    
    tree = parse_python_file(file_path)
    if not tree:
        return issues
    
    classes = find_class_methods(tree)
    
    for class_name, methods in classes.items():
        # Skip BaseService itself
        if class_name == "BaseService":
            continue
            
        # Check if this is a service (inherits from BaseService)
        with open(file_path, 'r') as f:
            content = f.read()
            if "BaseService" in content and f"class {class_name}" in content:
                # Check if it overrides start/stop instead of _start/_stop
                if "start" in methods and class_name != "BaseService":
                    issues.append(f"{file_path}: {class_name} overrides start() instead of _start()")
                if "stop" in methods and class_name != "BaseService":
                    issues.append(f"{file_path}: {class_name} overrides stop() instead of _stop()")
                    
                # Check if it's missing _start/_stop
                if "_start" not in methods:
                    issues.append(f"{file_path}: {class_name} is missing _start() method")
                if "_stop" not in methods:
                    issues.append(f"{file_path}: {class_name} is missing _stop() method")
    
    return issues

def scan_file_for_patterns(file_path: str) -> List[Tuple[str, int, str]]:
    """Scan a file for pattern violations.
    
    Args:
        file_path: Path to the file to scan
        
    Returns:
        List of (pattern_name, line_number, line) tuples for violations
    """
    base_name = os.path.basename(file_path)
    if base_name in IGNORE_FILES:
        return []
        
    violations = []
    
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
            
        for i, line in enumerate(lines):
            line_number = i + 1
            
            for pattern_name, pattern in PATTERNS.items():
                if re.search(pattern, line):
                    violations.append((pattern_name, line_number, line.strip()))
    except Exception as e:
        print(f"Error scanning {file_path}: {e}")
    
    return violations

def scan_directory(directory: str) -> Dict[str, List[Tuple[str, int, str]]]:
    """Recursively scan a directory for Python files.
    
    Args:
        directory: Directory to scan
        
    Returns:
        Dictionary mapping file paths to lists of violations
    """
    results = {}
    
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                violations = scan_file_for_patterns(file_path)
                if violations:
                    results[file_path] = violations
                    
                # Check service methods
                method_issues = check_service_methods(file_path)
                for issue in method_issues:
                    print(issue)
    
    return results

def main():
    """Main function to run the scanner."""
    all_results = {}
    
    for path in PATHS_TO_SCAN:
        results = scan_directory(path)
        all_results.update(results)
    
    # Display results
    print("\n=== Naming and Architecture Inconsistencies ===\n")
    
    if not all_results:
        print("No inconsistencies found!")
        return
    
    for file_path, violations in all_results.items():
        print(f"\n{file_path}:")
        
        for pattern_name, line_number, line in violations:
            print(f"  Line {line_number}: {pattern_name}")
            print(f"    {line}")
    
    # Print summary
    total_violations = sum(len(v) for v in all_results.values())
    print(f"\nTotal files with issues: {len(all_results)}")
    print(f"Total violations: {total_violations}")
    print("\nRun this script regularly to catch inconsistencies early!")

if __name__ == "__main__":
    main() 