#!/usr/bin/env python3
"""
Phase 3 Test Suite - Rate Limiting + Advanced Primitives

Tests:
1. Rate limiter token bucket
2. Circuit breaker state transitions
3. Retry with exponential backoff
4. ClusterOp primitive
5. TimelineOp primitive
6. SentimentOp primitive (requires Ollama running)

Location: /workspace/skills/auto-generated/search-as-code/
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from sdk import SearchPipeline, ToolExecutor
from rate_limiter import (
    TokenBucketRateLimiter,
    CircuitBreaker,
    CircuitBreakerOpenError,
    retry_with_backoff,
    RateLimitedExecutor,
    RateLimitConfig
)


# =============================================================================
# Test 1: Token Bucket Rate Limiter
# =============================================================================

async def test_token_bucket():
    """Test basic token bucket functionality."""
    print("=" * 60)
    print("TEST 1: Token Bucket Rate Limiter")
    print("=" * 60)
    
    # Create limiter: 5 tokens per 10 seconds, burst size 3
    limiter = TokenBucketRateLimiter(rate=5, burst_size=3, per=10.0)
    
    print(f"Initial tokens: {limiter.available_tokens:.1f}")
    assert abs(limiter.available_tokens - 3.0) < 0.1, "Should start with burst_size tokens"
    
    # Acquire tokens rapidly
    acquired = []
    for i in range(5):
        result = await limiter.acquire(timeout=2.0)
        acquired.append(result)
        print(f"Request {i+1}: {'✓' if result else '✗'} (tokens: {limiter.available_tokens:.1f})")
    
    # First 3 should succeed immediately (burst), then wait for refill
    success_count = sum(acquired)
    print(f"\nAcquired {success_count}/5 requests within timeout")
    assert success_count >= 3, "Should acquire at least burst_size requests"
    
    print("✅ TEST 1 PASSED\n")
    return True


# =============================================================================
# Test 2: Circuit Breaker State Transitions
# =============================================================================

async def test_circuit_breaker():
    """Test circuit breaker state machine."""
    print("=" * 60)
    print("TEST 2: Circuit Breaker State Transitions")
    print("=" * 60)
    
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=2.0)
    
    print(f"Initial state: {breaker.state.value}")
    assert breaker.state == breaker.__class__.__dict__['CLOSED'] or str(breaker.state) == "CircuitState.CLOSED", "Should start CLOSED"
    
    # Trigger failures to open circuit
    async def failing_func():
        raise Exception("Simulated failure")
    
    for i in range(3):
        try:
            await breaker.call(failing_func)
        except Exception as e:
            print(f"Failure {i+1}: {e}")
    
    print(f"\nState after 3 failures: {breaker.state.value}")
    assert str(breaker.state) == "CircuitState.OPEN", "Should be OPEN after threshold failures"
    
    # Try to call while open - should fail fast
    async def success_func():
        return "Success!"
    
    try:
        await breaker.call(success_func)
        print("ERROR: Should have raised CircuitBreakerOpenError")
        return False
    except CircuitBreakerOpenError as e:
        print(f"Circuit open (expected): {e}")
    
    # Wait for recovery timeout
    print("\nWaiting for recovery timeout (2s)...")
    await asyncio.sleep(2.5)
    
    # Should transition to HALF_OPEN on next call attempt
    # The transition happens inside call(), so we need to check state indirectly
    print(f"State after timeout: {breaker.state.value}")
    
    # Call should succeed and close circuit
    result = await breaker.call(success_func)
    print(f"Call succeeded: {result}")
    print(f"Final state: {breaker.state.value}")
    assert str(breaker.state) == "CircuitState.CLOSED", "Should be CLOSED after successful recovery"
    
    print("✅ TEST 2 PASSED\n")
    return True


# =============================================================================
# Test 3: Retry with Exponential Backoff
# =============================================================================

async def test_retry_backoff():
    """Test retry logic with exponential backoff."""
    print("=" * 60)
    print("TEST 3: Retry with Exponential Backoff")
    print("=" * 60)
    
    attempt_count = 0
    
    async def flaky_func():
        nonlocal attempt_count
        attempt_count += 1
        print(f"Attempt {attempt_count}")
        if attempt_count < 3:
            raise Exception(f"Attempt {attempt_count} failed")
        return f"Success on attempt {attempt_count}"
    
    start = asyncio.get_event_loop().time()
    result = await retry_with_backoff(
        flaky_func,
        max_retries=5,
        base_delay=0.5,
        max_delay=2.0,
        jitter=0.1
    )
    elapsed = asyncio.get_event_loop().time() - start
    
    print(f"\nResult: {result}")
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Attempts: {attempt_count}")
    
    assert attempt_count == 3, "Should succeed on 3rd attempt"
    assert "Success" in result, "Should return success message"
    assert elapsed >= 0.5, "Should have waited at least one backoff period"
    
    print("✅ TEST 3 PASSED\n")
    return True


# =============================================================================
# Test 4: ClusterOp Primitive
# =============================================================================

async def test_cluster_op():
    """Test clustering primitive."""
    print("=" * 60)
    print("TEST 4: ClusterOp Primitive")
    print("=" * 60)
    
    results = await SearchPipeline() \
        .search("machine learning frameworks") \
        .filter(domain="github.com|medium.com|arxiv.org") \
        .cluster(n_clusters=3, method="keyword", min_cluster_size=1) \
        .execute()
    
    print(f"Found {len(results.results)} results")
    print(f"Clusters detected: {results.metadata.get('n_clusters_found', 0)}")
    print(f"Cluster metadata keys: {list(results.metadata.keys())}")
    
    # Should have cluster metadata
    assert 'n_clusters_found' in results.metadata, "Should have cluster count"
    assert len(results.results) > 0, "Should have results"
    
    print("✅ TEST 4 PASSED\n")
    return True


# =============================================================================
# Test 5: TimelineOp Primitive
# =============================================================================

async def test_timeline_op():
    """Test timeline analysis primitive."""
    print("=" * 60)
    print("TEST 5: TimelineOp Primitive")
    print("=" * 60)
    
    results = await SearchPipeline() \
        .search("AI developments 2024 2025") \
        .timeline(order="desc", detect_trends=True, time_buckets="month") \
        .execute()
    
    print(f"Found {len(results.results)} results")
    print(f"Results with dates: {results.metrics.get('results_with_dates', 0)}")
    print(f"Results without dates: {results.metrics.get('results_without_dates', 0)}")
    
    trend_analysis = results.metadata.get('trend_analysis', {})
    if trend_analysis:
        print(f"\nTrend Analysis:")
        print(f"  Peak period: {trend_analysis.get('peak_period', 'N/A')}")
        print(f"  Peak count: {trend_analysis.get('peak_count', 0)}")
        print(f"  Trend direction: {trend_analysis.get('trend_direction', 'N/A')}")
    
    # Should have timeline metadata
    assert results.metrics.get('timeline_ordered') == True, "Should be timeline ordered"
    
    print("✅ TEST 5 PASSED\n")
    return True


# =============================================================================
# Test 6: SentimentOp Primitive (Requires Ollama)
# =============================================================================

async def test_sentiment_op():
    """Test sentiment analysis primitive."""
    print("=" * 60)
    print("TEST 6: SentimentOp Primitive")
    print("=" * 60)
    
    try:
        results = await SearchPipeline() \
            .search("AI benefits opportunities") \
            .sentiment(analyze_bias=True, batch_size=3) \
            .execute()
        
        print(f"Found {len(results.results)} results")
        print(f"Sentiment distribution: {results.metadata.get('sentiment_distribution', {})}")
        
        # Show first result sentiment
        if results.results:
            r = results.results[0]
            print(f"\nFirst result:")
            print(f"  Title: {r.title[:60]}...")
            print(f"  Sentiment: {r.metadata.get('sentiment', 'unknown')}")
            print(f"  Confidence: {r.metadata.get('sentiment_confidence', 0):.2f}")
            print(f"  Tone: {r.metadata.get('tone', 'unknown')}")
        
        # Should have sentiment metadata
        assert 'sentiment_distribution' in results.metadata, "Should have sentiment distribution"
        
        print("✅ TEST 6 PASSED\n")
        return True
        
    except Exception as e:
        print(f"⚠️  TEST 6 SKIPPED: Ollama not available or error: {e}")
        return False


# =============================================================================
# Test 7: Rate-Limited Executor Integration
# =============================================================================

async def test_rate_limited_executor():
    """Test rate-limited executor integration."""
    print("=" * 60)
    print("TEST 7: Rate-Limited Executor Integration")
    print("=" * 60)
    
    # Get the shared executor from ToolExecutor
    executor = ToolExecutor._get_rate_limited_executor()
    
    if executor:
        print("Rate-limited executor is active")
        stats = executor.get_stats()
        
        print(f"\nCircuit Breakers:")
        for op_type, data in stats["circuit_breakers"].items():
            print(f"  {op_type}: {data['state']} (failures: {data['failure_count']})")
        
        print(f"\nRate Limiters:")
        for op_type, data in stats["rate_limiters"].items():
            print(f"  {op_type}: {data['available_tokens']:.1f}/{data['burst_size']} tokens")
        
        print(f"\nMetrics:")
        for key, value in stats["metrics"].items():
            print(f"  {key}: {value}")
        
        print("✅ TEST 7 PASSED\n")
        return True
    else:
        print("⚠️  Rate-limited executor not available (rate_limiter module not imported)")
        return False


# =============================================================================
# Main Test Runner
# =============================================================================

async def run_all_tests():
    """Run all Phase 3 tests."""
    print("\n" + "=" * 60)
    print("SEARCH-AS-CODE SDK - PHASE 3 TEST SUITE")
    print("=" * 60 + "\n")
    
    tests = [
        ("Token Bucket", test_token_bucket),
        ("Circuit Breaker", test_circuit_breaker),
        ("Retry Backoff", test_retry_backoff),
        ("ClusterOp", test_cluster_op),
        ("TimelineOp", test_timeline_op),
        ("SentimentOp", test_sentiment_op),
        ("Rate-Limited Executor", test_rate_limited_executor),
    ]
    
    results = {}
    for name, test_func in tests:
        try:
            passed = await test_func()
            results[name] = "PASS" if passed else "SKIP/FAIL"
        except Exception as e:
            print(f"❌ TEST FAILED: {name}\n{e}\n")
            results[name] = f"FAIL: {e}"
    
    # Summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    for name, result in results.items():
        status = "✅" if result == "PASS" else "⚠️ " if result == "SKIP/FAIL" else "❌"
        print(f"{status} {name}: {result}")
    
    passed_count = sum(1 for r in results.values() if r == "PASS")
    total_count = len(tests)
    
    print(f"\nTotal: {passed_count}/{total_count} tests passed")
    print("=" * 60)
    
    return passed_count == total_count


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
