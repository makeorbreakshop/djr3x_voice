import os
import re

def rename_music_files():
    # Directory with the music files
    music_dir = 'audio/music'
    
    # Get all files in the directory
    files = os.listdir(music_dir)
    
    # Only process MP3 files
    mp3_files = [f for f in files if f.endswith('.mp3')]
    
    # Track renames for output
    renamed = []
    skipped = []
    
    for filename in mp3_files:
        # Skip already short filenames (less than 30 chars is probably already renamed)
        if len(filename) < 30:
            skipped.append(f"Skipped: {filename}")
            continue
        
        # Different patterns to extract song names
        new_name = None
        
        # Pattern 1: "Star Wars- Galaxy's Edge Oga's Cantina- R3X's Playlist #1 - 002 - Batuu Boogie (From..."
        match = re.search(r'#1 - \d+ - ([^(]+)', filename)
        if match:
            new_name = match.group(1).strip()
        
        # Pattern 2: For cases like "Cantina Song aka Mad About Mad About Me (From..."
        if not new_name and '(From' in filename:
            new_name = filename.split('(From')[0].strip()
            
        # If we couldn't extract a name with the patterns, skip this file
        if not new_name:
            skipped.append(f"Could not parse: {filename}")
            continue
            
        # Clean up the new name (remove artist if needed)
        if ' - ' in new_name and not new_name.startswith('Mus Kat'):
            # For songs with artist - title format, decide whether to keep artist
            # For now, let's keep the artist name for clarity
            pass
            
        # Add .mp3 extension
        new_name = f"{new_name}.mp3"
        
        # Rename the file
        old_path = os.path.join(music_dir, filename)
        new_path = os.path.join(music_dir, new_name)
        
        # Make sure we don't overwrite existing files
        if os.path.exists(new_path):
            skipped.append(f"Destination already exists: {new_name}")
            continue
            
        os.rename(old_path, new_path)
        renamed.append(f"Renamed: {filename} → {new_name}")
    
    # Print results
    print(f"Renamed {len(renamed)} files:")
    for item in renamed:
        print(f"  {item}")
        
    if skipped:
        print(f"\nSkipped {len(skipped)} files:")
        for item in skipped:
            print(f"  {item}")

if __name__ == "__main__":
    rename_music_files() 