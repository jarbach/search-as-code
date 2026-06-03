# Cross-Session Caching Design - Phase 2 Feature #2

**Goal:** Cache search results and LLM summaries across sessions to avoid redundant API/LLM calls.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│ SearchPipeline                                          │
│  .search("LLM context")                                 │
│  .cache(enabled=True, ttl="24h")                        │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ CacheOp (NEW)                                           │
│ - Generates cache key from query + params              │
│ - Checks Lite-LCM for cached results                   │
│ - Returns cached results on HIT                        │
│ - Passes through on MISS (caches after execution)      │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ Lite-LCM (SQLite)                                       │
│ Table: search_cache                                     │
│ - cache_key (TEXT PRIMARY KEY)                         │
│ - query_hash (TEXT INDEX)                              │
│ - results_json (TEXT)                                  │
│ - created_at (TIMESTAMP)                               │
│ - ttl_seconds (INTEGER)                                │
│ - expires_at (TIMESTAMP)                               │
│ - hit_count (INTEGER)                                  │
└─────────────────────────────────────────────────────────┘
```

---

## Cache Key Strategy

### Primary Key Components

```python
cache_key = hash(
    operation_type + "|" +
    query + "|" +
    num_results + "|" +
    time_range + "|" +
    domain_filter + "|" +
    compression_strategy + "|" +
    target_tokens
)
```

**Example:**
```
search:llm context:10:year:arxiv.org|github.com:summarize:300
→ SHA256 → "a3f8b2c9..."
```

### Why This Works

- ✅ Same query + params = same cache key
- ✅ Different params = different cache entries
- ✅ Hash prevents key length issues
- ✅ Human-readable prefix for debugging

---

## TTL Management

### Configuration

```python
DEFAULT_TTL = {
    "search": 3600,        # 1 hour (search results change frequently)
    "fetch": 86400,        # 24 hours (web pages change less often)
    "summarize": 604800,   # 7 days (summaries of same content stay valid)
}
```

### Expiration Logic

```python
def is_expired(cache_entry):
    return datetime.now() > cache_entry.expires_at

def cleanup_expired():
    DELETE FROM search_cache WHERE expires_at < NOW()
```

---

## Cache Operations

### 1. Check Cache (Pre-Execution)

```python
async def check_cache(key: str) -> Optional[PipelineState]:
    entry = await db.get_cache_entry(key)
    
    if not entry:
        return None  # MISS
    
    if is_expired(entry):
        await db.delete_cache_entry(key)
        return None  # EXPIRED
    
    # HIT
    entry.hit_count += 1
    await db.update_hit_count(key, entry.hit_count)
    
    return deserialize(entry.results_json)
```

### 2. Write Cache (Post-Execution)

```python
async def write_cache(key: str, state: PipelineState, ttl_seconds: int):
    expires_at = datetime.now() + timedelta(seconds=ttl_seconds)
    
    await db.insert_cache_entry(
        cache_key=key,
        query_hash=hash_query(state.query),
        results_json=serialize(state.results),
        ttl_seconds=ttl_seconds,
        expires_at=expires_at
    )
```

### 3. Invalidate Cache

```python
# Manual invalidation
await cache.invalidate(pattern="search:llm*")

