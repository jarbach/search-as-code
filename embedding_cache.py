#!/usr/bin/env python3
"""
Embedding Cache for Search-as-Code SDK - Phase 4

Caches embedding vectors to avoid recomputation.
Uses SQLite backend with TTL-based eviction.

Location: /workspace/skills/auto-generated/search-as-code/
"""

import asyncio
import hashlib
import pickle
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_CACHE_PATH = Path.home() / ".search-as-code" / "cache.db"
DEFAULT_TTL_DAYS = 7
DEFAULT_MAX_ENTRIES = 10000
DEFAULT_EVICTION_BATCH_SIZE = 100


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class EmbeddingCacheEntry:
    """A single cached embedding."""
    text_hash: str
    text_preview: str
    model_name: str
    vector: np.ndarray
    dimension: int
    created_at: datetime
    last_accessed: datetime
    access_count: int
    ttl_days: int = DEFAULT_TTL_DAYS


@dataclass
class EmbeddingCacheStats:
    """Statistics for embedding cache."""
    total_entries: int = 0
    total_size_bytes: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    oldest_entry: Optional[datetime] = None
    newest_entry: Optional[datetime] = None
    
    @property
    def hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0
    
    @property
    def avg_size_per_entry(self) -> float:
        return self.total_size_bytes / self.total_entries if self.total_entries > 0 else 0.0


# =============================================================================
# Embedding Cache
# =============================================================================

