# Web Dashboard Pydantic Expansion - Simple Implementation TODO

This TODO focuses on the core objective: **expand existing Pydantic system for web dashboard events** while maintaining 100% backward compatibility.

## Phase 1: Core Pydantic Expansion (Week 1)

### Step 1: Expand Existing Event Payloads
- [ ] Add web dashboard payloads to existing `cantina_os/cantina_os/core/event_payloads.py`
  - [ ] `WebDashboardCommandPayload` (base class)
  - [ ] `WebVoiceCommandPayload`
  - [ ] `WebMusicCommandPayload`
  - [ ] `WebSystemCommandPayload`
  - [ ] `WebMusicStatusPayload`
  - [ ] `WebVoiceStatusPayload`
  - [ ] `WebSystemStatusPayload`
  - [ ] `WebDJStatusPayload`
  - [ ] `WebServiceStatusPayload`
  - [ ] `WebProgressPayload`

### Step 2: Add Web Event Topics
- [ ] Add web dashboard topics to existing `cantina_os/cantina_os/core/event_topics.py`
  - [ ] `WEB_VOICE_COMMAND = "web.voice.command"`
  - [ ] `WEB_MUSIC_COMMAND = "web.music.command"`
  - [ ] `WEB_SYSTEM_COMMAND = "web.system.command"`
  - [ ] `WEB_VOICE_STATUS = "web.voice.status"`
  - [ ] `WEB_MUSIC_STATUS = "web.music.status"`
  - [ ] `WEB_SYSTEM_STATUS = "web.system.status"`
  - [ ] `WEB_DJ_STATUS = "web.dj.status"`
  - [ ] `WEB_SERVICE_STATUS = "web.service.status"`
  - [ ] `WEB_PROGRESS_UPDATE = "web.progress.update"`

### Step 3: Create Simple Validation Helper
- [ ] Create/enhance `cantina_os/cantina_os/schemas/validation.py`
  - [ ] Add status payload validation functions
  - [ ] Create `StatusPayloadValidationMixin` for WebBridge
  - [ ] Add `validate_and_serialize_status()` function
  - [ ] Add `broadcast_validated_status()` method

### Step 4: Enhance WebBridge with Fallback Validation
- [ ] Update `cantina_os/cantina_os/services/web_bridge_service.py`
  - [ ] Add `StatusPayloadValidationMixin` to inheritance
  - [ ] Enhance `_broadcast_event_to_dashboard()` with Pydantic validation + fallback
  - [ ] Ensure same JSON output to frontend
  - [ ] Test that existing functionality still works

## Phase 2: Complete Integration (Week 2)

### Step 5: Migrate Remaining Handlers
- [ ] Apply Pydantic validation + fallback to core WebBridge broadcasting method
- [ ] Ensure all status handlers use validated broadcasting automatically
- [ ] Test that all web dashboard functionality works as before

### Step 6: Optional TypeScript Generation
- [ ] Create TypeScript interface generation script
- [ ] Generate `dj-r3x-dashboard/src/types/cantina-payloads.ts`
- [ ] Auto-generate interfaces from Pydantic models
- [ ] Ready for frontend integration

## Phase 3: Testing & Verification

### Step 7: Comprehensive Testing
- [ ] Verify 100% backward compatibility with existing CLI system
- [ ] Test that web dashboard status broadcasting works with fallback
- [ ] Verify payload structures maintain identical format
- [ ] Test error handling and fallback mechanisms

## Key Implementation Notes

### Backward Compatibility Strategy
- **Add, don't replace**: Enhance existing handlers with Pydantic validation
- **Fallback mechanism**: If Pydantic fails, use existing manual validation
- **Same JSON output**: Ensure frontend receives identical data structures
- **Zero breaking changes**: CLI system remains completely untouched

### Simple Validation Pattern
```python
# In web_bridge_service.py
from ..schemas.validation import validate_web_command

@validate_web_command(WebVoiceCommandPayload)
async def voice_command(sid, data):
    """Enhanced with Pydantic validation + fallback."""
    # Handler receives validated data OR original data if validation fails
    # Same logic as before, just with optional type safety
```

### Files to Modify
1. `cantina_os/cantina_os/core/event_payloads.py` - Add web payloads
2. `cantina_os/cantina_os/core/event_topics.py` - Add web topics  
3. `cantina_os/cantina_os/schemas/validation.py` - Create simple validator
4. `cantina_os/cantina_os/services/web_bridge_service.py` - Add validation + fallback

## Success Criteria

- [ ] Web dashboard works exactly as before
- [ ] CLI system completely unchanged
- [ ] Web status broadcasts use Pydantic validation when possible
- [ ] Fallback preserves existing functionality
- [ ] TypeScript interfaces generated from Pydantic models

This approach focuses on your core goal: **expand existing Pydantic system for web dashboard** without the complexity of a complete rewrite.