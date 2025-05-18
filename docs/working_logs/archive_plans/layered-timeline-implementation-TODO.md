# DJ R3X Layered Timeline Implementation Checklist

This checklist tracks the implementation of the three new services defined in the Layered Timeline PRD. Each service must adhere to our CantinaOS architecture standards and service template guidelines.

## Pre-Implementation Planning

- [x] Review latest version of `dj-r3x-dev-log.md` for context
- [x] Validate event diagram in PRD against existing `EventTopics` enum
- [x] Map all new events needed:
  - [x] `PLAN_READY`
  - [x] `PLAN_STARTED`
  - [x] `STEP_READY`
  - [x] `STEP_EXECUTED` 
  - [x] `PLAN_ENDED`
  - [x] `MEMORY_UPDATED`

## Initial Setup (All Services)

- [x] Create directories:
  - [x] `cantina_os/cantina_os/services/brain_service/`
  - [x] `cantina_os/cantina_os/services/timeline_executor_service/`
  - [x] `cantina_os/cantina_os/services/memory_service/`
- [x] Create `__init__.py` files in each directory
- [x] Update event_payloads.py with new models
- [x] Update `EventTopics` enum with new events

## BrainService Implementation

- [x] Copy `service_template.py` to `brain_service.py` (rename class)
- [x] Implement `_Config` Pydantic model
- [x] Set up internal state tracking
- [x] Implement `_setup_subscriptions()`
- [x] Implement intent handler with intent type checking
- [x] Implement music playback started handler
- [x] Implement track intro plan generation
- [x] Implement `_stop()` method
- [x] Add test coverage

## TimelineExecutorService Implementation

- [x] Copy `service_template.py` to `timeline_executor_service.py` (rename class)
- [x] Implement `_Config` Pydantic model
- [x] Set up internal state tracking
- [x] Implement `_setup_subscriptions()`
- [x] Implement plan handler
- [x] Implement plan execution coroutine
- [x] Implement step execution
- [x] Implement layer management methods
- [x] Implement `_stop()` method
- [x] Add test coverage

## MemoryService Implementation

- [x] Copy `service_template.py` to `memory_service.py` (rename class)
- [x] Implement `_Config` Pydantic model
- [x] Set up internal state tracking
- [x] Implement `_setup_subscriptions()`
- [x] Implement state manipulation methods
- [x] Implement event handlers
- [x] Add test coverage

## Integration Steps

- [x] Register services in main.py
- [x] Update configuration in config.py or config.json
- [x] Update imports in __init__.py files for each service directory

## End-of-Day Demo Verification

### Test Suite Implementation ✅
- [x] BrainService Tests
  - [x] Intent handling and routing
  - [x] Music playback events
  - [x] Track intro generation
  - [x] LLM response processing
- [x] TimelineExecutorService Tests
  - [x] Layer priority management
  - [x] Audio ducking coordination
  - [x] Plan execution sequencing
  - [x] Step execution timing
- [x] MemoryService Tests
  - [x] State management
  - [x] Chat history operations
  - [x] Wait predicates
  - [x] Event handling

### Integration Testing 🚀
- [ ] Test: Voice → Music
  - [ ] User says "Play something funky"
  - [ ] Music starts within 1.25s
  - [ ] MemoryService shows `music_playing=True`
- [ ] Test: Filler Line
  - [ ] GPTService emits filler text
  - [ ] Line plays without ducking
  - [ ] Line plays before music starts
- [ ] Test: Track Intro
  - [ ] BrainService emits `PLAN_READY` with speak step
  - [ ] TimelineExecutor ducks, plays line, unducks
  - [ ] Events `PLAN_STARTED`, `STEP_EXECUTED`, `PLAN_ENDED` emitted
- [ ] Test: Layer Handling
  - [ ] Test override cancellation
  - [ ] Test foreground pausing ambient
  - [ ] Test ambient resumption

## New TODOs Identified During Testing

### High Priority
1. [ ] Run full integration tests with all services connected
2. [ ] Implement real GPT integration for track intros
3. [ ] Add proper event waiting mechanism in TimelineExecutor
4. [ ] Improve speech synthesis completion detection

### Medium Priority
1. [ ] Add metrics collection for timing and performance
2. [ ] Add debug logging for timeline execution
3. [ ] Create monitoring dashboard for timeline state
4. [ ] Implement error recovery for failed steps

### Low Priority
1. [ ] Add support for conditional steps in plans
2. [ ] Implement plan persistence
3. [ ] Add plan visualization tools
4. [ ] Create timeline execution replay capability 