class EmbeddingCache:
    """
    SQLite-backed cache for embedding vectors.
    
    Features:
    - SHA256 hash-based lookups
    - TTL-based automatic eviction
    - Access count tracking for LRU-style eviction
    - Configurable max entries
    - Thread-safe with connection pooling
    
    Usage:
        cache = EmbeddingCache()
        
        # Check cache
        vector = await cache.get("text to embed", model_name="all-MiniLM-L6-v2")
        
        # Save to cache
        await cache.set("text to embed", vector, model_name="all-MiniLM-L6-v2")
        
        # Get stats
        stats = cache.stats()
    """
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        max_entries: int = DEFAULT_MAX_ENTRIES
    ):
        self.db_path = db_path or DEFAULT_CACHE_PATH
        self.ttl_days = ttl_days
        self.max_entries = max_entries
        
        self._lock = asyncio.Lock()
        self._stats = EmbeddingCacheStats()
        
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
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    id TEXT PRIMARY KEY,
                    text_hash TEXT NOT NULL,
                    text_preview TEXT,
                    model_name TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    dimension INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 1,
                    ttl_days INTEGER DEFAULT 7
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embedding_cache_hash 
                ON embedding_cache(text_hash)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embedding_cache_accessed 
                ON embedding_cache(last_accessed)
            """)
            
            conn.commit()
    
    def _hash_text(self, text: str) -> str:
        """Generate SHA256 hash of text."""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()
    
    async def get(
        self,
        text: str,
        model_name: str
    ) -> Optional[np.ndarray]:
        """
        Get cached embedding for text.
        
        Args:
            text: Original text that was embedded
            model_name: Model used for embedding (must match)
        
        Returns:
            Cached vector or None if not found/expired
        """
        text_hash = self._hash_text(text)
        
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT vector, dimension, last_accessed, access_count, ttl_days
                    FROM embedding_cache
                    WHERE text_hash = ? AND model_name = ?
                """, (text_hash, model_name))
                
                row = cursor.fetchone()
                
                if row is None:
                    self._stats.cache_misses += 1
                    return None
                
                # Check TTL
                last_accessed = datetime.fromisoformat(row['last_accessed'])
                ttl = timedelta(days=row['ttl_days'])
                if datetime.now() - last_accessed > ttl:
                    # Expired, delete
                    conn.execute("""
                        DELETE FROM embedding_cache
                        WHERE text_hash = ? AND model_name = ?
                    """, (text_hash, model_name))
                    conn.commit()
                    self._stats.cache_misses += 1
                    return None
                
                # Update access metadata
                conn.execute("""
                    UPDATE embedding_cache
                    SET last_accessed = CURRENT_TIMESTAMP,
                        access_count = access_count + 1
                    WHERE text_hash = ? AND model_name = ?
                """, (text_hash, model_name))
                conn.commit()
                
                # Deserialize vector
                vector = pickle.loads(row['vector'])
                
                self._stats.cache_hits += 1
                return vector
    
    async def set(
        self,
        text: str,
        vector: np.ndarray,
        model_name: str,
        text_preview: Optional[str] = None
    ):
        """
        Cache an embedding vector.
        
        Args:
            text: Original text
            vector: Numpy array of embeddings
            model_name: Model used for embedding
            text_preview: Optional preview (first 100 chars if not provided)
        """
        text_hash = self._hash_text(text)
        
        if text_preview is None:
            text_preview = text[:100] if len(text) > 100 else text
        
        vector_blob = pickle.dumps(vector)
        dimension = vector.shape[0] if len(vector.shape) > 0 else 1
        
        async with self._lock:
            with self._get_connection() as conn:
                # Check if entry exists
                cursor = conn.execute("""
                    SELECT id FROM embedding_cache
                    WHERE text_hash = ? AND model_name = ?
                """, (text_hash, model_name))
                
                if cursor.fetchone():
                    # Update existing
                    conn.execute("""
                        UPDATE embedding_cache
                        SET vector = ?, dimension = ?, text_preview = ?,
                            last_accessed = CURRENT_TIMESTAMP,
                            access_count = access_count + 1
                        WHERE text_hash = ? AND model_name = ?
                    """, (vector_blob, dimension, text_preview, text_hash, model_name))
                else:
                    # Insert new
                    conn.execute("""
                        INSERT INTO embedding_cache
                        (id, text_hash, text_preview, model_name, vector, dimension, ttl_days)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (text_hash, text_hash, text_preview, model_name, vector_blob, dimension, self.ttl_days))
                
                conn.commit()
                
                # Enforce max entries
                await self._enforce_max_entries(conn)
    
    async def _enforce_max_entries(self, conn: sqlite3.Connection):
        """Delete oldest entries if over max size."""
        cursor = conn.execute("""
            SELECT COUNT(*) as count FROM embedding_cache
        """)
        count = cursor.fetchone()['count']
        
        if count > self.max_entries:
            to_delete = count - self.max_entries
            conn.execute("""
                DELETE FROM embedding_cache
                WHERE id IN (
                    SELECT id FROM embedding_cache
                    ORDER BY last_accessed ASC
                    LIMIT ?
                )
            """, (to_delete,))
            conn.commit()
    
    async def delete(self, text: str, model_name: str):
        """Delete a specific cached embedding."""
        text_hash = self._hash_text(text)
        
        async with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    DELETE FROM embedding_cache
                    WHERE text_hash = ? AND model_name = ?
                """, (text_hash, model_name))
                conn.commit()
    
    async def clear_expired(self) -> int:
        """
        Clear all expired entries.
        
        Returns:
            Number of entries deleted
        """
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT COUNT(*) as count FROM embedding_cache
                    WHERE datetime(last_accessed, '+' || ttl_days || ' days') < datetime('now')
                """)
                count = cursor.fetchone()['count']
                
                conn.execute("""
                    DELETE FROM embedding_cache
                    WHERE datetime(last_accessed, '+' || ttl_days || ' days') < datetime('now')
                """)
                conn.commit()
                
                return count
    
    async def clear_all(self):
        """Clear all cached embeddings."""
        async with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM embedding_cache")
                conn.commit()
    
    def stats(self) -> EmbeddingCacheStats:
        """Get cache statistics."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_entries,
                    SUM(length(vector)) as total_size_bytes,
                    MIN(last_accessed) as oldest_entry,
                    MAX(last_accessed) as newest_entry
                FROM embedding_cache
            """)
            
            row = cursor.fetchone()
            
            self._stats.total_entries = row['total_entries'] or 0
            self._stats.total_size_bytes = row['total_size_bytes'] or 0
            self._stats.oldest_entry = datetime.fromisoformat(row['oldest_entry']) if row['oldest_entry'] else None
            self._stats.newest_entry = datetime.fromisoformat(row['newest_entry']) if row['newest_entry'] else None
            
            return self._stats
    
    async def get_batch(
        self,
        texts: List[str],
        model_name: str
    ) -> Tuple[Dict[str, np.ndarray], List[int]]:
        """
        Get cached embeddings for multiple texts.
        
        Args:
            texts: List of texts to look up
            model_name: Model used for embedding
        
        Returns:
            Tuple of (cache_dict, miss_indices)
            - cache_dict: {text_index: vector} for hits
            - miss_indices: list of indices that were misses
        """
        cache_dict = {}
        miss_indices = []
        
        async with self._lock:
            with self._get_connection() as conn:
                for i, text in enumerate(texts):
                    text_hash = self._hash_text(text)
                    
                    cursor = conn.execute("""
                        SELECT vector, last_accessed, access_count, ttl_days
                        FROM embedding_cache
                        WHERE text_hash = ? AND model_name = ?
                    """, (text_hash, model_name))
                    
                    row = cursor.fetchone()
                    
                    if row is None:
                        self._stats.cache_misses += 1
                        miss_indices.append(i)
                        continue
                    
                    # Check TTL
                    last_accessed = datetime.fromisoformat(row['last_accessed'])
                    ttl = timedelta(days=self.ttl_days)
                    if datetime.now() - last_accessed > ttl:
                        # Expired
                        conn.execute("""
                            DELETE FROM embedding_cache
                            WHERE text_hash = ? AND model_name = ?
                        """, (text_hash, model_name))
                        self._stats.cache_misses += 1
                        miss_indices.append(i)
                        continue
                    
                    # Update access metadata
                    conn.execute("""
                        UPDATE embedding_cache
                        SET last_accessed = CURRENT_TIMESTAMP,
                            access_count = access_count + 1
                        WHERE text_hash = ? AND model_name = ?
                    """, (text_hash, model_name))
                    
                    vector = pickle.loads(row['vector'])
                    cache_dict[i] = vector
                    self._stats.cache_hits += 1
                
                conn.commit()
        
        return cache_dict, miss_indices
    
    async def set_batch(
        self,
        texts: List[str],
        vectors: List[np.ndarray],
        model_name: str
    ):
        """
        Cache multiple embedding vectors.
        
        Args:
            texts: List of original texts
            vectors: List of corresponding vectors
            model_name: Model used for embedding
        """
        async with self._lock:
            with self._get_connection() as conn:
                for text, vector in zip(texts, vectors):
                    text_hash = self._hash_text(text)
                    text_preview = text[:100] if len(text) > 100 else text
                    vector_blob = pickle.dumps(vector)
                    dimension = vector.shape[0] if len(vector.shape) > 0 else 1
                    
                    conn.execute("""
                        INSERT OR REPLACE INTO embedding_cache
                        (id, text_hash, text_preview, model_name, vector, dimension, ttl_days)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (text_hash, text_hash, text_preview, model_name, vector_blob, dimension, self.ttl_days))
                
                conn.commit()
                
                await self._enforce_max_entries(conn)


