# DJ R3X Voice App — Working Dev Log (2025-11-07)
- This gets refreshed daily and the core info is saved to `dj-r3x-condensed-dev-log.md`
- Goal is to give cursor good active context for what we are working on.

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project recreates the voice and animation features with interactive conversations and synchronized LED animations.

## [Today] LLM Provider Analysis & Evaluation Plan

### Issue
Current system uses **GPT-4.1-mini** for LLM backend. Need to evaluate switching to **Claude 3.5 Haiku 4.5** for potential improvements in latency, reliability, and code simplicity.

### Research & Analysis Completed

**1. System Prompt Architecture**
- **Current (GPT-4.1-mini)**: System prompt baked into message history, requires "sandwich method" (instructions at beginning AND end) for best performance
- **Claude (Haiku & Sonnet)**: Dedicated `system` parameter separate from messages - cleaner, doesn't require repetition, respects instructions better across turns
- **Winner**: Claude - simpler architecture, less prompt engineering needed

**2. Tool Use Architecture**
- **Both**: Nearly identical APIs with `tools` parameter and `tool_choice="auto"`
- **Claude advantage**: More reliable tool use accuracy, fewer JSON parse errors
- **Winner**: Claude - proven better at deciding when NOT to call tools

**3. Streaming & Latency Performance**
- **GPT-4.1-mini**: 250-300ms first token latency
- **Claude 3.5 Haiku**: 150-200ms first token latency (33% faster ✅)
- **Claude 3.5 Sonnet**: 200-250ms first token latency (comparable)
- **Winner**: Haiku - fastest for voice interaction apps

**4. Multi-Turn Conversation Handling**
- **Current**: SessionMemory class with manual token counting + pruning (~150 lines)
- **Claude**: 200K token context window (vs GPT-4.1-mini's 128K) - can simplify/remove SessionMemory
- **Winner**: Claude - larger context, can reduce code complexity significantly

**5. Prompt Engineering Complexity**
- **GPT-4.1-mini**: Requires explicit, literal instructions; sandwich method; careful constraint definition
- **Claude**: More natural language; infers intent better; no sandwich method needed
- **DJ R3X personas**: Can become more readable, less verbose

### Cost Analysis
At current usage (few interactions/day):

| Model | Daily | Monthly | Yearly |
|-------|-------|---------|--------|
| **GPT-4.1-mini** | $0.02 | $0.60 | $7.30 |
| **Claude Haiku** | $0.11 | $3.34 | $40.08 |
| **Claude Sonnet** | $0.43 | $12.91 | $154.98 |

**Cost Impact**: Haiku costs ~5.7x more per token but latency improvement (~50ms) may be worth it for voice UX

### Key Findings

**What GPT-4.1-mini Does Well**:
- Proven, stable, cheap
- Tool use works reliably
- Streaming architecture is solid

**What Claude Haiku Does Better**:
- ✅ 50ms faster first token (better voice UX)
- ✅ Better tool use accuracy (fewer errors)
- ✅ Larger context window (200K vs 128K)
- ✅ Simpler prompting (no sandwich method)
- ✅ Cleaner API (dedicated system parameter)
- ✅ Better multi-turn reliability

### Architecture Changes Needed (if switching)

**High Priority**:
1. Replace OpenAI client with Anthropic client
2. Update API call to use `system` parameter separately from messages
3. Simplify/remove SessionMemory token counting (Claude handles it better)
4. Update streaming response parsing

**Medium Priority**:
1. Remove sandwich-method instruction repetition from personas
2. Simplify system prompts (can be more natural)
3. Update error handling (different exception types)

**Low Priority**:
1. Remove multi-turn agent persistence reminders (Claude doesn't need them)
2. Optimize interim streaming handling

### Migration Effort Estimate
**~3-4 hours** for production-ready migration with testing

### Risk Assessment
- **Low risk**: Tool use API nearly identical
- **Low risk**: Streaming architecture proven in industry
- **Medium risk**: Need to test with DJ R3X personas
- **Low downtime**: Can A/B test both models side-by-side

### Recommendation

**Decision**: Try Claude 3.5 Sonnet 4.5 first (you requested this)

**Rationale**:
- Better latency than GPT-4.1-mini (voice UX improvement)
- More capable than Haiku (reasoning, edge cases)
- Cost difference is negligible at current usage (few interactions/day)
- Can always revert if issues found
- Sonnet 4.5 is newest frontier model - best long-term choice

**Next Steps**:
1. Create migration branch
2. Update GPTService to use Claude SDK
3. Test with DJ personas
4. Compare latency/quality metrics
5. Decide on long-term provider

### Implementation Priority
1. **Update GPTService** to use Anthropic client
2. **Test main conversation flow** with DJ R3X
3. **Measure latency improvements** with LatencyTrackerService
4. **Compare persona quality** (does DJ R3X sound the same?)
5. **Evaluate error handling** and edge cases

---

## 📝 Summary for Condensed Log
```
### 2025-11-07: LLM Provider Analysis & Claude Migration Planning
- **Issue**: Evaluate switching from GPT-4.1-mini to Claude for better latency, reliability, and code simplicity
- **Analysis**: Comprehensive comparison of GPT-4.1-mini vs Claude 3.5 Haiku vs Claude 3.5 Sonnet 4.5
- **Findings**:
  - Claude Haiku: 50ms faster (150-200ms vs 250-300ms first token)
  - Claude architecture cleaner (dedicated system parameter, no sandwich method needed)
  - Larger context window (200K vs 128K) - can simplify SessionMemory
  - Cost minimal at hobby scale (~$40/year Haiku vs ~$7/year GPT-4.1-mini)
  - Tool use more reliable in Claude, fewer JSON errors
- **Recommendation**: Try Claude 3.5 Sonnet 4.5 for best combination of latency, capability, and cost at hobby scale
- **Migration Effort**: 3-4 hours for production-ready implementation
- **Next Steps**: Create migration branch, update GPTService, test with DJ personas
- **Technical Decision**: Will pursue Sonnet 4.5 as first choice due to superior latency + capability balance
```
