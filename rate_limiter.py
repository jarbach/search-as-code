#!/usr/bin/env python3
"""
Rate Limiter Module for Search-as-Code SDK - Phase 3

Implements:
- Token bucket rate limiting
- Exponential backoff with jitter
- Circuit breaker pattern
- Request queuing

Location: /workspace/skills/auto-generated/search-as-code/
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
from collections import defaultdict


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_RATE_LIMIT = 10  # requests per minute
DEFAULT_BURST_SIZE = 5   # max burst requests
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_BACKOFF_MAX = 60.0  # max seconds to wait
DEFAULT_JITTER = 0.1  # 10% jitter


# =============================================================================
# Enums
# =============================================================================

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rejected_requests: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None


# =============================================================================
# Rate Limiter
# =============================================================================

class TokenBucketRateLimiter:
    """
    Token bucket rate limiter with async support.
    
    Tokens are added at a constant rate up to a maximum burst size.
    Each request consumes one token. If no tokens available, wait until one is.
    """
    
    def __init__(
        self,
        rate: int = DEFAULT_RATE_LIMIT,
        burst_size: int = DEFAULT_BURST_SIZE,
        per: float = 60.0  # time window in seconds
    ):
        self.rate = rate  # tokens per window
        self.burst_size = burst_size
        self.per = per  # window size in seconds
        self.tokens = float(burst_size)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
    
    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.burst_size, self.tokens + elapsed * (self.rate / self.per))
        self.last_update = now
    
    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        """
        Acquire tokens from the bucket.
        
        Args:
            tokens: Number of tokens to acquire
            timeout: Max seconds to wait (None = wait forever)
        
        Returns:
            True if acquired, False if timeout
        """
        start = time.monotonic()
        
        while True:
            async with self._lock:
                self._refill()
                
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                
                # Calculate wait time for enough tokens
                needed = tokens - self.tokens
                wait_time = needed * (self.per / self.rate)
            
            # Check timeout
            if timeout is not None:
                elapsed = time.monotonic() - start
                if elapsed + wait_time > timeout:
                    return False
            
            # Wait for tokens to refill
            await asyncio.sleep(min(wait_time, 0.1))  # Poll every 100ms max
    
    @property
    def available_tokens(self) -> float:
        """Current number of available tokens."""
        self._refill()
        return self.tokens


# =============================================================================
# Circuit Breaker
# =============================================================================

class CircuitBreaker:
    """
    Circuit breaker pattern implementation.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service failing, reject requests immediately
    - HALF_OPEN: Testing if service recovered
    
    Transitions:
    - CLOSED → OPEN: After failure_threshold consecutive failures
    - OPEN → HALF_OPEN: After recovery_timeout seconds
    - HALF_OPEN → CLOSED: On first success
    - HALF_OPEN → OPEN: On first failure
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_calls = 0
        self._lock = asyncio.Lock()
        self.stats = CircuitBreakerStats()
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.
        
        Args:
            func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments
        
        Returns:
            Function result
        
        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: Any exception from the function
        """
        async with self._lock:
            self.stats.total_requests += 1
            
            # Check if we should transition from OPEN to HALF_OPEN
            if self.state == CircuitState.OPEN:
                if self.last_failure_time and \
                   time.monotonic() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    self.failure_count = 0
                else:
                    self.stats.rejected_requests += 1
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker is OPEN. "
                        f"Retry after {self.recovery_timeout - (time.monotonic() - self.last_failure_time):.1f}s"
                    )
            
            # Check if we've exceeded half-open calls
            if self.state == CircuitState.HALF_OPEN and \
               self.half_open_calls >= self.half_open_max_calls:
                self.stats.rejected_requests += 1
                raise CircuitBreakerOpenError(
                    "Circuit breaker is HALF_OPEN but max test calls reached"
                )
            
            if self.state == CircuitState.HALF_OPEN:
                self.half_open_calls += 1
        
        # Execute the function
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure()
            raise
    
    async def _on_success(self):
        """Handle successful call."""
        async with self._lock:
            self.stats.successful_requests += 1
            self.stats.last_success_time = time.monotonic()
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_max_calls:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0
    
    async def _on_failure(self):
        """Handle failed call."""
        async with self._lock:
            self.stats.failed_requests += 1
            self.stats.last_failure_time = time.monotonic()
            self.last_failure_time = time.monotonic()
            
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self.success_count = 0
            elif self.state == CircuitState.CLOSED:
                self.failure_count += 1
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
    
    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitState.HALF_OPEN


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and rejecting requests."""
    pass


