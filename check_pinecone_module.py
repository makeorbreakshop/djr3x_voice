#!/usr/bin/env python3
import os
import sys

print("Checking Python version:")
print(sys.version)

print("\nTrying to import pinecone:")
try:
    import pinecone
    print("Successfully imported pinecone")
    print(f"Pinecone version: {pinecone.__version__ if hasattr(pinecone, '__version__') else 'unknown'}")
    
    print("\nPinecone module directory:")
    print(pinecone.__file__)
    
    print("\nPinecone module contents:")
    for item in dir(pinecone):
        if not item.startswith('__'):
            print(f"- {item}")
            
    # Try to access some common methods/classes
    print("\nChecking for common classes/methods:")
    for name in ["init", "list_indexes", "create_index", "Index", "delete_index"]:
        exists = hasattr(pinecone, name)
        print(f"pinecone.{name}: {'✓ exists' if exists else '✗ missing'}")
except Exception as e:
    print(f"Error importing pinecone: {e}") 