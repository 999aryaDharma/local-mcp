# docctx — Technical Design Document

**Version:** 1.0  
**Phase:** M1 (MVP)  
**Status:** Draft  
**Tanggal:** 2026-06

---

## 1. Arsitektur Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    OPERATOR INTERFACE                        │
│   CLI (Typer) — add, refresh, remove, list, inspect,        │
│                  explain, doctor, query, eval                │
└──────────────────────────┬───────────────────────────────────┘
                           │
          ┌────────────────▼─────────────────┐
          │          CORE SERVICES           │
          │  ┌─────────────┐ ┌────────────┐  │
          │  │IngestionSvc │ │RetrievalSvc│  │
          │  └──────┬──────┘ └─────┬──────┘  │
          └─────────┼──────────────┼──────────┘
                    │              │
          ┌─────────▼──────────────▼──────────┐
          │          DATA LAYER               │
          │  SQLite (store.db) + cache/       │
          └───────────────────────────────────┘
                    │
          ┌─────────▼──────────────────────────┐
          │         AGENT INTERFACE            │
          │  MCP Server (stdio) — 3 tools      │
          └────────────────────────────────────┘
```

**Prinsip satu arah:** Ingestion → DB. Retrieval ← DB. Keduanya tidak pernah saling call.

---

## 2. Token Efficiency Architecture

Ini adalah concern nomor satu. Semua keputusan desain dibuat dengan mempertimbangkan dampak ke token consumption agent.

### 2.1 Problem Statement

Competitor tools seperti Context7 mengembalikan dokumen lengkap atau section besar. Akibatnya:

- Agent menghabiskan 3000-8000 tokens untuk satu retrieval call
- Banyak content tidak relevan ikut terbawa
- Agent tidak bisa fit banyak context dalam satu prompt

Target docctx: **< 1500 tokens untuk typical search result (5 chunks, standard mode)**.

### 2.2 Two-Tier Content Architecture

```
Chunk Record:
┌─────────────────────────────────────┐
│ id, pack_name, heading_path         │  ~50 tokens (metadata)
├─────────────────────────────────────┤
│ summary (120 chars, rule-based)     │  ~30 tokens (always available)
├─────────────────────────────────────┤
│ content_preview (200 chars)         │  ~50 tokens (search result default)
├─────────────────────────────────────┤
│ content (full, 300-800 tokens)      │  on-demand via get_chunk
├─────────────────────────────────────┤
│ code_content (code blocks only)     │  on-demand via get_chunk
└─────────────────────────────────────┘
```

**Search call token budget breakdown:**

```
Per chunk (standard mode):
  - heading_path (compact): ~15 tokens
  - summary: ~30 tokens
  - content_preview (200 chars): ~50 tokens
  - metadata (score, confidence, url): ~20 tokens
  Total per chunk: ~115 tokens

5 chunks: ~575 tokens
Response envelope: ~50 tokens
Total: ~625 tokens ✓ (well under 1500 target)
```

**Compact mode:**

```
Per chunk: heading_path + summary only = ~45 tokens
5 chunks: ~225 tokens
Response envelope: ~50 tokens
Total: ~275 tokens ✓
```

### 2.3 Summary Generation Algorithm

Summary dibuat saat indexing, disimpan di DB, tidak ada LLM call.

**Algorithm:**

```python
def generate_summary(chunk: Chunk) -> str:
    # Format: [breadcrumb] first_meaningful_sentence

    # 1. Build breadcrumb from last 2 elements of heading_path
    path = chunk.heading_path_json  # ["Hooks", "useEffect", "Cleanup function"]
    if len(path) >= 2:
        breadcrumb = f"{path[-2]} > {path[-1]}"
    elif len(path) == 1:
        breadcrumb = path[0]
    else:
        breadcrumb = "Doc"

    # 2. Extract first meaningful sentence
    text = chunk.content.strip()
    # Find first sentence (period/newline) that's > 20 chars
    sentences = re.split(r'(?<=[.!?])\s+|\n', text)
    first = next(
        (s.strip() for s in sentences
         if len(s.strip()) > 20 and not s.strip().startswith('#')),
        text[:80]
    )

    # 3. Combine and truncate
    summary = f"[{breadcrumb}] {first}"
    return summary[:150]  # hard truncate
