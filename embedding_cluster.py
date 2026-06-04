#!/usr/bin/env python3
"""
Embedding-based Clustering for Search-as-Code SDK

Uses sentence-transformers for semantic similarity clustering.
Fallback to keyword-based clustering if sentence-transformers not available.

Location: /workspace/skills/auto-generated/search-as-code/
"""

import asyncio
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

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


class EmbeddingClusterer:
    """
    Semantic clustering using sentence embeddings.
    
    Features:
    - Uses sentence-transformers for high-quality embeddings
    - KMeans clustering for grouping
    - Automatic fallback to keyword clustering if unavailable
    - Configurable model and cluster count
    """
    
    def __init__(self, config: Optional[EmbeddingClusterConfig] = None):
        self.config = config or EmbeddingClusterConfig()
        self.model = None
        self._model_lock = asyncio.Lock()
    
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
            
            # Generate embeddings
            embeddings = self.model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True
            )
            
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
            # Remove stopwords and short words
            terms = [t for t in terms if len(t) > 3 and t not in {'this', 'that', 'with', 'have', 'been', 'were', 'will', 'would', 'could', 'should'}]
            all_terms.extend(terms)
        
        # Get top terms for clustering
        term_counts = Counter(all_terms)
        top_terms = [term for term, _ in term_counts.most_common(n_clusters * 3)]
        
        # Assign texts to clusters based on top terms
        clusters = {}
        for idx, text in enumerate(texts):
            text_lower = text.lower()
            best_term = None
            best_count = 0
            
            for term in top_terms:
                count = text_lower.count(term)
                if count > best_count:
                    best_count = count
                    best_term = term
            
            if best_term:
                cluster_key = f"cluster_{best_term}"
                if cluster_key not in clusters:
                    clusters[cluster_key] = []
                clusters[cluster_key].append({"text": text[:200], "index": idx})
        
        # Filter by min size
        filtered_clusters = {
            k: v for k, v in clusters.items()
            if len(v) >= min_cluster_size
        }
        
        cluster_sizes = [len(v) for v in filtered_clusters.values()]
        
        return {
            "clusters": filtered_clusters,
            "n_clusters_found": len(filtered_clusters),
            "method": "keyword_fallback",
            "cluster_sizes": cluster_sizes,
            "total_texts": len(texts),
            "assigned_texts": sum(cluster_sizes) if cluster_sizes else 0
        }


# =============================================================================
# Integration with SearchPipeline
# =============================================================================

async def cluster_results_by_embedding(
    results: List[Dict[str, Any]],
    field: str = "snippet",
    n_clusters: int = 3,
    min_cluster_size: int = 1
) -> Dict[str, Any]:
    """
    Cluster search results by semantic similarity.
    
    Args:
        results: List of search result dicts with 'snippet' field
        field: Field to use for clustering ('snippet', 'title', 'combined')
        n_clusters: Number of clusters
        min_cluster_size: Minimum results per cluster
    
    Returns:
        Clustering metadata to add to results
    """
    if not EMBEDDING_AVAILABLE:
        return {
            "clustering_available": False,
            "error": "sentence-transformers not installed"
        }
    
    # Extract texts for clustering
    texts = []
    for r in results:
        if field == "combined":
            text = f"{r.get('title', '')} {r.get('snippet', '')}".strip()
        else:
            text = r.get(field, "")
        
        if text:
            texts.append(text)
        else:
            texts.append(r.get("url", "unknown"))  # Fallback to URL
    
    # Cluster
    clusterer = EmbeddingClusterer()
    clustering_result = await clusterer.cluster(texts, n_clusters, min_cluster_size)
    
    # Map cluster assignments back to results
    cluster_map = {}  # index -> cluster_id
    for cluster_id, items in clustering_result.get("clusters", {}).items():
        for item in items:
            cluster_map[item["index"]] = cluster_id
    
    # Add cluster info to each result
    for idx, r in enumerate(results):
        r["cluster_id"] = cluster_map.get(idx, "unclustered")
    
    return {
        "clustering_available": True,
        **clustering_result
    }


# =============================================================================
# Example Usage
# =============================================================================

async def demo():
    """Demonstrate embedding-based clustering."""
    if not EMBEDDING_AVAILABLE:
        print("⚠️  sentence-transformers not installed")
        print("Install with: pip install sentence-transformers scikit-learn")
        return
    
    print("=== Embedding-Based Clustering Demo ===\n")
    
    # Sample search snippets
    snippets = [
        "Machine learning frameworks like TensorFlow and PyTorch enable deep learning research",
        "PyTorch 2.0 released with improved performance and torch.compile",
        "TensorFlow 3.0 announcement at Google I/O focuses on mobile deployment",
        "Natural language processing with transformers and BERT models",
        "GPT-4 and large language models for text generation tasks",
        "BERT fine-tuning for sentiment analysis and classification",
        "Computer vision with convolutional neural networks and ResNet",
        "Image classification using Vision Transformers (ViT)",
        "Object detection with YOLO and Faster R-CNN architectures",
    ]
    
    clusterer = EmbeddingClusterer(EmbeddingClusterConfig(n_clusters=3))
    result = await clusterer.cluster(snippets, n_clusters=3)
    
    print(f"Method: {result['method']}")
    print(f"Clusters found: {result['n_clusters_found']}")
    print(f"Total texts: {result['total_texts']}")
    print(f"Assigned: {result['assigned_texts']}")
    print(f"Avg cluster size: {result['avg_cluster_size']:.1f}\n")
    
    for cluster_id, items in result["clusters"].items():
        print(f"\n{cluster_id} ({len(items)} items):")
        for item in items[:2]:  # Show first 2
            print(f"  - {item['text'][:80]}...")


if __name__ == "__main__":
    asyncio.run(demo())
