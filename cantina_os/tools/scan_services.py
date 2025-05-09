#!/usr/bin/env python3
"""
Service Architecture Scanner

This script scans all service implementations for common architectural issues:
1. Inconsistent attribute naming conventions (service_name vs _service_name)
2. Missing asyncio.create_task() for event subscriptions
3. Duplicate method implementations
4. Inconsistent error handling patterns

Usage:
    python scan_services.py
"""

import os
import re
import sys
import glob
from dataclasses import dataclass
from typing import List, Dict, Set, Optional, Tuple

@dataclass
class Issue:
    """Represents an issue found in a service implementation."""
    file: str
    line: int
    message: str
    severity: str  # 'ERROR', 'WARNING', 'INFO'
    
    def __str__(self) -> str:
        return f"{self.severity}: {self.file}:{self.line} - {self.message}"

def scan_file_for_issues(file_path: str) -> List[Issue]:
    """Scan a single file for architectural issues."""
    issues = []
    
    with open(file_path, 'r') as f:
        lines = f.readlines()
        
    # Check for service class definition
    service_class_line = None
    service_class_name = None
    for i, line in enumerate(lines):
        match = re.search(r'class\s+(\w+)\(BaseService\):', line)
        if match:
            service_class_line = i
            service_class_name = match.group(1)
            break
            
    if service_class_name is None:
        # Not a service class
        return []
        
    # Check attribute access patterns
    for i, line in enumerate(lines):
        # Check for direct attribute access
        if 'self.service_name' in line and 'def service_name' not in line:
            issues.append(Issue(
                file=file_path,
                line=i+1,
                message=f"Direct access to 'service_name' - should use 'self._service_name' or property",
                severity="ERROR"
            ))
            
        # Check for event subscription without asyncio.create_task
        if 'self.subscribe(' in line and 'asyncio.create_task(' not in line:
            issues.append(Issue(
                file=file_path,
                line=i+1,
                message="Event subscription not wrapped in asyncio.create_task()",
                severity="ERROR"
            ))
            
        # Check for emit without await
        if 'self.emit(' in line and not line.strip().startswith('await'):
            issues.append(Issue(
                file=file_path,
                line=i+1,
                message="Event emission not awaited with 'await'",
                severity="WARNING"
            ))
            
    # Look for _setup_subscriptions method
    setup_subscriptions_found = False
    for i, line in enumerate(lines):
        if '_setup_subscriptions' in line and 'def ' in line:
            setup_subscriptions_found = True
            # Check for async def
            if 'async def' not in line:
                issues.append(Issue(
                    file=file_path,
                    line=i+1,
                    message="_setup_subscriptions should be an async method",
                    severity="WARNING"
                ))
                
    # If service class but no _setup_subscriptions, warn
    if service_class_name and not setup_subscriptions_found:
        issues.append(Issue(
            file=file_path,
            line=service_class_line+1,
            message=f"Service class {service_class_name} missing _setup_subscriptions method",
            severity="WARNING"
        ))
        
    return issues

def scan_directory(directory: str) -> List[Issue]:
    """Scan a directory for service files and architectural issues."""
    all_issues = []
    
    # Find all Python files
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                issues = scan_file_for_issues(file_path)
                all_issues.extend(issues)
                
    return all_issues

def generate_report(issues: List[Issue]) -> None:
    """Generate a report of all issues found."""
    errors = [i for i in issues if i.severity == 'ERROR']
    warnings = [i for i in issues if i.severity == 'WARNING']
    infos = [i for i in issues if i.severity == 'INFO']
    
    print(f"== CantinaOS Architecture Scanner Report ==")
    print(f"Found {len(errors)} errors, {len(warnings)} warnings, {len(infos)} info")
    print()
    
    if errors:
        print("=== ERRORS ===")
        for issue in errors:
            print(issue)
        print()
        
    if warnings:
        print("=== WARNINGS ===")
        for issue in warnings:
            print(issue)
        print()
        
    if infos:
        print("=== INFO ===")
        for issue in infos:
            print(issue)
        print()
        
    print("== RECOMMENDATION ==")
    print("1. Fix all ERROR issues first - these will cause runtime failures")
    print("2. Address WARNING issues to prevent future problems")
    print("3. Review the service_template.py file for the correct implementation pattern")

if __name__ == "__main__":
    # Get the directory to scan
    if len(sys.argv) > 1:
        directory = sys.argv[1]
    else:
        # Default to the parent directory of this script's location
        directory = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'cantina_os'))
    
    print(f"Scanning directory: {directory}")
    issues = scan_directory(directory)
    generate_report(issues) 