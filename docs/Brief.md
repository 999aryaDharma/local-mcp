# PART 1 — Project Brief Final v2

# `docctx` — Project Brief (Final)

## 0. Status Decisions

Semua keputusan di bawah sudah final untuk MVP. Mengubahnya = re-architect.

|Decision|Final|
|---|---|
|Bahasa|Python 3.11+|
|Package manager|uv|
|CLI framework|Typer|
|Storage|SQLite (single file)|
|Search|SQLite FTS5 + BM25|
|Embedding di MVP|Tidak ada|
|HTML fetch|httpx|
|HTML extract|trafilatura (primary), selectolax (discovery/links)|
|JS rendering|Tidak di MVP. Phase 2 via playwright fallback.|
|Interface|MCP server (primary) + CLI|
|Target user MVP|Single developer (you + similar). Bukan multi-tenant, bukan team.|
|Versioning model|Single-version per source. Multi-version via opt-in naming (`react@19`).|
|Re-add behavior|Error + suggest `refresh`|
|Source identity|URL canonical, name is display label|
|Robots policy|Polite default, configurable|
|Remove behavior|Hard delete|

---

## 1. What `docctx` Is

`docctx` adalah **local-first deterministic context retrieval engine** untuk coding agents.

Tugasnya satu kalimat:

> Saat coding agent butuh dokumentasi untuk menyelesaikan task, `docctx` memberikan chunk dokumentasi yang **paling presisi, dari source yang paling tepercaya, dengan ranking yang dapat dijelaskan, atau tidak memberikan apapun.**

Bukan:

- Web crawler general purpose
- Universal documentation index (Context7 territory)
- Vector database wrapper
- RAG framework

---

## 2. Core Problem (Sharpened)

LLM coding agent gagal bukan karena tidak ada dokumentasi — internet penuh. Mereka gagal karena:

1. **Wrong context** masuk ke prompt (versi salah, framework salah, deprecated API).
2. **Noisy context** mengencerkan signal (10 chunks marketing, 1 chunk API reference).
3. **Ambiguous context** memberi multiple jawaban tanpa otoritas (Stack Overflow vs official docs vs blog 2019).

Konsekuensi: **wrong context lebih berbahaya daripada missing context.** Agent yang tidak punya dokumentasi tahu dia tidak tahu. Agent yang punya dokumentasi salah akan **percaya diri menghasilkan kode salah**.

`docctx` dirancang untuk satu tujuan: **memastikan setiap chunk yang masuk ke agent layak masuk.**

---

## 3. Design Philosophy (5 Pegangan)

### 3.1 Precision Over Recall

Kembalikan sedikit chunk yang benar-benar tepat, atau jangan kembalikan apapun. Threshold confidence eksplisit, tunable, observable.

### 3.2 Bounded Scope

Tidak ada "ingest seluruh framework" sebagai default. Unit utama adalah **context pack** dengan crawl boundary yang ditentukan saat add.

### 3.3 Deterministic Retrieval, Reproducible Ingestion

Retrieval deterministik **given a frozen index**: query yang sama + DB yang sama = hasil yang sama. Ingestion **reproducible** (dokumented, dapat dijalankan ulang) tapi tidak bit-identical (network, source content berubah).

### 3.4 Inspectable Everything

Setiap stage (extract, chunk, rank) punya command CLI yang menunjukkan apa yang sistem lihat dan kenapa. Tidak ada black box.

### 3.5 Defer Complexity Ruthlessly

Embeddings, reranking, semantic graph, LLM enrichment — **semua tidak ada di MVP**. Hanya ditambahkan jika eval menunjukkan gap nyata yang tidak bisa ditutup BM25 + heuristic.

---

## 4. Core Concept: Context Pack

Unit utama bukan "source", bukan "framework", tapi **context pack**.

### 4.1 Definisi Formal

```
Context Pack = {
  name:        unique identifier ("react-useeffect")
  entry_url:   user-provided starting URL
  scope:       crawl boundary rules
  documents:   set of fetched pages within scope
  chunks:      derived retrieval units
  version:     optional semantic version tag
}
```