```

### 2.4 Token Counting

`token_count` field di chunks disimpan saat indexing. Menggunakan estimasi murah:

```python
def estimate_tokens(text: str) -> int:
    """
    Approximate token count.
    English: ~4 chars per token. Code: ~3 chars per token.
    This is good enough for budget management, not for billing.
    """
    return max(1, len(text) // 4)
```

Mengapa tidak tiktoken: menambah dependency besar hanya untuk estimasi. Kalau precision tinggi dibutuhkan (billing, strict budget enforcement), tambahkan sebagai optional dep di Phase 2.

### 2.5 Response Envelope Design

Response JSON dirancang untuk minimum overhead:

```python
class SearchResponse:
    """
    Hanya include field yang non-null dan informatif.
    """
    def to_json(self) -> dict:
        base = {
            "query": self.query,
            "result_status": self.result_status,
            "scanned_chunks": self.scanned_chunks,
            "chunks": [c.to_response_dict(mode=self.mode) for c in self.chunks],
        }
        # Conditional fields — tidak di-include kalau tidak informatif
        if self.result_status != "ok":
            base["scanned_packs"] = self.scanned_packs
            base["suggestion"] = self.suggestion
        if self.token_usage:
            base["token_usage"] = self.token_usage
        return base
```

---

## 3. Ingestion Pipeline Detail

### 3.1 Pipeline Stages

```
URL + Config
    │
    ▼ [1] Scope Resolution (scope.py)
URL set (bounded, validated)
    │
    ▼ [2] Discovery (discovery.py)
Expanded URL set (sitemap + link crawl)
    │
    ▼ [3] Fetch (fetcher.py) — async, rate-limited
Raw HTML + HTTP metadata
    │
    ▼ [4] Extract (extractor.py)
{text, heading_tree, code_blocks, title}
    │
    ▼ [5] Chunk (chunker.py)
[Chunk objects with heading_path]
    │
    ▼ [6] Index (indexer.py)
SQLite: documents + chunks + chunks_fts
```

### 3.2 Scope Resolution (scope.py)

```python
class ScopeResolver:
    def resolve(self, url: str, explicit_scope: str | None) -> ScopeRule:
        """
        Precedence:
        1. Explicit flag
        2. Infer from URL depth
        3. Error if ambiguous (root URL without explicit)
        """

        parsed = urlparse(url)
        depth = len([p for p in parsed.path.split('/') if p])

        if explicit_scope:
            return ScopeRule(type=explicit_scope, base_url=url)

        if depth <= 1:
            # Root URL — require explicit
            raise ScopeAmbiguousError(
                f"URL '{url}' is a root URL. Specify --scope explicitly.\n"
                f"Suggestions:\n"
                f"  --scope page-only  (just this page)\n"
                f"  --scope subtree    (this page + all descendants)\n"
                f"  --scope site       (entire domain, requires confirmation)"
            )

        # Deep URL → default siblings
        return ScopeRule(type="siblings", base_url=url)

    def apply(self, rule: ScopeRule, discovered_urls: list[str]) -> list[str]:
        """Filter discovered URLs by scope rule."""
        match rule.type:
            case "page-only":
                return [rule.base_url]
            case "siblings":
                prefix = "/".join(rule.base_url.split("/")[:-1])
                return [u for u in discovered_urls if u.startswith(prefix)]
            case "subtree":
                return [u for u in discovered_urls if u.startswith(rule.base_url)]
            case "site":
                base_domain = urlparse(rule.base_url).netloc
                return [u for u in discovered_urls
                        if urlparse(u).netloc == base_domain]
```

### 3.3 Discovery (discovery.py)

Urutan discovery strategy (stop when enough URLs found):

1. **`llms.txt`** — check `{domain}/llms.txt`. Ini adalah emerging standard untuk LLM-friendly doc listing. Kalau ada, gunakan sebagai primary source.
2. **Sitemap** — `{domain}/sitemap.xml` atau link dari robots.txt. Parse `<url><loc>` entries.
3. **Link crawl** — BFS dari entry URL, extract `<a href>` via selectolax, filter by scope.

```python
class Discoverer:
    async def discover(self, entry_url: str, scope: ScopeRule) -> list[str]:
        urls = set()

        # 1. llms.txt (new standard — prioritize)
        llms = await self._try_llms_txt(entry_url)
        if llms:
            return scope.apply(llms)

        # 2. Sitemap
        sitemap = await self._try_sitemap(entry_url)
        urls.update(sitemap)

        # 3. Link crawl (BFS, max depth 2)
        if not urls:
            crawled = await self._bfs_crawl(entry_url, max_depth=2)
            urls.update(crawled)

        return scope.apply(list(urls))[:50]  # hard limit
```

**`llms.txt` support** adalah differentiator penting — docs site yang support standar ini (Anthropic docs, beberapa Python library) akan memberikan URL list yang sudah dikurasi untuk LLM consumption. `docctx` harus prioritize ini sebelum scraping.

### 3.4 Fetcher (fetcher.py)

```python
class Fetcher:
    def __init__(self, config: IngestionConfig):
        self.client = httpx.AsyncClient(
            headers={"User-Agent": config.user_agent},
            follow_redirects=True,
            timeout=config.request_timeout_sec,
        )
        self.rate_limiter = TokenBucket(rate=config.rate_limit_rps)
        self.cache = DiskCache(config.cache_dir)
        self.robots = RobotsCache()

    async def fetch(self, url: str) -> FetchResult:
        # 1. Check cache (skip network if hash match)
        cached = self.cache.get(url)
        if cached and not cached.is_stale():
            return cached

        # 2. Check robots.txt
        if not await self.robots.is_allowed(url, self.config.user_agent):
            return FetchResult.disallowed(url)

        # 3. Rate limit
        await self.rate_limiter.acquire()

        # 4. Fetch
        response = await self.client.get(url)

        # 5. Cache
        result = FetchResult.from_response(url, response)
        self.cache.put(url, result)
        return result
```

**Cache strategy:**

- Key: `sha256(normalized_url)`
- TTL: configurable (default tidak ada TTL, invalidated only by `refresh`)
- Storage: raw HTML di `~/.docctx/cache/<hash>.html`
- Content-hash: SHA256 dari response body, stored di `documents.content_hash`

### 3.5 Extractor (extractor.py)

Wrapper di atas trafilatura dengan post-processing:

````python
class Extractor:
    def extract(self, html: str, url: str) -> ExtractedDocument:
        # 1. trafilatura extraction
        result = trafilatura.extract(
            html,
            include_tables=True,
            include_links=False,   # links add noise
            include_images=False,
            output_format="markdown",  # markdown preserves heading structure
        )

        if not result:
            # fallback: selectolax basic text extraction
            result = self._selectolax_fallback(html)

        # 2. Parse heading tree from markdown output
        heading_tree = self._parse_heading_tree(result)

        # 3. Extract code blocks (pre-separated untuk symbol boost)
        text_only, code_blocks = self._separate_code_blocks(result)

        return ExtractedDocument(
            text=text_only,
            heading_tree=heading_tree,
            code_blocks=code_blocks,
            title=self._extract_title(html, result),
            url=url,
        )

    def _parse_heading_tree(self, markdown: str) -> HeadingTree:
        """Parse # ## ### headers menjadi tree structure."""
        nodes = []
        for line in markdown.split('\n'):
            match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                nodes.append(HeadingNode(level=level, title=title))
        return HeadingTree(nodes)

    def _separate_code_blocks(self, markdown: str) -> tuple[str, list[str]]:
        """Pisahkan code blocks dari prose text."""
        code_blocks = []

        def extract_code(match):
            code_blocks.append(match.group(1))
            return f"[CODE_BLOCK_{len(code_blocks)-1}]"  # placeholder

        text = re.sub(r'```[^\n]*\n(.*?)```', extract_code, markdown, flags=re.DOTALL)
        return text, code_blocks
````

### 3.6 Chunker (chunker.py)

Heading-aware chunking adalah core algorithm. Target: 300-800 tokens per chunk, tidak potong di tengah heading section.

```python
class HeadingAwareChunker:
    def __init__(self, target_tokens=400, max_tokens=800, min_tokens=80):
        self.target = target_tokens
        self.max = max_tokens
        self.min = min_tokens

    def chunk(self, doc: ExtractedDocument) -> list[Chunk]:
        """
        Algorithm:
        1. Split document at heading boundaries
        2. Each heading section = candidate chunk
        3. If section > max_tokens: split at paragraph boundary
        4. If section < min_tokens: merge with next sibling
        5. Assign heading_path based on heading hierarchy
        6. Generate summary for each chunk
        """
        sections = self._split_at_headings(doc.text, doc.heading_tree)
        chunks = []

        for section in sections:
            section_tokens = estimate_tokens(section.content)

            if section_tokens > self.max:
                # Split at paragraph boundaries
                sub_chunks = self._split_by_paragraph(section)
                chunks.extend(sub_chunks)
            elif section_tokens < self.min and chunks:
                # Merge with previous chunk if same heading level
                if chunks[-1].heading_level == section.heading_level:
                    chunks[-1] = self._merge_chunks(chunks[-1], section)
                else:
                    chunks.append(self._make_chunk(section, doc))
            else:
                chunks.append(self._make_chunk(section, doc))

        # Assign prev/next links
        for i, chunk in enumerate(chunks):
            chunk.prev_chunk_id = chunks[i-1].id if i > 0 else None
            chunk.next_chunk_id = chunks[i+1].id if i < len(chunks)-1 else None

        # Generate summaries
        for chunk in chunks:
            chunk.summary = generate_summary(chunk)

        return chunks

    def _split_at_headings(self, text: str, tree: HeadingTree) -> list[Section]:
        """
        Split markdown text at # headings.
        Maintain heading_path (breadcrumb) for each section.
        """
        # Implementation: regex split on ^#{1,6} lines
        # Track heading stack to build full path
        pass

    def _assign_heading_path(self, heading_stack: list[str]) -> list[str]:
        """Return current breadcrumb path."""
        return list(heading_stack)  # ["Hooks", "useEffect", "Cleanup function"]
```

### 3.7 Indexer (indexer.py)

```python
class Indexer:
    def index_pack(self, pack: Pack, documents: list[Document],
                   chunks_per_doc: dict[str, list[Chunk]]) -> None:
        with db.transaction() as conn:
            # 1. Insert pack
            conn.execute(INSERT_PACK, pack.to_db_dict())

            # 2. Insert documents (source of truth)
            for doc in documents:
                conn.execute(INSERT_DOCUMENT, doc.to_db_dict())

            # 3. Insert chunks
            for doc_id, chunks in chunks_per_doc.items():
                for chunk in chunks:
                    conn.execute(INSERT_CHUNK, chunk.to_db_dict())

            # FTS5 index update is handled by triggers (see schema.sql)

    def reindex_fts(self, pack_name: str | None = None) -> None:
        """Full rebuild FTS5 index. Used after re-chunking."""
        with db.connection() as conn:
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
```

---

## 4. Retrieval Pipeline Detail

### 4.1 Search Pipeline

```python
class SearchService:
    def search(self, request: SearchRequest) -> SearchResponse:
        # Stage 1: FTS5 query
        candidates = self.fts.query(
            query=request.query,
            pack_filter=request.pack,
            limit=20,  # get top 20 for re-ranking
        )

        # Stage 2: Boost re-rank
        scored = self.ranker.rank(candidates, query=request.query)

        # Stage 3: Pack/version filter
        filtered = self.filter.apply(scored, pack=request.pack)

        # Stage 4: Threshold check
        passed, dropped = self.threshold.split(
            filtered,
            floor=self.config.floor_score
        )

        # Stage 5: Token budget trim (if specified)
        if request.token_budget:
            passed = self.trim_to_budget(passed, request.token_budget)

        # Stage 6: Format response
        status = self._determine_status(passed, dropped)
        return SearchResponse(
            query=request.query,
            chunks=passed[:request.limit],
            result_status=status,
            scanned_packs=self.fts.last_scanned_packs,
            scanned_chunks=self.fts.last_scanned_count,
            suggestion=self._generate_suggestion(status, passed, dropped),
        )
```

### 4.2 FTS5 Query (search.py)

```python
class FTSSearcher:
    def query(self, query: str, pack_filter: str | None, limit: int) -> list[CandidateChunk]:
        """
        Two-phase search:
        1. Exact phrase match (higher precision)
        2. Individual term match (higher recall)

        Merge and deduplicate results.
        """

        # Phase 1: Quoted phrase search
        phrase_sql = """
            SELECT c.*,
                   bm25(chunks_fts, 1.0, 0.5, 1.5, 1.5) as bm25_score
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            AND (? IS NULL OR c.pack_name GLOB ?)
            ORDER BY bm25_score
            LIMIT ?
        """
        phrase_results = self.db.execute(
            phrase_sql,
            [f'"{query}"', pack_filter, pack_filter, limit]
        ).fetchall()

        # Phase 2: Individual terms (fallback if phrase match too few)
        if len(phrase_results) < 5:
            term_query = " OR ".join(query.split())
            term_results = self.db.execute(
                phrase_sql,
                [term_query, pack_filter, pack_filter, limit]
            ).fetchall()

            # Merge, deduplicate by id
            all_results = {r['id']: r for r in phrase_results}
            for r in term_results:
                if r['id'] not in all_results:
                    all_results[r['id']] = r
            return list(all_results.values())[:limit]

        return phrase_results
```

**BM25 column weights** di `bm25()` function:

- `content`: 1.0 (prose text — standard weight)
- `code_content`: 0.5 (lower base weight, boosted manually if matches)
- `heading_path`: 1.5 (heading match sangat penting)
- `heading_title`: 1.5 (sama pentingnya dengan heading_path)

### 4.3 Ranking (ranking.py)

```python
@dataclass
class BoostTrace:
    """Track every boost applied untuk explain command."""
    factor_name: str
    multiplier: float
    reason: str

class Ranker:
    def rank(self, candidates: list[CandidateChunk], query: str) -> list[ScoredChunk]:
        results = []
        query_terms = set(query.lower().split())

        for candidate in candidates:
            base_score = candidate.bm25_score
            boosts: list[BoostTrace] = []

            # Boost 1: Exact term in heading_path
            heading_text = " ".join(candidate.heading_path).lower()
            if any(term in heading_text for term in query_terms):
                boosts.append(BoostTrace("heading_exact", 1.5,
                    "Query term found in heading path"))

            # Boost 2: Query term in code content
            if candidate.code_content:
                code_lower = candidate.code_content.lower()
                if any(term in code_lower for term in query_terms):
                    boosts.append(BoostTrace("code_match", 1.3,
                        "Query term found in code block"))

            # Boost 3: Official source trust tier
            if candidate.trust_tier == 1:
                boosts.append(BoostTrace("trust_official", 1.2,
                    "Source is official documentation"))

            # Boost 4: Exact heading title match
            if candidate.heading_title and candidate.heading_title.lower() == query.lower():
                boosts.append(BoostTrace("heading_title_exact", 1.1,
                    "Heading title exactly matches query"))

            # Apply all boosts multiplicatively
            final_score = base_score
            for boost in boosts:
                final_score *= boost.multiplier

            results.append(ScoredChunk(
                chunk=candidate,
                score=round(final_score, 3),
                boosts=boosts,
            ))

        return sorted(results, key=lambda x: x.score, reverse=True)
```

### 4.4 Threshold (threshold.py)

```python
class ThresholdFilter:
    def split(
        self,
        chunks: list[ScoredChunk],
        floor: float,
        confidence_cutoff: float | None = None,
    ) -> tuple[list[ScoredChunk], list[ScoredChunk]]:
        """
        Split chunks into passed/dropped by floor score.
        Also assign confidence label.
        """
        cutoff = confidence_cutoff or self.config.confidence_cutoff
        passed = []
        dropped = []

        for chunk in chunks:
            if chunk.score < floor:
                dropped.append(chunk)
            else:
                chunk.confidence = "high" if chunk.score >= cutoff else "low"
                passed.append(chunk)

        return passed, dropped
```

### 4.5 Custom FTS5 Tokenizer (tokenizer.py)

SQLite FTS5 default tokenizer (`unicode61`) tidak handle camelCase dan snake_case dengan baik. `useEffect` akan di-tokenize sebagai satu token, bukan dua.

**Strategy:** Pre-process query dan heading content sebelum insert ke FTS5. Tidak butuh custom C extension.

```python
def normalize_for_index(text: str) -> str:
    """
    Expand camelCase dan snake_case untuk FTS5.

    Input:  "useEffect fetchData fetch_data HttpRequest"
    Output: "useEffect use Effect fetchData fetch Data fetch_data fetch data HttpRequest Http Request"

    Original token tetap ada, expansion di-append. Ini memungkinkan
    exact match "useEffect" dan partial match "effect" keduanya bekerja.
    """
    words = text.split()
    expanded = []

    for word in words:
        expanded.append(word)  # original

        # camelCase → split
        camel_parts = re.sub(r'([A-Z])', r' \1', word).strip().split()
        if len(camel_parts) > 1:
            expanded.extend([p.lower() for p in camel_parts])

        # snake_case → split
        snake_parts = word.split('_')
        if len(snake_parts) > 1:
            expanded.extend([p.lower() for p in snake_parts if p])

    return ' '.join(expanded)
```

Fungsi ini dipanggil di indexer (untuk content yang masuk ke FTS5) dan di searcher (untuk query sebelum dikirim ke FTS5).

---

## 5. Database Design

### 5.1 Schema Additions dari Brief

Brief sudah mendefinisikan schema dasar. Berikut tambahan/refinement:

```sql
-- Tambahkan ke tabel chunks:
ALTER TABLE chunks ADD COLUMN summary TEXT;              -- generated at index time
ALTER TABLE chunks ADD COLUMN content_preview TEXT;     -- first 200 chars of content

-- Tambahkan ke tabel packs:
ALTER TABLE packs ADD COLUMN doc_count INTEGER DEFAULT 0;
ALTER TABLE packs ADD COLUMN chunk_count INTEGER DEFAULT 0;

-- Stub untuk Phase 2 graph features (schema-ready, empty di M1)
CREATE TABLE IF NOT EXISTS chunk_relations (
  from_chunk_id   TEXT REFERENCES chunks(id) ON DELETE CASCADE,
  to_chunk_id     TEXT REFERENCES chunks(id) ON DELETE CASCADE,
  relation_type   TEXT NOT NULL,  -- 'see_also' | 'child_of' | 'example_of'
  weight          REAL DEFAULT 1.0,
  PRIMARY KEY (from_chunk_id, to_chunk_id, relation_type)
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
  version         INTEGER NOT NULL,
  applied_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  notes           TEXT
);
INSERT OR IGNORE INTO schema_version (version, notes) VALUES (1, 'M1 initial schema');
```

### 5.2 FTS5 Trigger

FTS5 harus tetap sync dengan perubahan di chunks. Gunakan triggers:

```sql
-- Insert trigger
CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, content, code_content, heading_path, heading_title)
  VALUES (
    new.rowid,
    normalize_text(new.content),        -- custom function, registered di Python
    normalize_text(new.code_content),
    new.heading_path_json,
    new.heading_title
  );
