# DJ R3X Portable Command Setup

This guide helps you set up the `dj-r3x` command to work from any directory on any computer.

## 🚀 Quick Setup

1. **Clone/download the project** to any location on your computer
2. **Navigate to the project directory**:
   ```bash
   cd /path/to/djr3x_voice
   ```
3. **Run the setup script**:
   ```bash
   ./setup-dj-r3x-command.sh
   ```
4. **Test the setup**:
   ```bash
   ./test-dj-r3x-setup.sh
   ```

## 🎯 What the setup does

The setup script automatically:
- ✅ Detects your project location
- ✅ Finds your virtual environment (if it exists)
- ✅ Creates a portable launcher script
- ✅ Installs the command using the best method for your system

## 📍 Installation Methods

The setup script tries these methods in order:

### Method 1: System-wide installation
- Installs to `/usr/local/bin/dj-r3x`
- Requires `sudo` password
- Works for all users on the system
- Recommended for single-user systems

### Method 2: User-local installation
- Installs to `~/.local/bin/dj-r3x`
- No `sudo` required
- May need to add `~/.local/bin` to your PATH
- Good for shared systems

### Method 3: Shell alias
- Adds alias to your shell profile (`~/.bashrc` or `~/.zshrc`)
- No special permissions needed
- Requires terminal restart or `source ~/.zshrc`

## 🧪 Testing

After setup, test with:
```bash
# Test the setup
./test-dj-r3x-setup.sh

# Try the command
dj-r3x --help
```

## 🔧 Manual Setup (if automatic setup fails)

If the automatic setup doesn't work, you can manually add an alias:

### For Zsh (macOS default):
```bash
echo "alias dj-r3x='$(pwd)/launch-dj-r3x.sh'" >> ~/.zshrc
source ~/.zshrc
```

### For Bash:
```bash
echo "alias dj-r3x='$(pwd)/launch-dj-r3x.sh'" >> ~/.bashrc
source ~/.bashrc
```

## 🚚 Moving the Project

If you move the project to a different location:
1. Run the setup script again from the new location
2. The old command will be automatically updated

## 🗑️ Uninstalling

To remove the `dj-r3x` command:
```bash
# If installed system-wide:
sudo rm /usr/local/bin/dj-r3x

# If installed user-local:
rm ~/.local/bin/dj-r3x

# If using alias, remove from shell profile:
# Edit ~/.zshrc or ~/.bashrc and remove the dj-r3x alias line
```

## 🐛 Troubleshooting

### Command not found
- Make sure you ran the setup script: `./setup-dj-r3x-command.sh`
- If using `~/.local/bin`, add it to PATH: `export PATH="$HOME/.local/bin:$PATH"`
- If using alias method, restart terminal or `source ~/.zshrc`

### Permission denied
- Make sure the script is executable: `chmod +x setup-dj-r3x-command.sh`
- For system-wide install, you need `sudo` access

### Virtual environment not found
- Create a virtual environment in the project root: `python -m venv venv`
- Or the command will use system Python (which may still work)

### Project moved/deleted
- Re-run the setup script from the new location
- Or remove the old command and set up again

## 💡 How it works

The setup creates a portable launcher script that:
1. Auto-detects the project location (no hardcoded paths!)
2. Activates your virtual environment automatically
3. Launches the CantinaOS system
4. Works regardless of your current directory 