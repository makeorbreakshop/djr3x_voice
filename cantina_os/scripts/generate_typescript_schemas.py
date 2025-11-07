#!/usr/bin/env python3
"""
TypeScript Schema Generator for DJ R3X CantinaOS

Generates TypeScript interfaces from Python Pydantic models to ensure
type safety across the web dashboard and backend API boundaries.

This script:
1. Parses Pydantic models from schemas/web_commands.py
2. Converts Python types to TypeScript equivalents  
3. Handles enums, unions, optionals, and complex nested structures
4. Generates clean TypeScript interfaces with JSDoc documentation
5. Creates response and error types for complete API type safety

Usage:
    python scripts/generate_typescript_schemas.py [--output path] [--watch]
    
Output:
    Generates TypeScript definitions in ../dj-r3x-dashboard/src/types/schemas.ts
"""

import ast
import argparse
import inspect
import json
import os
import re
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type, Union, get_type_hints
from dataclasses import dataclass

# Add cantina_os to path for imports
SCRIPT_DIR = Path(__file__).parent
CANTINA_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CANTINA_ROOT))

try:
    from pydantic import BaseModel
    from pydantic.fields import FieldInfo
    
    # Import our schema modules
    from cantina_os.schemas import BaseWebCommand, BaseWebResponse, WebCommandError
    from cantina_os.schemas.web_commands import (
        VoiceCommandSchema, MusicCommandSchema, DJCommandSchema, SystemCommandSchema,
        VoiceActionEnum, MusicActionEnum, DJActionEnum, SystemActionEnum, SystemModeEnum
    )
except ImportError as e:
    print(f"Error importing CantinaOS modules: {e}")
    print("Make sure you're running from the cantina_os directory with dependencies installed")
    sys.exit(1)


@dataclass
class TypeScriptField:
    """Represents a TypeScript interface field."""
    name: str
    typescript_type: str
    optional: bool
    description: Optional[str] = None
    default_value: Optional[str] = None


@dataclass 
class TypeScriptEnum:
    """Represents a TypeScript enum."""
    name: str
    values: Dict[str, str]
    description: Optional[str] = None


@dataclass
class TypeScriptInterface:
    """Represents a TypeScript interface."""
    name: str
    fields: List[TypeScriptField]
    description: Optional[str] = None
    extends: Optional[str] = None


