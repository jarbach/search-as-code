#!/usr/bin/env python3
"""
Smart Compression Demo - Phase 2 Feature

Compare truncate vs LLM-powered summarization strategies.

Usage:
    python examples/smart_compression.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sdk import SearchPipeline


async def compare_strategies():
    """Compare truncate vs summarize compression strategies."""
    
    print("=" * 70)
    print("LLM Smart Compression: Truncate vs Summarize Comparison")
    print("=" * 70)
    
    query = "LLM context management techniques"
    target_tokens = 300
    
    # Strategy 1: Truncate
    print(f"\n[1] TRUNCATE STRATEGY")
    print("-" * 70)
    print(f"Query: {query}")
    print(f"Target tokens: {target_tokens}")
    
    state1 = await SearchPipeline() \
        .search(query, num_results=10) \
        .compress(target_tokens=target_tokens, strategy='truncate') \
        .execute()
    
    print(f"\nResults:")
    print(f"  • Results kept: {len(state1.results)}")
    print(f"  • Compressed tokens: {state1.metrics.get('compressed_tokens', 0)}")
    print(f"  • Method: {state1.metrics.get('compression_method', 'unknown')}")
    
    if state1.results:
        print(f"\nFirst result (raw snippet):")
        snippet = state1.results[0].snippet[:200]
        for line in snippet.split('\n'):
            print(f"    {line}")
        if len(state1.results[0].snippet) > 200:
            print("    ...")
    
    # Strategy 2: Summarize
    print("\n" + "=" * 70)
    print(f"\n[2] SUMMARIZE STRATEGY (LLM-powered)")
    print("-" * 70)
    print(f"Query: {query}")
    print(f"Target tokens: {target_tokens}")
    print(f"Model: qwen2.5:7b (local)")
    
    state2 = await SearchPipeline() \
        .search(query, num_results=10) \
        .compress(target_tokens=target_tokens, strategy='summarize') \
        .execute()
    
    print(f"\nResults:")
    print(f"  • Summary batches: {len(state2.results)}")
    print(f"  • Compressed tokens: {state2.metrics.get('compressed_tokens', 0)}")
    print(f"  • Method: {state2.metrics.get('compression_method', 'unknown')}")
    print(f"  • Batches summarized: {state2.metrics.get('batches_summarized', 0)}")
    
    if state2.results:
        print(f"\nFirst summary (LLM-generated):")
        snippet = state2.results[0].snippet[:200]
        for line in snippet.split('\n'):
            print(f"    {line}")
        if len(state2.results[0].snippet) > 200:
            print("    ...")
        
        # Show metadata with original URLs
        if 'original_urls' in state2.results[0].metadata:
            print(f"\n  Original URLs in this batch:")
            for url in state2.results[0].metadata['original_urls'][:3]:
                print(f"    - {url}")
            if len(state2.results[0].metadata['original_urls']) > 3:
                print(f"    ... and {len(state2.results[0].metadata['original_urls']) - 3} more")
    
    # Comparison summary
    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"\nTruncate:  {len(state1.results)} raw results with direct snippets")
    print(f"Summarize: {len(state2.results)} synthesized summaries, LLM-generated")
    
    print("\n✓ Benefits of Summarize:")
    print("    • Removes redundancy across results")
    print("    • Synthesizes key points coherently")
    print("    • Better for high-level understanding")
    print("    • Cites sources by number [1], [2], etc.")
    
    print("\n✗ Trade-offs:")
    print("    • Loses individual result URLs (stored in metadata)")
    print("    • Slower (~3-5s per batch for local LLM)")
    print("    • May introduce summarization artifacts")
    
    print("\n✓ Use Cases:")
    print("    • Truncate: When you need exact source URLs and snippets")
    print("    • Summarize: When you need quick overview of many results")
    
    print("\n✗ Not Recommended:")
    print("    • Summarize for legal/medical research (need exact quotes)")
    print("    • Truncate when you have 50+ results (too much to scan)")


async def demo_custom_batching():
    """Demonstrate custom batch sizing for summarization."""
    
    print("\n\n" + "=" * 70)
    print("CUSTOM BATCH SIZING DEMO")
    print("=" * 70)
    
    query = "AI agent architecture patterns"
    
    print(f"\nQuery: {query}")
    print(f"Running with different batch sizes...\n")
    
    for batch_factor in [2, 3, 4]:
        state = await SearchPipeline() \
            .search(query, num_results=10) \
            .compress(target_tokens=400, strategy='summarize') \
            .execute()
        
        batches = state.metrics.get('batches_summarized', 0)
        tokens = state.metrics.get('compressed_tokens', 0)
        
        print(f"  Batch factor ~{batch_factor}: {batches} batches, {tokens} tokens")


if __name__ == "__main__":
    print("\nSearch-as-Code SDK v0.2.0 - Phase 2: LLM Smart Compression")
    print("=" * 70)
    
    asyncio.run(compare_strategies())
    asyncio.run(demo_custom_batching())
    
    print("\n" + "=" * 70)
    print("Demo complete!")
    print("=" * 70 + "\n")
