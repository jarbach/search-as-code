#!/usr/bin/env python3
"""
Basic Search Examples - Search-as-Code SDK

Demonstrates fundamental search pipeline patterns.
"""

import asyncio
import sys
from pathlib import Path

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sdk import SearchPipeline, SearchResult


async def example_1_simple_search():
    """Simple search with basic filtering."""
    print("\n=== Example 1: Simple Search ===\n")
    
    results = await SearchPipeline() \
        .search("Python async best practices 2025") \
        .filter(domain="realpython.com") \
        .compress(target_tokens=400) \
        .execute()
    
    print(f"Found {len(results.results)} results")
    print(f"Metrics: {results.metrics}")
    
    for r in results.results[:3]:
        print(f"\n[{r.rank}] {r.title}")
        print(f"    {r.url}")
        print(f"    {r.snippet[:150]}...")


async def example_2_multi_domain():
    """Search across multiple trusted domains."""
    print("\n=== Example 2: Multi-Domain Filter ===\n")
    
    # Search then filter to multiple domains
    results = await SearchPipeline() \
        .search("LLM context window techniques") \
        .filter(domain="arxiv.org") \
        .dedupe(by="url") \
        .execute()
    
    arxiv_count = len(results.results)
    
    # Now search ACL anthology
    results2 = await SearchPipeline() \
        .search("LLM context window techniques") \
        .filter(domain="aclanthology.org") \
        .dedupe(by="url") \
        .execute()
    
    print(f"ArXiv results: {arxiv_count}")
    print(f"ACL Anthology results: {len(results2.results)}")


async def example_3_fanout_dedupe():
    """Fan-out search with automatic deduplication."""
    print("\n=== Example 3: Fan-out + Dedupe ===\n")
    
    results = await SearchPipeline() \
        .fanout(
            variants=["2024", "2025", "2026"],
            base_query="retrieval augmented generation RAG",
            parallel=True,
            dedupe_immediately=True
        ) \
        .rank_by("relevance") \
        .execute()
    
    print(f"Total unique results: {len(results.results)}")
    print(f"Fan-out metrics: {results.metrics}")
    
    # Show domain distribution
    domains = {}
    for r in results.results:
        domain = r.domain
        domains[domain] = domains.get(domain, 0) + 1
    
    print(f"\nTop domains:")
    for domain, count in sorted(domains.items(), key=lambda x: -x[1])[:5]:
        print(f"  {domain}: {count}")


async def example_4_custom_ranking():
    """Custom ranking function."""
    print("\n=== Example 4: Custom Ranking ===\n")
    
    def rank_by_title_length(result: SearchResult) -> float:
        """Prefer results with longer, more descriptive titles."""
        return len(result.title)
    
    results = await SearchPipeline() \
        .search("microservices architecture patterns") \
        .rank_by(custom_fn=rank_by_title_length) \
        .compress(target_tokens=600) \
        .execute()
    
    print(f"Results ranked by title length:")
    for r in results.results[:5]:
        print(f"  [{r.rank}] ({len(r.title)} chars) {r.title[:60]}...")


async def example_5_retry_handling():
    """Demonstrate retry logic on failures."""
    print("\n=== Example 5: Retry Handling ===\n")
    
    # This will retry up to 5 times on failure
    results = await SearchPipeline() \
        .search(
            "distributed systems consensus algorithms",
            retries=5,
            num_results=15
        ) \
        .execute()
    
    print(f"Search completed after {results.metrics.get('search_retries', 1)} attempt(s)")
    print(f"Found {len(results.results)} results")
    
    if results.errors:
        print(f"Errors encountered: {results.errors}")


async def main():
    print("Search-as-Code SDK - Basic Examples")
    print("=" * 50)
    
    examples = [
        ("Simple Search", example_1_simple_search),
        ("Multi-Domain Filter", example_2_multi_domain),
        ("Fan-out + Dedupe", example_3_fanout_dedupe),
        ("Custom Ranking", example_4_custom_ranking),
        ("Retry Handling", example_5_retry_handling),
    ]
    
    for name, func in examples:
        try:
            await func()
        except Exception as e:
            print(f"\n❌ {name} failed: {e}")
        
        await asyncio.sleep(1)  # Rate limiting
    
    print("\n" + "=" * 50)
    print("All examples completed!")


if __name__ == "__main__":
    asyncio.run(main())