Satu pack = satu task domain. Bukan "React docs" tapi "React useEffect docs."

### 4.2 Scope Resolution

Saat user run `docctx add <url>`, scope ditentukan oleh **scope rule** dengan urutan precedence:

1. **Explicit flag** (`--scope subtree | siblings | page-only | site`).
2. **Inferred from URL depth**: deep URL (e.g., `/reference/react/useEffect`) → `siblings` default. Root URL (e.g., `react.dev`) → require explicit flag, error otherwise.
3. **Hard limits** (always apply): max 50 pages, max depth 2, same-domain only.

|Scope|Definition|Use case|
|---|---|---|
|`page-only`|Entry URL only|Single API page, no nearby needed|
|`siblings`|Entry URL + URLs sharing path prefix (1 level up)|Specific concept (default for deep URLs)|
|`subtree`|Entry URL + all descendants in path|Full section (e.g., `/reference/*`)|
|`site`|Whole domain (requires explicit flag + confirmation)|Full doc site (rare, escape hatch)|

Crawl boundary di-snapshot di DB. User selalu bisa lihat: "pack ini scope-nya apa, halaman apa yang masuk."

### 4.3 Source vs Pack Identity

```
source = (canonical_url, version_tag)      # primary key, internal
pack   = name                              # user-facing identifier, unique
```

Satu source bisa punya banyak pack (sub-scopes berbeda dari URL yang sama). Tapi `add` yang sama persis (URL + scope + version) ke pack name yang sama = error. Mau update? Pakai `refresh`. Mau scope berbeda? Pakai pack name berbeda.

---

## 5. Versioning Model

**Single-version per pack by default.** Refresh = overwrite.

Multi-version = opt-in via naming:

```bash
docctx add https://react.dev/reference --as react-19 --version 19
docctx add https://react.dev/reference --as react-18 --version 18 --url-override https://18.react.dev/reference
```

Query support multi-version filter:

```bash
docctx query "useEffect" --pack "react-*"           # cross-version
docctx query "useEffect" --pack react-19            # specific
```

Tidak ada native versioning di MVP. Versioning adalah **convention atas naming**. Phase 2 boleh promote ke first-class kalau pattern usage mendukung.

---

## 6. Retrieval Philosophy

### 6.1 Confidence Thresholding (Operasional)

Brief sebelumnya bilang "no result > misleading result" tapi tidak operasional. Sekarang operasional:

```
For each query, retrieval returns either:
  - chunks with score above threshold (high confidence)
  - chunks with score below threshold (low confidence, marked)
  - empty result (no chunks above floor)
```

Tiga level threshold (kalibrasi via eval, lihat §10):

- **Floor** (default ~3.0 BM25): di bawah ini, drop completely.
- **Confidence cutoff** (default ~6.0): di atas ini, marked "high confidence."
- **Top score reference**: hasil teratas selalu di-include kalau lewat floor, untuk konteks.

Semua threshold **tunable per query** dan **observable**. Tidak ada threshold hardcoded yang user tidak bisa override.

### 6.2 Apa yang Dikembalikan Saat Empty

`search_docs` **tidak pernah return null**. Selalu structured response:

```json
{
  "query": "useEffect cleanup",
  "chunks": [],
  "confidence": "none",
  "scanned_packs": ["react-19", "react-18"],
  "scanned_chunks": 4203,
  "max_score": 2.1,
  "threshold_floor": 3.0,
  "suggestion": "No chunks above confidence floor. Top match scored 2.1. Try rephrasing or check `list_packs` for scope."
}
```

Agent dapat sinyal yang actionable: tahu sistem cari di mana, sebanyak apa, dan kenapa kosong.

### 6.3 Ranking Stack

```
[1] FTS5 BM25 search                    → top 20 candidates
[2] Boosting:                           → re-rank
      +1.5x  exact term in heading_path
      +1.3x  query term in code_block
      +1.2x  source trust_tier == official
      +1.1x  exact heading title match
[3] Filter: scope (pack, version)
[4] Threshold check (floor)
[5] Return top N (default N=5, max N=10)
```

