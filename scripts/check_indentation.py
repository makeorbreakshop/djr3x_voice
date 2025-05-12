#!/usr/bin/env python3
import re

def check_indentation(file_path):
    """Check the file for indentation issues."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    line_num = 570
    context_lines = 20
    
    print(f"Examining lines {line_num-context_lines}-{line_num+context_lines}:")
    for i in range(line_num-context_lines, line_num+context_lines):
        if i < 0 or i >= len(lines):
            continue
        
        # Calculate indentation level
        indent = len(lines[i]) - len(lines[i].lstrip())
        print(f"{i+1:4d}: {indent:2d} spaces | {lines[i].rstrip()}")
        
        # Check for "if" statements that might be problematic
        if re.search(r'^\s*if\s+.*:$', lines[i]) and i+1 < len(lines):
            next_indent = len(lines[i+1]) - len(lines[i+1].lstrip())
            if next_indent <= indent:
                print(f"POTENTIAL ISSUE: Line {i+1} has an if statement, but line {i+2} doesn't increase indentation!")

if __name__ == "__main__":
    check_indentation("scripts/create_vectors_robust.py") 