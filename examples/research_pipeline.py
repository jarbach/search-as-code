#!/usr/bin/env python3
"""
Research Pipeline Examples - Search-as-Code SDK

Demonstrates pre-built research workflows for academic and technical literature review.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sdk import ResearchPipeline, SearchPipeline, PipelineState


async def example_1_basic_research():
    """Basic research pipeline for a technical topic."""
    print("\n=== Example 1: Basic Research Pipeline ===\n")
    
    pipeline = ResearchPipeline(
        topic="LLM context management techniques",
        sources=["arxiv.org", "aclanthology.org"],
        time_range="year:2025",
        max_results=30
    )
    
    results = await pipeline.run()
    
    print(f"📚 Research Results")
    print(f"   Found: {len(results.results)} relevant papers")
    print(f"   Metrics: {results.metrics}")
    
    if results.errors:
        print(f"   Errors: {results.errors}")
    
    print(f"\nTop 5 results:")
    for r in results.results[:5]:
        print(f"\n  [{r.rank}] {r.title}")
        print(f"      {r.url}")


async def example_2_custom_sources():
    """Research with custom source list."""
    print("\n=== Example 2: Custom Sources ===\n")
    
    # Focus on specific venues
    custom_sources = [
        "arxiv.org/cs.AI",
        "arxiv.org/cs.LG",
        "proceedings.mlr.press",  # ICML, COLT, etc.
        "openreview.net"  # NeurIPS, ICLR reviews
    ]
    
    pipeline = ResearchPipeline(
        topic="mixture of experts MoE transformer",
        sources=custom_sources,
        time_range="year:2024",
        max_results=40
    )
    
    results = await pipeline.run()
    
    print(f"Found {len(results.results)} papers on MoE transformers")
    
    # Analyze venue distribution
    venues = {}
    for r in results.results:
        # Extract venue from URL
        if "arxiv.org" in r.url:
            venue = "arXiv"
        elif "proceedings.mlr.press" in r.url:
            venue = "PMLR"
        elif "openreview.net" in r.url:
            venue = "OpenReview"
        else:
            venue = "Other"
        
        venues[venue] = venues.get(venue, 0) + 1
    
    print(f"\nVenue distribution:")
    for venue, count in sorted(venues.items(), key=lambda x: -x[1]):
        print(f"  {venue}: {count} papers")


async def example_3_state_persistence():
    """Save and load research state across turns."""
    print("\n=== Example 3: State Persistence ===\n")
    
    import hashlib
    session_id = hashlib.md5(b"research_session_1").hexdigest()[:8]
    
    # Turn 1: Initial search and save
    print("Turn 1: Running initial search...")
    state = await SearchPipeline(session_id=session_id) \
        .search("attention mechanism improvements") \
        .filter(domain="arxiv.org") \
        .fetch(max_concurrent=3) \
        .execute()
    
    state_file = state.save_state("attention_research")
    print(f"Saved state to: {state_file}")
    print(f"Fetched {len(state.fetched_content)} pages")
    
    # Simulate turn 2: Load and process
    print("\nTurn 2: Loading saved state...")
    loaded_state = PipelineState.load_state("attention_research", session_id=session_id)
    print(f"Loaded {len(loaded_state.fetched_content)} fetched pages")
    print(f"Original query: {loaded_state.query}")
    
    # Continue processing (in real scenario, this would be in a new session)
    # state = await SearchPipeline(session_id=session_id) \
    #     .extract({"title": "h1", "abstract": ".abstract"}) \
    #     .execute(loaded_state)


async def example_4_iterative_research():
    """Iterative research with refinement."""
    print("\n=== Example 4: Iterative Research ===\n")
    
    # First pass: Broad search
    print("Pass 1: Broad search...")
    results1 = await SearchPipeline() \
        .search("graph neural networks GNN") \
        .compress(target_tokens=300) \
        .execute()
    
    print(f"  Found {len(results1.results)} initial results")
    
    # Extract key terms from results for refinement
    # (In practice, an LLM would analyze these)
    refined_terms = ["temporal", "dynamic", "evolving"]
    
    # Second pass: Refined search
    print("\nPass 2: Refined search with extracted terms...")
    results2 = await SearchPipeline() \
        .fanout(refined_terms, base_query="graph neural networks GNN") \
        .dedupe(by="url") \
        .filter(domain="arxiv.org") \
        .execute()
    
    print(f"  Found {len(results2.results)} refined results")
    
    # Compare
    print(f"\nComparison:")
    print(f"  Broad search: {len(results1.results)} results")
    print(f"  Refined search: {len(results2.results)} results")


async def example_5_competitive_tech_landscape():
    """Map the technical landscape for a technology."""
    print("\n=== Example 5: Technology Landscape ===\n")
    
    # Search for different aspects of a technology
    aspects = [
        "benchmark performance",
        "scalability challenges",
        "production deployment",
        "cost optimization",
        "security considerations"
    ]
    
    results = await SearchPipeline() \
        .fanout(aspects, base_query="LLM inference optimization", parallel=True) \
        .dedupe(by="domain") \
        .rank_by("relevance") \
        .compress(target_tokens=800) \
        .execute()
    
    print(f"Technology landscape analysis:")
    print(f"  Total unique domains: {results.metrics.get('deduped_count', 'N/A')}")
    print(f"  Compressed to: {results.metrics.get('compressed_tokens', 0)} tokens")
    
    # Group by aspect (based on which fanout variant matched)
    aspect_results = {aspect: [] for aspect in aspects}
    for r in results.results:
        for aspect in aspects:
            if aspect.lower() in r.snippet.lower():
                aspect_results[aspect].append(r)
                break
    
    print(f"\nResults by aspect:")
    for aspect, items in aspect_results.items():
        if items:
            print(f"  {aspect}: {len(items)} results")


async def main():
    print("Search-as-Code SDK - Research Pipeline Examples")
    print("=" * 60)
    
    examples = [
        ("Basic Research", example_1_basic_research),
        ("Custom Sources", example_2_custom_sources),
        ("State Persistence", example_3_state_persistence),
        ("Iterative Research", example_4_iterative_research),
        ("Tech Landscape", example_5_competitive_tech_landscape),
    ]
    
    for name, func in examples:
        try:
            await func()
        except Exception as e:
            print(f"\n❌ {name} failed: {e}")
            import traceback
            traceback.print_exc()
        
        await asyncio.sleep(2)  # Rate limiting between searches
    
    print("\n" + "=" * 60)
    print("All research examples completed!")


if __name__ == "__main__":
    asyncio.run(main())