Setiap multiplier **tracked per chunk** dan dapat di-inspect via `docctx explain`. Semua angka adalah starting point — kalibrasi lewat eval.

### 6.4 Symbol Awareness

Technical retrieval butuh ini: query `useEffect` harus prefer chunk yang punya `useEffect` di code block, bukan chunk yang ngomongin "effect" sebagai konsep.

**Implementasi MVP:**

- Code block content di-index **terpisah** (`chunks_fts.code_content`).
- Query match di `code_content` dapat +1.3x boost (lihat §6.3).
- FTS5 tokenizer custom: pertahankan camelCase dan snake_case sebagai token utuh (`useEffect`, `fetch_data`), tapi juga indeks komponennya (`use`, `Effect`, `fetch`, `data`).

Phase 2: full identifier index (separate table) kalau accuracy belum cukup.

---

## 7. Architecture

### 7.1 Two-Mode Pipeline (Unchanged dari sebelumnya)

```
┌─────────── INGESTION MODE (CLI, network) ──────────────┐
│ URL → Discover → Fetch → Extract → Chunk → Index → DB  │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼ store.db
┌─────────── SERVING MODE (MCP, offline) ────────────────┐
│ Query → FTS5 → Boost → Filter → Threshold → Chunks     │
└─────────────────────────────────────────────────────────┘
```

### 7.2 Data Flow

```
Entry URL + Scope
    │
    ▼ Discovery
URL set (bounded by scope + hard limits)
    │
    ▼ Fetch (httpx, polite rate limit, cache to disk)
Raw HTML files (cached, addressable by URL hash)
    │
    ▼ Extract (trafilatura → text + heading tree + code blocks)
Extracted documents (stored verbatim — SOURCE OF TRUTH)
    │
    ▼ Chunk (heading-aware, 1000 token soft cap)
Chunks with heading_path
    │
    ▼ Index (SQLite FTS5)
Searchable index
```

**Critical invariant:** Extracted documents are immutable source of truth. Chunks are derived. Re-chunking with new strategy = SQL operation, no re-crawl.

---

## 8. Storage Design

### 8.1 Layout

```
~/.docctx/
├── store.db                 # SQLite database
├── cache/                   # raw HTML, addressable, throwaway
│   └── <url-hash>.html
├── config.toml              # user prefs
└── mcp.json                 # MCP server config snippet
```

### 8.2 Schema

```sql
-- A pack is a user-facing retrieval scope
CREATE TABLE packs (
  name              TEXT PRIMARY KEY,        -- 'react-useeffect'
  entry_url         TEXT NOT NULL,
  scope_rule        TEXT NOT NULL,           -- 'siblings' | 'subtree' | etc
  version_tag       TEXT,                    -- optional, user-provided
  trust_tier        INTEGER DEFAULT 2,       -- 1=official, 2=community, 3=user
  created_at        TIMESTAMP,
  last_refreshed    TIMESTAMP,
  config_json       TEXT                     -- scope config, rate limits, etc
);

-- A document is a fetched, extracted page (SOURCE OF TRUTH)
CREATE TABLE documents (
  id                TEXT PRIMARY KEY,        -- sha256(canonical_url + version)
  pack_name         TEXT REFERENCES packs(name) ON DELETE CASCADE,
  canonical_url     TEXT NOT NULL,
  fetched_at        TIMESTAMP,
  content_hash      TEXT NOT NULL,           -- for incremental refresh
  raw_html_path     TEXT,                    -- pointer to cache/
  extracted_text    TEXT NOT NULL,           -- trafilatura output
  heading_tree_json TEXT NOT NULL,           -- structured headings
  title             TEXT,
  fetch_status      TEXT                     -- 'ok' | 'partial' | 'failed'
);

-- Chunks are derived from documents (REGENERABLE)
CREATE TABLE chunks (
  id                TEXT PRIMARY KEY,        -- sha256(doc_id + position)
  document_id       TEXT REFERENCES documents(id) ON DELETE CASCADE,
  pack_name         TEXT REFERENCES packs(name) ON DELETE CASCADE,
  position          INTEGER,                 -- order within document
  heading_path_json TEXT NOT NULL,           -- ["Hooks", "useEffect", "Cleanup"]
  heading_title     TEXT,                    -- last element of path
  content           TEXT NOT NULL,
  code_content      TEXT,                    -- code blocks only (for symbol boost)
  token_count       INTEGER,
  prev_chunk_id     TEXT,                    -- for neighbor traversal
  next_chunk_id     TEXT
);

-- FTS5 index (auto-synced via triggers, see migrations)
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  content,
  code_content,
  heading_path,
  heading_title,
  content=chunks,
  content_rowid=rowid,
  tokenize='porter unicode61'
);

-- Future-proof slot (empty in MVP)
CREATE TABLE embeddings (
  chunk_id          TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  model             TEXT NOT NULL,
  vector            BLOB
);

-- Indexes
CREATE INDEX idx_chunks_pack    ON chunks(pack_name);
CREATE INDEX idx_chunks_doc     ON chunks(document_id);
CREATE INDEX idx_documents_pack ON documents(pack_name);
```