END;

-- Delete trigger
CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid) VALUES('delete', old.rowid);
END;
```

`normalize_text` adalah scalar function yang didaftarkan via `conn.create_function("normalize_text", 1, normalize_for_index)`.

### 5.3 Connection Factory (connection.py)

```python
def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row  # dict-like access

    # Critical pragmas
    conn.execute("PRAGMA journal_mode=WAL")     # concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")       # enforce cascades
    conn.execute("PRAGMA synchronous=NORMAL")    # balance safety/speed
    conn.execute("PRAGMA cache_size=-32000")     # 32MB cache

    # Register custom functions
    conn.create_function("normalize_text", 1, normalize_for_index)

    return conn
```

---

## 6. MCP Server Design

### 6.1 Server Structure

```python
# mcp/server.py
from mcp.server import Server
from mcp.server.stdio import stdio_server

app = Server("docctx")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="search_docs", ...),
        Tool(name="get_chunk", ...),
        Tool(name="list_packs", ...),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    # Route to handler
    match name:
        case "search_docs":
            result = await handle_search_docs(arguments)
        case "get_chunk":
            result = await handle_get_chunk(arguments)
        case "list_packs":
            result = await handle_list_packs(arguments)
        case _:
            raise ValueError(f"Unknown tool: {name}")

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

