#!/usr/bin/env python3
"""
Cross-Session Caching Demo - Phase 2 Feature #2

Demonstrates cache HIT/MISS behavior and performance benefits.

Usage:
    python examples/caching_demo.py
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sdk import SearchPipeline


async def demo_cache_behavior():
    """Demonstrate cache MISS then HIT."""
    
    print("=" * 70)
    print("CROSS-SESSION CACHING DEMO")
    print("=" * 70)
    
    query = "AI agent memory systems"
    
    # First run: Cache MISS
    print(f"\n[RUN 1] Cache MISS (first time)")
    print("-" * 70)
    print(f"Query: {query}")
    start = datetime.now()
    
    state1 = await SearchPipeline() \
        .search(query, num_results=8) \
        .cache(enabled=True, ttl_seconds=3600) \
        .execute()
    
    elapsed1 = (datetime.now() - start).total_seconds() * 1000
    
    print(f"Results: {len(state1.results)}")
    print(f"Time: {elapsed1:.0f}ms")
    print(f"Cache hit: {state1.metrics.get('cache_hit', False)}")
    if 'cache_key' in state1.metrics:
        print(f"Cache key: {state1.metrics['cache_key']}")
    
    # Second run: Cache HIT
    print(f"\n[RUN 2] Cache HIT (same query)")
    print("-" * 70)
    print(f"Query: {query}")
    start = datetime.now()
    
    state2 = await SearchPipeline() \
        .search(query, num_results=8) \
        .cache(enabled=True, ttl_seconds=3600) \
        .execute()
    
    elapsed2 = (datetime.now() - start).total_seconds() * 1000
    
    print(f"Results: {len(state2.results)}")
    print(f"Time: {elapsed2:.0f}ms")
    print(f"Cache hit: {state2.metrics.get('cache_hit', False)}")
    if state2.metrics.get('cache_hit'):
        print(f"Latency saved: ~{state2.metrics.get('latency_saved_ms', 0)}ms")
        print(f"Hit count: {state2.metrics.get('hit_count', 1)}")
    
    # Performance comparison
    print("\n" + "=" * 70)
    print("PERFORMANCE COMPARISON")
    print("=" * 70)
    print(f"Run 1 (MISS): {elapsed1:.0f}ms - Fetched from API")
    print(f"Run 2 (HIT):  {elapsed2:.0f}ms - Retrieved from cache")
    print(f"Speedup:      {elapsed1/elapsed2:.1f}x faster")
    print(f"Time saved:   {elapsed1 - elapsed2:.0f}ms ({(elapsed1-elapsed2)/elapsed1*100:.1f}%)")


async def demo_cache_stats():
    """Show cache statistics."""
    
    from cache import CacheManager
    
    print("\n\n" + "=" * 70)
    print("CACHE STATISTICS")
    print("=" * 70)
    
    manager = CacheManager()
    stats = await manager.stats()
    
    print(f"Total entries:   {stats['total_entries']}")
    print(f"Total hits:      {stats['total_hits']}")
    print(f"Total misses:    {stats['total_misses']}")
    print(f"Hit rate:        {stats['hit_rate']:.1%}")
    print(f"Storage used:    {stats['storage_bytes'] / 1024:.1f} KB")
    print(f"Expired entries: {stats['expired_entries']}")
    if stats['oldest_entry']:
        print(f"Oldest entry:    {stats['oldest_entry']}")
    if stats['newest_entry']:
        print(f"Newest entry:    {stats['newest_entry']}")


async def demo_cache_management():
    """Demonstrate cache management commands."""
    
    from cache import CacheManager
    
    print("\n\n" + "=" * 70)
    print("CACHE MANAGEMENT")
    print("=" * 70)
    
    manager = CacheManager()
    
    # Show current stats
    print("\n[Before cleanup]")
    stats = await manager.stats()
    print(f"Entries: {stats['total_entries']}, Expired: {stats['expired_entries']}")
    
    # Cleanup expired entries
    print("\n[Running cleanup...]")
    result = await manager.cleanup()
    print(f"Removed {result['deleted_expired']} expired entries")
    
    # Show updated stats
    print("\n[After cleanup]")
    stats = await manager.stats()
    print(f"Entries: {stats['total_entries']}, Expired: {stats['expired_entries']}")
    
    # Clear all cache (optional)
    # print("\n[Clearing all cache...]")
    # result = await manager.clear()
    # print(f"Cleared {result['deleted_count']} entries")


if __name__ == "__main__":
    print("\nSearch-as-Code SDK v0.3.0 - Phase 2: Cross-Session Caching")
    print("=" * 70)
    
    asyncio.run(demo_cache_behavior())
    asyncio.run(demo_cache_stats())
    asyncio.run(demo_cache_management())
    
    print("\n" + "=" * 70)
    print("Demo complete!")
    print("=" * 70 + "\n")