### 8.3 Foreign Key & Cascade

`ON DELETE CASCADE` di mana-mana berarti `docctx remove <pack>` adalah satu DELETE: pack hilang, documents hilang, chunks hilang, embeddings (kosong di MVP) hilang. FTS5 di-rebuild via trigger.

---

## 9. MCP Interface (Detailed for Agent Use)

Tiga tool. Setiap tool dirancang untuk **iterative retrieval pattern**: agent search → inspect → refine → get neighbor → synthesize.

### 9.1 `search_docs`

Tool utama. Selalu return structured response, tidak pernah null.

**Input:**

```json
{
  "query": "useEffect cleanup function",
  "pack": "react-19",              // optional, glob supported ("react-*")
  "limit": 5,                       // optional, default 5, max 10
  "min_confidence": "low"           // optional: "high" | "low" | "any"
}
```

**Output:**

```json
{
  "query": "useEffect cleanup function",
  "chunks": [
    {
      "id": "abc123...",
      "pack": "react-19",
      "heading_path": ["Reference", "Hooks", "useEffect", "Cleanup function"],
      "content": "...",              // truncated to ~500 chars by default
      "url": "https://react.dev/...",
      "score": 8.42,
      "confidence": "high",
      "boosts_applied": ["heading_exact", "code_match"]
    }
  ],
  "result_status": "ok",             // "ok" | "low_confidence" | "empty"
  "scanned_packs": ["react-19"],
  "scanned_chunks": 1204,
  "suggestion": null
}
```

### 9.2 `get_chunk`

Untuk agent yang sudah dapat chunk dari search dan butuh konteks lebih.

**Input:**

```json
{
  "id": "abc123...",
  "include_neighbors": true,         // optional, default false
  "full_content": true               // optional, default true
}
```

**Output:**

```json
{
  "chunk": { /* full chunk */ },
  "previous": { /* prev_chunk or null */ },
  "next": { /* next_chunk or null */ },
  "document": {
    "url": "...",
    "title": "...",
    "heading_tree": { /* structure */ }
  }
}
```

### 9.3 `list_packs`

Discovery untuk agent. Agent bisa lihat scope tersedia sebelum query.

**Input:**

```json
{
  "name_pattern": "react-*"          // optional glob filter
}
```

**Output:**

```json
{
  "packs": [
    {
      "name": "react-19",
      "entry_url": "https://react.dev/reference",
      "version": "19",
      "chunks": 3201,
      "last_refreshed": "2026-05-11T14:22:00Z",
      "freshness": "fresh"            // "fresh" | "stale" | "very_stale"
    }
  ]
}
```

### 9.4 Optional: `inspect_pack` (Defer Decision)

Agent navigates pack structure sebelum query. Return heading tree.

Saya rekomendasikan **tunda ke Phase 2** — tiga tool di atas sudah cukup untuk pattern iteratif, dan tambah tool keempat berarti tambah API surface yang harus dijaga.