async def run():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())
```

### 6.2 Handler Pattern

MCP handlers adalah thin wrappers. Business logic ada di `retrieval/search.py`:

```python
# mcp/tools.py

async def handle_search_docs(args: dict) -> dict:
    """Thin wrapper — validate input, call service, format output."""

    # Input validation
    if not args.get("query"):
        return error_response("MISSING_REQUIRED", "Field 'query' is required")

    query = args["query"]
    pack = args.get("pack")
    limit = min(args.get("limit", 5), 10)
    mode = args.get("response_mode", "standard")
    budget = args.get("token_budget")

    # Call core service (sync, wrapped in executor)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: search_service.search(SearchRequest(
            query=query, pack=pack, limit=limit,
            response_mode=mode, token_budget=budget
        ))
    )

    return result.to_json()

def error_response(code: str, message: str) -> dict:
    return {
        "error": True,
        "error_code": code,
        "message": message,
        "chunks": [],
        "result_status": "error",
    }
```

### 6.3 Tool Descriptions (Token Efficiency Guidance)

Tool descriptions di MCP harus explicitly guide agent ke retrieval pattern yang efisien:

```python
Tool(
    name="search_docs",
    description="""Search indexed documentation packs.

Returns compact summaries by default (response_mode='standard').
For maximum token efficiency when scanning many options, use response_mode='compact'.
Then call get_chunk only for chunks worth reading in full.

Pattern:
  1. list_packs → know what's available
  2. search_docs (compact) → identify relevant chunk IDs (~275 tokens for 5 results)
  3. get_chunk (for relevant IDs only) → get full content

Token cost guide:
  compact mode: ~50 tokens per chunk
  standard mode: ~115 tokens per chunk
  full mode: ~250 tokens per chunk""",
    inputSchema={...}
)
```

---

## 7. Error Handling

### 7.1 Error Taxonomy

```python
class DocctxError(Exception):
    """Base error. Semua error harus subclass ini."""
    error_code: str
    user_message: str
    hint: str | None = None

