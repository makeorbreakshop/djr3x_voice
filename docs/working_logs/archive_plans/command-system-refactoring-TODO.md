# Command System Refactoring Checklist

## Background
This document outlines the tasks needed to refactor the command handling system to address the fundamental architectural issues identified in the dev log. The goal is to create a clean, consistent command processing architecture that follows our standards and eliminates the current confusion around command routing and responsibility.

## Implementation Tasks - Completed

### 1. Update `CLIService` ✅
- [x] Modify `_process_command()` to ALWAYS emit to `CLI_COMMAND` topic
- [x] Remove direct routing to service-specific topics (e.g., `MUSIC_COMMAND`, `MODE_COMMAND`)
- [x] Ensure consistent `CliCommandPayload` creation with proper `raw_input` field
- [x] Remove command-specific parsing logic from CLIService
- [x] Update docstrings to reflect new responsibility (user I/O only)

### 2. Enhance `CommandDispatcherService` ✅
- [x] Make `_handle_command()` the single entry point for all command processing
- [x] Create standard parsing logic for primary commands and subcommands
- [x] Implement compound command processing (e.g., "play music 5")
- [x] Update command registration system to handle command hierarchies
- [x] Create utility methods for command validation and normalization
- [x] Use `StandardCommandPayload` for ALL outgoing messages
- [x] Implement handlers for service-specific topics (MUSIC_COMMAND, EYE_COMMAND)
- [x] Add consistent logging for all command types for debugging visibility
- [x] Fix issue where compound commands bypass dispatcher-level handling

### 3. Update Service Handlers ✅
- [x] Simplify `MusicControllerService._handle_music_command()` to only handle service logic
- [x] Remove command parsing from service handlers (rely on `CommandDispatcherService`)
- [x] Update handlers to consistently use `StandardCommandPayload`
- [x] Add validation for expected payload fields
- [x] Remove duplicate special-case handling logic

### 4. Standardize Payload Models ✅
- [x] Enhance `StandardCommandPayload` to fully support command hierarchies
- [x] Add validation and normalization methods to payload model
- [x] Implement proper serialization/deserialization
- [x] Add helper methods for common operations

### 5. Update Command Registration ✅
- [x] Consolidate command registration in main.py
- [x] Use a declarative approach for command registration
- [x] Support hierarchical commands (command, subcommand, subcommand...)
- [x] Register shortcuts and aliases consistently

### 6. Verify All Command Types ✅
- [x] Test mode commands (engage, ambient, disengage) 
- [x] Test eye commands (eye pattern, eye test, eye status)
- [x] Test music commands (play music, stop music, list music)
- [x] Test debug commands (debug level, debug trace)
- [x] Verify all command shortcuts and aliases
- [x] Verify consistent command flow and logging for ALL command types
- [x] Add detailed flow validation in logs for compound commands
- [x] Test all error scenarios and fallback mechanisms

### 7. Complete Command Handling System ✅
- [x] Add proper subscriptions for service command topics (MUSIC_COMMAND, etc.)
- [x] Implement consistent logging pattern for all command types
- [x] Ensure all commands (basic, compound, and special cases) follow the same flow
- [x] Create proper diagnostic logs for all command processing steps
- [x] Create a visualization of command flow for documentation
- [x] Update developers guide with the complete command system details

## Summary of Changes

The command system has been completely refactored to follow a clean, consistent architecture:

1. **Consistent Command Flow**: All commands now flow through a single path:
   - CLIService → CLI_COMMAND → CommandDispatcherService → Service-specific topics

2. **Clear Responsibility Separation**:
   - CLIService: Only handles user I/O and emits raw commands
   - CommandDispatcherService: Central point for command parsing, validation, and routing
   - Service handlers: Focus on business logic, not command parsing

3. **Standardized Command Payloads**:
   - Enhanced StandardCommandPayload with validation, arg parsing, and utility methods
   - Consistent payload structure across all services
   - Better support for compound commands and command hierarchies

4. **Simplified Command Registration**:
   - Declarative registration in CommandDispatcherService
   - Support for basic commands and compound commands
   - Consistent shortcut handling

5. **Improved Error Handling**:
   - Better validation of command arguments
   - Clearer error messages for users
   - More robust handling of edge cases
   
6. **Enhanced Logging and Monitoring**:
   - Consistent logging for all command types
   - Service-specific command monitoring
   - Improved troubleshooting capabilities
   - Better diagnostic information for command flow 