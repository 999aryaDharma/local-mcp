# docctx — Product Requirements Document (PRD)

**Version:** 1.0  
**Phase:** M1 (MVP)  
**Status:** Draft — ready for review  
**Tanggal:** 2026-06

---

## 1. Ringkasan Produk

`docctx` adalah **local-first deterministic context retrieval engine** untuk coding agent.

Satu tujuan: ketika coding agent butuh dokumentasi, `docctx` memberikan chunk yang **paling presisi dari source yang paling tepercaya**, atau tidak memberikan apapun. Tidak ada noise. Tidak ada wrong version.

**Primary differentiator dari Context7 dan tools sejenis:**

| Dimensi        | Context7 / tools lain | docctx                            |
| -------------- | --------------------- | --------------------------------- |
| Scope          | Universal, semua docs | Bounded, curated per task         |
| Confidence     | Tidak dinyatakan      | Eksplisit, tunable, auditable     |
| Token usage    | Dump full docs        | Precision chunks + lazy expansion |
| Freshness      | Best-effort           | Tracked per pack, agent-visible   |
| Explainability | Black box             | Setiap ranking decision auditable |
| Hosting        | Cloud dependency      | Local-first, offline-capable      |

---

## 2. User Personas

### 2.1 Persona A — Developer (Operator)

Developer yang setup dan maintain docctx. Menentukan pack apa yang di-ingest, scope crawl, dan version apa yang aktif. Bertanggung jawab atas **kualitas knowledge base**.

**Pain points yang perlu diselesaikan:**

- Tidak tahu dokumentasi mana yang masuk ke agent context (black box)
- Agent pakai dokumentasi outdated tanpa warning
- Sulit audit kenapa agent menghasilkan kode salah (ranking tidak jelas)
- Setup yang boros waktu untuk setiap proyek baru

**Goal utama:** Setup pack sekali, percaya hasilnya, bisa inspect kalau ada masalah.

### 2.2 Persona B — Coding Agent (Consumer)

Claude Code, Cursor, atau agent lain yang query docctx via MCP. Agent tidak punya akses internet dan bergantung pada docctx sebagai satu-satunya source of truth untuk library-specific knowledge.

**Pain points yang perlu diselesaikan:**

- Menerima context yang terlalu besar → token waste
- Menerima context yang salah versi → confident wrong code
- Tidak tahu harus query apa → query terlalu broad atau terlalu narrow
- Tidak dapat sinyal ketika hasil tidak reliable

**Goal utama:** Query sekali, dapat chunk yang presisi, tahu kapan harus stop atau retry.

---

## 3. Core Features — M1

### F1: Pack Management (CLI)

**F1.1 — `docctx add`**

Ingest URL sebagai context pack baru.

```
docctx add <url> [--as <name>] [--scope page-only|siblings|subtree|site]
                 [--version <tag>] [--trust official|community|user]
                 [--rate-limit <req/s>] [--max-pages <n>]
```

Acceptance criteria:

- [ ] URL yang sama + scope yang sama + pack name yang sama → error dengan pesan `Pack '<name>' already exists. Use 'docctx refresh <name>' to update.`
- [ ] Deep URL tanpa explicit `--scope` → default ke `siblings`. Root URL tanpa explicit `--scope` → error dengan suggestion.
- [ ] Hard limits selalu applied: max 50 pages, max depth 2, same-domain only.
- [ ] Output CLI shows: pages discovered, pages fetched, chunks created, time elapsed.
- [ ] Progress bar live untuk fetch stage (httpx, polite 1 req/s default).
- [ ] robots.txt dihormati secara default. Flag `--ignore-robots` tersedia dengan explicit warning.

**F1.2 — `docctx refresh`**

Re-crawl pack yang sudah ada. Overwrite existing content.

```
docctx refresh <pack-name> [--force]
```

Acceptance criteria:

- [ ] Hanya re-fetch halaman yang `content_hash` berubah (incremental).
- [ ] Flag `--force` untuk full re-ingest tanpa hash check.
- [ ] Output shows: unchanged / updated / new / removed pages.

**F1.3 — `docctx remove`**

Hard delete pack beserta semua documents dan chunks.