# =============================================================================
# Module-level singleton
# =============================================================================

_embedding_cache_instance: Optional[EmbeddingCache] = None


def get_embedding_cache(
    db_path: Optional[Path] = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
    max_entries: int = DEFAULT_MAX_ENTRIES
) -> EmbeddingCache:
    """Get or create the global embedding cache instance."""
    global _embedding_cache_instance
    
    if _embedding_cache_instance is None:
        _embedding_cache_instance = EmbeddingCache(
            db_path=db_path,
            ttl_days=ttl_days,
            max_entries=max_entries
        )
    
    return _embedding_cache_instance


# =============================================================================
# CLI for testing and management
# =============================================================================

if __name__ == "__main__":
    import sys
    
    async def main():
        cache = get_embedding_cache()
        
        if len(sys.argv) < 2:
            print("Usage: python embedding_cache.py <command>")
            print("Commands: stats, clear, clear-expired")
            sys.exit(1)
        
        command = sys.argv[1]
        
        if command == "stats":
            stats = cache.stats()
            print(f"Embedding Cache Statistics:")
            print(f"  Total entries: {stats.total_entries}")
            print(f"  Total size: {stats.total_size_bytes / 1024:.2f} KB")
            print(f"  Avg size per entry: {stats.avg_size_per_entry / 1024:.2f} KB")
            print(f"  Cache hits: {stats.cache_hits}")
            print(f"  Cache misses: {stats.cache_misses}")
            print(f"  Hit rate: {stats.hit_rate:.1%}")
            if stats.oldest_entry:
                print(f"  Oldest entry: {stats.oldest_entry}")
            if stats.newest_entry:
                print(f"  Newest entry: {stats.newest_entry}")
        
        elif command == "clear":
            await cache.clear_all()
            print("✅ Cleared all cached embeddings")
        
        elif command == "clear-expired":
            deleted = await cache.clear_expired()
            print(f"✅ Deleted {deleted} expired entries")
        
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    
    asyncio.run(main())
