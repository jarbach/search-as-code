# Search-as-Code SDK v0.2.1 - Quick Start

**Phase 2 Complete** ✅ | 2026-06-03 | Production Ready

---

## Installation

```bash
cd /workspace/skills/auto-generated/search-as-code/
pip install beautifulsoup4 aiohttp
```

**Requirements:**
- OpenClaw Gateway running on `http://127.0.0.1:18789`
- Ollama with `qwen2.5:7b` model (for smart compression)
- Lite-LCM initialized: `python3 ../lite-lcm/lite_lcm.py init`

---

## Quick Examples

### Basic Search (with automatic caching)

```python
from sdk import SearchPipeline

# First run: CACHE MISS (~800ms)
# Second run: CACHE HIT (<10ms, saves ~800ms)
results = await SearchPipeline() \
    .search("quantum computing breakthroughs 2025") \
    .execute()

print(f"Found {len(results.results)} results")
print(f"Metrics: {results.metrics}")
# {'search_count': 5, 'cache_hit': True, 'latency_saved_ms': 800}
```

### Smart Compression (LLM Summarization)

```python
# Replace truncation with intelligent summarization
results = await SearchPipeline() \
    .search("AI agent architectures") \
    .compress(target_tokens=800, strategy="summarize") \
    .execute()

# Output: Coherent narrative with citations [1], [2], [3]
# Original URLs preserved in metadata
```

### Combined: Caching + Summarization

```python
# Both features work together automatically
results = await SearchPipeline() \
    .search("LLM context management techniques", cache_ttl=3600) \
    .filter(domain="arxiv.org|aclanthology.org") \
    .compress(target_tokens=600, strategy="summarize") \
    .execute()
```

### Pre-built Pipelines

```python
from sdk import ResearchPipeline, CybersecurityThreatIntelPipeline

# Academic research with smart compression
research = ResearchPipeline(
    topic="your topic",
    sources=["arxiv.org", "aclanthology.org"],
    time_range="year:2025"
)
results = await research.run(compression_strategy="summarize")

# Threat intelligence with caching
threat = CybersecurityThreatIntelPipeline(
    threat_type="zero-day exploit",
    targets=["Windows", "macOS"]
)
results = await threat.run()  # Caching enabled by default
```

---

## Configuration

### Environment Variables

```bash
# Gateway (required)
export OPENCLAW_GATEWAY_URL="http://127.0.0.1:18789"
export OPENCLAW_GATEWAY_TOKEN="your-token-here"

# LLM (optional, defaults shown)
export SEARCH_SDK_SUMMARIZER_MODEL="qwen2.5:7b"
export OLLAMA_BASE_URL="http://127.0.0.1:11434"
```

### SDK Parameters

```python
# Search with custom cache TTL (2 hours)
SearchPipeline().search("query", use_cache=True, cache_ttl=7200)

# Disable caching for fresh results
SearchPipeline().search("query", use_cache=False)

# Smart compression with custom token budget
SearchPipeline().compress(target_tokens=1000, strategy="summarize")
```

---

## Cache Management CLI

```bash
# View statistics
python3 cache.py stats

# Clear expired entries
python3 cache.py cleanup

# Clear all cache
python3 cache.py clear

# Invalidate by pattern
python3 cache.py invalidate "search:*quantum*"
```

**Default TTLs:**
- Search: 1 hour
- Fetch: 24 hours
- Summarize: 7 days

---

## Performance Benchmarks

| Operation | Latency | Notes |
|-----------|---------|-------|
| Search (MISS) | ~800ms | Gateway API call |
| Search (HIT) | <10ms | SQLite lookup |
| **Latency Saved** | **~790ms** | **98.7% reduction** |
| Smart Compression | +3-5s | Per batch (local LLM) |
| Full Pipeline | 5-15s | With both features |

**Expected Hit Rates:**
- Research workflows: 60-80%
- Monitoring dashboards: 90-95%
- One-off searches: 0%

---

## Run Examples

```bash
# Basic search
python3 sdk.py basic

# Fan-out + dedupe
python3 sdk.py fanout

# Research pipeline
python3 sdk.py research

# Cybersecurity threat intel
python3 sdk.py cybersecurity

# Smart compression demo
python3 examples/smart_compression.py

# Caching integration test
python3 examples/test_caching_integration.py
```

---

## Files Reference

```
search-as-code/
├── sdk.py                          # Core SDK
├── cache.py                        # Caching module + CLI
├── README.md                       # Full documentation
├── SKILL.md                        # Skill definition
├── PHASE_2_COMPLETE.md             # Comprehensive report
├── QUICKSTART_PHASE2.md            # This file
├── TEST_RESULTS.md                 # All test results
├── examples/
│   ├── basic_search.py
│   ├── research_pipeline.py
│   ├── cybersecurity.py
│   ├── smart_compression.py        # LLM summarization demo
│   ├── test_cache_simple.py        # Basic cache test
│   └── test_caching_integration.py # Full integration test
└── CACHE_DESIGN.md                 # Architecture design
```

---

## Troubleshooting

### Cache not working?
1. Check Lite-LCM initialized: `python3 ../lite-lcm/lite_lcm.py init`
2. Verify `cache.py stats` shows entries
3. Check `use_cache=True` parameter (enabled by default)

### Summarization failing?
1. Ensure Ollama running: `ollama list` should show `qwen2.5:7b`
2. Check Ollama URL: `curl http://localhost:11434/api/tags`
3. Fallback to truncation on errors (automatic)

### Gateway errors?
1. Verify Gateway running: `openclaw gateway status`
2. Check token in `~/.openclaw/openclaw.json`
3. Test endpoint: `curl -X POST http://localhost:18789/tools/invoke ...`

---

## Next Steps (Phase 3)

- [ ] Rate Limiting & Retry Logic
- [ ] Metrics Dashboard
- [ ] Advanced Caching (multi-level, LRU)
- [ ] New Primitives (ClusterOp, TimelineOp, SentimentOp)

---

**Full Documentation:** See `PHASE_2_COMPLETE.md` for comprehensive details.  
**Obsidian Note:** `../../Obsidian/29 - Search-as-Code SDK.md`
