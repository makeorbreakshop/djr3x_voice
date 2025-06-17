/**
 * Generated TypeScript schemas for DJ R3X CantinaOS Web Commands
 * 
 * This file is auto-generated from Python Pydantic models.
 * DO NOT EDIT MANUALLY - changes will be overwritten.
 * 
 * Generated on: 2025-06-17T15:27:21.872077
 * Source: cantina_os/schemas/web_commands.py
 */

// ============================================================================
// ENUMS
// ============================================================================

/**
 * Valid actions for voice commands.
 */
export enum VoiceActionEnum {
  START = "start",
  STOP = "stop"
}

/**
 * Valid actions for music commands.
 */
export enum MusicActionEnum {
  PLAY = "play",
  PAUSE = "pause",
  RESUME = "resume",
  STOP = "stop",
  NEXT = "next",
  QUEUE = "queue",
  VOLUME = "volume"
}

/**
 * Valid actions for DJ mode commands.
 */
export enum DJActionEnum {
  START = "start",
  STOP = "stop",
  NEXT = "next",
  UPDATE_SETTINGS = "update_settings"
}

/**
 * Valid actions for system commands.
 */
export enum SystemActionEnum {
  SET_MODE = "set_mode",
  RESTART = "restart",
  REFRESH_CONFIG = "refresh_config"
}

/**
 * Valid system modes for CantinaOS.
 */
export enum SystemModeEnum {
  IDLE = "IDLE",
  AMBIENT = "AMBIENT",
  INTERACTIVE = "INTERACTIVE"
}

// ============================================================================
// INTERFACES
// ============================================================================

/**
 * Base class for all web dashboard socket.io commands.
 * 
 * Provides standardized validation patterns, command metadata,
 * and integration with CantinaOS event bus topology.
 * 
 * All web commands must inherit from this class to ensure
 * consistent validation and event bus integration.
 */
export interface BaseWebCommand {
  /**
   * Specific action to perform within command type
   * @default PydanticUndefined
   */
  action: string;
  /**
   * Source of command for audit trail
   * @default "web_dashboard"
   */
  source?: string;
  /**
   * Command creation timestamp
   * @default PydanticUndefined
   */
  timestamp?: string;
  /**
   * Unique command identifier
   * @default PydanticUndefined
   */
  command_id?: string;
}

/**
 * Base class for all web dashboard socket.io responses.
 * 
 * Provides standardized response format with success/error handling,
 * timestamp tracking, and integration with ServiceStatusPayload patterns.
 */
export interface BaseWebResponse {
  /**
   * Whether the command was successful
   * @default PydanticUndefined
   */
  success: boolean;
  /**
   * Human-readable response message
   * @default PydanticUndefined
   */
  message: string;
  /**
   * Response creation timestamp
   * @default PydanticUndefined
   */
  timestamp?: string;
  /**
   * Original command ID if available
   */
  command_id?: string;
  /**
   * Additional response data
   */
  data?: Record<string, any>;
  /**
   * Error code for failed commands
   */
  error_code?: string;
}

/**
 * Schema for voice control commands from web dashboard.
 * 
 * Handles voice recording start/stop commands and translates them
 * to proper CantinaOS system mode transitions via YodaModeManagerService.
 * 
 * Examples:
 * {"action": "start"} -> SYSTEM_SET_MODE_REQUEST (INTERACTIVE)
 * {"action": "stop"} -> SYSTEM_SET_MODE_REQUEST (AMBIENT)
 */
export interface VoiceCommandSchema extends BaseWebCommand {
  /**
   * Voice action to perform
   * @default PydanticUndefined
   */
  action: VoiceActionEnum;
  /**
   * Source of command for audit trail
   * @default "web_dashboard"
   */
  source?: string;
  /**
   * Command creation timestamp
   * @default PydanticUndefined
   */
  timestamp?: string;
  /**
   * Unique command identifier
   * @default PydanticUndefined
   */
  command_id?: string;
}

/**
 * Schema for music control commands from web dashboard.
 * 
 * Handles all music playback controls and translates them to proper
 * CantinaOS MUSIC_COMMAND events for MusicControllerService processing.
 * 
 * Supports both track selection and queue management operations.
 */
export interface MusicCommandSchema extends BaseWebCommand {
  /**
   * Music action to perform
   * @default PydanticUndefined
   */
  action: MusicActionEnum;
  /**
   * Source of command for audit trail
   * @default "web_dashboard"
   */
  source?: string;
  /**
   * Command creation timestamp
   * @default PydanticUndefined
   */
  timestamp?: string;
  /**
   * Unique command identifier
   * @default PydanticUndefined
   */
  command_id?: string;
  /**
   * Track name or search query for play/queue actions
   */
  track_name?: string;
  /**
   * Track ID for play/queue actions
   */
  track_id?: string;
  /**
   * Volume level (0.0-1.0) for volume action
   */
  volume_level?: number;
}

/**
 * Schema for DJ mode commands from web dashboard.
 * 
 * Handles DJ mode lifecycle and configuration commands, translating them
 * to appropriate CantinaOS events for BrainService and timeline coordination.
 */
export interface DJCommandSchema extends BaseWebCommand {
  /**
   * DJ mode action to perform
   * @default PydanticUndefined
   */
  action: DJActionEnum;
  /**
   * Source of command for audit trail
   * @default "web_dashboard"
   */
  source?: string;
  /**
   * Command creation timestamp
   * @default PydanticUndefined
   */
  timestamp?: string;
  /**
   * Unique command identifier
   * @default PydanticUndefined
   */
  command_id?: string;
  /**
   * Enable automatic track transitions
   * @default true
   */
  auto_transition?: boolean;
  /**
   * Crossfade duration in seconds
   * @default 5.0
   */
  transition_duration?: number;
  /**
   * Preferred music genre for DJ selection
   */
  genre_preference?: string;
}

/**
 * Schema for system control commands from web dashboard.
 * 
 * Handles system-level operations like mode changes, restarts, and configuration
 * updates, ensuring proper integration with CantinaOS system management.
 */
export interface SystemCommandSchema extends BaseWebCommand {
  /**
   * System action to perform
   * @default PydanticUndefined
   */
  action: SystemActionEnum;
  /**
   * Source of command for audit trail
   * @default "web_dashboard"
   */
  source?: string;
  /**
   * Command creation timestamp
   * @default PydanticUndefined
   */
  timestamp?: string;
  /**
   * Unique command identifier
   * @default PydanticUndefined
   */
  command_id?: string;
  /**
   * Target system mode for set_mode action
   */
  mode?: SystemModeEnum;
  /**
   * Delay before restart in seconds
   * @default 5.0
   */
  restart_delay?: number;
}

// ============================================================================
// RESPONSE TYPES
// ============================================================================

/**
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


// ============================================================================
// TYPE EXPORTS
// ============================================================================