---

## 10. Evaluation Plan (BARU — wajib ada)

MVP tidak ship sebelum evaluation harness exist.

### 10.1 Eval Dataset

30 query, dengan ground truth manual:

- 10 query "exact API" (e.g., "useEffect cleanup", "axios interceptor request"): satu chunk yang **harus** muncul di top-3.
- 10 query "conceptual" (e.g., "how to handle errors in async functions"): satu chunk yang **acceptable** di top-5.
- 10 query "out of scope" (intentional misses): result harus **empty atau low_confidence**.

### 10.2 Metrics

|Metric|Target MVP|Critical|
|---|---|---|
|Hit@1 untuk "exact API"|≥ 80%|Yes|
|Hit@5 untuk "conceptual"|≥ 70%|Yes|
|Empty rate untuk "out of scope"|≥ 80%|Yes|
|False positive rate (wrong chunk top-1 untuk OOS)|< 10%|Yes|
|p50 latency|< 50ms|Yes|
|p95 latency|< 200ms|Yes|

### 10.3 Kalibrasi Threshold

Threshold di §6.1 (floor ~3.0, cutoff ~6.0) adalah starting point. Setelah eval dataset ada:

1. Jalankan eval dengan threshold floor = 0 (everything passes).
2. Plot score distribution: hit vs miss.
3. Pilih floor yang maksimalkan F1 di Hit@5 sambil jaga empty rate untuk OOS.
4. Lock angka di config dengan komentar `# calibrated 2026-05, eval set v1`.

Re-kalibrasi tiap kali eval set diperbarui atau ranking stack diubah.

---

## 11. Observability Commands

```bash
docctx inspect <url>                # show extraction result for one URL
docctx inspect <pack>               # show pack structure (heading tree)
docctx explain <query> [--pack X]   # show retrieval reasoning, scores, boosts
docctx doctor                       # health check: integrity, freshness, broken FTS
docctx eval [--dataset path]        # run eval harness, show metrics
```

`explain` adalah tool paling penting untuk debugging retrieval. Output-nya menunjukkan: BM25 score per chunk, boost yang applied, urutan ranking, threshold check.

---

## 12. Success Criteria (Quantified)

MVP ship-ready jika:

1. ≥80% Hit@1 untuk exact API queries di eval set
2. ≥70% Hit@5 untuk conceptual queries di eval set
3. ≥80% empty rate untuk out-of-scope queries
4. p95 retrieval latency <200ms di 10k chunks
5. `docctx add` untuk 5 source berbeda kategori (HTML, GitHub MD, deep URL, local FS, sub-path) jalan tanpa kode khusus per-source
6. `explain` output dapat dipahami dalam 30 detik oleh developer non-author
7. `doctor` zero errors di fresh install

Setiap kriteria punya angka. "Sederhana dan inspectable" bukan kriteria — itu cara, bukan ukuran.

---

## 13. Out of Scope (MVP)

Eksplisit tidak ada di v1:

- Embeddings (vector search)
- LLM enrichment (any LLM call di ingestion)
- Reranking dengan cross-encoder
- Semantic graph / relation extraction
- JS rendering (playwright)
- OpenAPI adapter (Phase 2)
- Markdown-git adapter (Phase 2, masuk M2 berikut)
- Local filesystem adapter (Phase 2)
- Web UI
- Multi-user
- Cloud sync
- Auto-refresh / cron
- Diff-based incremental refresh
- Native multi-version per pack

---

## 14. Phase Roadmap (Tentative)

|Phase|Scope|Trigger to proceed|
|---|---|---|
|M1|HTML adapter end-to-end + 3 MCP tools + CLI + eval harness|All success criteria met|
|M2|Markdown-git + filesystem adapter|M1 used in real workflow 2 weeks, gap identified|
|M3|Embedding via sqlite-vec (if eval shows BM25 gap)|Eval shows specific query class failing|
|M4|OpenAPI adapter + playwright fallback|Real use case demands|

