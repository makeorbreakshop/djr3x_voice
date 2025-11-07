#!/usr/bin/env python3
"""
TypeScript Interface Generator for CantinaOS Web Dashboard

Generates TypeScript interfaces from Pydantic models to ensure type consistency 
between Python backend and TypeScript frontend.

Usage:
    python scripts/generate_typescript_interfaces.py
    
Output:
    - Generates interfaces to dj-r3x-dashboard/src/types/cantina-payloads.ts
    - Overwrites existing file with updated interfaces
"""

import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, get_type_hints, get_origin, get_args
from datetime import datetime

# Add cantina_os to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cantina_os.core.event_payloads import (
    WebMusicStatusPayload,
    WebVoiceStatusPayload, 
    WebSystemStatusPayload,
    WebDJStatusPayload,
    WebServiceStatusPayload,
    WebProgressPayload,
    ServiceStatus,
    LogLevel
)

from cantina_os.schemas.web_commands import (
    VoiceCommandSchema,
    MusicCommandSchema,
    DJCommandSchema,
    SystemCommandSchema
)


def python_type_to_typescript(py_type: Any) -> str:
    """Convert Python type annotations to TypeScript types."""
    
    # Handle None/Optional types
    if py_type is type(None):
        return "null"
    
    # Handle basic types
    if py_type is str:
        return "string"
    elif py_type is int or py_type is float:
        return "number"
    elif py_type is bool:
        return "boolean"
    elif py_type is dict or py_type is Dict:
        return "Record<string, any>"
    elif py_type is list or py_type is List:
        return "any[]"
    
    # Handle generic types (Union, Optional, etc.)
    origin = get_origin(py_type)
    args = get_args(py_type)
    
    if origin is dict or origin is Dict:
        if len(args) == 2:
            key_type = python_type_to_typescript(args[0])
            value_type = python_type_to_typescript(args[1])
            return f"Record<{key_type}, {value_type}>"
        return "Record<string, any>"
    
    elif origin is list or origin is List:
        if args:
            item_type = python_type_to_typescript(args[0])
            return f"{item_type}[]"
        return "any[]"
    
    elif origin is type(Union):  # Handle Union types (Optional)
        non_none_types = [arg for arg in args if arg is not type(None)]
        if len(non_none_types) == 1:
            # This is Optional[T] -> T | null
            base_type = python_type_to_typescript(non_none_types[0])
            return f"{base_type} | null"
        else:
            # Multiple union types
            union_types = [python_type_to_typescript(arg) for arg in args]
            return " | ".join(union_types)
    
    # Handle Literal types
    if hasattr(py_type, '__origin__') and str(py_type.__origin__) == 'typing.Literal':
        literal_values = [f'"{val}"' if isinstance(val, str) else str(val) for val in py_type.__args__]
        return " | ".join(literal_values)
    
    # Handle enums
    if hasattr(py_type, '__members__'):
        enum_values = [f'"{val.value}"' for val in py_type.__members__.values()]
        return " | ".join(enum_values)
    
    # Fallback to string representation
    type_str = str(py_type)
    
    # Handle typing.Literal manually if above didn't catch it
    if 'Literal[' in type_str:
        # Extract literal values from string representation
        start = type_str.find('Literal[') + 8
        end = type_str.rfind(']')
        literal_content = type_str[start:end]
        
        # Split by comma and clean up
        values = [v.strip().strip("'\"") for v in literal_content.split(',')]
        return " | ".join(f'"{val}"' for val in values if val)
    
    # Default fallback
    return "any"


def generate_interface_from_model(model_class, interface_name: str) -> str:
    """Generate TypeScript interface from Pydantic model."""
    
    # Get field information from Pydantic model (V2 compatible)
    if hasattr(model_class, 'model_fields'):
        fields = model_class.model_fields
    elif hasattr(model_class, '__fields__'):
        fields = model_class.__fields__
    else:
        # Fallback - try to get type hints
        fields = get_type_hints(model_class)
    
    interface_lines = [f"export interface {interface_name} {{"]
    
    for field_name, field_info in fields.items():
        # Get type annotation
        if hasattr(field_info, 'annotation'):
            field_type = field_info.annotation
        elif hasattr(field_info, 'type_'):
            field_type = field_info.type_
        else:
            field_type = field_info
        
        # Convert to TypeScript type
        ts_type = python_type_to_typescript(field_type)
        
        # Check if field is optional
        is_optional = False
        if hasattr(field_info, 'default') and field_info.default is not None:
            is_optional = True
        elif hasattr(field_info, 'is_required') and not field_info.is_required():
            is_optional = True
        elif 'Optional' in str(field_type) or 'null' in ts_type:
            is_optional = True
        
        # Generate field line
        optional_marker = "?" if is_optional else ""
        interface_lines.append(f"  {field_name}{optional_marker}: {ts_type};")
    
    interface_lines.append("}")
    
    return "\n".join(interface_lines)