# =============================================================================
# Retry with Exponential Backoff
# =============================================================================

async def retry_with_backoff(
    func: Callable,
    *args,
    max_retries: int = 3,
    base_delay: float = DEFAULT_BACKOFF_BASE,
    max_delay: float = DEFAULT_BACKOFF_MAX,
    jitter: float = DEFAULT_JITTER,
    exceptions: tuple = (Exception,),
    **kwargs
) -> Any:
    """
    Execute function with exponential backoff retry logic.
    
    Args:
        func: Async function to retry
        *args: Positional arguments
        max_retries: Maximum number of retries
        base_delay: Base delay in seconds (multiplied by 2^attempt)
        max_delay: Maximum delay between retries
        jitter: Random jitter factor (0-1) to add to delay
        exceptions: Tuple of exception types to catch
        **kwargs: Keyword arguments
    
    Returns:
        Function result
    
    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            
            if attempt == max_retries:
                break
            
            # Calculate delay with exponential backoff and jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            if jitter > 0:
                delay *= (1 + random.uniform(-jitter, jitter))
            
            await asyncio.sleep(delay)
    
    raise last_exception


# =============================================================================
# Rate-Limited Tool Executor Wrapper
# =============================================================================

@dataclass
class RateLimitConfig:
    """Configuration for rate-limited operations."""
    rate: int = DEFAULT_RATE_LIMIT
    burst_size: int = DEFAULT_BURST_SIZE
    per: float = 60.0
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 30.0
    max_retries: int = 3
    base_backoff: float = 2.0
    max_backoff: float = 60.0


class RateLimitedExecutor:
    """
    Wraps ToolExecutor with rate limiting, circuit breaker, and retry logic.
    
    Usage:
        executor = RateLimitedExecutor()
        success, results, error = await executor.web_search("query")
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        
        # Rate limiters per operation type
        self._rate_limiters: Dict[str, TokenBucketRateLimiter] = {
            "search": TokenBucketRateLimiter(
                rate=self.config.rate,
                burst_size=self.config.burst_size,
                per=self.config.per
            ),
            "fetch": TokenBucketRateLimiter(
                rate=self.config.rate,
                burst_size=self.config.burst_size,
                per=self.config.per
            ),
        }
        
        # Circuit breakers per operation type
        self._circuit_breakers: Dict[str, CircuitBreaker] = {
            "search": CircuitBreaker(
                failure_threshold=self.config.circuit_failure_threshold,
                recovery_timeout=self.config.circuit_recovery_timeout
            ),
            "fetch": CircuitBreaker(
                failure_threshold=self.config.circuit_failure_threshold,
                recovery_timeout=self.config.circuit_recovery_timeout
            ),
        }
        
        # Metrics
        self.metrics: Dict[str, Any] = defaultdict(int)
    
    async def web_search(
        self,
        query: str,
        num_results: int = 10,
        freshness: Optional[str] = None,
        timeout: int = 30
    ) -> tuple[bool, List[Dict], str]:
        """Execute rate-limited web_search."""
        return await self._execute_with_rate_limit(
            "search",
            self._do_web_search,
            query=query,
            num_results=num_results,
            freshness=freshness,
            timeout=timeout
        )
    
    async def web_fetch(
        self,
        url: str,
        max_chars: int = 50000,
        timeout: int = 60
    ) -> tuple[bool, str, str]:
        """Execute rate-limited web_fetch."""
        return await self._execute_with_rate_limit(
            "fetch",
            self._do_web_fetch,
            url=url,
            max_chars=max_chars,
            timeout=timeout
        )
    
    async def _execute_with_rate_limit(
        self,
        op_type: str,
        func: Callable,
        **kwargs
    ) -> tuple[bool, Any, str]:
        """
        Execute operation with rate limiting, circuit breaker, and retries.
        
        Flow:
        1. Check circuit breaker (fail fast if open)
        2. Acquire rate limit token (wait if necessary)
        3. Execute with retry + exponential backoff
        4. Update circuit breaker state
        """
        rate_limiter = self._rate_limiters[op_type]
        circuit_breaker = self._circuit_breakers[op_type]
        
        try:
            # Acquire rate limit token
            await rate_limiter.acquire(timeout=30.0)
            self.metrics[f"{op_type}_rate_limited"] += 1
            
            # Execute with circuit breaker and retries
            async def wrapped_call():
                return await func(**kwargs)
            
            result = await retry_with_backoff(
                wrapped_call,
                max_retries=self.config.max_retries,
                base_delay=self.config.base_backoff,
                max_delay=self.config.max_backoff
            )
            
            # Record success
            self.metrics[f"{op_type}_success"] += 1
            return result
            
        except CircuitBreakerOpenError as e:
            self.metrics[f"{op_type}_circuit_rejected"] += 1
            return False, None, str(e)
        except Exception as e:
            self.metrics[f"{op_type}_error"] += 1
            return False, None, str(e)
    
    async def _do_web_search(
        self,
        query: str,
        num_results: int,
        freshness: Optional[str],
        timeout: int
    ) -> tuple[bool, List[Dict], str]:
        """Actual web_search execution (called by rate limiter)."""
        # Import here to avoid circular dependency
        from sdk import ToolExecutor
        return await ToolExecutor.web_search(query, num_results, freshness)
    
    async def _do_web_fetch(
        self,
        url: str,
        max_chars: int,
        timeout: int
    ) -> tuple[bool, str, str]:
        """Actual web_fetch execution (called by rate limiter)."""
        from sdk import ToolExecutor
        return await ToolExecutor.web_fetch(url, max_chars)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiting and circuit breaker statistics."""
        stats = {
            "metrics": dict(self.metrics),
            "rate_limiters": {
                name: {
                    "available_tokens": limiter.available_tokens,
                    "rate": limiter.rate,
                    "burst_size": limiter.burst_size
                }
                for name, limiter in self._rate_limiters.items()
            },
            "circuit_breakers": {
                name: {
                    "state": breaker.state.value,
                    "failure_count": breaker.failure_count,
                    "stats": {
                        "total": breaker.stats.total_requests,
                        "success": breaker.stats.successful_requests,
                        "failed": breaker.stats.failed_requests,
                        "rejected": breaker.stats.rejected_requests
                    }
                }
                for name, breaker in self._circuit_breakers.items()
            }
        }
        return stats


# =============================================================================
# Example Usage
# =============================================================================

async def demo():
    """Demonstrate rate limiting features."""
    print("=== Rate Limiter Demo ===\n")
    
    # Test token bucket
    limiter = TokenBucketRateLimiter(rate=5, burst_size=3, per=10.0)
    print(f"Initial tokens: {limiter.available_tokens:.1f}")
    
    for i in range(5):
        acquired = await limiter.acquire(timeout=5.0)
        print(f"Request {i+1}: {'✓' if acquired else '✗'} (tokens: {limiter.available_tokens:.1f})")
    
    print("\n=== Circuit Breaker Demo ===\n")
    
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=5.0)
    
    async def failing_func():
        raise Exception("Simulated failure")
    
    async def success_func():
        return "Success!"
    
    # Trigger failures
    for i in range(3):
        try:
            await breaker.call(failing_func)
        except Exception as e:
            print(f"Failure {i+1}: {e}")
    
    print(f"\nCircuit state: {breaker.state.value}")
    
    # Try to call while open
    try:
        await breaker.call(success_func)
    except CircuitBreakerOpenError as e:
        print(f"Circuit open: {e}")
    
    print("\n=== Retry with Backoff Demo ===\n")
    
    attempt_count = 0
    
    async def flaky_func():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise Exception(f"Attempt {attempt_count} failed")
        return f"Success on attempt {attempt_count}"
    
    result = await retry_with_backoff(flaky_func, max_retries=5)
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(demo())