# Automatic invalidation on TTL expiry (cron job)
await cache.cleanup_expired()
```

---

## Lite-LCM Integration

### Schema Extension

```sql
-- Add to lite_lcm.py
CREATE TABLE IF NOT EXISTS search_cache (
    cache_key TEXT PRIMARY KEY,
    query_hash TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    query_params TEXT,
    results_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ttl_seconds INTEGER NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    hit_count INTEGER DEFAULT 0,
    last_hit_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_query_hash ON search_cache(query_hash);
CREATE INDEX IF NOT EXISTS idx_expires_at ON search_cache(expires_at);
```

### API Methods

```python
class LiteLCM:
    # ... existing methods ...
    
    async def cache_get(self, key: str) -> Optional[dict]:
        """Get cached result by key."""
        
    async def cache_set(self, key: str, data: dict, ttl_seconds: int):
        """Cache result with TTL."""
        
    async def cache_delete(self, key: str):
        """Delete specific cache entry."""
        
    async def cache_clear(self, pattern: str = None):
        """Clear cache (optionally by pattern)."""
        
    async def cache_cleanup(self):
        """Remove expired entries."""
        
    async def cache_stats(self) -> dict:
        """Get cache statistics (size, hit rate, etc.)."""
```

---

## Usage Patterns

### Pattern 1: Enable Caching on Pipeline

```python
from sdk import SearchPipeline

# Enable caching with default TTL
results = await SearchPipeline() \
    .search("LLM context management") \
    .cache(enabled=True) \
    .execute()

# Custom TTL
results = await SearchPipeline() \
    .search("LLM context management") \
    .cache(enabled=True, ttl_seconds=7200) \
    .execute()
```

### Pattern 2: Cache-Aware Pipeline

```python
from sdk import SearchPipeline, CacheOp

pipeline = SearchPipeline() \
    .cache(enabled=True, ttl_seconds=3600) \
    .search("AI agents", num_results=10) \
    .filter(domain="arxiv.org") \
    .compress(target_tokens=500, strategy="summarize")

state = await pipeline.execute()

# Check if results came from cache
if state.metrics.get("cache_hit"):
    print(f"✓ Cache HIT (saved ~{state.metrics['latency_saved_ms']}ms)")
else:
    print(f"✓ Cache MISS (cached for next time)")
```

### Pattern 3: Manual Cache Management

```python
from sdk import CacheManager

cache = CacheManager()

# View cache stats
stats = await cache.stats()
print(f"Total entries: {stats['total_entries']}")
print(f"Hit rate: {stats['hit_rate']:.2%}")
print(f"Storage used: {stats['storage_bytes'] / 1024:.1f} KB")

# Invalidate specific queries
await cache.invalidate(pattern="search:llm*")

# Cleanup expired entries
await cache.cleanup()

# Export cache data
data = await cache.export(format="json")
```

---

## Cache Metrics

### Tracked Metrics

```python
{
    "cache_enabled": True,
    "cache_hit": False,  # or True
    "cache_key": "a3f8b2c9...",
    "cache_ttl_seconds": 3600,
    "cache_expires_at": "2026-06-03T20:42:00",
    "latency_saved_ms": 847,  # On HIT
    "cache_write_time_ms": 12,  # On MISS
}
```

### Aggregate Statistics

```python
{
    "total_entries": 127,
    "total_hits": 543,
    "total_misses": 892,
    "hit_rate": 0.378,  # 37.8%
    "storage_bytes": 2847392,
    "oldest_entry": "2026-06-02T14:30:00",
    "newest_entry": "2026-06-03T12:42:00",
    "expired_entries": 23,
    "avg_latency_saved_ms": 623
}
```

---

## Implementation Plan

### Phase 2A: Core Caching (2-3 hours)

1. ✅ Add SQLite schema to `lite_lcm.py`
2. ✅ Implement `cache_get`, `cache_set`, `cache_delete` methods
3. ✅ Create `CacheOp` primitive
4. ✅ Integrate with `SearchPipeline`
5. ✅ Add cache metrics tracking

### Phase 2B: Management Tools (1 hour)

1. ✅ Create `CacheManager` class
2. ✅ Implement CLI commands (`cache stats`, `cache clear`, etc.)
3. ✅ Add cache invalidation patterns

### Phase 2C: Testing & Docs (1 hour)

1. ✅ Test cache HIT/MISS scenarios
2. ✅ Test TTL expiration
3. ✅ Test pattern-based invalidation
4. ✅ Update documentation

---

## Edge Cases & Considerations

### 1. Cache Stampede Prevention

**Problem:** Multiple agents request same uncached query simultaneously → redundant API calls.

**Solution:** Lock mechanism during cache population.

```python
async def get_or_compute(key: str):
    cached = await cache_get(key)
    if cached:
        return cached
    
    # Acquire lock
    lock = await cache_lock(key)
    if not lock:
        # Another agent is computing, wait
        return await wait_for_cache(key)
    
    try:
        result = await compute()
        await cache_set(key, result)
        return result
    finally:
        await cache_unlock(key)
```

### 2. Large Result Sets

**Problem:** Caching 100+ results consumes significant storage.

**Solution:** Size-based eviction policy.

```python
MAX_CACHE_SIZE_MB = 50

async def enforce_size_limit():
    size = await cache_size()
    while size > MAX_CACHE_SIZE_MB:
        oldest = await get_oldest_entry()
        await cache_delete(oldest.key)
        size = await cache_size()
```

### 3. Sensitive Queries

**Problem:** Some queries may contain sensitive information.

**Solution:** Opt-out mechanism.

```python
# Don't cache sensitive queries
results = await SearchPipeline() \
    .search("confidential internal project") \
    .cache(enabled=False) \
    .execute()
```

### 4. Cache Coherency

**Problem:** Web content changes, cached results become stale.

**Solution:** 
- Short TTLs for search (1 hour default)
- Manual invalidation when needed
- Optional freshness check (HEAD request to validate URLs)

---

## Performance Expectations

| Scenario | Without Cache | With Cache (HIT) | Savings |
|----------|---------------|------------------|---------|
| Basic search | ~800ms | ~15ms | 98% |
| Search + fetch (5 URLs) | ~5s | ~15ms | 99.7% |
| Search + summarize | ~8s | ~15ms | 99.8% |
| Fanout (3 variants) | ~10s | ~15ms | 99.8% |

**Expected Hit Rate:** 30-50% for research workflows (similar queries across sessions)

---

## Security Considerations

1. **Token Storage:** Gateway token not cached (only results)
2. **Query Privacy:** Cache stored locally (SQLite, file permissions)
3. **PII Handling:** Users should disable cache for sensitive queries
4. **Injection Prevention:** Parameterized SQL queries (no string interpolation)

---

## Next Steps

1. Implement core caching in `lite_lcm.py`
2. Create `CacheOp` primitive in `sdk.py`
3. Add cache configuration to SDK
4. Test with realistic workloads
5. Document usage patterns
