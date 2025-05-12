#!/usr/bin/env python3
import os
import re
import json
import glob
import shutil
from datetime import datetime

def backup_file(file_path):
    """Create a backup of the file before modifying it."""
    if os.path.exists(file_path):
        backup_dir = os.path.dirname(file_path) + "/backups"
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(file_path)
        backup_path = f"{backup_dir}/{filename}_{timestamp}.bak"
        shutil.copy2(file_path, backup_path)
        print(f"Created backup at {backup_path}")
    else:
        print(f"File {file_path} does not exist, no backup created")

def fix_wiki_dump_processor():
    """Fix the Canon/Legends detection in the wiki dump processor."""
    file_path = "src/holocron/wiki_processing/process_wiki_dump.py"
    
    # Create backup
    backup_file(file_path)
    
    # Read the file
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Find the is_canonical_content method
    is_canonical_method = re.search(r'def _is_canonical_content\(.*?\):.*?return False', content, re.DOTALL)
    
    if not is_canonical_method:
        print("Couldn't find the _is_canonical_content method in the file")
        return
    
    # Extract the method content
    old_method = is_canonical_method.group(0)
    
    # Create a new method that properly detects Canon vs Legends
    new_method = """def _is_canonical_content(self, categories: Set[str], text: str) -> bool:
        \"\"\"
        Determine if content is Canon based on categories and text.
        
        Args:
            categories: Set of article categories
            text: Article text content
            
        Returns:
            True if content is canonical, False otherwise
        \"\"\"
        # First check for explicit Canon/Legends markers
        
        # Look for explicit Canon template or category
        if re.search(r'\\{\\{Canon\\}\\}|\\{\\{Canon article\\}\\}|\\[\\[Category:Canon articles\\]\\]', text, re.IGNORECASE):
            return True
            
        # Look for explicit Legends template or category
        if re.search(r'\\{\\{Legends\\}\\}|\\{\\{Legends article\\}\\}|\\[\\[Category:Legends articles\\]\\]', text, re.IGNORECASE):
            return False
            
        # If it has {{Top|leg}} or leg marker, it's Legends
        if re.search(r'\\{\\{Top\\|leg\\}\\}', text, re.IGNORECASE):
            return False
            
        # If it has {{Top|can}} marker, it's Canon
        if re.search(r'\\{\\{Top\\|can\\}\\}', text, re.IGNORECASE):
            return True
            
        # By default, treat articles as Canon
        # This is a reasonable default as we're primarily interested in Canon content
        # and most articles are properly marked with explicit indicators
        return True"""
    
    # Replace the old method with the new one
    new_content = content.replace(old_method, new_method)
    
    # Write the changes back to the file
    with open(file_path, 'w') as f:
        f.write(new_content)
    
    print(f"Updated _is_canonical_content method in {file_path}")
    print("The updated method now properly detects Canon vs. Legends based on multiple markers.")

def test_detection_logic():
    """Test the detection logic on some sample content."""
    test_cases = [
        {
            "description": "Explicit Canon template",
            "text": "{{Canon}}\nThis is a Canon article.",
            "expected": True
        },
        {
            "description": "Explicit Legends template",
            "text": "{{Legends}}\nThis is a Legends article.",
            "expected": False
        },
        {
            "description": "Top|leg marker",
            "text": "{{Top|leg}}\nThis is a Legends article.",
            "expected": False
        },
        {
            "description": "Top|can marker",
            "text": "{{Top|can}}\nThis is a Canon article.",
            "expected": True
        },
        {
            "description": "No markers (default to Canon)",
            "text": "This is an article with no markers.",
            "expected": True
        }
    ]
    
    def is_canonical_content(text):
        """Simplified version of the detection logic for testing."""
        # Look for explicit Canon template or category
        if re.search(r'\{\{Canon\}\}|\{\{Canon article\}\}|\[\[Category:Canon articles\]\]', text, re.IGNORECASE):
            return True
            
        # Look for explicit Legends template or category
        if re.search(r'\{\{Legends\}\}|\{\{Legends article\}\}|\[\[Category:Legends articles\]\]', text, re.IGNORECASE):
            return False
            
        # If it has {{Top|leg}} marker, it's Legends
        if re.search(r'\{\{Top\|leg\}\}', text, re.IGNORECASE):
            return False
            
        # If it has {{Top|can}} marker, it's Canon
        if re.search(r'\{\{Top\|can\}\}', text, re.IGNORECASE):
            return True
            
        # By default, treat articles as Canon
        return True
    
    print("\nTesting detection logic:")
    for i, case in enumerate(test_cases, 1):
        result = is_canonical_content(case["text"])
        status = "PASS" if result == case["expected"] else "FAIL"
        print(f"{i}. {case['description']}: {status} (got {result}, expected {case['expected']})")

if __name__ == "__main__":
    print("This script will fix the Canon/Legends detection in the wiki dump processor.")
    print("It will create a backup of the original file before making changes.")
    
    # Ask for confirmation
    confirm = input("Do you want to proceed? (y/n): ").lower()
    if confirm == 'y':
        fix_wiki_dump_processor()
        test_detection_logic()
    else:
        print("Operation canceled.")
        # Still run the tests even if canceled
        test_detection_logic() 