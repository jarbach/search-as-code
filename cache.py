#!/usr/bin/env python3
"""
Search-as-Code SDK - Cross-Session Caching (Phase 2 Feature #2)

Simple cache wrapper for search operations using Lite-LCM storage.
"""

import hashlib
import json
import os
import sys
from datetime import datetime
from typing import Dict, Optional, Any, List

# Import Lite-LCM for storage
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lite-lcm'))
try:
    from lite_lcm import LiteLCM
    LITE_LCM_AVAILABLE = True
except ImportError:
    LITE_LCM_AVAILABLE = False


# Default TTLs (in seconds)
DEFAULT_TTL = {
    "search": 3600,        # 1 hour
    "fetch": 86400,        # 24 hours  
    "summarize": 604800,   # 7 days
}


def generate_cache_key(operation_type: str, query: str, **params) -> str:
    """
    Generate deterministic cache key from operation and parameters.
    
    Example: search:a3f8b2c9d4e5f678
    """
    key_parts = [operation_type, query]
    for k in sorted(params.keys()):
        v = params[k]
        if v is not None:
            key_parts.append(f"{k}:{v}")
    
    key_string = ":".join(str(p) for p in key_parts)
    key_hash = hashlib.sha256(key_string.encode()).hexdigest()[:16]
    
    return f"{operation_type}:{key_hash}"


class SearchCache:
    """Cache wrapper for search operations."""
    
    def __init__(self, enabled: bool = True, ttl_seconds: int = 3600):
        self.enabled = enabled and LITE_LCM_AVAILABLE
        self.ttl_seconds = ttl_seconds
        self.cache = LiteLCM() if self.enabled else None
    
    def get(self, cache_key: str) -> tuple[Optional[Any], dict]:
        """Get cached result."""
        metrics = {"cache_hit": False, "cache_key": cache_key}
        
        if not self.enabled or not self.cache:
            return None, metrics
        
        try:
            result = self.cache.cache_get(cache_key)
            if result.get("hit"):
                metrics["cache_hit"] = True
                metrics["hit_count"] = result.get("hit_count", 1)
                metrics["latency_saved_ms"] = 800  # Estimate
                return result["data"], metrics
        except Exception as e:
            pass  # Silently ignore cache errors
        
        return None, metrics
    
    def set(self, cache_key: str, query: str, results: Any, operation_type: str = "search"):
        """Write result to cache."""
        if not self.enabled or not self.cache:
            return False
        
        try:
            query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
            self.cache.cache_set(
                cache_key=cache_key,
                query_hash=query_hash,
                operation_type=operation_type,
                query_params="",
                results=results,
                ttl_seconds=self.ttl_seconds
            )
            return True
        except Exception as e:
            return False


class CacheManager:
    """High-level cache management."""
    
    def __init__(self):
        self.cache = LiteLCM() if LITE_LCM_AVAILABLE else None
    
    def stats(self) -> Dict:
        """Get cache statistics."""
        if not self.cache:
            return {"error": "Lite-LCM not available"}
        
        return self.cache.cache_stats()
    
    def clear(self, pattern: str = None) -> Dict:
        """Clear cache entries."""
        if not self.cache:
            return {"deleted_count": 0}
        return self.cache.cache_clear(pattern)
    
    def cleanup(self) -> Dict:
        """Remove expired entries."""
        if not self.cache:
            return {"deleted_expired": 0}
        return self.cache.cache_cleanup()
    
    def invalidate(self, pattern: str) -> Dict:
        """Invalidate cache entries matching pattern."""
        if not self.cache:
            return {"deleted_count": 0}
        return self.cache.cache_clear(pattern)
    
    def export(self, format: str = "text") -> str:
        """Export cache statistics."""
        stats = self.stats()
        
        if format == "json":
            return json.dumps(stats, indent=2)
        else:
            lines = [
                "=== Search Cache Statistics ===",
                f"Total entries:   {stats.get('total_entries', 0)}",
                f"Total hits:      {stats.get('total_hits', 0)}",
                f"Total misses:    {stats.get('total_misses', 0)}",
                f"Hit rate:        {stats.get('hit_rate', 0):.1%}",
                f"Storage used:    {stats.get('storage_bytes', 0) / 1024:.1f} KB",
                f"Expired entries: {stats.get('expired_entries', 0)}",
            ]
            return "\n".join(lines)


# CLI entry point
def main():
    if len(sys.argv) < 2:
        print("Usage: python cache.py <command> [args]")
        print("\nCommands:")
        print("  stats              - Show cache statistics")
        print("  clear [pattern]    - Clear cache (optionally by pattern)")
        print("  cleanup            - Remove expired entries")
        print("  invalidate <pattern> - Invalidate entries matching pattern")
        sys.exit(1)
    
    command = sys.argv[1]
    args = sys.argv[2:]
    
    manager = CacheManager()
    
    if command == "stats":
        print(manager.export(format="text"))
    
    elif command == "clear":
        pattern = args[0] if args else None
        result = manager.clear(pattern)
        print(f"Cleared {result.get('deleted_count', 0)} cache entries")
    
    elif command == "cleanup":
        result = manager.cleanup()
        print(f"Removed {result.get('deleted_expired', 0)} expired entries")
    
    elif command == "invalidate":
        if not args:
            print("Usage: cache invalidate <pattern>")
            return
        result = manager.invalidate(args[0])
        print(f"Invalidated {result.get('deleted_count', 0)} entries matching '{args[0]}'")
    
    else:
        print(f"Unknown cache command: {command}")


if __name__ == "__main__":
    main()