```
docctx remove <pack-name> [--yes]
```

Acceptance criteria:

- [ ] Tanpa `--yes` → konfirmasi interaktif dengan summary (N pages, M chunks).
- [ ] DELETE CASCADE: pack → documents → chunks → embeddings (stub). FTS5 re-indexed via trigger.

**F1.4 — `docctx list`**

List semua pack dengan status.

```
docctx list [--verbose]
```

Acceptance criteria:

- [ ] Default output: nama, entry_url, chunk count, last_refreshed, freshness label.
- [ ] `--verbose`: tambahkan scope_rule, trust_tier, version_tag, page count.
- [ ] Freshness: `fresh` (<7 hari), `stale` (7-30 hari), `very_stale` (>30 hari).

---

### F2: Retrieval (CLI)

**F2.1 — `docctx query`**

Query via CLI untuk testing dan debugging.

```
docctx query "<query>" [--pack <name-or-glob>] [--limit N] [--min-confidence low|high|any]
                       [--format compact|standard|full]
```

Acceptance criteria:

- [ ] Output shows: heading_path breadcrumb, content preview, score, confidence label.
- [ ] `--format compact` shows only heading_path + first line per chunk.
- [ ] `--format full` shows complete chunk content.
- [ ] Empty result menampilkan: scanned packs, max_score, threshold, suggestion.

---

### F3: Observability (CLI)

**F3.1 — `docctx inspect <target>`**

Inspect extraction result untuk satu URL atau satu pack.

```
docctx inspect <url>          # show extracted text, heading tree, chunk boundaries
docctx inspect <pack-name>    # show pack structure: heading tree across all docs
```

Acceptance criteria:

- [ ] Untuk URL: menampilkan raw extracted text, detected headings, code blocks count, chunk boundaries.
- [ ] Untuk pack: menampilkan tree structure semua heading, URL per node, chunk count per node.

**F3.2 — `docctx explain`**

Show retrieval reasoning untuk satu query.

```
docctx explain "<query>" [--pack X]
```

Output yang diharapkan:

```
Query: "useEffect cleanup"
Scanned: 1204 chunks in react-19

Rank 1 — score: 8.42 (HIGH CONFIDENCE)
  chunk: abc123
  heading: Reference > Hooks > useEffect > Cleanup function
  BM25 base: 5.6
  boosts: heading_exact (+1.5x), code_match (+1.3x)
  url: https://react.dev/...

Rank 2 — score: 4.1 (LOW CONFIDENCE)
  ...

Threshold floor: 3.0
Threshold confidence cutoff: 6.0
Dropped: 3 chunks below floor
```

Acceptance criteria:

- [ ] Output bisa dipahami dalam 30 detik oleh developer yang tidak menulis kode ini.
- [ ] Setiap boost ditampilkan dengan label human-readable dan multiplier-nya.

**F3.3 — `docctx doctor`**

Health check system.

```
docctx doctor
```

Checks yang dilakukan:

- [ ] DB file exist dan readable
- [ ] Schema version match binary version
- [ ] FTS5 index integrity (`PRAGMA integrity_check`)
- [ ] Orphaned chunks (chunks tanpa document)
- [ ] Broken pack references
- [ ] Config file valid TOML
- [ ] Cache dir accessible (jika ada)

Acceptance criteria:

- [ ] Zero errors pada fresh install.
- [ ] Output jelas: `✓ OK` atau `✗ ERROR: <deskripsi> [HINT: cara fix]`.

---

### F4: MCP Server

**F4.1 — `docctx serve`**

Jalankan MCP server di stdio.

```
docctx serve
```

Acceptance criteria:

- [ ] Tiga tools exposed: `search_docs`, `get_chunk`, `list_packs`.
- [ ] Server bisa diregister di Claude Code / Cursor via `mcp.json`.
- [ ] Server handle concurrent queries (async handler wrap sync core).
- [ ] Server tidak crash on malformed input — return structured error response.

---

## 4. Token Efficiency Requirements

**Ini adalah requirement paling kritikal yang membedakan docctx dari tool lain.**

### 4.1 Prinsip Utama

