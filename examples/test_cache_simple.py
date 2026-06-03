#!/usr/bin/env python3
"""
Simple cache test - demonstrates HIT/MISS behavior.
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from cache import SearchCache, generate_cache_key, CacheManager


async def test_cache_basic():
    """Test basic cache MISS then HIT."""
    
    print("=" * 70)
    print("CACHE TEST: Basic MISS → HIT")
    print("=" * 70)
    
    query = "AI agent memory systems"
    cache_key = generate_cache_key("search", query, num_results=8)
    
    cache = SearchCache(enabled=True, ttl_seconds=3600)
    
    # Test 1: Cache MISS
    print(f"\n[TEST 1] First lookup (MISS)")
    print(f"Cache key: {cache_key}")
    
    result, metrics = cache.get(cache_key)
    print(f"Hit: {metrics['cache_hit']}")
    
    # Simulate search results
    fake_results = [
        {"title": "Result 1", "url": "https://example.com/1", "snippet": "Snippet 1"},
        {"title": "Result 2", "url": "https://example.com/2", "snippet": "Snippet 2"},
    ]
    
    # Write to cache
    print(f"\n[WRITE] Caching results...")
    success = cache.set(cache_key, query, fake_results)
    print(f"Success: {success}")
    
    # Test 2: Cache HIT
    print(f"\n[TEST 2] Second lookup (HIT)")
    result, metrics = cache.get(cache_key)
    print(f"Hit: {metrics['cache_hit']}")
    print(f"Hit count: {metrics.get('hit_count', 0)}")
    print(f"Latency saved: ~{metrics.get('latency_saved_ms', 0)}ms")
    
    if result:
        print(f"\nCached data retrieved:")
        for r in result[:2]:
            print(f"  - {r['title']}: {r['url']}")
    
    # Show stats
    print(f"\n" + "=" * 70)
    print("CACHE STATISTICS")
    print("=" * 70)
    
    manager = CacheManager()
    print(manager.export(format="text"))


if __name__ == "__main__":
    asyncio.run(test_cache_basic())
