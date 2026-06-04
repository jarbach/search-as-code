#!/usr/bin/env python3
"""
Search-as-Code SDK - Phase 1A Prototype

Exposes search stack as composable Python primitives for agentic workflows.
Wraps OpenClaw tools (web_search, web_fetch) via subprocess execution.

Usage:
    from sdk import SearchPipeline
    
    results = await SearchPipeline() \
        .search("LLM context window optimization") \
        .filter(domain="arxiv.org") \
        .rank_by("relevance") \
        .compress(target_tokens=500) \
        .execute()

Location: /workspace/skills/auto-generated/search-as-code/
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from pathlib import Path

# Import cache module (Phase 2)
try:
    from cache import SearchCache, generate_cache_key, DEFAULT_TTL
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False

# Lite-LCM integration
try:
    sys.path.insert(0, str(Path.home() / ".openclaw/workspace/skills/auto-generated/lite-lcm"))
    from lite_lcm import LiteLCM
    LITE_LCM_AVAILABLE = True
except ImportError:
    LITE_LCM_AVAILABLE = False

# Phase 3: Rate limiting
try:
    from rate_limiter import RateLimitedExecutor, RateLimitConfig
    RATE_LIMIT_AVAILABLE = True
except ImportError:
    RATE_LIMIT_AVAILABLE = False

# Phase 3.5: Embedding-based clustering
try:
    from embedding_cluster import EmbeddingClusterer, cluster_results_by_embedding
    EMBEDDING_CLUSTER_AVAILABLE = True
except ImportError:
    EMBEDDING_CLUSTER_AVAILABLE = False


# =============================================================================
# Configuration
# =============================================================================

OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "9a78a3afe33dc798d08752adcc1756720797d5d022bf9bde")
DEFAULT_TIMEOUT_SEARCH = 30
DEFAULT_TIMEOUT_FETCH = 60
DEFAULT_MAX_RESULTS = 10  # web_search tool limit: 1-10
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_DELAY = 2.0

# Phase 3: Rate Limiting Configuration
DEFAULT_RATE_LIMIT = int(os.getenv("SEARCH_SDK_RATE_LIMIT", "10"))  # requests per minute
DEFAULT_BURST_SIZE = int(os.getenv("SEARCH_SDK_BURST_SIZE", "5"))
DEFAULT_CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("SEARCH_SDK_CIRCUIT_THRESHOLD", "5"))
DEFAULT_CIRCUIT_RECOVERY_TIMEOUT = float(os.getenv("SEARCH_SDK_CIRCUIT_TIMEOUT", "30.0"))

# LLM Configuration for Smart Compression (Phase 2)
DEFAULT_SUMMARIZER_MODEL = os.getenv("SEARCH_SDK_SUMMARIZER_MODEL", "qwen2.5:7b")  # Local model (no ollama/ prefix)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_COMPRESSION_RATIO = 0.4  # Target 40% of original token count

# State persistence path
STATE_DIR = Path.home() / ".openclaw/workspace/state/search-pipelines"


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class SearchResult:
    """Individual search result item."""
    title: str
    url: str
    snippet: str
    source: str
    rank: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "rank": self.rank,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SearchResult':
        return cls(**data)
    
    def token_estimate(self) -> int:
        """Rough token count (4 chars ≈ 1 token)."""
        text = f"{self.title} {self.snippet}"
        return len(text) // 4
    
    @property
    def domain(self) -> str:
        match = re.search(r"https?://([^/]+)", self.url)
        return match.group(1) if match else ""


@dataclass
class PipelineState:
    """Intermediate state passed through pipeline stages."""
    query: Optional[str] = None
    results: List[SearchResult] = field(default_factory=list)
    fetched_content: Dict[str, str] = field(default_factory=dict)
    extracted_data: List[Dict[str, Any]] = field(default_factory=list)
    compressed_output: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Lite-LCM integration
    session_id: Optional[str] = None
    lcm: Optional[Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "fetched_content": self.fetched_content,
            "extracted_data": self.extracted_data,
            "compressed_output": self.compressed_output,
            "errors": self.errors,
            "metrics": self.metrics,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PipelineState':
        state = cls()
        state.query = data.get("query")
        state.results = [SearchResult.from_dict(r) for r in data.get("results", [])]
        state.fetched_content = data.get("fetched_content", {})
        state.extracted_data = data.get("extracted_data", [])
        state.compressed_output = data.get("compressed_output")
        state.errors = data.get("errors", [])
        state.metrics = data.get("metrics", {})
        state.metadata = data.get("metadata", {})
        return state
    
    def save_state(self, name: str) -> str:
        """Persist state to filesystem for cross-turn use."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        
        # Generate unique state ID
        state_id = f"{self.session_id or 'default'}_{name}"
        state_file = STATE_DIR / f"{state_id}.json"
        
        with open(state_file, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        
        # Also save to Lite-LCM if available
        if LITE_LCM_AVAILABLE and self.lcm:
            self.lcm.ingest_message(
                role="system",
                content=f"Search state saved: {name}",
                metadata={"state_id": state_id, "state_name": name}
            )
        
        return str(state_file)
    
    @classmethod
    def load_state(cls, name: str, session_id: str = "default") -> 'PipelineState':
        """Load persisted state from filesystem."""
        state_id = f"{session_id}_{name}"
        state_file = STATE_DIR / f"{state_id}.json"
        
        if not state_file.exists():
            raise FileNotFoundError(f"State '{name}' not found for session '{session_id}'")
        
        with open(state_file, 'r') as f:
            data = json.load(f)
        
        return cls.from_dict(data)


# =============================================================================
# Tool Execution Backend
# =============================================================================

class ToolExecutor:
    """
    Executes OpenClaw tools via Gateway HTTP API.
    
    Phase 1B: Direct HTTP calls to Gateway /tools/invoke endpoint.
    This is the production-ready backend for the SDK.
    
    Phase 3: Integrated rate limiting, circuit breaker, and retry logic.
    
    Auth: Uses Gateway token auth (configured via env vars or constants)
    Policy: Tool availability filtered through Gateway tool policy
    """
    
    # Shared rate-limited executor (initialized on first use)
    _rate_limited_executor: Optional[RateLimitedExecutor] = None
    
    @classmethod
    def _get_rate_limited_executor(cls) -> RateLimitedExecutor:
        """Get or create the shared rate-limited executor."""
        if cls._rate_limited_executor is None:
            if RATE_LIMIT_AVAILABLE:
                config = RateLimitConfig(
                    rate=DEFAULT_RATE_LIMIT,
                    burst_size=DEFAULT_BURST_SIZE,
                    circuit_failure_threshold=DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
                    circuit_recovery_timeout=DEFAULT_CIRCUIT_RECOVERY_TIMEOUT
                )
                cls._rate_limited_executor = RateLimitedExecutor(config)
            else:
                cls._rate_limited_executor = None
        return cls._rate_limited_executor
    
    @staticmethod
    def _invoke_tool(tool_name: str, args: Dict[str, Any], timeout: int = 30) -> tuple[bool, Any, str]:
        """
        Invoke a tool via Gateway HTTP API.
        
        Returns: (success, result_or_none, error_message)
        """
        import urllib.request
        import urllib.error
        
        url = f"{OPENCLAW_GATEWAY_URL}/tools/invoke"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENCLAW_GATEWAY_TOKEN}"
        }
        
        payload = {
            "tool": tool_name,
            "args": args
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                if result.get("ok"):
                    return True, result.get("result"), ""
                else:
                    error_info = result.get("error", {})
                    return False, None, f"{error_info.get('type', 'Error')}: {error_info.get('message', 'Unknown error')}"
                    
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else str(e)
            return False, None, f"HTTP {e.code}: {error_body}"
        except urllib.error.URLError as e:
            return False, None, f"Connection error: {e.reason}"
        except Exception as e:
            return False, None, str(e)
    
    @staticmethod
    async def web_search(query: str, num_results: int = 10, freshness: Optional[str] = None) -> tuple[bool, List[Dict], str]:
        """
        Execute web_search via Gateway HTTP API.
        
        Returns: (success, results_list, error_message)
        """
        # Use rate-limited executor if available (Phase 3)
        executor = ToolExecutor._get_rate_limited_executor()
        if executor and RATE_LIMIT_AVAILABLE:
            return await executor.web_search(query, num_results, freshness)
        
        # Fallback to direct execution (no rate limiting)
        args = {"query": query, "count": num_results}
        if freshness:
            args["freshness"] = freshness
        
        success, result, error = ToolExecutor._invoke_tool("web_search", args, timeout=DEFAULT_TIMEOUT_SEARCH)
        
        if success:
            # Result structure: {"content": [{"type": "text", "text": "<json>"}], "details": {...}}
            # The actual search results are in content[0].text as a JSON string
            try:
                if isinstance(result, dict):
                    content_list = result.get("content", [])
                    if content_list and len(content_list) > 0:
                        text_content = content_list[0].get("text", "{}")
                        parsed = json.loads(text_content)
                        results_list = parsed.get("results", [])
                        return True, results_list, ""
                
                # Fallback: try to get results directly
                if isinstance(result, dict):
                    results_list = result.get("results", [])
                    return True, results_list, ""
                elif isinstance(result, list):
                    return True, result, ""
                
                return True, [], ""
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                return False, [], f"Failed to parse search results: {e}"
        
        return success, [], error
    
    @staticmethod
    async def web_fetch(url: str, max_chars: int = 50000) -> tuple[bool, str, str]:
        """
        Execute web_fetch via Gateway HTTP API.
        
        Returns: (success, content, error_message)
        """
        # Use rate-limited executor if available (Phase 3)
        executor = ToolExecutor._get_rate_limited_executor()
        if executor and RATE_LIMIT_AVAILABLE:
            return await executor.web_fetch(url, max_chars)
        
        # Fallback to direct execution (no rate limiting)
        args = {"url": url, "extractMode": "markdown", "maxChars": max_chars}
        success, result, error = ToolExecutor._invoke_tool("web_fetch", args, timeout=DEFAULT_TIMEOUT_FETCH)
        
        if success:
            return True, result if isinstance(result, str) else str(result), error
        
        return success, "", error


# =============================================================================
# Core Primitives
# =============================================================================

class SearchPrimitive:
    """Base class for search pipeline primitives."""
    
    async def execute(self, state: PipelineState) -> PipelineState:
        raise NotImplementedError


class SearchOp(SearchPrimitive):
    """Base search operation - wraps web_search tool."""
    
    def __init__(
        self,
        query: str,
        sources: List[str] = None,
        time_range: Optional[str] = None,
        num_results: int = DEFAULT_MAX_RESULTS,
        retries: int = DEFAULT_RETRY_COUNT,
        use_cache: bool = True,
        cache_ttl: Optional[int] = None
    ):
        self.query = query
        self.sources = sources or ["web"]
        self.time_range = time_range
        self.num_results = num_results
        self.retries = retries
        self.use_cache = use_cache and CACHE_AVAILABLE
        self.cache_ttl = cache_ttl or DEFAULT_TTL.get("search", 3600)
        self._cache = SearchCache(enabled=self.use_cache, ttl_seconds=self.cache_ttl) if self.use_cache else None
    
    async def execute(self, state: PipelineState) -> PipelineState:
        state.query = self.query
        
        # Clamp num_results to tool limits (1-10)
        num_results = max(1, min(10, self.num_results))
        
        # Map time_range to freshness parameter
        # Supports: "year:2025", "year", "month", "week", "day", or None
        freshness = None
        if self.time_range:
            if self.time_range.startswith("year:"):
                # Specific year not supported by freshness param, use just "year"
                freshness = "year"
            elif self.time_range in ["year", "month", "week", "day"]:
                freshness = self.time_range
        
        # Check cache first
        if self._cache:
            cache_key = generate_cache_key("search", self.query, num_results=num_results, freshness=freshness)
            cached_results, cache_metrics = self._cache.get(cache_key)
            
            if cached_results is not None:
                # Cache HIT - restore results
                for idx, item in enumerate(cached_results):
                    state.results.append(SearchResult(
                        title=item.get("title", "Untitled"),
                        url=item.get("url", ""),
                        snippet=item.get("snippet", ""),
                        source=item.get("source", "web"),
                        rank=idx + 1,
                        metadata={"time_range": self.time_range, "from_cache": True}
                    ))
                
                state.metrics["search_count"] = len(state.results)
                state.metrics["search_retries"] = 0
                state.metrics.update(cache_metrics)
                return state
        
        # Retry logic with exponential backoff
        last_error = ""
        for attempt in range(self.retries):
            success, results_list, error = await ToolExecutor.web_search(
                self.query,
                num_results=num_results,
                freshness=freshness
            )
            
            if success:
                break
            
            last_error = error
            if attempt < self.retries - 1:
                await asyncio.sleep(DEFAULT_RETRY_DELAY * (2 ** attempt))
        
        if not success:
            state.errors.append(f"Search failed after {self.retries} attempts: {last_error}")
            return state
        
        # Convert to SearchResult objects
        for idx, item in enumerate(results_list):
            state.results.append(SearchResult(
                title=item.get("title", "Untitled"),
                url=item.get("url", ""),
                snippet=item.get("snippet", ""),
                source=item.get("source", "web"),
                rank=idx + 1,
                metadata={"time_range": self.time_range}
            ))
        
        state.metrics["search_count"] = len(state.results)
        state.metrics["search_retries"] = attempt + 1 if not success else 0
        
        # Write to cache if successful and cache enabled
        if self._cache and success:
            cache_key = generate_cache_key("search", self.query, num_results=num_results, freshness=freshness)
            results_data = [r.to_dict() for r in state.results]
            self._cache.set(cache_key, self.query, results_data, operation_type="search")
            state.metrics["cache_written"] = True
        
        return state


class FetchOp(SearchPrimitive):
    """Fetch content from URLs - wraps web_fetch tool."""
    
    def __init__(
        self,
        urls: List[str] = None,
        extract_text: bool = True,
        max_concurrent: int = 5,
        timeout: int = DEFAULT_TIMEOUT_FETCH,
        retries: int = DEFAULT_RETRY_COUNT
    ):
        self.urls = urls
        self.extract_text = extract_text
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.retries = retries
    
    async def _fetch_single(self, url: str) -> tuple[str, Optional[str]]:
        """Fetch single URL with retries."""
        last_error = None
        
        for attempt in range(self.retries):
            success, content, error = await ToolExecutor.web_fetch(url)
            
            if success:
                return content, None
            
            last_error = error
            if attempt < self.retries - 1:
                await asyncio.sleep(DEFAULT_RETRY_DELAY * (2 ** attempt))
        
        return "", last_error
    
    async def execute(self, state: PipelineState) -> PipelineState:
        # Use results from previous search if no URLs provided
        urls_to_fetch = self.urls or [r.url for r in state.results[:5]]
        
        # Fetch with concurrency limit
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def fetch_with_semaphore(url):
            async with semaphore:
                content, error = await self._fetch_single(url)
                return url, content, error
        
        tasks = [fetch_with_semaphore(url) for url in urls_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                state.errors.append(f"Fetch exception: {str(result)}")
            else:
                url, content, error = result
                if content:
                    state.fetched_content[url] = content
                    state.metrics[f"fetched_{hashlib.md5(url.encode()).hexdigest()[:8]}"] = len(content)
                if error:
                    state.errors.append(f"Fetch failed for {url}: {error}")
        
        state.metrics["fetched_count"] = len(state.fetched_content)
        state.metrics["fetch_errors"] = len([e for e in state.errors if "Fetch" in e])
        
        return state


class FilterOp(SearchPrimitive):
    """Filter results by predicates."""
    
    def __init__(
        self,
        domain: Optional[str] = None,
        exclude_domain: Optional[str] = None,
        date_after: Optional[str] = None,
        custom_fn: Optional[Callable[[SearchResult], bool]] = None
    ):
        self.domain = domain
        self.exclude_domain = exclude_domain
        self.date_after = date_after
        self.custom_fn = custom_fn
    
    async def execute(self, state: PipelineState) -> PipelineState:
        filtered = []
        
        # Compile domain regex if pipe-separated
        domain_pattern = None
        if self.domain and "|" in self.domain:
            domain_pattern = re.compile(self.domain)
        
        # Compile exclude_domain regex if pipe-separated
        exclude_pattern = None
        if self.exclude_domain and "|" in self.exclude_domain:
            exclude_pattern = re.compile(self.exclude_domain)
        
        for result in state.results:
            keep = True
            
            if domain_pattern:
                # Regex match for multiple domains
                if not domain_pattern.search(result.url):
                    keep = False
            elif self.domain:
                # Simple substring match for single domain
                if self.domain not in result.url:
                    keep = False
            
            if exclude_pattern:
                if exclude_pattern.search(result.url):
                    keep = False
            elif self.exclude_domain:
                if self.exclude_domain in result.url:
                    keep = False
            
            if self.custom_fn and not self.custom_fn(result):
                keep = False
            
            if keep:
                filtered.append(result)
        
        removed_count = len(state.results) - len(filtered)
        state.results = filtered
        state.metrics["filtered_count"] = len(filtered)
        state.metrics["filtered_removed"] = removed_count
        
        return state


class RankOp(SearchPrimitive):
    """Re-rank results by signals."""
    
    def __init__(
        self,
        by: str = "relevance",
        custom_fn: Optional[Callable[[SearchResult], float]] = None
    ):
        self.by = by
        self.custom_fn = custom_fn
    
    async def execute(self, state: PipelineState) -> PipelineState:
        if self.custom_fn:
            state.results.sort(key=self.custom_fn, reverse=True)
        elif self.by == "length":
            state.results.sort(key=lambda r: len(r.snippet), reverse=True)
        elif self.by == "domain":
            state.results.sort(key=lambda r: r.domain)
        # "relevance" keeps original rank (assumes search engine already ranked)
        
        # Reassign ranks
        for idx, result in enumerate(state.results):
            result.rank = idx + 1
        
        state.metrics["ranked_by"] = self.by
        return state


class FanoutOp(SearchPrimitive):
    """Execute multiple search variants in parallel."""
    
    def __init__(
        self,
        variants: List[str],
        base_query: str = "",
        parallel: bool = True,
        dedupe_immediately: bool = True
    ):
        self.variants = variants
        self.base_query = base_query
        self.parallel = parallel
        self.dedupe_immediately = dedupe_immediately
    
    async def execute(self, state: PipelineState) -> PipelineState:
        queries = [f"{self.base_query} {v}".strip() for v in self.variants]
        
        if self.parallel:
            # Execute all variants concurrently
            tasks = [SearchOp(q, num_results=5).execute(PipelineState()) for q in queries]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            # Execute sequentially
            results_list = []
            for q in queries:
                op = SearchOp(q, num_results=5)
                result = await op.execute(PipelineState())
                results_list.append(result)
        
        # Aggregate results
        all_results = []
        for result_state in results_list:
            if isinstance(result_state, PipelineState):
                all_results.extend(result_state.results)
            elif isinstance(result_state, Exception):
                state.errors.append(f"Fanout variant error: {result_state}")
        
        # Dedupe if requested
        if self.dedupe_immediately:
            seen_urls = set()
            deduped = []
            for r in all_results:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    deduped.append(r)
            all_results = deduped
        
        state.results = all_results
        state.metrics["fanout_variants"] = len(queries)
        state.metrics["fanout_total_before_dedupe"] = len(all_results) if not self.dedupe_immediately else len(all_results)
        
        return state


class DedupeOp(SearchPrimitive):
    """Remove duplicate results."""
    
    def __init__(self, by: str = "url"):
        self.by = by
    
    async def execute(self, state: PipelineState) -> PipelineState:
        seen = set()
        deduped = []
        
        for result in state.results:
            if self.by == "url":
                key = result.url
            elif self.by == "domain":
                key = result.domain
            elif self.by == "snippet_hash":
                key = hashlib.md5(result.snippet.encode()).hexdigest()
            else:
                key = result.url
            
            if key not in seen:
                seen.add(key)
                deduped.append(result)
        
        removed_count = len(state.results) - len(deduped)
        state.results = deduped
        state.metrics["deduped_count"] = len(deduped)
        state.metrics["removed_duplicates"] = removed_count
        
        return state


class LLMSummarizer:
    """LLM-based summarization for smart compression (Phase 2)."""
    
    def __init__(self, model: str = DEFAULT_SUMMARIZER_MODEL):
        self.model = model
        self.base_url = OLLAMA_BASE_URL
    
    async def summarize_results(
        self,
        results: List[SearchResult],
        target_tokens: int,
        query_context: str = ""
    ) -> str:
        """
        Summarize multiple search results into a coherent summary.
        
        Args:
            results: List of SearchResult objects to summarize
            target_tokens: Target token budget for the summary
            query_context: Original search query for context
        
        Returns:
            Summarized text string
        """
        # Build input from results
        items = []
        for i, r in enumerate(results, 1):
            item = f"[{i}] {r.title}\nURL: {r.url}\nSummary: {r.snippet}\n"
            items.append(item)
        
        input_text = "\n".join(items)
        
        # Estimate output length (4 chars per token)
        max_chars = target_tokens * 4
        
        # Build prompt
        prompt = f"""You are a research assistant. Summarize the following search results about "{query_context}" into a concise, coherent summary.

Guidelines:
- Focus on key findings and main points
- Remove redundant information
- Cite sources by number [1], [2], etc.
- Keep it under {max_chars} characters
- Maintain factual accuracy

Search Results:
{input_text}

Provide only the summary, no preamble."""

        try:
            # Call Ollama API
            import aiohttp
            
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "top_p": 0.9
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        summary = result.get("response", "")
                        return summary
                    else:
                        error_text = await response.text()
                        raise Exception(f"Ollama API error: {response.status} - {error_text}")
        
        except Exception as e:
            # Fallback to truncation
            return f"[Summarization failed: {str(e)}. Using fallback.]\n\n{input_text[:max_chars]}..."
    
    async def compress_with_summary(
        self,
        state: PipelineState,
        target_tokens: int
    ) -> PipelineState:
        """
        Compress results by summarizing groups of results.
        
        Strategy: Group results into batches that fit within token budget,
        then summarize each batch.
        """
        if not state.results:
            return state
        
        # Calculate how many results we can keep
        total_tokens = sum(r.token_estimate() for r in state.results)
        
        if total_tokens <= target_tokens:
            # Already under budget, no compression needed
            state.metrics["compressed_tokens"] = total_tokens
            state.metrics["compression_method"] = "none_needed"
            return state
        
        # Group results into batches
        batch_size = max(3, len(state.results) // 3)  # At least 3 results per batch
        batches = [state.results[i:i+batch_size] for i in range(0, len(state.results), batch_size)]
        
        # Summarize each batch
        summaries = []
        for i, batch in enumerate(batches):
            batch_query = state.query or "search results"
            summary = await self.summarize_results(batch, target_tokens // len(batches), batch_query)
            summaries.append(SearchResult(
                title=f"Summary Batch {i+1}/{len(batches)}",
                url="synthetic://summary",
                snippet=summary,
                source="llm_summary",
                rank=i+1,
                metadata={"batch_size": len(batch), "original_urls": [r.url for r in batch]}
            ))
        
        state.results = summaries
        state.metrics["compressed_tokens"] = sum(r.token_estimate() for r in summaries)
        state.metrics["compression_method"] = "llm_summarization"
        state.metrics["batches_summarized"] = len(batches)
        
        return state


class CompressOp(SearchPrimitive):
    """Compress results to target token count."""
    
    def __init__(self, target_tokens: int = 500, strategy: str = "truncate"):
        self.target_tokens = target_tokens
        self.strategy = strategy
        self.summarizer = LLMSummarizer()
    
    async def execute(self, state: PipelineState) -> PipelineState:
        if self.strategy == "truncate":
            tokens_used = 0
            compressed = []
            
            for result in state.results:
                token_cost = result.token_estimate()
                if tokens_used + token_cost <= self.target_tokens:
                    compressed.append(result)
                    tokens_used += token_cost
                else:
                    # Truncate snippet to fit remaining budget
                    remaining = self.target_tokens - tokens_used
                    if remaining > 10:  # At least include title
                        max_snippet_len = remaining * 4
                        truncated = SearchResult(
                            title=result.title,
                            url=result.url,
                            snippet=result.snippet[:max_snippet_len] + "...",
                            source=result.source,
                            rank=result.rank,
                            metadata=result.metadata
                        )
                        compressed.append(truncated)
                    break
            
            state.results = compressed
            state.metrics["compressed_tokens"] = sum(r.token_estimate() for r in compressed)
            state.metrics["compression_method"] = "truncate"
        
        elif self.strategy == "summarize":
            # Phase 2: Use LLM to summarize multiple results
            state = await self.summarizer.compress_with_summary(state, self.target_tokens)
        
        return state


class ExtractOp(SearchPrimitive):
    """Extract structured data from fetched content using CSS selectors."""
    
    def __init__(self, selectors: Dict[str, str]):
        """
        selectors: dict mapping field names to CSS selectors
        Example: {"title": "h1", "content": ".article-body", "author": ".byline"}
        """
        self.selectors = selectors
    
    async def execute(self, state: PipelineState) -> PipelineState:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            state.errors.append("beautifulsoup4 required: pip install beautifulsoup4")
            return state
        
        for url, html in state.fetched_content.items():
            try:
                soup = BeautifulSoup(html, 'html.parser')
                extracted = {"url": url}
                
                for field_name, selector in self.selectors.items():
                    element = soup.select_one(selector)
                    extracted[field_name] = element.get_text(strip=True) if element else ""
                
                state.extracted_data.append(extracted)
                
            except Exception as e:
                state.errors.append(f"Extract failed for {url}: {str(e)}")
        
        state.metrics["extracted_count"] = len(state.extracted_data)
        return state


# =============================================================================
# Phase 3: Advanced Primitives
# =============================================================================

class ClusterOp(SearchPrimitive):
    """
    Cluster results by topic/embedding similarity.
    
    Uses embedding-based clustering (sentence-transformers) when available.
    Falls back to keyword-based clustering if embeddings unavailable.
    """
    
    def __init__(
        self,
        n_clusters: int = 3,
        method: str = "auto",  # auto | embedding | keyword
        min_cluster_size: int = 1,
        model_name: str = "all-MiniLM-L6-v2"
    ):
        self.n_clusters = n_clusters
        self.method = method
        self.min_cluster_size = min_cluster_size
        self.model_name = model_name
    
    async def execute(self, state: PipelineState) -> PipelineState:
        if len(state.results) < 2:
            state.errors.append("Not enough results for clustering")
            return state
        
        # Determine clustering method
        use_embeddings = (
            self.method == "embedding" or
            (self.method == "auto" and EMBEDDING_CLUSTER_AVAILABLE)
        )
        
        if use_embeddings and EMBEDDING_CLUSTER_AVAILABLE:
            # Use embedding-based clustering
            try:
                texts = [f"{r.title} {r.snippet}" for r in state.results]
                
                clusterer = EmbeddingClusterer()
                clustering_result = await clusterer.cluster(
                    texts,
                    n_clusters=self.n_clusters,
                    min_cluster_size=self.min_cluster_size
                )
                
                # Map cluster assignments back to results
                cluster_map = {}
                for cluster_id, items in clustering_result.get("clusters", {}).items():
                    for item in items:
                        cluster_map[item["index"]] = cluster_id
                
                # Add cluster info to each result
                for idx, r in enumerate(state.results):
                    r.metadata["cluster_id"] = cluster_map.get(idx, "unclustered")
                
                # Reorder results by cluster
                clustered_results = []
                for cluster_id in sorted(clustering_result.get("clusters", {}).keys()):
                    for idx, r in enumerate(state.results):
                        if r.metadata.get("cluster_id") == cluster_id:
                            clustered_results.append(r)
                
                # Add unclustered at end
                for r in state.results:
                    if r not in clustered_results:
                        clustered_results.append(r)
                
                state.results = clustered_results
                state.metadata["clustering_method"] = "embedding"
                state.metadata["clustering_model"] = self.model_name
                state.metadata["n_clusters_found"] = clustering_result.get("n_clusters_found", 0)
                state.metadata["cluster_sizes"] = clustering_result.get("cluster_sizes", [])
                state.metrics["clustered_count"] = clustering_result.get("assigned_texts", 0)
                
                return state
                
            except Exception as e:
                state.errors.append(f"Embedding clustering failed: {e}. Falling back to keyword.")
                # Fall through to keyword clustering
        
        # Keyword-based clustering (fallback or explicit choice)
        clusters = self._keyword_cluster(state.results)
        
        # Reorder results by cluster
        clustered_results = []
        for cluster_name, items in sorted(clusters.items()):
            if len(items) >= self.min_cluster_size:
                for r in items:
                    r.metadata["cluster_id"] = f"cluster_{cluster_name}"
                clustered_results.extend(items)
                state.metadata[f"cluster_{cluster_name}"] = len(items)
        
        # Add unclustered items at the end
        clustered_count = len(clustered_results)
        if clustered_count < len(state.results):
            for r in state.results:
                if r not in clustered_results:
                    r.metadata["cluster_id"] = "unclustered"
                    clustered_results.append(r)
        
        state.results = clustered_results
        state.metadata["clustering_method"] = "keyword"
        state.metadata["n_clusters_found"] = len([c for c in clusters.values() if len(c) >= self.min_cluster_size])
        state.metrics["clustered_count"] = clustered_count
        
        return state
    
    def _keyword_cluster(self, results: List[SearchResult]) -> Dict[str, List[SearchResult]]:
        """
        Simple keyword-based clustering.
        Groups results by common terms in title/snippet.
        """
        from collections import defaultdict
        
        # Extract key terms from all results
        term_freq = defaultdict(int)
        for r in results:
            text = f"{r.title} {r.snippet}".lower()
            words = re.findall(r'\b[a-z]{4,}\b', text)  # Words 4+ chars
            for word in set(words):  # Count each word once per result
                term_freq[word] += 1
        
        # Select top terms as cluster seeds
        top_terms = sorted(term_freq.items(), key=lambda x: x[1], reverse=True)[:self.n_clusters * 2]
        
        # Assign results to clusters based on term presence
        clusters = defaultdict(list)
        for r in results:
            text = f"{r.title} {r.snippet}".lower()
            best_term = None
            best_score = 0
            
            for term, freq in top_terms:
                score = text.count(term) * freq
                if score > best_score:
                    best_term = term
                    best_score = score
            
            if best_term:
                clusters[best_term].append(r)
        
        return dict(clusters)


class TimelineOp(SearchPrimitive):
    """
    Sort results by date and detect trends over time.
    
    Attempts to extract dates from snippets/URLs and orders results chronologically.
    Can identify trending topics by analyzing result distribution over time.
    """
    
    def __init__(
        self,
        order: str = "desc",  # desc (newest first) | asc (oldest first)
        detect_trends: bool = True,
        time_buckets: str = "month"  # day | week | month | year
    ):
        self.order = order
        self.detect_trends = detect_trends
        self.time_buckets = time_buckets
    
    async def execute(self, state: PipelineState) -> PipelineState:
        # Try to extract dates from results
        dated_results = []
        undated_results = []
        
        for r in state.results:
            date = self._extract_date(r)
            if date:
                r.metadata["extracted_date"] = date.isoformat()
                dated_results.append((date, r))
            else:
                undated_results.append(r)
        
        # Sort dated results
        reverse = (self.order == "desc")
        dated_results.sort(key=lambda x: x[0], reverse=reverse)
        
        # Rebuild results list: dated first, then undated
        state.results = [r for _, r in dated_results] + undated_results
        
        # Detect trends if requested
        if self.detect_trends and len(dated_results) > 0:
            trend_analysis = self._analyze_trends(dated_results)
            state.metadata["trend_analysis"] = trend_analysis
            state.metrics["results_with_dates"] = len(dated_results)
            state.metrics["results_without_dates"] = len(undated_results)
        
        state.metrics["timeline_ordered"] = True
        return state
    
    def _extract_date(self, result: SearchResult) -> Optional[Any]:
        """
        Extract date from result snippet or URL.
        Returns datetime.date object or None.
        """
        import re
        from datetime import datetime
        
        # Common date patterns
        patterns = [
            r'(\d{4})-(\d{2})-(\d{2})',  # YYYY-MM-DD
            r'(\d{2})/(\d{2})/(\d{4})',  # MM/DD/YYYY
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{1,2}),? (\d{4})',  # Month DD, YYYY
            r'(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{4})',  # DD Month YYYY
        ]
        
        text = f"{result.title} {result.snippet} {result.url}"
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    groups = match.groups()
                    if len(groups) == 3:
                        if groups[0].isdigit() and len(groups[0]) == 4:  # YYYY-MM-DD
                            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                        elif groups[2].isdigit() and len(groups[2]) == 4:  # MM/DD/YYYY
                            month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
                        else:  # Month name patterns
                            month_map = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                                       'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
                            if groups[0].isalpha():
                                month = month_map.get(groups[0][:3], 1)
                                day = int(groups[1])
                                year = int(groups[2])
                            else:
                                day = int(groups[0])
                                month = month_map.get(groups[1][:3], 1)
                                year = int(groups[2])
                        
                        if 1 <= month <= 12 and 1 <= day <= 31 and 2000 <= year <= 2100:
                            from datetime import date
                            return date(year, month, day)
                except (ValueError, TypeError):
                    pass
        
        return None
    
    def _analyze_trends(self, dated_results: List[tuple]) -> Dict[str, Any]:
        """
        Analyze result distribution over time to detect trends.
        """
        from collections import defaultdict
        from datetime import timedelta
        
        if not dated_results:
            return {}
        
        # Bucket results by time period
        buckets = defaultdict(int)
        for date, _ in dated_results:
            if self.time_buckets == "year":
                key = date.year
            elif self.time_buckets == "month":
                key = f"{date.year}-{date.month:02d}"
            elif self.time_buckets == "week":
                # ISO week number
                key = f"{date.isocalendar()[0]}-W{date.isocalendar()[1]:02d}"
            else:  # day
                key = date.isoformat()
            
            buckets[key] += 1
        
        # Find peak period
        if buckets:
            peak_period = max(buckets.items(), key=lambda x: x[1])
            avg_per_bucket = sum(buckets.values()) / len(buckets)
            
            trend = {
                "time_buckets": self.time_buckets,
                "total_dated_results": len(dated_results),
                "n_buckets": len(buckets),
                "peak_period": peak_period[0],
                "peak_count": peak_period[1],
                "average_per_bucket": round(avg_per_bucket, 2),
                "trend_direction": "increasing" if list(buckets.keys())[-1] == peak_period[0] else
                                  "decreasing" if list(buckets.keys())[0] == peak_period[0] else
                                  "stable"
            }
            return trend
        
        return {}


class SentimentOp(SearchPrimitive):
    """
    Analyze sentiment/tone/bias of search results.
    
    Uses LLM to classify each result's sentiment as positive/negative/neutral
    and detects potential bias indicators.
    """
    
    def __init__(
        self,
        model: str = DEFAULT_SUMMARIZER_MODEL,
        analyze_bias: bool = True,
        batch_size: int = 5
    ):
        self.model = model
        self.analyze_bias = analyze_bias
        self.batch_size = batch_size
        self.base_url = OLLAMA_BASE_URL
    
    async def execute(self, state: PipelineState) -> PipelineState:
        if not state.results:
            return state
        
        # Process in batches
        for i in range(0, len(state.results), self.batch_size):
            batch = state.results[i:i + self.batch_size]
            await self._analyze_batch(batch)
        
        # Aggregate sentiment stats
        sentiments = [r.metadata.get("sentiment", "unknown") for r in state.results]
        sentiment_counts = {}
        for s in sentiments:
            sentiment_counts[s] = sentiment_counts.get(s, 0) + 1
        
        state.metadata["sentiment_distribution"] = sentiment_counts
        state.metrics["sentiment_analyzed"] = len(state.results)
        
        return state
    
    async def _analyze_batch(self, results: List[SearchResult]):
        """Analyze sentiment for a batch of results."""
        import aiohttp
        
        for result in results:
            prompt = f"""Analyze the sentiment and tone of this text. Respond with ONLY a JSON object.

Text:
Title: {result.title}
Snippet: {result.snippet}

Classify as:
- sentiment: "positive" | "negative" | "neutral"
- confidence: 0.0 to 1.0
- tone: e.g., "factual", "promotional", "critical", "alarmist", "optimistic"
{"- bias_indicators: list of phrases suggesting bias (if analyze_bias is enabled)" if self.analyze_bias else ""}

Response format:
{{
  "sentiment": "...",
  "confidence": 0.0,
  "tone": "...",
  {"\"bias_indicators\": [...]" if self.analyze_bias else ""}
}}"""

            try:
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.1,
                        "top_p": 0.9
                    }
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.base_url}/api/generate",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status == 200:
                            result_data = await response.json()
                            analysis_text = result_data.get("response", "{}")
                            
                            # Parse JSON response
                            import json
                            try:
                                # Clean up markdown code blocks if present
                                analysis_text = analysis_text.strip()
                                if analysis_text.startswith("```"):
                                    analysis_text = analysis_text.split("```json")[-1].split("```")[0].strip()
                                
                                analysis = json.loads(analysis_text)
                                result.metadata["sentiment"] = analysis.get("sentiment", "unknown")
                                result.metadata["sentiment_confidence"] = analysis.get("confidence", 0.0)
                                result.metadata["tone"] = analysis.get("tone", "unknown")
                                if self.analyze_bias and "bias_indicators" in analysis:
                                    result.metadata["bias_indicators"] = analysis["bias_indicators"]
                            except json.JSONDecodeError:
                                result.metadata["sentiment"] = "parse_error"
                                result.metadata["tone"] = "unknown"
                        else:
                            result.metadata["sentiment"] = "analysis_failed"
            
            except Exception as e:
                result.metadata["sentiment"] = f"error: {str(e)[:50]}"


class AlertOp(SearchPrimitive):
    """
    Monitor for new results compared to a baseline.
    
    Compares current search results against cached baseline to detect:
    - New URLs not seen before
    - Significant changes in result composition
    - Emerging topics or sources
    
    Requires cross-session caching to be enabled.
    Supports encryption for sensitive monitoring topics.
    """
    
    def __init__(
        self,
        baseline_name: str,
        create_baseline: bool = False,
        notify_on_new: bool = True,
        min_new_results_threshold: int = 3,
        encrypt_baseline: bool = True
    ):
        self.baseline_name = baseline_name
        self.create_baseline = create_baseline
        self.notify_on_new = notify_on_new
        self.min_new_results_threshold = min_new_results_threshold
        self.encrypt_baseline = encrypt_baseline
        self._cache = SearchCache(enabled=CACHE_AVAILABLE, encryption_key=os.getenv("SEARCH_CACHE_ENCRYPTION_KEY")) if CACHE_AVAILABLE else None
    
    async def execute(self, state: PipelineState) -> PipelineState:
        if not CACHE_AVAILABLE:
            state.errors.append("AlertOp requires caching to be enabled (Phase 2)")
            return state
        
        cache_key = f"alert_baseline:{self.baseline_name}"
        
        if self.create_baseline:
            # Save current results as baseline (encrypted if requested)
            baseline_data = [r.to_dict() for r in state.results]
            self._cache.set(
                cache_key,
                state.query or "baseline",
                baseline_data,
                operation_type="alert_baseline",
                encrypt=self.encrypt_baseline
            )
            state.metadata["alert_baseline_created"] = self.baseline_name
            state.metadata["baseline_result_count"] = len(state.results)
            state.metadata["baseline_encrypted"] = self.encrypt_baseline
        else:
            # Compare against existing baseline
            baseline_results, metrics = self._cache.get(cache_key, decrypt=True)
            
            if baseline_results is None:
                state.errors.append(f"Baseline '{self.baseline_name}' not found. Create it first with create_baseline=True")
                if metrics.get("decryption_error"):
                    state.errors.append(f"Decryption failed: {metrics['decryption_error']}")
                return state
            
            # Find new results
            baseline_urls = {r.get("url") for r in baseline_results}
            new_results = [r for r in state.results if r.url not in baseline_urls]
            
            state.metadata["baseline_comparison"] = {
                "baseline_count": len(baseline_results),
                "current_count": len(state.results),
                "new_results_count": len(new_results),
                "new_urls": [r.url for r in new_results[:10]]  # First 10 new URLs
            }
            
            if len(new_results) >= self.min_new_results_threshold:
                state.metadata["alert_triggered"] = True
                state.metadata["alert_reason"] = f"{len(new_results)} new results detected (threshold: {self.min_new_results_threshold})"
                
                # Prepend new results to highlight them
                state.results = new_results + state.results
            else:
                state.metadata["alert_triggered"] = False
        
        return state


# =============================================================================
# Pre-built Pipeline Templates
# =============================================================================

class ResearchPipeline:
    """Pre-built pipeline for academic/technical research."""
    
    def __init__(
        self,
        topic: str,
        sources: List[str] = None,
        time_range: str = "year:2024",
        max_results: int = 50
    ):
        self.topic = topic
        self.sources = sources or ["arxiv.org", "aclanthology.org", "scholar.google.com"]
        self.time_range = time_range
        self.max_results = max_results
    
    async def run(self, compression_strategy: str = "truncate", target_tokens: int = 800) -> PipelineState:
        """Execute the research pipeline."""
        return await SearchPipeline() \
            .search(self.topic, time_range=self.time_range, num_results=self.max_results) \
            .filter(domain="|".join(self.sources)) \
            .dedupe(by="url") \
            .rank_by("relevance") \
            .compress(target_tokens=target_tokens, strategy=compression_strategy) \
            .execute()


class CompetitiveAnalysisPipeline:
    """Pre-built pipeline for competitive intelligence."""
    
    def __init__(
        self,
        companies: List[str],
        topics: List[str],
        sources: List[str] = None
    ):
        self.companies = companies
        self.topics = topics
        self.sources = sources or ["news", "blogs", "press releases"]
    
    async def run(self, compression_strategy: str = "truncate", target_tokens: int = 800) -> PipelineState:
        """Execute competitive analysis pipeline."""
        # Build fanout queries: company + topic combinations
        variants = []
        for company in self.companies:
            for topic in self.topics:
                variants.append(f"{company} {topic}")
        
        return await SearchPipeline() \
            .fanout(variants, base_query="", parallel=True) \
            .dedupe(by="domain") \
            .fetch(max_concurrent=10) \
            .compress(target_tokens=target_tokens, strategy=compression_strategy) \
            .execute()


class CybersecurityThreatIntelPipeline:
    """Pre-built pipeline for cybersecurity threat intelligence."""
    
    def __init__(
        self,
        threat_type: str,
        targets: List[str] = None,
        include_cve: bool = True,
        time_range: str = "month"
    ):
        self.threat_type = threat_type
        self.targets = targets or []
        self.include_cve = include_cve
        self.time_range = time_range
    
    async def run(self, compression_strategy: str = "truncate", target_tokens: int = 800) -> PipelineState:
        """Execute threat intelligence pipeline."""
        queries = [self.threat_type]
        
        if self.targets:
            queries.extend([f"{self.threat_type} {target}" for target in self.targets])
        
        if self.include_cve:
            queries.append(f"{self.threat_type} CVE vulnerability")
        
        # Trusted security sources
        security_domains = [
            "cve.mitre.org",
            "nvd.nist.gov",
            "github.com/advisories",
            "securityweek.com",
            "bleepingcomputer.com",
            "therecord.media",
            "mandiant.com",
            "crowdstrike.com"
        ]
        
        return await SearchPipeline() \
            .fanout(queries, base_query="", parallel=True) \
            .filter(domain="|".join(security_domains)) \
            .dedupe(by="url") \
            .fetch(max_concurrent=5) \
            .extract({
                "title": "h1",
                "summary": ".summary, .abstract, p:first-of-type",
                "severity": ".severity, .cvss-score",
                "cve_ids": ".cve-id, .cve-list"
            }) \
            .compress(target_tokens=1200) \
            .execute()


# =============================================================================
# Pipeline Builder
# =============================================================================

class SearchPipeline:
    """Fluent builder for search pipelines."""
    
    def __init__(self, session_id: str = None):
        self.operations: List[SearchPrimitive] = []
        self.session_id = session_id or hashlib.md5(str(asyncio.get_event_loop().time()).encode()).hexdigest()[:8]
        self.lcm = None
        
        # Initialize Lite-LCM if available
        if LITE_LCM_AVAILABLE:
            try:
                self.lcm = LiteLCM()
            except Exception as e:
                pass  # Silently ignore, will work without Lite-LCM
    
    def search(
        self,
        query: str,
        sources: List[str] = None,
        time_range: Optional[str] = None,
        num_results: int = DEFAULT_MAX_RESULTS,
        retries: int = DEFAULT_RETRY_COUNT,
        use_cache: bool = True,
        cache_ttl: Optional[int] = None
    ) -> 'SearchPipeline':
        self.operations.append(SearchOp(query, sources, time_range, num_results, retries, use_cache, cache_ttl))
        return self
    
    def fetch(
        self,
        urls: List[str] = None,
        extract_text: bool = True,
        max_concurrent: int = 5,
        timeout: int = DEFAULT_TIMEOUT_FETCH,
        retries: int = DEFAULT_RETRY_COUNT
    ) -> 'SearchPipeline':
        self.operations.append(FetchOp(urls, extract_text, max_concurrent, timeout, retries))
        return self
    
    def filter(
        self,
        domain: Optional[str] = None,
        exclude_domain: Optional[str] = None,
        date_after: Optional[str] = None,
        custom_fn: Optional[Callable[[SearchResult], bool]] = None
    ) -> 'SearchPipeline':
        self.operations.append(FilterOp(domain, exclude_domain, date_after, custom_fn))
        return self
    
    def rank_by(
        self,
        by: str = "relevance",
        custom_fn: Optional[Callable[[SearchResult], float]] = None
    ) -> 'SearchPipeline':
        self.operations.append(RankOp(by, custom_fn))
        return self
    
    def fanout(
        self,
        variants: List[str],
        base_query: str = "",
        parallel: bool = True,
        dedupe_immediately: bool = True
    ) -> 'SearchPipeline':
        self.operations.append(FanoutOp(variants, base_query, parallel, dedupe_immediately))
        return self
    
    def dedupe(self, by: str = "url") -> 'SearchPipeline':
        self.operations.append(DedupeOp(by))
        return self
    
    def compress(self, target_tokens: int = 500, strategy: str = "truncate") -> 'SearchPipeline':
        self.operations.append(CompressOp(target_tokens, strategy))
        return self
    
    def extract(self, selectors: Dict[str, str]) -> 'SearchPipeline':
        self.operations.append(ExtractOp(selectors))
        return self
    
    # Phase 3: Advanced Primitives
    
    def cluster(
        self,
        n_clusters: int = 3,
        method: str = "keyword",
        min_cluster_size: int = 2
    ) -> 'SearchPipeline':
        """Cluster results by topic/embedding similarity."""
        self.operations.append(ClusterOp(n_clusters, method, min_cluster_size))
        return self
    
    def timeline(
        self,
        order: str = "desc",
        detect_trends: bool = True,
        time_buckets: str = "month"
    ) -> 'SearchPipeline':
        """Sort results by date and detect trends over time."""
        self.operations.append(TimelineOp(order, detect_trends, time_buckets))
        return self
    
    def sentiment(
        self,
        model: str = DEFAULT_SUMMARIZER_MODEL,
        analyze_bias: bool = True,
        batch_size: int = 5
    ) -> 'SearchPipeline':
        """Analyze sentiment/tone/bias of search results."""
        self.operations.append(SentimentOp(model, analyze_bias, batch_size))
        return self
    
    def alert(
        self,
        baseline_name: str,
        create_baseline: bool = False,
        notify_on_new: bool = True,
        min_new_results_threshold: int = 3
    ) -> 'SearchPipeline':
        """Monitor for new results compared to a baseline."""
        self.operations.append(AlertOp(baseline_name, create_baseline, notify_on_new, min_new_results_threshold))
        return self
    
    async def execute(self) -> PipelineState:
        """Execute the pipeline and return final state."""
        state = PipelineState(session_id=self.session_id, lcm=self.lcm)
        
        for op in self.operations:
            state = await op.execute(state)
        
        return state
    
    def execute_sync(self) -> PipelineState:
        """Synchronous wrapper for non-async contexts."""
        return asyncio.run(self.execute())


# =============================================================================
# Example Usage
# =============================================================================

async def example_basic():
    """Basic search with filtering and compression."""
    results = await SearchPipeline() \
        .search("LLM context window optimization 2025") \
        .filter(domain="arxiv.org") \
        .rank_by("relevance") \
        .compress(target_tokens=500) \
        .execute()
    
    print(f"Found {len(results.results)} results")
    print(f"Metrics: {results.metrics}")
    print(f"Errors: {results.errors}")


async def example_fanout():
    """Fan-out search with deduplication."""
    results = await SearchPipeline() \
        .fanout(["2024", "2025", "2026"], base_query="transformer architecture improvements") \
        .dedupe(by="url") \
        .rank_by("relevance") \
        .execute()
    
    print(f"Fan-out found {len(results.results)} unique results")
    print(f"Metrics: {results.metrics}")


async def example_research_pipeline():
    """Pre-built research pipeline."""
    pipeline = ResearchPipeline(
        topic="LLM context management techniques",
        sources=["arxiv.org", "aclanthology.org"],
        time_range="year:2025",
        max_results=30
    )
    
    results = await pipeline.run()
    print(f"Research found {len(results.results)} relevant papers")
    print(f"Metrics: {results.metrics}")


async def example_cybersecurity_pipeline():
    """Pre-built cybersecurity threat intel pipeline."""
    pipeline = CybersecurityThreatIntelPipeline(
        threat_type="zero-day exploit Chrome",
        targets=["Windows", "macOS"],
        include_cve=True,
        time_range="month"
    )
    
    results = await pipeline.run()
    print(f"Threat intel: {len(results.results)} items")
    print(f"Extracted: {len(results.extracted_data)} detailed records")
    print(f"Metrics: {results.metrics}")


# =============================================================================
# Phase 3 Examples
# =============================================================================

async def example_cluster():
    """Cluster results by topic."""
    print("=== Phase 3: Clustering Demo ===\n")
    
    results = await SearchPipeline() \
        .search("AI agent architectures 2025") \
        .filter(domain="arxiv.org|github.com|medium.com") \
        .cluster(n_clusters=3, method="keyword") \
        .execute()
    
    print(f"Found {len(results.results)} results")
    print(f"Clusters detected: {results.metadata.get('n_clusters_found', 0)}")
    print(f"Cluster metadata: {results.metadata}")


async def example_timeline():
    """Timeline analysis with trend detection."""
    print("=== Phase 3: Timeline Analysis Demo ===\n")
    
    results = await SearchPipeline() \
        .search("LLM context management techniques") \
        .timeline(order="desc", detect_trends=True, time_buckets="month") \
        .execute()
    
    print(f"Found {len(results.results)} results")
    print(f"Results with dates: {results.metrics.get('results_with_dates', 0)}")
    print(f"Trend analysis: {results.metadata.get('trend_analysis', {})}")


async def example_sentiment():
    """Sentiment analysis of search results."""
    print("=== Phase 3: Sentiment Analysis Demo ===\n")
    
    results = await SearchPipeline() \
        .search("AI safety concerns 2025") \
        .sentiment(analyze_bias=True, batch_size=5) \
        .execute()
    
    print(f"Found {len(results.results)} results")
    print(f"Sentiment distribution: {results.metadata.get('sentiment_distribution', {})}")
    
    # Show first 3 results with sentiment
    for i, r in enumerate(results.results[:3]):
        print(f"\n{i+1}. {r.title}")
        print(f"   Sentiment: {r.metadata.get('sentiment', 'unknown')} "
              f"(confidence: {r.metadata.get('sentiment_confidence', 0):.2f})")
        print(f"   Tone: {r.metadata.get('tone', 'unknown')}")


async def example_rate_limit_demo():
    """Demonstrate rate limiting in action."""
    print("=== Phase 3: Rate Limiting Demo ===\n")
    
    # This will show rate limiting kicking in
    start_time = asyncio.get_event_loop().time()
    
    results = await SearchPipeline() \
        .search("quantum computing breakthroughs") \
        .execute()
    
    elapsed = asyncio.get_event_loop().time() - start_time
    
    print(f"Search completed in {elapsed:.2f}s")
    print(f"Rate limit metrics: {results.metrics}")
    
    # Check if rate limiter is active
    executor = ToolExecutor._get_rate_limited_executor()
    if executor:
        stats = executor.get_stats()
        print(f"\nCircuit breaker states:")
        for op_type, data in stats["circuit_breakers"].items():
            print(f"  {op_type}: {data['state']}")
        print(f"\nAvailable tokens:")
        for op_type, data in stats["rate_limiters"].items():
            print(f"  {op_type}: {data['available_tokens']:.1f}/{data['burst_size']}")


if __name__ == "__main__":
    print("Search-as-Code SDK v0.3.0 - Phase 3 Complete")
    print("=" * 50)
    print("\nRun examples:")
    print("  python sdk.py basic         - Basic search")
    print("  python sdk.py fanout        - Fan-out + dedupe")
    print("  python sdk.py research      - Research pipeline")
    print("  python sdk.py cybersecurity - Threat intel pipeline")
    print("  python sdk.py cluster       - Cluster results by topic (Phase 3)")
    print("  python sdk.py timeline      - Timeline analysis (Phase 3)")
    print("  python sdk.py sentiment     - Sentiment analysis (Phase 3)")
    print("  python sdk.py rate_limit    - Rate limiting demo (Phase 3)")
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "basic":
            asyncio.run(example_basic())
        elif sys.argv[1] == "fanout":
            asyncio.run(example_fanout())
        elif sys.argv[1] == "research":
            asyncio.run(example_research_pipeline())
        elif sys.argv[1] == "cybersecurity":
            asyncio.run(example_cybersecurity_pipeline())
        elif sys.argv[1] == "cluster":
            asyncio.run(example_cluster())
        elif sys.argv[1] == "timeline":
            asyncio.run(example_timeline())
        elif sys.argv[1] == "sentiment":
            asyncio.run(example_sentiment())
        elif sys.argv[1] == "rate_limit":
            asyncio.run(example_rate_limit_demo())
        else:
            print(f"Unknown example: {sys.argv[1]}")
