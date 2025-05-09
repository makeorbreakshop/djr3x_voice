#!/usr/bin/env python3
"""
Subscription Issue Scanner

This script scans the CantinaOS codebase for potential event subscription issues
similar to the one found in DeepgramTranscriptionService.

It looks for patterns where:
1. A service defines a _setup_subscriptions method
2. The method calls self.subscribe() without awaiting or wrapping in asyncio.create_task()

Usage:
python find_subscription_issues.py [path_to_scan]
"""

import os
import re
import argparse
import ast
from typing import List, Dict, Tuple, Optional

# Constants
DEFAULT_PATH = os.path.join(os.getcwd(), 'cantina_os')
ISSUE_PATTERN = r'def\s+_setup_subscriptions.*?self\.subscribe\s*\('
GOOD_PATTERN = r'(await\s+self\.subscribe|asyncio\.create_task\s*\(\s*self\.subscribe)'

class SubscriptionIssueScanner(ast.NodeVisitor):
    """AST-based scanner for subscription issues."""

    def __init__(self):
        self.issues = []
        self.current_file = None
        self.in_setup_subscriptions = False
        self.class_name = None

    def visit_ClassDef(self, node):
        """Visit class definition."""
        old_class_name = self.class_name
        self.class_name = node.name
        self.generic_visit(node)
        self.class_name = old_class_name

    def visit_FunctionDef(self, node):
        """Visit function definition."""
        if node.name == '_setup_subscriptions':
            self.in_setup_subscriptions = True
            self.generic_visit(node)
            self.in_setup_subscriptions = False
        else:
            self.generic_visit(node)

    def visit_Call(self, node):
        """Visit function calls."""
        if self.in_setup_subscriptions:
            # Check if this is a subscribe call
            if (isinstance(node.func, ast.Attribute) and 
                isinstance(node.func.value, ast.Name) and
                node.func.value.id == 'self' and
                node.func.attr == 'subscribe'):
                
                # Check if it's inside an await or asyncio.create_task
                parent_is_await = isinstance(self.parent_node(node), ast.Await)
                parent_is_create_task = (
                    isinstance(self.parent_node(node), ast.Call) and
                    isinstance(self.parent_node(node).func, ast.Attribute) and
                    self.parent_node(node).func.attr == 'create_task'
                )
                
                if not (parent_is_await or parent_is_create_task):
                    line_num = node.lineno
                    self.issues.append((self.current_file, self.class_name, line_num))
        
        self.generic_visit(node)
    
    def parent_node(self, node):
        """Get parent node of the current node."""
        for field, value in ast.iter_fields(self.current_parent):
            if isinstance(value, list):
                for item in value:
                    if item == node:
                        return self.current_parent
            elif value == node:
                return self.current_parent
        return None

def scan_file(file_path: str) -> List[Tuple[str, str, int]]:
    """Scan a single file for subscription issues."""
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Quick regex check before more expensive AST parsing
    if re.search(ISSUE_PATTERN, content, re.DOTALL) and not re.search(GOOD_PATTERN, content, re.DOTALL):
        try:
            tree = ast.parse(content)
            scanner = SubscriptionIssueScanner()
            scanner.current_file = file_path
            scanner.current_parent = tree
            scanner.visit(tree)
            return scanner.issues
        except SyntaxError:
            print(f"Syntax error in {file_path}, skipping")
    
    return []

def scan_directory(directory: str) -> Dict[str, List[Tuple[str, int]]]:
    """Recursively scan a directory for subscription issues."""
    all_issues = {}
    
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                
                # Skip test files
                if 'test_' in file or '/tests/' in file_path:
                    continue
                
                issues = scan_file(file_path)
                if issues:
                    for file_path, class_name, line_num in issues:
                        key = f"{file_path} ({class_name})"
                        if key not in all_issues:
                            all_issues[key] = []
                        all_issues[key].append(line_num)
    
    return all_issues

def manual_scan_directory(directory: str) -> Dict[str, List[int]]:
    """Scan a directory using regex patterns."""
    all_issues = {}
    
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                
                # Skip test files
                if 'test_' in file or '/tests/' in file_path:
                    continue
                
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # Check for the bad pattern without the good pattern
                if re.search(ISSUE_PATTERN, content, re.DOTALL) and not re.search(GOOD_PATTERN, content, re.DOTALL):
                    # Find all occurrences and their line numbers
                    lines = content.split('\n')
                    in_setup_subscriptions = False
                    issue_lines = []
                    
                    for i, line in enumerate(lines):
                        if re.match(r'\s*def\s+_setup_subscriptions', line):
                            in_setup_subscriptions = True
                        elif in_setup_subscriptions and re.match(r'\s*def\s+', line):
                            in_setup_subscriptions = False
                        
                        if in_setup_subscriptions and 'self.subscribe(' in line:
                            # Check if this line or the previous has await or create_task
                            prev_line = lines[i-1] if i > 0 else ""
                            if not ('await' in line or 'create_task' in line or 
                                    'await' in prev_line or 'create_task' in prev_line):
                                issue_lines.append(i + 1)  # 1-indexed line numbers
                    
                    if issue_lines:
                        all_issues[file_path] = issue_lines
    
    return all_issues

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Scan for event subscription issues')
    parser.add_argument('--path', type=str, default=DEFAULT_PATH, 
                        help='Path to scan (default: ./cantina_os)')
    parser.add_argument('--manual', action='store_true', 
                        help='Use regex-based scanning instead of AST parsing')
    args = parser.parse_args()
    
    print(f"Scanning {args.path} for subscription issues...")
    
    if not os.path.exists(args.path):
        print(f"Error: Path {args.path} does not exist")
        return
    
    if args.manual:
        issues = manual_scan_directory(args.path)
    else:
        try:
            issues = scan_directory(args.path)
        except Exception as e:
            print(f"Error with AST parsing: {e}")
            print("Falling back to manual regex scanning...")
            issues = manual_scan_directory(args.path)
    
    if not issues:
        print("\n✅ No subscription issues found!")
        return
    
    print("\n⚠️ Potential subscription issues found:\n")
    
    for file_path, lines in issues.items():
        print(f"📁 {file_path}")
        for line in lines:
            print(f"  - Line {line}: Missing await or asyncio.create_task() around self.subscribe()")
    
    print("\nRecommended fix pattern:")
    print("""
def _setup_subscriptions(self) -> None:
    \"\"\"Set up event subscriptions.\"\"\"
    asyncio.create_task(self.subscribe(
        EventTopics.SOME_TOPIC,
        self._handle_event
    ))
    self.logger.info("Set up subscription for events")
    """)
    
    print(f"\nTotal files with issues: {len(issues)}")

if __name__ == "__main__":
    main() 