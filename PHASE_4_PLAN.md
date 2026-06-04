# Phase 4 Implementation Plan

**Version:** v0.4.0  
**Date:** 2026-06-04  
**Status:** 🚧 In Progress

---

## Overview

Phase 4 focuses on **performance optimization** and **observability** to prepare the SDK for production-scale usage.

### Priority 1: Embedding Cache ⭐⭐⭐

**Problem:** Embedding generation is expensive (~800ms first run) even for repeated queries.

**Solution:** Cache embedding vectors in SQLite to avoid recomputation.

**Expected Impact:**
- First run: ~800ms (unchanged)
- Cache hit: ~50ms (16x faster)
- Cache hit rate: 70-90% for typical research workflows

**Implementation:**
- New table: `embedding_cache` with SHA256 hash keys
- TTL-based eviction (7 days default)
- Transparent integration with `EmbeddingClusterer`
- Backward compatible (graceful fallback if unavailable)

---

### Priority 2: Metrics Dashboard ⭐⭐⭐

**Problem:** No visibility into SDK performance, costs, or usage patterns.

**Solution:** Comprehensive metrics collection and dashboard.

**Tracked Metrics:**
- Query volume (total, by operation type)
- Latency (avg, p50, p95, p99)
- Cache hit rates (search, fetch, summarize, embeddings)
- Rate limiting (requests throttled, circuit breaker trips)
- Token usage (for LLM operations)
- Estimated costs (API calls, compute time)

**Expected Impact:**
- Data-driven optimization decisions
- Cost visibility and budgeting
- Performance bottleneck identification
- SLA monitoring capability

**Implementation:**
- New module: `metrics.py` with `MetricsCollector` class
- SQLite backend (shared with cache)
- Dashboard API + CLI commands
- Optional Prometheus/Grafana export (Phase 5 candidate)

---

## Architecture

### Embedding Cache Flow

```
EmbeddingClusterer.cluster(texts)
    ↓
┌─────────────────────────────────────┐
│  EmbeddingCache                     │
│  ┌───────────────────────────────┐  │
│  │ Check cache for each text    │  │
│  │ Hash: SHA256(text)           │  │
│  ├───────────────────────────────┤  │
│  │ Cache Hit → Load vector      │  │
│  │ Cache Miss → Generate + Save │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
    ↓
KMeans clustering on vectors
```

### Metrics Collection Flow

```
SDK Operation (search/cluster/sentiment/etc.)
    ↓
┌─────────────────────────────────────┐
│  MetricsCollector                   │
│  - Start timer                      │
│  - Track operation type             │
│  - Record success/failure           │
│  - Log latency                      │
│  - Update counters                  │
└─────────────────────────────────────┘
    ↓
SQLite: metrics_store table
    ↓
MetricsDashboard.query(...)
```

---

## Database Schema

### embedding_cache Table

```sql
CREATE TABLE embedding_cache (
    id TEXT PRIMARY KEY,              -- SHA256 hash of text
    text_hash TEXT NOT NULL,          -- SHA256(text)
    text_preview TEXT,                -- First 100 chars (for debugging)
    model_name TEXT NOT NULL,         -- e.g., "all-MiniLM-L6-v2"
    vector BLOB NOT NULL,             -- Pickled numpy array
    dimension INTEGER NOT NULL,       -- Vector dimension (384 for MiniLM)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 1,
    ttl_days INTEGER DEFAULT 7
);

CREATE INDEX idx_embedding_cache_hash ON embedding_cache(text_hash);
CREATE INDEX idx_embedding_cache_accessed ON embedding_cache(last_accessed);
```

### metrics_store Table

```sql
CREATE TABLE metrics_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    operation TEXT NOT NULL,          -- search/cluster/timeline/sentiment/alert/compress
    sub_operation TEXT,               -- embed/cache_lookup/kmeans/etc.
    duration_ms REAL NOT NULL,
    success INTEGER NOT NULL,         -- 1 or 0
    error_message TEXT,
    metadata TEXT,                    -- JSON blob with additional context
    session_id TEXT                   -- Optional session tracking
);

CREATE INDEX idx_metrics_timestamp ON metrics_store(timestamp);
CREATE INDEX idx_metrics_operation ON metrics_store(operation);
```

