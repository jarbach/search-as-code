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
    
    Auth: Uses Gateway token auth (configured via env vars or constants)
    Policy: Tool availability filtered through Gateway tool policy
    """
    
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


if __name__ == "__main__":
    print("Search-as-Code SDK v0.2.1 - Phase 2 Complete")
    print("=" * 50)
    print("\nRun examples:")
    print("  python sdk.py basic         - Basic search")
    print("  python sdk.py fanout        - Fan-out + dedupe")
    print("  python sdk.py research      - Research pipeline")
    print("  python sdk.py cybersecurity - Threat intel pipeline")
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "basic":
            asyncio.run(example_basic())
        elif sys.argv[1] == "fanout":
            asyncio.run(example_fanout())
        elif sys.argv[1] == "research":
            asyncio.run(example_research_pipeline())
        elif sys.argv[1] == "cybersecurity":
            asyncio.run(example_cybersecurity_pipeline())
        else:
            print(f"Unknown example: {sys.argv[1]}")
