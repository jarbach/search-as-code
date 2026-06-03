# Search-as-Code SDK v0.2.1

**Location:** `/workspace/skills/auto-generated/search-as-code/`

**Phase:** 2 Complete (LLM Smart Compression + Cross-Session Caching + Gateway HTTP API)

**Status:** ✅ Production Ready

**Inspired by:** Perplexity's "Rethinking Search as Code Generation" architecture

---

## Overview

Exposes search stack as composable Python primitives that agents can assemble into custom pipelines via code generation. Instead of making sequential tool calls, agents generate Python code that chains operations together.

## Core Concepts

### Primitives

Fine-grained operations that can be chained:

| Primitive | Purpose | Example |
|-----------|---------|---------|
| `search()` | Base retrieval with sources/time_range params | `.search("LLM context optimization", num_results=20)` |
| `fetch()` | Batch URL fetching with concurrency control | `.fetch(max_concurrent=10)` |
| `filter()` | Domain/date/custom predicates | `.filter(domain="arxiv.org")` |
| `rank_by()` | Re-ranking signals | `.rank_by("relevance")` |
| `fanout()` | Parallel search variants | `.fanout(["2025", "2026"], base_query="...")` |
| `dedupe()` | URL/domain/hash deduplication | `.dedupe(by="url")` |
| `compress()` | Token budget enforcement (truncate or summarize) | `.compress(target_tokens=800, strategy="summarize")` |
| `extract()` | CSS selector extraction | `.extract({"title": "h1", "content": ".article"})` |
| **`use_cache`** | Enable/disable cross-session caching | `.search("query", use_cache=True)` |

### Pre-built Pipelines

Task-oriented templates for common workflows:

- `ResearchPipeline` - Academic/technical literature review
- `CompetitiveAnalysisPipeline` - Competitive intelligence gathering
- `CybersecurityThreatIntelPipeline` - Threat intelligence + CVE tracking

### State Persistence

Cross-turn state management via filesystem + Lite-LCM:

```python
# Turn 1
state = await pipeline.fetch(urls).save_state("research_batch_1")

# Turn 2
state = PipelineState.load_state("research_batch_1")
```

---

## Usage Examples

### Basic Search

```python
from sdk import SearchPipeline

results = await SearchPipeline() \
    .search("LLM context window optimization 2025") \
    .filter(domain="arxiv.org") \
    .rank_by("relevance") \
    .compress(target_tokens=500) \
    .execute()

print(f"Found {len(results.results)} results")
print(f"Metrics: {results.metrics}")
```

### Fan-out + Dedupe

```python
results = await SearchPipeline() \
    .fanout(["2024", "2025", "2026"], base_query="transformer architecture") \
    .dedupe(by="url") \
    .execute()

print(f"Fan-out found {len(results.results)} unique results")
```

### Research Pipeline (Pre-built)

```python
from sdk import ResearchPipeline

pipeline = ResearchPipeline(
    topic="LLM context management techniques",
    sources=["arxiv.org", "aclanthology.org"],
    time_range="year:2025",
    max_results=30
)

results = await pipeline.run()
```

### Cybersecurity Threat Intel

```python
from sdk import CybersecurityThreatIntelPipeline

pipeline = CybersecurityThreatIntelPipeline(
    threat_type="zero-day exploit Chrome",
    targets=["Windows", "macOS"],
    include_cve=True,
    time_range="month"
)

results = await pipeline.run()
```

### Cross-Turn State Persistence

```python
# Turn 1: Fetch many URLs
state = await SearchPipeline() \
    .search("Python async patterns") \
    .fetch(max_concurrent=10) \
    .save_state("async_research")

# Turn 2: Load and process
state = PipelineState.load_state("async_research")
state = await SearchPipeline() \
    .extract({"title": "h1", "content": ".article-body"}) \
    .execute(state)
```

### Smart Compression (Phase 2)

```python
# LLM-powered summarization instead of truncation
results = await SearchPipeline() \
    .search("AI agent architectures") \
    .compress(target_tokens=800, strategy="summarize") \
    .execute()

# Output: Coherent narrative with citations [1], [2], etc.
```

### Cross-Session Caching (Phase 2)

```python
# Automatic caching (enabled by default)
results1 = await SearchPipeline() \
    .search("quantum computing 2025") \
    .execute()  # CACHE MISS (~800ms)

results2 = await SearchPipeline() \
    .search("quantum computing 2025") \
    .execute()  # CACHE HIT (<10ms, ~800ms saved)

# Disable for fresh results
results3 = await SearchPipeline() \
    .search("quantum computing 2025", use_cache=False) \
    .execute()
```

