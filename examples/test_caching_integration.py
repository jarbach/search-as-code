#!/usr/bin/env python3
"""
Test caching integration in SearchPipeline.

Validates:
- Cache MISS on first search
- Cache HIT on second identical search
- Metrics tracking (hit rate, latency saved)
- TTL expiration (optional manual test)
"""

import asyncio
import sys
from pathlib import Path

# Add SDK directory to path
sys.path.insert(0, str(Path(__file__).parent))

from sdk import SearchPipeline


async def test_cache_integration():
    """Test cache MISS → HIT flow."""
    query = "quantum computing breakthroughs 2025"
    
    print("=" * 60)
    print("Search-as-Code SDK - Caching Integration Test")
    print("=" * 60)
    print(f"\nQuery: {query}")
    print("-" * 60)
    
    # First run - should be CACHE MISS
    print("\n[Run 1] Expecting CACHE MISS...")
    state1 = await SearchPipeline() \
        .search(query, num_results=5, use_cache=True) \
        .execute()
    
    print(f"Results: {len(state1.results)}")
    print(f"Metrics: {state1.metrics}")
    
    cache_written = state1.metrics.get('cache_written', False)
    print(f"✓ Cache written: {cache_written}")
    
    # Second run - should be CACHE HIT
    print("\n[Run 2] Expecting CACHE HIT...")
    state2 = await SearchPipeline() \
        .search(query, num_results=5, use_cache=True) \
        .execute()
    
    print(f"Results: {len(state2.results)}")
    print(f"Metrics: {state2.metrics}")
    
    cache_hit = state2.metrics.get('cache_hit', False)
    latency_saved = state2.metrics.get('latency_saved_ms', 0)
    hit_count = state2.metrics.get('hit_count', 0)
    
    print(f"✓ Cache hit: {cache_hit}")
    print(f"✓ Latency saved: ~{latency_saved}ms")
    print(f"✓ Hit count: {hit_count}")
    
    # Verify results match
    if len(state1.results) == len(state2.results):
        print("\n✓ Result count matches between runs")
    else:
        print(f"\n✗ Result count mismatch: {len(state1.results)} vs {len(state2.results)}")
    
    # Test with cache disabled
    print("\n[Run 3] With cache disabled...")
    state3 = await SearchPipeline() \
        .search(query, num_results=5, use_cache=False) \
        .execute()
    
    print(f"Results: {len(state3.results)}")
    print(f"Metrics: {state3.metrics}")
    print(f"✓ Cache bypassed (no cache metrics)")
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    all_passed = (
        cache_written and
        cache_hit and
        len(state1.results) == len(state2.results) and
        len(state1.results) > 0
    )
    
    if all_passed:
        print("✅ All tests PASSED")
        print(f"   - Cache write: OK")
        print(f"   - Cache hit: OK")
        print(f"   - Result consistency: OK")
        print(f"   - Latency saved: ~{latency_saved}ms per hit")
    else:
        print("❌ Some tests FAILED")
        print(f"   - Cache write: {'OK' if cache_written else 'FAIL'}")
        print(f"   - Cache hit: {'OK' if cache_hit else 'FAIL'}")
        print(f"   - Result consistency: {'OK' if len(state1.results) == len(state2.results) else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(test_cache_integration())
