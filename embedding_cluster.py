#!/usr/bin/env python3
"""
Embedding-based Clustering for Search-as-Code SDK - Phase 4

Uses sentence-transformers for semantic similarity clustering.
Embedding cache for performance optimization.
Fallback to keyword-based clustering if sentence-transformers not available.

Location: /workspace/skills/auto-generated/search-as-code/
"""

import asyncio
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

# Optional embedding cache
try:
    from embedding_cache import get_embedding_cache
    EMBEDDING_CACHE_AVAILABLE = True
except ImportError:
    EMBEDDING_CACHE_AVAILABLE = False
    get_embedding_cache = None

# Optional dependency
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    from sklearn.cluster import KMeans
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    SentenceTransformer = None
    np = None
    KMeans = None


@dataclass
class EmbeddingClusterConfig:
    """Configuration for embedding-based clustering."""
    model_name: str = "all-MiniLM-L6-v2"  # Fast, good quality
    n_clusters: int = 3
    min_cluster_size: int = 1
    max_iterations: int = 100
    random_state: int = 42
    use_cache: bool = True  # Enable embedding cache


class EmbeddingClusterer:
    """
    Semantic clustering using sentence embeddings.
    
    Features:
    - Uses sentence-transformers for high-quality embeddings
    - KMeans clustering for grouping
    - Embedding cache for performance (Phase 4)
    - Automatic fallback to keyword clustering if unavailable
    - Configurable model and cluster count
    """
    
    def __init__(self, config: Optional[EmbeddingClusterConfig] = None):
        self.config = config or EmbeddingClusterConfig()
        self.model = None
        self._model_lock = asyncio.Lock()
        self._cache = None
        if self.config.use_cache and EMBEDDING_CACHE_AVAILABLE:
            self._cache = get_embedding_cache()
    
    async def _ensure_model_loaded(self):
        """Lazy-load the embedding model."""
        if self.model is None:
            async with self._model_lock:
                if self.model is None:
                    if not EMBEDDING_AVAILABLE:
                        raise ImportError(
                            "sentence-transformers not installed. "
                            "Install with: pip install sentence-transformers scikit-learn"
                        )
                    # Load model (runs on CPU by default)
                    self.model = SentenceTransformer(self.config.model_name)
    
    async def cluster(
        self,
        texts: List[str],
        n_clusters: Optional[int] = None,
        min_cluster_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Cluster texts by semantic similarity.
        
        Args:
            texts: List of text snippets to cluster
            n_clusters: Override default cluster count
            min_cluster_size: Override minimum cluster size
        
        Returns:
            Dict with cluster assignments and metadata
        """
        if not texts:
            return {
                "clusters": [],
                "n_clusters_found": 0,
                "method": "embedding",
                "error": "No texts provided"
            }
        
        n_clusters = n_clusters or self.config.n_clusters
        min_cluster_size = min_cluster_size or self.config.min_cluster_size
        
        # Adjust cluster count if we have fewer texts
        n_clusters = min(n_clusters, len(texts))
        
        try:
            # Load model
            await self._ensure_model_loaded()
            
            # Generate embeddings (with caching)
            embeddings = await self._get_embeddings(texts)
            
            # Perform KMeans clustering
            kmeans = KMeans(
                n_clusters=n_clusters,
                max_iter=self.config.max_iterations,
                random_state=self.config.random_state,
                n_init='auto'
            )
            cluster_labels = kmeans.fit_predict(embeddings)
            
            # Group texts by cluster
            clusters = {}
            for idx, label in enumerate(cluster_labels):
                cluster_key = f"cluster_{label}"
                if cluster_key not in clusters:
                    clusters[cluster_key] = []
                clusters[cluster_key].append({
                    "text": texts[idx][:200],  # Truncate for display
                    "index": idx
                })
            
            # Filter by min cluster size
            filtered_clusters = {
                k: v for k, v in clusters.items()
                if len(v) >= min_cluster_size
            }
            
            # Calculate cluster statistics
            cluster_sizes = [len(v) for v in filtered_clusters.values()]
            
            return {
                "clusters": filtered_clusters,
                "n_clusters_found": len(filtered_clusters),
                "method": "embedding",
                "model": self.config.model_name,
                "cluster_sizes": cluster_sizes,
                "avg_cluster_size": sum(cluster_sizes) / len(cluster_sizes) if cluster_sizes else 0,
                "total_texts": len(texts),
                "assigned_texts": sum(cluster_sizes),
                "unassigned": len(texts) - sum(cluster_sizes)
            }
            
        except Exception as e:
            # Fallback to keyword clustering
            print(f"Embedding clustering failed: {e}. Falling back to keyword method.")
            return await self._keyword_cluster_fallback(texts, n_clusters, min_cluster_size)
    
    async def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        """
        Get embeddings for texts, using cache if available.
        
        Args:
            texts: List of texts to embed
        
        Returns:
            Numpy array of embeddings
        """
        if not self._cache or not EMBEDDING_CACHE_AVAILABLE:
            # No cache, generate directly
            return self.model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True
            )
        
        # Try cache first
        cache_dict, miss_indices = await self._cache.get_batch(
            texts, 
            model_name=self.config.model_name
        )
        
        if not miss_indices:
            # All hits - reconstruct embedding array
            first_vec = next(iter(cache_dict.values()))
            embeddings = np.zeros((len(texts), len(first_vec)))
            for idx, vec in cache_dict.items():
                embeddings[idx] = vec
            return embeddings
        
        # Partial miss - generate missing embeddings
        missing_texts = [texts[i] for i in miss_indices]
        missing_embeddings = self.model.encode(
            missing_texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True
        )
        
        # Cache the new embeddings
        missing_vectors = [
            missing_embeddings[i] for i in range(len(missing_texts))
        ]
        await self._cache.set_batch(missing_texts, missing_vectors, self.config.model_name)
        
        # Reconstruct full embedding array
        all_embeddings = []
        for i in range(len(texts)):
            if i in cache_dict:
                all_embeddings.append(cache_dict[i])
            else:
                miss_idx = miss_indices.index(i)
                all_embeddings.append(missing_embeddings[miss_idx])
        
        return np.array(all_embeddings)
    
    async def _keyword_cluster_fallback(
        self,
        texts: List[str],
        n_clusters: int,
        min_cluster_size: int
    ) -> Dict[str, Any]:
        """Fallback keyword-based clustering when embeddings unavailable."""
        # Simple TF-IDF-like approach using term frequency
        from collections import Counter
        
        # Tokenize and count terms
        all_terms = []
        for text in texts:
            terms = text.lower().split()
            all_terms.append(Counter(terms))
        
        # Build vocabulary
        vocab = set()
        for terms in all_terms:
            vocab.update(terms.keys())
        vocab = sorted(vocab)
        
        if not vocab or len(texts) < n_clusters:
            # Not enough data, put everything in one cluster
            return {
                "clusters": {
                    "cluster_0": [{"text": t[:200], "index": i} for i, t in enumerate(texts)]
                },
                "n_clusters_found": 1,
                "method": "keyword",
                "total_texts": len(texts),
                "assigned_texts": len(texts)
            }
        
        # Create simple TF vectors
        vectors = []
        for terms in all_terms:
            vec = [terms.get(word, 0) for word in vocab]
            vectors.append(vec)
        
        # Normalize vectors
        import math
        normalized = []
        for vec in vectors:
            norm = math.sqrt(sum(x*x for x in vec))
            if norm > 0:
                normalized.append([x/norm for x in vec])
            else:
                normalized.append(vec)
        
        # Simple cosine similarity clustering
        clusters = {f"cluster_{i}": [] for i in range(n_clusters)}
        
        # Assign each text to nearest centroid (random init)
        import random
        random.seed(42)
        centroids = random.sample(normalized, min(n_clusters, len(normalized)))
        
        for i, vec in enumerate(normalized):
            # Find nearest centroid
            min_dist = float('inf')
            best_cluster = 0
            for j, centroid in enumerate(centroids):
                dist = sum((a-b)**2 for a,b in zip(vec, centroid))
                if dist < min_dist:
                    min_dist = dist
                    best_cluster = j
            
            clusters[f"cluster_{best_cluster}"].append({
                "text": texts[i][:200],
                "index": i
            })
        
        # Filter by min cluster size
        filtered_clusters = {
            k: v for k, v in clusters.items()
            if len(v) >= min_cluster_size
        }
        
        cluster_sizes = [len(v) for v in filtered_clusters.values()]
        
        return {
            "clusters": filtered_clusters,
            "n_clusters_found": len(filtered_clusters),
            "method": "keyword",
            "cluster_sizes": cluster_sizes,
            "avg_cluster_size": sum(cluster_sizes) / len(cluster_sizes) if cluster_sizes else 0,
            "total_texts": len(texts),
            "assigned_texts": sum(cluster_sizes)
        }


# =============================================================================
# Module-level singleton
# =============================================================================

_clusterer_instance: Optional[EmbeddingClusterer] = None


def get_embedding_clusterer(
    config: Optional[EmbeddingClusterConfig] = None
) -> EmbeddingClusterer:
    """Get or create the global embedding clusterer instance."""
    global _clusterer_instance
    
    if _clusterer_instance is None:
        _clusterer_instance = EmbeddingClusterer(config=config)
    
    return _clusterer_instance


# =============================================================================
# CLI for testing
# =============================================================================

if __name__ == "__main__":
    import asyncio
    
    async def main():
        print("=== Embedding Cluster Test ===")
        print()
        
        texts = [
            "Machine learning frameworks like TensorFlow and PyTorch",
            "Deep learning neural networks for computer vision",
            "Natural language processing with transformers",
            "Python web development Django Flask",
            "JavaScript React Vue frontend frameworks",
            "Database optimization SQL indexing"
        ]
        
        config = EmbeddingClusterConfig(n_clusters=2, use_cache=True)
        clusterer = EmbeddingClusterer(config)
        
        result = await clusterer.cluster(texts)
        
        print(f"Method: {result['method']}")
        print(f"Clusters found: {result['n_clusters_found']}")
        for name, items in result['clusters'].items():
            print(f"\n{name}:")
            for item in items:
                print(f"  - {item['text']}")
        
        print()
        print("✅ Test complete")
    
    asyncio.run(main())