Phase berikutnya **tidak otomatis**. Tiap phase requires evidence dari phase sebelumnya.

---

---

# PART 2 — Project Structure

Sekarang struktur proyek. Saya rancang dengan tiga prinsip:

1. **Adapter pattern untuk fetcher**, supaya M2 (markdown-git, fs) tidak butuh restructure.
2. **Pipeline stages sebagai modul terpisah**, supaya stage bisa diuji terisolasi dan di-replace.
3. **MCP server dan CLI share core**, tapi entry point terpisah.

## File Tree

```
docctx/
├── pyproject.toml                  # uv + dependencies
├── README.md
├── .gitignore
├── .python-version                 # 3.11
│
├── src/
│   └── docctx/
│       ├── __init__.py
│       ├── __main__.py             # python -m docctx → CLI
│       ├── cli.py                  # Typer app, all CLI commands
│       ├── mcp_server.py           # MCP stdio server entry
│       │
│       ├── config.py               # Config loading, defaults, paths
│       ├── paths.py                # ~/.docctx/* resolution
│       │
│       ├── db/
│       │   ├── __init__.py
│       │   ├── connection.py       # SQLite conn factory, WAL mode
│       │   ├── schema.sql          # DDL (loaded at init)
│       │   ├── migrations.py       # version tracking (v1, v2, ...)
│       │   └── queries.py          # all SQL as named functions
│       │
│       ├── models/
│       │   ├── __init__.py
│       │   ├── pack.py             # Pack dataclass
│       │   ├── document.py         # Document dataclass
│       │   └── chunk.py            # Chunk dataclass
│       │
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── pipeline.py         # orchestrate stages
│       │   ├── discovery.py        # sitemap, llms.txt, link crawl
│       │   ├── fetcher.py          # httpx with rate limit, robots
│       │   ├── extractor.py        # trafilatura wrapper, normalize
│       │   ├── chunker.py          # heading-aware chunking
│       │   ├── indexer.py          # write to documents + chunks + fts
│       │   └── scope.py            # scope rule resolver
│       │
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── base.py             # Adapter ABC
│       │   ├── html.py             # HTML site adapter (M1)
│       │   ├── markdown_git.py     # M2 (stub)
│       │   └── filesystem.py       # M2 (stub)
│       │
│       ├── retrieval/
│       │   ├── __init__.py
│       │   ├── search.py           # search_docs implementation
│       │   ├── ranking.py          # BM25 + boost stack
│       │   ├── threshold.py        # confidence logic
│       │   ├── tokenizer.py        # custom FTS5 tokenizer (camelCase)
│       │   └── explain.py          # ranking trace for explain cmd
│       │
│       ├── mcp/
│       │   ├── __init__.py
│       │   ├── server.py           # MCP protocol handler
│       │   └── tools.py            # tool schemas + handlers
│       │
│       ├── observability/
│       │   ├── __init__.py
│       │   ├── inspect.py          # docctx inspect logic
│       │   ├── explain.py          # docctx explain logic (CLI side)
│       │   └── doctor.py           # health check
│       │
│       └── eval/
│           ├── __init__.py
│           ├── harness.py          # run eval, compute metrics
│           ├── metrics.py          # Hit@K, empty rate, etc
│           └── datasets/
│               └── v1.json         # initial 30-query eval set
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # shared fixtures
│   ├── unit/
│   │   ├── test_chunker.py
│   │   ├── test_scope.py
│   │   ├── test_ranking.py
│   │   ├── test_threshold.py
│   │   └── test_tokenizer.py
│   ├── integration/
│   │   ├── test_pipeline.py        # end-to-end ingestion
│   │   ├── test_retrieval.py       # end-to-end search
│   │   └── test_mcp.py             # MCP protocol roundtrip
│   └── fixtures/
│       ├── react_useeffect.html    # captured page for offline test
│       └── ...
│
└── scripts/
    ├── seed_dev.sh                 # ingest sample packs for local dev
    └── benchmark.py                # latency benchmark
```

## Mengapa Struktur Ini

