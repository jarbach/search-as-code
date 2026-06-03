# Search-as-Code SDK v0.2.1

**Phase 2 Complete** - LLM Smart Compression + Cross-Session Caching + Gateway HTTP API

## Quick Start

```bash
cd /workspace/skills/auto-generated/search-as-code/

# Install dependencies
pip install beautifulsoup4 aiohttp

# Configure Gateway access (optional if using defaults)
export OPENCLAW_GATEWAY_URL="http://127.0.0.1:18789"
export OPENCLAW_GATEWAY_TOKEN="your-token-here"

# Configure LLM for smart compression (optional)
export SEARCH_SDK_SUMMARIZER_MODEL="qwen2.5:7b"  # Local model
export OLLAMA_BASE_URL="http://127.0.0.1:11434"

# Run examples
python sdk.py basic
python sdk.py research
python sdk.py cybersecurity
python examples/smart_compression.py  # NEW: Phase 2 demo
python examples/test_caching_integration.py  # NEW: Caching test

# Or run specific example files
python examples/basic_search.py
python examples/research_pipeline.py
python examples/cybersecurity.py
python examples/smart_compression.py
```

## Usage

### Basic Search

```python
from sdk import SearchPipeline

results = await SearchPipeline() \
    .search("your query here") \
    .filter(domain="arxiv.org") \
    .compress(target_tokens=500) \
    .execute()
```

### Compression Strategies

**Truncate (default):** Keeps raw results, cuts off snippets at token limit

```python
results = await SearchPipeline() \
    .search("your query") \
    .compress(target_tokens=500, strategy="truncate") \
    .execute()
```

**Summarize (NEW in Phase 2):** LLM-powered synthesis of results

```python
results = await SearchPipeline() \
    .search("your query") \
    .compress(target_tokens=500, strategy="summarize") \
    .execute()

# Output: Coherent summaries with citations [1], [2], etc.
# Original URLs preserved in metadata
```

### Pre-built Pipelines

```python
from sdk import ResearchPipeline, CybersecurityThreatIntelPipeline

# Academic research
research = ResearchPipeline(
    topic="your topic",
    sources=["arxiv.org", "aclanthology.org"],
    time_range="year:2025"
)
results = await research.run(compression_strategy="summarize")

# Cybersecurity threat intel
threat = CybersecurityThreatIntelPipeline(
    threat_type="zero-day exploit",
    targets=["Windows", "macOS"]
)
results = await threat.run()
```

### Cross-Session Caching (NEW in v0.2.1)

Caching is **enabled by default** for search operations:

```python
# First run - CACHE MISS (~800ms)
results1 = await SearchPipeline() \
    .search("quantum computing 2025") \
    .execute()

# Second run - CACHE HIT (<10ms, ~800ms saved)
results2 = await SearchPipeline() \
    .search("quantum computing 2025") \
    .execute()

# Disable caching for fresh results
results3 = await SearchPipeline() \
    .search("quantum computing 2025", use_cache=False) \
    .execute()

# Custom TTL (in seconds)
results4 = await SearchPipeline() \
    .search("quantum computing 2025", cache_ttl=7200)  # 2 hours \
    .execute()
```

**Cache Management CLI:**

```bash
# View statistics
python cache.py stats

# Clear expired entries
python cache.py cleanup

# Clear all cache
python cache.py clear

# Invalidate by query pattern
python cache.py invalidate "search:*quantum*"
```

**Default TTLs:**
- Search: 1 hour
- Fetch: 24 hours  
- Summarize: 7 days
results = await research.run()

# Threat intelligence
threat_intel = CybersecurityThreatIntelPipeline(
    threat_type="ransomware",
    targets=["healthcare"],
    include_cve=True
)
results = await threat_intel.run()
```

## Documentation

- [SKILL.md](SKILL.md) - Full documentation and architecture
- [examples/](examples/) - Working code examples

## Status

✅ Phase 1B Complete (2026-06-03) - Real tool invocation via HTTP API

## Phase 2 Features (NEW)

**LLM Smart Compression** - Synthesize search results instead of truncating

- Uses local Ollama model (qwen2.5:7b) for fast summarization
- Groups results into batches and summarizes each batch
- Preserves source URLs in metadata
- Cites sources by number [1], [2], etc.

**Comparison:**
- Truncate: 2-3 raw results, exact snippets preserved
- Summarize: 4 synthesized batches, coherent overview with citations

See `examples/smart_compression.py` for detailed comparison.
