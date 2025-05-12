#!/usr/bin/env python3
"""
Quick script to rewrite create_vectors_robust.py with fixed indentation.
"""

def fix_file(input_file, output_file):
    with open(input_file, 'r') as f:
        lines = f.readlines()
    
    fixed_lines = []
    in_create_embeddings_batch = False
    
    for line in lines:
        # Detect if we're in the create_embeddings_batch method
        if "async def create_embeddings_batch" in line:
            in_create_embeddings_batch = True
        
        # Fix the indentation of the estimated_tokens line
        if in_create_embeddings_batch and "estimated_tokens = min" in line:
            # Add 8 spaces of indentation (match method body)
            fixed_line = "        " + line.lstrip()
            fixed_lines.append(fixed_line)
        else:
            fixed_lines.append(line)
    
    with open(output_file, 'w') as f:
        f.writelines(fixed_lines)
    
    print(f"Fixed file written to {output_file}")

if __name__ == "__main__":
    fix_file("scripts/create_vectors_robust.py", "scripts/create_vectors_robust_fixed.py") 