class TypeScriptGenerator:
    """Generates TypeScript interfaces from Pydantic models."""
    
    def __init__(self):
        self.generated_interfaces: Dict[str, TypeScriptInterface] = {}
        self.generated_enums: Dict[str, TypeScriptEnum] = {}
        self.type_mapping = {
            'str': 'string',
            'int': 'number', 
            'float': 'number',
            'bool': 'boolean',
            'datetime': 'string',  # ISO string format
            'UUID': 'string',
            'Any': 'any',
            'Dict[str, Any]': 'Record<string, any>',
            'List[str]': 'string[]',
        }
    
    def python_type_to_typescript(self, python_type: Any, is_optional: bool = False) -> str:
        """Convert Python type annotation to TypeScript type."""
        
        # Handle None type
        if python_type is type(None):
            return 'null'
            
        # Handle Union types (including Optional)
        if hasattr(python_type, '__origin__') and python_type.__origin__ is Union:
            union_args = python_type.__args__
            
            # Check if this is Optional[T] (Union[T, NoneType])
            if len(union_args) == 2 and type(None) in union_args:
                non_none_type = next(arg for arg in union_args if arg is not type(None))
                return self.python_type_to_typescript(non_none_type, is_optional=True)
            
            # Handle other Union types
            ts_types = []
            for arg in union_args:
                if arg is not type(None):
                    ts_types.append(self.python_type_to_typescript(arg))
            return ' | '.join(ts_types)
        
        # Handle List types
        if hasattr(python_type, '__origin__') and python_type.__origin__ is list:
            if python_type.__args__:
                item_type = self.python_type_to_typescript(python_type.__args__[0])
                return f'{item_type}[]'
            return 'any[]'
        
        # Handle Dict types
        if hasattr(python_type, '__origin__') and python_type.__origin__ is dict:
            if len(python_type.__args__) == 2:
                key_type = self.python_type_to_typescript(python_type.__args__[0])
                value_type = self.python_type_to_typescript(python_type.__args__[1])
                return f'Record<{key_type}, {value_type}>'
            return 'Record<string, any>'
        
        # Handle Enum types
        if inspect.isclass(python_type) and issubclass(python_type, Enum):
            enum_name = python_type.__name__
            self.extract_enum(python_type)
            return enum_name
        
        # Handle string type name
        type_name = getattr(python_type, '__name__', str(python_type))
        
        # Remove module path if present
        if '.' in type_name:
            type_name = type_name.split('.')[-1]
        
        # Direct mapping
        if type_name in self.type_mapping:
            return self.type_mapping[type_name]
        
        # Handle common special cases
        if type_name == 'datetime':
            return 'string'
        elif type_name == 'UUID':
            return 'string'
        elif 'Model' in type_name or 'Schema' in type_name:
            # Reference to another Pydantic model - use the class name
            return type_name
        
        # Default fallback
        return 'any'
    
    def extract_enum(self, enum_class: Type[Enum]) -> TypeScriptEnum:
        """Extract TypeScript enum from Python Enum."""
        if enum_class.__name__ in self.generated_enums:
            return self.generated_enums[enum_class.__name__]
        
        values = {}
        for item in enum_class:
            # Use the enum value, ensuring it's properly quoted if it's a string
            if isinstance(item.value, str):
                values[item.name] = f'"{item.value}"'
            else:
                values[item.name] = str(item.value)
        
        ts_enum = TypeScriptEnum(
            name=enum_class.__name__,
            values=values,
            description=enum_class.__doc__
        )
        
        self.generated_enums[enum_class.__name__] = ts_enum
        return ts_enum
    
    def extract_pydantic_model(self, model_class: Type[BaseModel]) -> TypeScriptInterface:
        """Extract TypeScript interface from Pydantic model."""
        if model_class.__name__ in self.generated_interfaces:
            return self.generated_interfaces[model_class.__name__]
        
        fields = []
        
        # Get type hints for the model
        try:
            type_hints = get_type_hints(model_class)
        except (NameError, AttributeError) as e:
            print(f"Warning: Could not get type hints for {model_class.__name__}: {e}")
            type_hints = {}
        
        # Handle Pydantic v2 vs v1 compatibility
        model_fields = getattr(model_class, 'model_fields', None) or getattr(model_class, '__fields__', {})
        
        # Process each field
        for field_name, field_info in model_fields.items():
            # Determine if field is optional (Pydantic v2 compatibility)
            is_optional = False
            field_type = None
            description = None
            default_value = None
            
            # Handle Pydantic v2 FieldInfo
            if hasattr(field_info, 'is_required'):
                is_optional = not field_info.is_required()
            elif hasattr(field_info, 'annotation'):
                # Pydantic v2 style
                field_type = field_info.annotation
                is_optional = field_info.default is not ...
            else:
                # Pydantic v1 style fallback
                is_optional = not getattr(field_info, 'required', True)
            
            # Get field type
            if field_type is None:
                field_type = type_hints.get(field_name)
                if field_type is None:
                    # Try to get from field_info
                    if hasattr(field_info, 'annotation'):
                        field_type = field_info.annotation
                    elif hasattr(field_info, 'type_'):
                        field_type = field_info.type_
                    else:
                        field_type = Any
            
            # Convert to TypeScript type
            ts_type = self.python_type_to_typescript(field_type, is_optional)
            
            # Get field description (Pydantic v2 compatibility)
            if hasattr(field_info, 'description') and field_info.description:
                description = field_info.description
            elif hasattr(field_info, 'field_info') and field_info.field_info:
                description = getattr(field_info.field_info, 'description', None)
            
            # Get default value if present
            if hasattr(field_info, 'default') and field_info.default is not None and field_info.default != ...:
                if isinstance(field_info.default, str):
                    default_value = f'"{field_info.default}"'
                elif isinstance(field_info.default, bool):
                    default_value = 'true' if field_info.default else 'false'
                else:
                    default_value = str(field_info.default)
            
            fields.append(TypeScriptField(
                name=field_name,
                typescript_type=ts_type,
                optional=is_optional,
                description=description,
                default_value=default_value
            ))
        
        # Determine extends
        extends = None
        for base in model_class.__bases__:
            if base != BaseModel and issubclass(base, BaseModel):
                extends = base.__name__
                # Make sure to process the base class too
                self.extract_pydantic_model(base)
                break
        
        ts_interface = TypeScriptInterface(
            name=model_class.__name__,
            fields=fields,
            description=model_class.__doc__,
            extends=extends
        )
        
        self.generated_interfaces[model_class.__name__] = ts_interface
        return ts_interface
    
    def generate_typescript_enum(self, enum_def: TypeScriptEnum) -> str:
        """Generate TypeScript enum code."""
        lines = []
        
        # Add JSDoc comment
        if enum_def.description:
            lines.append('/**')
            for line in enum_def.description.strip().split('\n'):
                lines.append(f' * {line.strip()}')
            lines.append(' */')
        
        # Add enum declaration
        lines.append(f'export enum {enum_def.name} {{')
        
        # Add enum values
        for i, (key, value) in enumerate(enum_def.values.items()):
            comma = ',' if i < len(enum_def.values) - 1 else ''
            lines.append(f'  {key} = {value}{comma}')
        
        lines.append('}')
        lines.append('')
        
        return '\n'.join(lines)
    
    def generate_typescript_interface(self, interface_def: TypeScriptInterface) -> str:
        """Generate TypeScript interface code."""
        lines = []
        
        # Add JSDoc comment
        if interface_def.description:
            lines.append('/**')
            for line in interface_def.description.strip().split('\n'):
                lines.append(f' * {line.strip()}')
            lines.append(' */')
        
        # Add interface declaration
        interface_line = f'export interface {interface_def.name}'
        if interface_def.extends:
            interface_line += f' extends {interface_def.extends}'
        interface_line += ' {'
        lines.append(interface_line)
        
        # Add fields
        for field in interface_def.fields:
            field_lines = []
            
            # Add field JSDoc if description exists
            if field.description:
                field_lines.append('  /**')
                for line in field.description.strip().split('\n'):
                    field_lines.append(f'   * {line.strip()}')
                if field.default_value:
                    field_lines.append(f'   * @default {field.default_value}')
                field_lines.append('   */')
            
            # Add field declaration
            optional_marker = '?' if field.optional else ''
            field_lines.append(f'  {field.name}{optional_marker}: {field.typescript_type};')
            
            lines.extend(field_lines)
        
        lines.append('}')
        lines.append('')
        
        return '\n'.join(lines)
    
    def generate_response_types(self) -> str:
        """Generate response and error types for API communication."""
        return '''/**
 * Standard response wrapper for all web commands
 */
export interface WebCommandResponse<T = any> {
  /** Whether the command was successful */
  success: boolean;
  /** Human-readable response message */
  message: string;
  /** Response creation timestamp (ISO string) */
  timestamp: string;
  /** Original command ID if available */
  command_id?: string;
  /** Response data if successful */
  data?: T;
  /** Error code for failed commands */
  error_code?: string;
}

/**
 * Error response for failed web commands
 */
export interface WebCommandError {
  /** Always true for error responses */
  error: boolean;
  /** Primary error message */
  message: string;
  /** Command that failed */
  command?: string;
  /** List of specific validation errors */
  validation_errors: string[];
  /** Error timestamp (ISO string) */
  timestamp: string;
}

/**
 * Socket.io event payload wrapper
 */
export interface SocketEventPayload<T = any> {
  /** Event type/topic */
  type: string;
  /** Event payload data */
  data: T;
  /** Source of the event */
  source?: string;
  /** Event timestamp */
  timestamp?: string;
}

'''
    
    def generate_full_typescript_file(self) -> str:
        """Generate complete TypeScript file with all types."""
        lines = [
            '/**',
            ' * Generated TypeScript schemas for DJ R3X CantinaOS Web Commands',
            ' * ',
            ' * This file is auto-generated from Python Pydantic models.',
            ' * DO NOT EDIT MANUALLY - changes will be overwritten.',
            ' * ',
            f' * Generated on: {datetime.now().isoformat()}',
            ' * Source: cantina_os/schemas/web_commands.py',
            ' */',
            '',
            '// ============================================================================',
            '// ENUMS',
            '// ============================================================================',
            ''
        ]
        
        # Add all enums
        for enum_def in self.generated_enums.values():
            lines.append(self.generate_typescript_enum(enum_def))
        
        lines.extend([
            '// ============================================================================',
            '// INTERFACES', 
            '// ============================================================================',
            ''
        ])
        
        # Add all interfaces
        for interface_def in self.generated_interfaces.values():
            lines.append(self.generate_typescript_interface(interface_def))
        
        lines.extend([
            '// ============================================================================',
            '// RESPONSE TYPES',
            '// ============================================================================',
            ''
        ])
        
        # Add response types
        lines.append(self.generate_response_types())
        
        lines.extend([
            '// ============================================================================',
            '// TYPE EXPORTS',
            '// ============================================================================',
            ''
        ])
        
        # Note: Types are already exported with their declarations
        # No need for additional export type block since we use 'export enum' and 'export interface'
        
        return '\n'.join(lines)
    
    def process_schema_modules(self):
        """Process all schema modules and extract types."""
        print("Processing CantinaOS schema modules...")
        
        # Core schemas
        schemas_to_process = [
            BaseWebCommand,
            BaseWebResponse,
            VoiceCommandSchema,
            MusicCommandSchema, 
            DJCommandSchema,
            SystemCommandSchema
        ]
        
        # Process all schemas
        for schema_class in schemas_to_process:
            print(f"  Processing {schema_class.__name__}...")
            self.extract_pydantic_model(schema_class)
        
        # Process all enums
        enums_to_process = [
            VoiceActionEnum,
            MusicActionEnum,
            DJActionEnum,
            SystemActionEnum,
            SystemModeEnum
        ]
        
        for enum_class in enums_to_process:
            print(f"  Processing {enum_class.__name__}...")
            self.extract_enum(enum_class)
        
        print(f"Generated {len(self.generated_interfaces)} interfaces and {len(self.generated_enums)} enums")