class PackExistsError(DocctxError):
    error_code = "PACK_EXISTS"

class ScopeAmbiguousError(DocctxError):
    error_code = "SCOPE_AMBIGUOUS"

class FetchError(DocctxError):
    error_code = "FETCH_FAILED"

class SchemaVersionError(DocctxError):
    error_code = "SCHEMA_VERSION_MISMATCH"
    # Hint: run `docctx doctor` untuk migrate

class EmptyExtractionError(DocctxError):
    error_code = "EXTRACTION_EMPTY"
    # Hint: URL mungkin JS-rendered, coba --js-render di Phase 2
```

### 7.2 MCP Error Contract

MCP server tidak boleh crash. Semua error di-catch dan dikembalikan sebagai structured response:

```json
{
  "error": true,
  "error_code": "FETCH_FAILED",
  "message": "Could not fetch https://...: Connection timeout after 30s",
  "hint": "Check network connectivity or increase timeout in config.toml",
  "chunks": [],
  "result_status": "error"
}
```

### 7.3 Ingestion Partial Failure

Kalau beberapa halaman gagal di-fetch/extract selama `docctx add`:

- Lanjutkan ke halaman berikutnya (don't abort)
- Catat failed pages di `documents.fetch_status = 'failed'`
- Report di CLI output: `3 pages failed (see docctx inspect <pack> for details)`
- Pack tetap dibuat dengan partial content

---

## 8. Observability Implementation

### 8.1 Explain Output (observability/explain.py)

```python
class Explainer:
    def explain(self, query: str, pack: str | None) -> ExplainReport:
        """
        Run retrieval pipeline dengan full trace mode ON.
        Collect trace dari setiap stage.
        """
        with trace_mode():
            # Same pipeline sebagai search, tapi:
            # - ambil top 10 (bukan 5)
            # - include dropped chunks
            # - capture full boost trace per chunk
            result = search_service.search(
                SearchRequest(query=query, pack=pack, limit=10),
                trace=True
            )

        return ExplainReport(
            query=query,
            ranked_chunks=result.chunks,
            dropped_chunks=result.dropped,
            threshold_floor=self.config.floor_score,
            confidence_cutoff=self.config.confidence_cutoff,
        )

    def format_report(self, report: ExplainReport) -> str:
        lines = [
            f"Query: \"{report.query}\"",
            f"Scanned: {report.scanned_chunks} chunks",
            ""
        ]

        for i, chunk in enumerate(report.ranked_chunks, 1):
            conf = "HIGH CONFIDENCE" if chunk.confidence == "high" else "LOW CONFIDENCE"
            lines.extend([
                f"Rank {i} — score: {chunk.score} ({conf})",
                f"  id: {chunk.id[:12]}...",
                f"  heading: {' > '.join(chunk.heading_path)}",
                f"  BM25 base: {chunk.bm25_base_score}",
                f"  boosts: " + ", ".join(
                    f"{b.factor_name} (+{b.multiplier}x)" for b in chunk.boosts
                ) if chunk.boosts else "  boosts: none",
                f"  url: {chunk.url}",
                "",
            ])

        lines.extend([
            f"--- Threshold ---",
            f"Floor: {report.threshold_floor}",
            f"Confidence cutoff: {report.confidence_cutoff}",
            f"Dropped (below floor): {len(report.dropped_chunks)} chunks",
        ])

        return "\n".join(lines)