---

## Implementation Steps

### Phase 4.1: Embedding Cache

1. ✅ Design schema (above)
2. ⏳ Create `embedding_cache.py` module
   - `EmbeddingCache` class
   - Methods: `get()`, `set()`, `delete()`, `clear_expired()`, `stats()`
   - TTL-based eviction
   - Graceful degradation if unavailable
3. ⏳ Integrate into `EmbeddingClusterer`
   - Check cache before generating embeddings
   - Save new embeddings to cache
   - Add `use_cache=True/False` parameter
4. ⏳ Add tests
   - Cache hit/miss scenarios
   - TTL eviction
   - Performance benchmarks
5. ⏳ Update documentation

### Phase 4.2: Metrics Dashboard

1. ⏳ Create `metrics.py` module
   - `MetricsCollector` class (context manager + decorator)
   - `MetricsDashboard` class (query + visualization)
   - Aggregation functions (avg, p50, p95, p99)
2. ⏳ Integrate into SDK operations
   - Wrap all primitives with metrics collection
   - Track rate limiting events
   - Track cache hits/misses
3. ⏳ Create CLI commands
   - `python metrics.py summary` - Last 24h overview
   - `python metrics.py latency` - Latency breakdown
   - `python metrics.py cache` - Cache statistics
   - `python metrics.py cost` - Estimated costs
4. ⏳ Add tests
5. ⏳ Update documentation

---

## Configuration

### Environment Variables

```bash
# Embedding Cache
export SEARCH_EMBEDDING_CACHE_TTL=7        # days
export SEARCH_EMBEDDING_CACHE_SIZE=10000   # max entries

# Metrics
export SEARCH_METRICS_ENABLED=true
export SEARCH_METRICS_RETENTION_DAYS=30    # how long to keep metrics
export SEARCH_METRICS_SAMPLE_RATE=1.0      # 1.0 = log all, 0.1 = 10%
```

### SDK Parameters

```python
# Disable embedding cache for specific operation
results = await pipeline \
    .cluster(n_clusters=3, use_cache=False) \
    .execute()

# Get metrics summary
from sdk import MetricsDashboard
dashboard = MetricsDashboard()
print(dashboard.summary(hours=24))
```

---

## Success Criteria

### Embedding Cache

- [ ] Cache hit rate >70% for repeated queries
- [ ] Cache hit latency <100ms (vs 800ms uncached)
- [ ] Zero breaking changes to existing API
- [ ] Automatic TTL eviction working
- [ ] Graceful fallback if cache unavailable

### Metrics Dashboard

- [ ] All SDK operations tracked
- [ ] Latency percentiles accurate (p50, p95, p99)
- [ ] Cache hit rates calculated correctly
- [ ] Cost estimates within 20% of actual
- [ ] CLI commands working
- [ ] Query API functional

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Cache bloat (DB size) | Medium | TTL eviction + max size limit |
| Metrics overhead | Low | Async writes + sampling option |
| Schema migration complexity | Low | New tables, no changes to existing |
| Vector storage size | Medium | Monitor DB size, add compression if needed |

---

## Timeline Estimate

- **Embedding Cache:** 2-3 hours
- **Metrics Dashboard:** 3-4 hours
- **Testing & Documentation:** 1-2 hours
- **Total:** 6-9 hours

---

## Next Steps

1. Implement `embedding_cache.py`
2. Test caching performance
3. Implement `metrics.py`
4. Integrate metrics into SDK
5. Create dashboard CLI
6. Document everything
7. Deploy to GitHub

---

**Location:** `/workspace/skills/auto-generated/search-as-code/`  
**GitHub:** https://github.com/jarbach/search-as-code
