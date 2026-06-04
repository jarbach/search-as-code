#!/usr/bin/env python3
"""
Metrics Collection for Search-as-Code SDK - Phase 4

Comprehensive metrics tracking for performance, costs, and usage patterns.

Location: /workspace/skills/auto-generated/search-as-code/
"""

import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Literal


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_DB_PATH = Path.home() / ".search-as-code" / "cache.db"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_SAMPLE_RATE = 1.0  # Log all operations


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class MetricEntry:
    """A single metric entry."""
    operation: str
    sub_operation: Optional[str]
    duration_ms: float
    success: bool
    error_message: Optional[str]
    metadata: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass
class MetricsSummary:
    """Summary statistics for a time period."""
    period_hours: int
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    rate_limited_requests: int = 0
    circuit_breaker_trips: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    
    @property
    def success_rate(self) -> float:
        return self.successful_operations / self.total_operations if self.total_operations > 0 else 0.0
    
    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0


# =============================================================================
# Metrics Collector
# =============================================================================

class MetricsCollector:
    """
    Collects and stores SDK operation metrics.
    
    Features:
    - Context manager for automatic timing
    - Decorator for function wrapping
    - SQLite backend for persistence
    - Configurable sampling rate
    - TTL-based retention
    
    Usage:
        collector = MetricsCollector()
        
        # Context manager
        async with collector.track("search", sub_op="web_search"):
            result = await perform_search()
        
        # Manual tracking
        collector.record(MetricEntry(...))
        
        # Get summary
        summary = collector.summary(hours=24)
    """
    
    # Cost estimates (USD per 1K operations)
    COST_ESTIMATES = {
        "search": 0.005,      # Tavily/Serper API
        "fetch": 0.001,       # Web fetch
        "summarize": 0.02,    # LLM tokens (approximate)
        "sentiment": 0.01,    # LLM inference
        "embed": 0.0,         # Local embedding (compute cost only)
        "cluster": 0.0,       # Local computation
    }
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
        enabled: bool = True
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.retention_days = retention_days
        self.sample_rate = sample_rate
        self.enabled = enabled
        
        self._lock = asyncio.Lock()
        
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics_store (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    operation TEXT NOT NULL,
                    sub_operation TEXT,
                    duration_ms REAL NOT NULL,
                    success INTEGER NOT NULL,
                    error_message TEXT,
                    metadata TEXT,
                    session_id TEXT
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_timestamp 
                ON metrics_store(timestamp)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_operation 
                ON metrics_store(operation)
            """)
    
    @asynccontextmanager
    async def track(
        self,
        operation: str,
        sub_operation: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        """
        Context manager for tracking operation metrics.
        
        Args:
            operation: Main operation type (search/cluster/sentiment/etc.)
            sub_operation: Optional sub-operation (embed/cache_lookup/etc.)
            session_id: Optional session identifier
        
        Yields:
            TrackContext object to set metadata and success status
        """
        import random
        
        # Sample rate check
        if self.enabled and random.random() > self.sample_rate:
            yield None
            return
        
        start_time = time.perf_counter()
        error_message = None
        metadata = {}
        success = True
        
        try:
            yield TrackContext(metadata)
        except Exception as e:
            success = False
            error_message = str(e)
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            await self._record(
                MetricEntry(
                    operation=operation,
                    sub_operation=sub_operation,
                    duration_ms=duration_ms,
                    success=success,
                    error_message=error_message,
                    metadata=metadata if metadata else None,
                    session_id=session_id
                )
            )
    
    async def _record(self, entry: MetricEntry):
        """Record a metric entry to the database."""
        async with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO metrics_store
                    (operation, sub_operation, duration_ms, success, error_message, metadata, session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry.operation,
                    entry.sub_operation,
                    entry.duration_ms,
                    1 if entry.success else 0,
                    entry.error_message,
                    json.dumps(entry.metadata) if entry.metadata else None,
                    entry.session_id
                ))
                conn.commit()
    
    def record(self, entry: MetricEntry):
        """Synchronously record a metric entry."""
        import threading
        
        def sync_record():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._record(entry))
            finally:
                loop.close()
        
        thread = threading.Thread(target=sync_record)
        thread.start()
    
    async def get_summary(self, hours: int = 24) -> MetricsSummary:
        """
        Get summary statistics for a time period.
        
        Args:
            hours: Number of hours to summarize
        
        Returns:
            MetricsSummary with aggregated statistics
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        
        with self._get_connection() as conn:
            # Basic counts
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed
                FROM metrics_store
                WHERE timestamp >= ?
            """, (cutoff.isoformat(),))
            
            row = cursor.fetchone()
            summary = MetricsSummary(period_hours=hours)
            summary.total_operations = row['total'] or 0
            summary.successful_operations = row['successful'] or 0
            summary.failed_operations = row['failed'] or 0
            
            # Latency percentiles
            cursor = conn.execute("""
                SELECT duration_ms FROM metrics_store
                WHERE timestamp >= ? AND duration_ms IS NOT NULL
                ORDER BY duration_ms
            """, (cutoff.isoformat(),))
            
            durations = [r['duration_ms'] for r in cursor.fetchall()]
            
            if durations:
                summary.avg_latency_ms = sum(durations) / len(durations)
                summary.p50_latency_ms = self._percentile(durations, 50)
                summary.p95_latency_ms = self._percentile(durations, 95)
                summary.p99_latency_ms = self._percentile(durations, 99)
            
            # Cache hits/misses
            cursor = conn.execute("""
                SELECT 
                    SUM(CASE WHEN sub_operation = 'cache_hit' THEN 1 ELSE 0 END) as hits,
                    SUM(CASE WHEN sub_operation = 'cache_miss' THEN 1 ELSE 0 END) as misses
                FROM metrics_store
                WHERE timestamp >= ? AND sub_operation IN ('cache_hit', 'cache_miss')
            """, (cutoff.isoformat(),))
            
            row = cursor.fetchone()
            summary.cache_hits = row['hits'] or 0
            summary.cache_misses = row['misses'] or 0
            
            # Rate limiting
            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM metrics_store
                WHERE timestamp >= ? AND sub_operation = 'rate_limited'
            """, (cutoff.isoformat(),))
            
            summary.rate_limited_requests = cursor.fetchone()['count'] or 0
            
            # Circuit breaker
            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM metrics_store
                WHERE timestamp >= ? AND sub_operation = 'circuit_open'
            """, (cutoff.isoformat(),))
            
            summary.circuit_breaker_trips = cursor.fetchone()['count'] or 0
            
            # Token usage (from metadata)
            cursor = conn.execute("""
                SELECT metadata FROM metrics_store
                WHERE timestamp >= ? AND metadata IS NOT NULL
            """, (cutoff.isoformat(),))
            
            total_tokens = 0
            for row in cursor.fetchall():
                try:
                    metadata = json.loads(row['metadata'])
                    total_tokens += metadata.get('tokens', 0)
                except (json.JSONDecodeError, TypeError):
                    pass
            
            summary.total_tokens = total_tokens
            
            # Cost estimation
            cursor = conn.execute("""
                SELECT operation, COUNT(*) as count
                FROM metrics_store
                WHERE timestamp >= ?
                GROUP BY operation
            """, (cutoff.isoformat(),))
            
            total_cost = 0.0
            for row in cursor.fetchall():
                op = row['operation']
                count = row['count']
                cost_per_k = self.COST_ESTIMATES.get(op, 0.0)
                total_cost += (count / 1000) * cost_per_k
            
            summary.estimated_cost_usd = total_cost
            
            return summary
    
    def _percentile(self, sorted_data: List[float], percentile: int) -> float:
        """Calculate percentile from sorted data."""
        if not sorted_data:
            return 0.0
        
        n = len(sorted_data)
        k = (n - 1) * percentile / 100
        f = int(k)
        c = f + 1 if f + 1 < n else f
        
        if f == c:
            return sorted_data[f]
        
        return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)
    
    async def get_operation_breakdown(self, hours: int = 24) -> Dict[str, Any]:
        """Get breakdown by operation type."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 
                    operation,
                    COUNT(*) as count,
                    AVG(duration_ms) as avg_latency,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful
                FROM metrics_store
                WHERE timestamp >= ?
                GROUP BY operation
                ORDER BY count DESC
            """, (cutoff.isoformat(),))
            
            breakdown = {}
            for row in cursor.fetchall():
                breakdown[row['operation']] = {
                    'count': row['count'],
                    'avg_latency_ms': row['avg_latency'],
                    'success_rate': row['successful'] / row['count'] if row['count'] > 0 else 0
                }
            
            return breakdown
    
    async def clear_old_entries(self) -> int:
        """Clear entries older than retention period."""
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT COUNT(*) as count FROM metrics_store
                    WHERE timestamp < ?
                """, (cutoff.isoformat(),))
                
                count = cursor.fetchone()['count'] or 0
                
                conn.execute("""
                    DELETE FROM metrics_store
                    WHERE timestamp < ?
                """, (cutoff.isoformat(),))
                conn.commit()
                
                return count
    
    async def clear_all(self):
        """Clear all metrics."""
        async with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM metrics_store")
                conn.commit()


