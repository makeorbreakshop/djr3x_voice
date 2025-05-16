import json
import re
import datetime

# Read the current corrupted checkpoint file
with open('e5_migration_checkpoint.json', 'r') as f:
    content = f.read()

# Extract all IDs (quoted strings) from the file
pattern = r'"([^"]+)"'
all_ids = re.findall(pattern, content)

# Create properly formatted checkpoint JSON
checkpoint = {
    'processed_ids': all_ids,
    'timestamp': datetime.datetime.now().isoformat()
}

# Save the fixed checkpoint file
with open('e5_migration_checkpoint.json.fixed', 'w') as f:
    json.dump(checkpoint, f)

print(f"Fixed checkpoint file created with {len(all_ids)} IDs") 