```

### 8.2 Doctor Checks (observability/doctor.py)

```python
class Doctor:
    def run(self) -> DoctorReport:
        checks = [
            self.check_db_exists,
            self.check_schema_version,
            self.check_fts_integrity,
            self.check_orphaned_chunks,
            self.check_broken_pack_refs,
            self.check_config_valid,
            self.check_cache_dir,
        ]

        results = []
        for check in checks:
            try:
                results.append(check())
            except Exception as e:
                results.append(CheckResult.error(check.__name__, str(e)))

        return DoctorReport(results)

    def check_fts_integrity(self) -> CheckResult:
        row = self.db.execute(
            "INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')"
        ).fetchone()
        if row and row[0] == 'ok':
            return CheckResult.ok("fts_integrity")
        return CheckResult.error("fts_integrity",
            "FTS5 index corrupted. Run: docctx doctor --repair")
```

---

## 9. Configuration & Paths

### 9.1 Path Resolution (paths.py)

```python
from platformdirs import user_data_dir
from pathlib import Path

def get_docctx_home() -> Path:
    """
    Resolution order:
    1. DOCCTX_HOME env var
    2. ~/.docctx (manual default, cross-platform consistent)
    3. platformdirs fallback (for packaged installs)
    """
    if env := os.environ.get("DOCCTX_HOME"):
        return Path(env).expanduser()

    # Prefer explicit ~/.docctx over platformdirs
    # (easier for users to find and backup)
    default = Path.home() / ".docctx"
    return default