def generate_all_interfaces() -> str:
    """Generate all TypeScript interfaces for web dashboard payloads."""
    
    header = f"""/**
 * Auto-generated TypeScript interfaces for CantinaOS Web Dashboard
 * 
 * Generated on: {datetime.now().isoformat()}
 * Source: cantina_os/core/event_payloads.py and cantina_os/schemas/web_commands.py
 * 
 * DO NOT EDIT MANUALLY - This file is automatically generated
 * Run: python scripts/generate_typescript_interfaces.py
 */

"""
    
    interfaces = []
    
    # Status payload interfaces (outbound to frontend)
    status_models = [
        (WebMusicStatusPayload, "WebMusicStatusPayload"),
        (WebVoiceStatusPayload, "WebVoiceStatusPayload"), 
        (WebSystemStatusPayload, "WebSystemStatusPayload"),
        (WebDJStatusPayload, "WebDJStatusPayload"),
        (WebServiceStatusPayload, "WebServiceStatusPayload"),
        (WebProgressPayload, "WebProgressPayload"),
    ]
    
    # Command interfaces (inbound from frontend)  
    command_models = [
        (VoiceCommandSchema, "VoiceCommand"),
        (MusicCommandSchema, "MusicCommand"),
        (DJCommandSchema, "DJCommand"),
        (SystemCommandSchema, "SystemCommand"),
    ]
    
    interfaces.append("// Status Payload Interfaces (outbound to frontend)")
    for model_class, interface_name in status_models:
        try:
            interface = generate_interface_from_model(model_class, interface_name)
            interfaces.append(interface)
            interfaces.append("")
        except Exception as e:
            print(f"Warning: Could not generate interface for {interface_name}: {e}")
    
    interfaces.append("// Command Interfaces (inbound from frontend)")
    for model_class, interface_name in command_models:
        try:
            interface = generate_interface_from_model(model_class, interface_name)
            interfaces.append(interface)
            interfaces.append("")
        except Exception as e:
            print(f"Warning: Could not generate interface for {interface_name}: {e}")
    
    # Add enum interfaces
    interfaces.append("// Enum Types")
    interfaces.append(f"export type ServiceStatus = {python_type_to_typescript(ServiceStatus)};")
    interfaces.append(f"export type LogLevel = {python_type_to_typescript(LogLevel)};")
    interfaces.append("")
    
    # Add utility types
    interfaces.append("""// Utility Types
export interface CantinaEvent<T = any> {
  topic: string;
  data: T;
  timestamp: string;
}

export interface WebSocketResponse {
  success: boolean;
  message?: string;
  error?: string;
  data?: any;
}""")
    
    return header + "\n".join(interfaces)


def main():
    """Generate TypeScript interfaces and write to dashboard directory."""
    
    print("🔄 Generating TypeScript interfaces from Pydantic models...")
    
    try:
        # Generate interfaces
        typescript_content = generate_all_interfaces()
        
        # Determine output path
        script_dir = Path(__file__).parent
        project_root = script_dir.parent.parent
        output_path = project_root / "dj-r3x-dashboard" / "src" / "types" / "cantina-payloads.ts"
        
        # Create directory if it doesn't exist
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write the file
        with open(output_path, 'w') as f:
            f.write(typescript_content)
        
        print(f"✅ TypeScript interfaces generated successfully!")
        print(f"📁 Output: {output_path}")
        print(f"📊 Generated {typescript_content.count('export interface')} interfaces")
        
        # Show preview
        lines = typescript_content.split('\n')
        preview_lines = lines[:30]  # First 30 lines
        print("\n📋 Preview:")
        print('\n'.join(preview_lines))
        
        if len(lines) > 30:
            print(f"... ({len(lines) - 30} more lines)")
            
    except Exception as e:
        print(f"❌ Error generating TypeScript interfaces: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()