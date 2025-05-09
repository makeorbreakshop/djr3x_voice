#!/usr/bin/env python3
"""
Test script to diagnose Deepgram SDK import structure
"""

import os
import sys
import inspect
import pkgutil

# Try basic imports
try:
    import deepgram
    print(f"Deepgram SDK version: {deepgram.__version__ if hasattr(deepgram, '__version__') else 'Unknown'}")
    print(f"Deepgram package location: {deepgram.__file__}")
    
    # Try importing DeepgramClient
    from deepgram import DeepgramClient
    print("✅ Successfully imported DeepgramClient")
    
    # Explore the structure of the deepgram module
    print("\n📦 Deepgram Package Structure:")
    def explore_package(package, indent=0):
        prefix = "  " * indent
        for finder, name, is_pkg in pkgutil.iter_modules(package.__path__, package.__name__ + '.'):
            print(f"{prefix}└─ {name.split('.')[-1]}" + (" (package)" if is_pkg else ""))
            if is_pkg:
                try:
                    subpackage = __import__(name, fromlist=[""])
                    explore_package(subpackage, indent + 1)
                except ImportError as e:
                    print(f"{prefix}  └─ Error importing: {e}")
    
    explore_package(deepgram)
    
    # Check for LiveTranscriptionEvents
    print("\n🔍 Searching for LiveTranscriptionEvents...")
    
    # Check some potential locations based on SDK documentation
    potential_paths = [
        "deepgram.clients.listen.v1",
        "deepgram.clients.listen",
        "deepgram.listen.websocket",
        "deepgram.listen.websocket.v1",
        "deepgram.listen",
        "deepgram.transcription",
        "deepgram.transcription.live",
        "deepgram.enums",
        "deepgram.events"
    ]
    
    for path in potential_paths:
        try:
            module = __import__(path, fromlist=[""])
            if hasattr(module, "LiveTranscriptionEvents"):
                print(f"✅ Found LiveTranscriptionEvents in {path}")
                print(f"  Usage: from {path} import LiveTranscriptionEvents")
            else:
                # Look at all objects in the module
                print(f"❌ No LiveTranscriptionEvents in {path}")
                print(f"  Available objects in {path}:")
                for name in dir(module):
                    if not name.startswith("_"):  # Skip private/internal objects
                        obj = getattr(module, name)
                        if inspect.isclass(obj) or inspect.ismodule(obj):
                            print(f"    - {name} ({type(obj).__name__})")
        except ImportError as e:
            print(f"❌ Could not import {path}: {e}")
    
    # Try to find any class that might have "Event" in its name
    print("\n🔍 Searching for any Event-related classes...")
    for path in potential_paths:
        try:
            module = __import__(path, fromlist=[""])
            for name in dir(module):
                if "event" in name.lower() and not name.startswith("_"):
                    obj = getattr(module, name)
                    print(f"  - Found: {path}.{name} ({type(obj).__name__})")
        except ImportError:
            pass

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc() 