---

## Architecture

### Phase 2: Full Feature Set

```
┌─────────────────────────────────────────────────────────┐
│ Agent generates Python code using SearchPipeline API   │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ sdk.py: Search-as-Code SDK v0.2.1                       │
│ - Primitives (SearchOp, FetchOp, FilterOp, etc.)       │
│ - LLM Summarizer (qwen2.5:7b via Ollama)               │
│ - Cross-Session Cache (SQLite via Lite-LCM)            │
│ - Pipeline builder (fluent interface)                  │
│ - Retry logic + error handling                         │
│ - State persistence (filesystem + Lite-LCM)            │
└─────────────────────────────────────────────────────────┘
                          ↓
        ┌───────────────┴───────────────┐
        ↓                               ↓
┌──────────────────┐          ┌──────────────────┐
│ SearchCache      │          │ ToolExecutor     │
│ - SQLite cache   │          │ - HTTP client    │
│ - SHA256 keys    │          │ - Gateway API    │
│ - TTL management │          │ - web_search     │
│ - Hit tracking   │          │ - web_fetch      │
└──────────────────┘          └──────────────────┘
                                        ↓
                              ┌──────────────────┐
                              │ OpenClaw Gateway │
                              │ - Tool policy    │
                              │ - Auth validation│
                              │ - Provider route │
                              └──────────────────┘
```

---

## Installation

```bash
cd /workspace/skills/auto-generated/search-as-code/
pip install beautifulsoup4 aiohttp  # aiohttp for LLM summarization
```

**Requirements:**
- OpenClaw Gateway running on port 18789
- Ollama running locally with `qwen2.5:7b` (for smart compression)
- Lite-LCM initialized (`lite_lcm.py init`) for caching

---

## Testing

Run the built-in examples:

```bash
# Basic search
python sdk.py basic

# Fan-out + dedupe
python sdk.py fanout

# Pre-built research pipeline
python sdk.py research

# Cybersecurity threat intel
python sdk.py cybersecurity

# Phase 2: Smart compression demo
python examples/smart_compression.py

# Phase 2: Caching integration test
python examples/test_caching_integration.py
```

See `PHASE_2_COMPLETE.md` for comprehensive test results.

---

## Limitations (Phase 2)

1. **HTTP overhead**: Each tool call is an HTTP request (~50-100ms overhead)
2. **Auth required**: Needs Gateway token in environment or hardcoded
3. **Tool policy**: Subject to Gateway tool allowlist/denylist
4. **No sandbox isolation**: Code runs at host level (trust the agent)
5. **LLM latency**: Smart compression adds ~3-5s per batch (local inference)
6. **Cache storage**: Search queries stored in SQLite (consider encryption for sensitive use cases)

---

## Files

```
search-as-code/
├── sdk.py                    # Core SDK (this file)
├── openclaw_plugin.py        # OpenClaw plugin wrapper (TODO)
├── examples/
│   ├── basic_search.py       # Basic usage examples
│   ├── research_pipeline.py  # Research workflow
│   └── competitive_analysis.py # Competitive intel
├── SKILL.md                  # This documentation
└── TEST_RESULTS.md           # Validation results (TODO)
```

---

## Design Decisions

### Why Subprocess First?

- ✅ Works immediately with current OpenClaw
- ✅ No gateway modifications needed
- ✅ Easy to swap backend later (adapter pattern)
- ❌ Slower than direct API calls
- ❌ Approval prompts may interrupt automation

### Why Lite-LCM Integration?

- Provides cross-turn state persistence
- Enables search state to survive session restarts
- Optional dependency (SDK works without it)

### Why Pre-built Pipelines?

- Reduces code generation burden for common tasks
- Agents can use templates as starting points
- Easier to maintain than generated code for standard workflows

---

## Future Enhancements

1. **Gateway HTTP API**: Direct tool invocation without subprocess
2. **Sandbox hardening**: Docker isolation for untrusted code
3. **LLM summarization**: Smart compression instead of truncation
4. **More templates**: Literature review, trend tracking, regulatory monitoring
5. **Caching**: Deduplicate across sessions, not just within pipeline
6. **Rate limiting**: Respect search provider rate limits
7. **Metrics dashboard**: Track search costs, success rates, latency

---

**Status:** ✅ Phase 1A Complete (2026-06-03)

**Next Steps:**
- Test with real workflows
- Gather feedback on primitive set
- Implement Phase 2 (Gateway HTTP API) if needed