Coding agent punya token budget terbatas per task. Setiap token yang dihabiskan untuk context documentation adalah token yang tidak bisa dipakai untuk reasoning atau code generation. `docctx` harus **aktif manage token pressure**, bukan serahkan ke agent.

**Target token consumption per operasi:**

| Operasi                                         | Target (tokens dikonsumsi agent) |
| ----------------------------------------------- | -------------------------------- |
| `list_packs` response                           | < 300 tokens total               |
| `search_docs` response (default, 5 chunks)      | < 1500 tokens total              |
| `search_docs` response (compact mode, 5 chunks) | < 500 tokens total               |
| `get_chunk` single chunk full content           | < 1200 tokens                    |
| Empty result response                           | < 100 tokens                     |

### 4.2 Two-Tier Content Model

Ini adalah fitur kunci untuk token efficiency. Setiap chunk punya dua layer:

**Layer 1 — Summary** (generated at index time, rule-based, stored in DB):

- Format: `[heading_path] first_sentence_of_chunk`
- Max 120 chars
- Contoh: `[Hooks > useEffect > Cleanup] Returns a cleanup function that React runs before the effect runs again.`
- Token cost: ~25-35 tokens per chunk

**Layer 2 — Full Content** (stored verbatim, returned on-demand):

- Full chunk text + code blocks
- Max ~1000 tokens
- Hanya dikembalikan ketika agent explicitly call `get_chunk`

**Search behavior:**

- Default mode (`response_mode: "standard"`): kembalikan summary + truncated content (~200 chars)
- Compact mode (`response_mode: "compact"`): kembalikan summary saja
- Full mode (`response_mode: "full"`): kembalikan full content langsung dari search

### 4.3 Response Format Efficiency

JSON response harus compact — hapus field yang tidak informatif ketika kosong.

**Anti-pattern (verbose):**

```json
{
  "id": "abc123",
  "boosts_applied": [],
  "previous_chunk_id": null,
  "next_chunk_id": null,
  "code_content": null,
  "token_count": 342
}
```

**Pattern yang benar (compact):**

```json
{
  "id": "abc123",
  "score": 8.42,
  "confidence": "high"
}
```

Aturan:

- Null fields → omit dari response
- Empty arrays → omit dari response
- Field `token_count`, `prev_chunk_id`, `next_chunk_id` hanya muncul kalau non-null dan relevan

### 4.4 `token_budget` Parameter

`search_docs` dan `get_chunk` support parameter `token_budget`:

```json
{
  "query": "useEffect cleanup",
  "token_budget": 2000
}
```

Behavior:

- System menghitung estimasi token per chunk (menggunakan `token_count` field yang di-store saat indexing)
- Pack chunk sebanyak mungkin dalam budget
- Prioritize high-confidence chunks
- Include budget usage di response header

```json
{
  "query": "...",
  "chunks": [...],
  "token_usage": {
    "estimated": 1842,
    "budget": 2000,
    "chunks_included": 4,
    "chunks_dropped": 1,
    "drop_reason": "budget_exceeded"
  }
}
```

### 4.5 Chunk Sizing Strategy

Chunking harus menghasilkan unit yang **semantically complete** dalam batas token yang ketat:

- **Target size:** 300-600 tokens per chunk (bukan 1000 seperti di brief awal)
- **Hard cap:** 800 tokens (split jika lebih besar)
- **Minimum viable:** 80 tokens (merge dengan sibling jika lebih kecil)
- **Override untuk code blocks:** code block lengkap tidak dipotong di tengah, bahkan jika lewat cap

Rationale: Chunk 1000 token = agent butuh 5000 tokens untuk 5 chunks. Chunk 400 token = 2000 tokens untuk 5 chunks, muat lebih banyak kandidat dalam budget yang sama.

### 4.6 Iterative Retrieval Pattern (untuk Agent Instructions)

MCP tool description harus explicitly guide agent ke pattern yang benar:

```
search_docs:
  "Search documentation. Returns summaries by default. Use compact mode to scan
   many candidates cheaply, then call get_chunk only for the most relevant ones."

get_chunk:
  "Get full content of a specific chunk. Only call this after search_docs
   identifies a candidate worth reading in full."
```