**`src/` layout** (bukan flat `docctx/` di root) — standard Python packaging modern. Mencegah accidental import dari working directory.

**`db/` dipisah dari ingestion/retrieval** — semua SQL hidup di satu tempat. Kalau schema berubah, satu folder yang berubah. Migrations.py melacak versi schema; init pertama jalan v1, future ingestion check version dan migrate.

**`adapters/base.py` adalah ABC, bahkan untuk MVP yang hanya punya HTML adapter** — supaya M2 (markdown_git, filesystem) tinggal implement ABC. Tanpa abstraction layer di M1, M2 akan butuh refactor. Tanpa over-engineer: ABC-nya minimal, 3-4 method.

**`ingestion/` dipisah jadi stages** — discovery, fetcher, extractor, chunker, indexer. Pipeline orchestrate. Setiap stage testable independent. Stage bisa di-replace tanpa menyentuh yang lain (mis. Phase 2 swap extractor dari trafilatura ke playwright fallback).

**`retrieval/ranking.py` terpisah dari `retrieval/search.py`** — search adalah orchestration (FTS query + boost + filter + threshold). Ranking adalah pure function (chunks + query → scored chunks). Pure function = mudah di-test, mudah di-tune, mudah di-explain.

**`mcp/` punya folder sendiri** — protocol handling terpisah dari business logic. Tools di `mcp/tools.py` adalah thin wrapper yang call ke `retrieval/search.py`. Kalau MCP SDK berubah, hanya satu folder yang berubah.

**`observability/` adalah first-class folder** — bukan utility, bukan helper. `inspect`, `explain`, `doctor` adalah produk, bukan afterthought. Konsisten dengan brief.

**`eval/` punya datasets in-tree** — eval set di-version dengan kode. Tidak ada "download eval set dari mana." Reproducibility.

**`tests/` mirror src structure** — unit per module, integration end-to-end. Fixtures sebagai HTML files captured (bukan live fetch dalam test), supaya test offline-deterministic.

**`scripts/` untuk dev workflow** — seed sample data, benchmark. Bukan production code.

## Entry Points

```toml
# pyproject.toml (excerpt)
[project.scripts]
docctx = "docctx.cli:app"
```

Satu binary `docctx`. Subcommand `serve` masuk ke MCP server, subcommand lain ke CLI biasa. Single binary, dual mode — sesuai keputusan kamu.

```python
# cli.py
import typer
app = typer.Typer()

@app.command()
def add(url: str, ...): ...

@app.command()
def serve():
    """Start MCP server on stdio."""
    from docctx.mcp_server import run
    run()

# ... etc
```

## Dependencies (Minimal)

```toml
# pyproject.toml (excerpt)
[project]
dependencies = [
    "typer>=0.12",           # CLI
    "httpx>=0.27",           # HTTP client
    "trafilatura>=1.12",     # extraction
    "selectolax>=0.3",       # fast HTML for discovery/links
    "mcp>=1.0",              # official MCP SDK
    "platformdirs>=4.2",     # ~/.docctx path resolution
    "tomli; python_version<'3.11'",  # config
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "ruff",
    "mypy",
]
```

Total runtime deps: 6. Tidak ada BeautifulSoup (selectolax cukup untuk parse sitemap/extract links; trafilatura handle extraction internally). Tidak ada SQLAlchemy (raw SQL via stdlib `sqlite3` — schema cukup sederhana, SQL eksplisit lebih mudah audit untuk tool yang klaim "inspectable").

## Yang Sengaja TIDAK Ada Sekarang

- `embeddings/` folder — defer ke Phase 3
- `cache/` di src — caching adalah implementation detail di `ingestion/fetcher.py`
- `utils/` folder — biasanya jadi dumping ground; saya hindari sengaja. Kalau ada utility, taruh di module yang paling related
- `core/` atau `lib/` folder — terlalu generic
- Async-everywhere — sync default. Async hanya di fetcher (network bound) via httpx.AsyncClient. CLI commands sync. Mengurangi kompleksitas. MCP server harus async karena protocol, tapi handler async-nya wrap sync core.

---