def get_db_path(home: Path | None = None) -> Path:
    return (home or get_docctx_home()) / "store.db"

def get_cache_dir(home: Path | None = None) -> Path:
    return (home or get_docctx_home()) / "cache"

def get_config_path(home: Path | None = None) -> Path:
    return (home or get_docctx_home()) / "config.toml"
```

---

## 10. Testing Strategy

### 10.1 Unit Tests

| Module         | Test focus                                                     |
| -------------- | -------------------------------------------------------------- |
| `chunker.py`   | Heading boundary detection, token count, merge/split logic     |
| `scope.py`     | URL classification, scope rule application, edge cases         |
| `ranking.py`   | Boost calculation, multiplicative application, ordering        |
| `threshold.py` | Floor/cutoff logic, confidence label assignment                |
| `tokenizer.py` | camelCase/snake_case expansion, edge cases                     |
| `extractor.py` | trafilatura wrapper, heading tree parse, code block separation |

### 10.2 Integration Tests

| Test                | What it tests                                                            |
| ------------------- | ------------------------------------------------------------------------ |
| `test_pipeline.py`  | Full ingestion dari HTML fixture → DB. Assert chunk count, heading paths |
| `test_retrieval.py` | Search terhadap indexed fixtures. Assert ranking, confidence labels      |
| `test_mcp.py`       | MCP roundtrip: call tool via stdio → parse response → assert schema      |

**Fixtures philosophy:** Semua fixtures adalah captured HTML files (offline). Tidak ada network call dalam tests. Satu exception: integration test dengan `--live` flag untuk sanity check terhadap real URLs.

### 10.3 Eval Harness

Eval bukan unit test — di-run manual atau CI nightly, bukan setiap commit:

```python
# eval/harness.py
class EvalHarness:
    def run(self, dataset: EvalDataset) -> EvalReport:
        results = []

        for query in dataset.queries:
            response = search_service.search(SearchRequest(query=query.text))

            hit_at_1 = query.expected_chunk_id in [response.chunks[0].id] if response.chunks else False
            hit_at_5 = query.expected_chunk_id in [c.id for c in response.chunks[:5]]

            results.append(QueryResult(
                query=query.text,
                category=query.category,  # exact_api | conceptual | out_of_scope
                hit_at_1=hit_at_1,
                hit_at_5=hit_at_5,
                is_empty=(len(response.chunks) == 0),
                top_score=response.chunks[0].score if response.chunks else 0,
            ))

        return EvalReport.from_results(results)
```