# =============================================================================
# Track Context
# =============================================================================

class TrackContext:
    """Context object for metric tracking."""
    
    def __init__(self, metadata: Dict[str, Any]):
        self.metadata = metadata
    
    def set_token_count(self, tokens: int):
        """Set token count for LLM operations."""
        self.metadata['tokens'] = tokens
    
    def set_cache_status(self, hit: bool):
        """Set cache hit/miss status."""
        self.metadata['cache_hit'] = hit


# =============================================================================
# Metrics Dashboard
# =============================================================================

class MetricsDashboard:
    """
    Query and visualize metrics.
    
    Usage:
        dashboard = MetricsDashboard()
        
        # Get summary
        summary = dashboard.summary(hours=24)
        print(f"Total operations: {summary.total_operations}")
        print(f"Success rate: {summary.success_rate:.1%}")
        print(f"Avg latency: {summary.avg_latency_ms:.0f}ms")
        
        # Get breakdown
        breakdown = dashboard.operation_breakdown(hours=24)
        
        # CLI-style output
        dashboard.print_summary(hours=24)
    """
    
    def __init__(self, collector: Optional[MetricsCollector] = None):
        self.collector = collector or MetricsCollector()
    
    def summary(self, hours: int = 24) -> MetricsSummary:
        """Get summary for time period."""
        # Run in separate thread to avoid event loop conflicts
        import concurrent.futures
        
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.collector.get_summary(hours))
            finally:
                loop.close()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result()
    
    def operation_breakdown(self, hours: int = 24) -> Dict[str, Any]:
        """Get breakdown by operation type."""
        import concurrent.futures
        
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.collector.get_operation_breakdown(hours))
            finally:
                loop.close()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result()
    
    def print_summary(self, hours: int = 24):
        """Print formatted summary to console."""
        summary = self.summary(hours)
        
        print(f"📊 Metrics Summary (Last {hours}h)")
        print("=" * 50)
        print(f"Total Operations:     {summary.total_operations:,}")
        print(f"Success Rate:         {summary.success_rate:.1%}")
        print()
        print(f"Latency:")
        print(f"  Average:            {summary.avg_latency_ms:.0f}ms")
        print(f"  P50:                {summary.p50_latency_ms:.0f}ms")
        print(f"  P95:                {summary.p95_latency_ms:.0f}ms")
        print(f"  P99:                {summary.p99_latency_ms:.0f}ms")
        print()
        print(f"Cache Performance:")
        print(f"  Hits:               {summary.cache_hits:,}")
        print(f"  Misses:             {summary.cache_misses:,}")
        print(f"  Hit Rate:           {summary.cache_hit_rate:.1%}")
        print()
        print(f"Rate Limiting:")
        print(f"  Throttled Requests: {summary.rate_limited_requests:,}")
        print(f"  Circuit Breaker:    {summary.circuit_breaker_trips:,} trips")
        print()
        if summary.total_tokens > 0:
            print(f"Token Usage:")
            print(f"  Total Tokens:       {summary.total_tokens:,}")
        print()
        print(f"Estimated Cost:       ${summary.estimated_cost_usd:.4f} USD")
        print("=" * 50)
    
    def print_breakdown(self, hours: int = 24):
        """Print operation breakdown."""
        breakdown = self.operation_breakdown(hours)
        
        print(f"📈 Operation Breakdown (Last {hours}h)")
        print("=" * 60)
        print(f"{'Operation':<20} {'Count':>10} {'Avg Latency':>15} {'Success':>10}")
        print("-" * 60)
        
        for op, stats in breakdown.items():
            print(f"{op:<20} {stats['count']:>10,} {stats['avg_latency_ms']:>12.0f}ms {stats['success_rate']:>9.1%}")
        
        print("=" * 60)


# =============================================================================
# Module-level singleton
# =============================================================================

_metrics_collector_instance: Optional[MetricsCollector] = None


def get_metrics_collector(
    db_path: Optional[Path] = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    enabled: bool = True
) -> MetricsCollector:
    """Get or create the global metrics collector instance."""
    global _metrics_collector_instance
    
    if _metrics_collector_instance is None:
        _metrics_collector_instance = MetricsCollector(
            db_path=db_path,
            retention_days=retention_days,
            sample_rate=sample_rate,
            enabled=enabled
        )
    
    return _metrics_collector_instance


# =============================================================================
# CLI for testing and management
# =============================================================================

if __name__ == "__main__":
    import sys
    
    dashboard = MetricsDashboard()
    collector = get_metrics_collector()
    
    if len(sys.argv) < 2:
        print("Usage: python metrics.py <command>")
        print("Commands: summary, breakdown, latency, cache, cost, clear")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "summary":
        dashboard.print_summary(hours=24)
    
    elif command == "breakdown":
        dashboard.print_breakdown(hours=24)
    
    elif command == "clear":
        import asyncio
        asyncio.run(collector.clear_all())
        print("✅ Cleared all metrics")
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