def main():
    """Main script entry point."""
    parser = argparse.ArgumentParser(description='Generate TypeScript schemas from Pydantic models')
    parser.add_argument('--output', '-o', type=str, 
                       help='Output file path (default: ../dj-r3x-dashboard/src/types/schemas.ts)')
    parser.add_argument('--watch', action='store_true',
                       help='Watch for changes and regenerate (not implemented)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose output')
    
    args = parser.parse_args()
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        # Default to dashboard types directory
        dashboard_root = CANTINA_ROOT.parent / 'dj-r3x-dashboard'
        output_path = dashboard_root / 'src' / 'types' / 'schemas.ts'
    
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"DJ R3X TypeScript Schema Generator")
    print(f"Output: {output_path}")
    print()
    
    # Generate schemas
    generator = TypeScriptGenerator()
    generator.process_schema_modules()
    
    # Generate TypeScript file
    typescript_content = generator.generate_full_typescript_file()
    
    # Write to file
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(typescript_content)
        
        print(f"✅ Successfully generated TypeScript schemas: {output_path}")
        print(f"   - {len(generator.generated_interfaces)} interfaces")
        print(f"   - {len(generator.generated_enums)} enums") 
        print(f"   - {len(typescript_content.splitlines())} lines of TypeScript")
        
    except Exception as e:
        print(f"❌ Error writing TypeScript file: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())