---

## 5. MCP Tool Specifications

### 5.1 `search_docs`

**Schema Input:**

```json
{
  "query": "string (required) — natural language or symbol query",
  "pack": "string (optional) — pack name or glob pattern, e.g. 'react-*'",
  "limit": "integer (optional, default 5, max 10)",
  "min_confidence": "'high' | 'low' | 'any' (optional, default 'any')",
  "response_mode": "'compact' | 'standard' | 'full' (optional, default 'standard')",
  "token_budget": "integer (optional) — max tokens to use in response"
}
```

**Schema Output (standard mode):**

```json
{
  "query": "string",
  "chunks": [
    {
      "id": "string",
      "pack": "string",
      "heading_path": ["string"],
      "summary": "string (~120 chars)",
      "content_preview": "string (~200 chars, omit in compact mode)",
      "url": "string",
      "score": "float",
      "confidence": "'high' | 'low'",
      "boosts_applied": ["string"] // omit if empty
    }
  ],
  "result_status": "'ok' | 'low_confidence' | 'empty'",
  "scanned_packs": ["string"],
  "scanned_chunks": "integer",
  "token_usage": { ... }, // only if token_budget was specified
  "suggestion": "string | null"
}
```

**Behavior rules:**

- Selalu return structured response, tidak pernah null atau unstructured error
- Empty result HARUS include `scanned_packs`, `scanned_chunks`, `max_score`, `threshold_floor`
- `suggestion` wajib ada jika `result_status == 'empty'`

### 5.2 `get_chunk`

**Schema Input:**

```json
{
  "id": "string (required) — chunk ID dari search_docs result",
  "include_neighbors": "boolean (optional, default false)",
  "include_document_meta": "boolean (optional, default false)",
  "token_budget": "integer (optional)"
}
```

**Schema Output:**

```json
{
  "chunk": {
    "id": "string",
    "pack": "string",
    "heading_path": ["string"],
    "content": "string (full content)",
    "code_content": "string | null",
    "url": "string",
    "token_count": "integer"
  },
  "previous": { "id": "string", "heading_title": "string", "summary": "string" },
  "next": { "id": "string", "heading_title": "string", "summary": "string" },
  "document": {
    "url": "string",
    "title": "string",
    "heading_tree": { ... }
  }
}
```

**Note:** `previous` dan `next` hanya include `summary`, bukan full content, untuk menjaga token cost tetap rendah. Agent bisa decide apakah perlu call `get_chunk` untuk neighbor.

### 5.3 `list_packs`

**Schema Input:**

```json
{
  "name_pattern": "string (optional) — glob filter, e.g. 'react-*'"
}
```

**Schema Output:**

```json
{
  "packs": [
    {
      "name": "string",
      "entry_url": "string",
      "version": "string | null",
      "chunks": "integer",
      "last_refreshed": "ISO 8601 string",
      "freshness": "'fresh' | 'stale' | 'very_stale'"
    }
  ],
  "total": "integer"
}
```

---

## 6. Non-Functional Requirements

### 6.1 Performance

| Metric                           | Target                           |
| -------------------------------- | -------------------------------- |
| `search_docs` p50 latency        | < 30ms                           |
| `search_docs` p95 latency        | < 100ms                          |
| `search_docs` p99 latency        | < 200ms                          |
| `docctx add` per-page throughput | < 2s per page (network excluded) |
| DB size per 1000 chunks          | < 20MB                           |
| Memory usage (MCP server idle)   | < 50MB RSS                       |

### 6.2 Reliability

- MCP server tidak crash on malformed input. Semua error → structured JSON response dengan `error_code` dan `message`.
- SQLite WAL mode untuk concurrent read + write.
- Setiap ingestion idempotent: double-add sama persis = clean error, bukan corrupt state.

### 6.3 Portability

- macOS 13+, Linux (Ubuntu 22.04+), Windows 11 (WSL minimal)
- Python 3.11+
- Zero system dependencies selain Python dan uv
- Store di `~/.docctx/` by default, configurable via `DOCCTX_HOME` env var

### 6.4 Observability

- Semua CLI commands punya `--verbose` flag yang menampilkan internal stages
- `--json` flag di semua commands untuk machine-readable output
- Log level configurable via `DOCCTX_LOG_LEVEL` env var
- Semua thresholds, boost multipliers, dan limits visible di `config.toml` dengan komentar

---

## 7. Configuration Schema

`~/.docctx/config.toml`:

```toml
[retrieval]
floor_score = 3.0           # calibrated 2026-05, eval set v1
confidence_cutoff = 6.0     # calibrated 2026-05, eval set v1
default_limit = 5
max_limit = 10
default_response_mode = "standard"

[retrieval.boosts]
heading_exact = 1.5
code_match = 1.3
trust_tier_official = 1.2
heading_title_exact = 1.1

[chunking]
target_tokens = 400         # target chunk size
max_tokens = 800            # hard cap
min_tokens = 80             # minimum before merge

[ingestion]
rate_limit_rps = 1.0        # requests per second
max_pages = 50
max_depth = 2
respect_robots = true
request_timeout_sec = 30
user_agent = "docctx/1.0 (+https://github.com/you/docctx)"

[freshness]
fresh_days = 7
stale_days = 30

[storage]
home = "~/.docctx"          # override via DOCCTX_HOME
cache_enabled = true
```

---

## 8. Eval Requirements

### 8.1 Eval Dataset (v1)

30 query dengan ground truth manual:

**10 Exact API queries:**

- Harus muncul di top-3. Contoh: `"useEffect cleanup function"`, `"axios interceptor add request header"`, `"prisma findMany where clause"`.

**10 Conceptual queries:**

- Acceptable di top-5. Contoh: `"how to handle async errors in React"`, `"difference between controlled and uncontrolled inputs"`.

**10 Out-of-scope queries:**

- Result harus empty atau low_confidence. Contoh: query tentang library yang tidak ada di index, query umum yang tidak ada di docs.

### 8.2 Success Metrics

| Metric                            | Target MVP | Critical |
| --------------------------------- | ---------- | -------- |
| Hit@1 exact API                   | ≥ 80%      | Yes      |
| Hit@5 conceptual                  | ≥ 70%      | Yes      |
| Empty rate OOS                    | ≥ 80%      | Yes      |
| False positive OOS top-1          | < 10%      | Yes      |
| p95 latency                       | < 100ms    | Yes      |
| Token/search (standard, 5 chunks) | < 1500     | Yes      |
| Token/search (compact, 5 chunks)  | < 500      | Yes      |

### 8.3 Token Efficiency Validation

Sebelum ship, jalankan token count audit:

1. Ambil 10 random query dari eval set
2. Call `search_docs` dengan mode `standard`, hitung token di response (gunakan tiktoken atau equivalent)
3. Pastikan rata-rata < 1500 tokens
4. Repeat dengan mode `compact` → pastikan < 500 tokens

---

## 9. Out of Scope (M1)

- Embeddings / vector search
- LLM enrichment saat ingestion
- JS rendering (playwright)
- Markdown-git adapter
- Local filesystem adapter
- OpenAPI adapter
- Web UI
- Multi-user / team sync
- Auto-refresh / cron
- Native multi-version per pack
- Graph relationship extraction between concepts
- Cross-pack concept linking
- Diff-based incremental refresh (hash check yes, diff no)

---

## 10. Open Questions

| #   | Pertanyaan                                                                                  | Status                                                    |
| --- | ------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Q1  | Summary generation: rule-based (first sentence) atau lightweight template?                  | Perlu keputusan sebelum implement chunker                 |
| Q2  | Token counting: tiktoken dependency atau approx (chars/4)?                                  | Prefer approx untuk zero-dep, tapi perlu validate akurasi |
| Q3  | `response_mode: "full"` di search — apakah perlu di M1 atau cukup standard + get_chunk?     | Bisa defer ke M1.1                                        |
| Q4  | FTS5 custom tokenizer untuk camelCase — implementasi di Python layer atau SQLite extension? | Perlu spike sebelum indexer                               |
| Q5  | MCP server: apakah perlu support HTTP transport selain stdio?                               | Defer ke M2                                               |
