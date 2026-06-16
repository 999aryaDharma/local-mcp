# docctx — Milestone 2 (M2) Planning

**Version:** 2.0 (Draft Plan)  
**Status:** Planning Phase  

---

## 1. Visi & Tujuan M2
Jika M1 fokus pada penyediaan fondasi yang deterministik, murah, dan bebas halusinasi melalui *Keyword/BM25 Search*, maka M2 bertujuan memberikan **"Otak Semantik"** pada `docctx`. Tujuannya agar agent bisa bertanya menggunakan bahasa abstrak atau konseptual, dan `docctx` mampu menerjemahkannya ke dalam potongan dokumentasi yang tepat.

Tujuan utama M2:
1. **Hybrid Search:** Menjawab *Conceptual Queries* dengan akurasi setara *Exact API Queries*.
2. **High-Quality Summaries:** Rangkuman yang dihasilkan oleh LLM, bukan sekadar "kalimat pertama".
3. **Omni-Source Ingestion:** Mampu membaca file lokal dan repository Git, bukan hanya URL.

---

## 2. Peta Jalan (Roadmap) Fase M2

### Fase 2.1: Hybrid Search Implementation (Semantic + Keyword)
BM25 sangat pintar mencari "useEffect", tapi bodoh mencari "how to fetch data on mount". Kita akan menggabungkan keduanya.

* **Teknologi:** 
  - Menggunakan ekstensi SQLite vector (`sqlite-vec` atau `sqlite-vss`) agar tetap *local-first* tanpa perlu install *database* terpisah seperti Pinecone/Milvus.
  - Model embedding ringan (contoh: `all-MiniLM-L6-v2` berukuran ~80MB) berjalan lokal via `onnxruntime` atau `sentence-transformers`.
* **Mekanisme Retrieval:**
  - Agent mengirim query.
  - `docctx` memanggil BM25 (FTS5) **DAN** Vector Search secara bersamaan.
  - Skor digabungkan menggunakan algoritma **Reciprocal Rank Fusion (RRF)**.
  - Hasil akhirnya di-filter menggunakan *confidence threshold* seperti di M1.

### Fase 2.2: LLM-Powered Ingestion (Gemini API)
Di M1, summary di-generate secara statis menggunakan rule-based (kalimat pertama). Di M2, proses `docctx add` akan jauh lebih pintar berkat kemampuan LLM berkapasitas tinggi.

* **Teknologi:** 
  - Integrasi dengan **Gemini API** (Gemini 1.5 Pro/Flash) untuk penalaran yang dalam dengan konteks window yang sangat luas.
* **Mekanisme Ingestion:**
  - Saat web di-crawl dan di-chunk, setiap chunk dikirim ke Gemini dengan prompt khusus untuk mengekstrak intisari API tersebut ke dalam kalimat padat (maks 150 karakter) khusus untuk dibaca oleh coding agent.
  - Ringkasan ini disimpan di kolom `summary` pada database.
* **Keuntungan:** Agent hanya akan membaca summary beresolusi tinggi, menghemat token besar-besaran sambil meningkatkan akurasi *Search*.

### Fase 2.3: Knowledge Graph Extraction (Relasi Antar Dokumen)
Dokumentasi teknis tidak berdiri sendiri; mereka saling terhubung. Contoh: komponen A butuh package B, atau fungsi X mengembalikan tipe data Y.

* **Teknologi:**
  - Memanfaatkan Gemini API saat ingestion untuk mengenali entitas dan relasi antar dokumen (*Cross-document entity resolution*).
* **Mekanisme:**
  - Saat memproses chunk, Gemini API mengidentifikasi *Dependencies*, *Related Concepts*, dan *Deprecation Warnings*.
  - Relasi ini disimpan dalam tabel SQLite terpisah (seperti `concept_edges`).
  - Saat agent memanggil `get_chunk`, `docctx` bisa menambahkan metadata: *"Fungsi ini membutuhkan tipe data `User`, pertimbangkan untuk me-retrieve chunk tipe data `User` jika belum memilikinya."*

### Fase 2.4: Ingestion Adapters (Local Files & Repositories)
Banyak dokumentasi perusahaan bersifat *private* dan tidak tersedia di web publik.

* **Local Filesystem Adapter:**
  - Perintah baru: `docctx add /path/to/project/docs --type local`
  - Membaca file `.md`, `.mdx`, `.txt`, `.rst` langsung dari disk komputermu.
* **Git Repository Adapter:**
  - Perintah baru: `docctx add https://github.com/user/repo --type git`
  - Otomatis melakukan *shallow clone*, mencari file dokumentasi (README, folder `docs/`), dan meng-ingest isinya.

---

## 3. Perubahan Arsitektur (Architecture Changes)

**Database Schema (`store.db`)**
- Tambahan Virtual Table baru untuk Vector: `chunks_vec`.
- Kolom baru di tabel `chunks`: `embedding` (blob) dan `llm_summary` (text).

**Configuration (`config.toml`)**
```toml
[retrieval]
mode = "hybrid" # Pilihan: keyword, semantic, hybrid
rrf_k = 60      # Konstanta Reciprocal Rank Fusion

[embeddings]
model = "all-MiniLM-L6-v2"
provider = "local" # Pilihan: local, openai

[ingestion]
llm_summarize = true
llm_provider = "gemini"
llm_model = "gemini-1.5-flash"
api_key_env = "GEMINI_API_KEY"

[graph]
extract_entities = true
```

---

## 4. Kriteria Sukses (Success Metrics) M2

Evaluasi akan dijalankan kembali menggunakan `docctx eval` dengan dataset yang diperbesar.

| Metric | Target M1 (Saat ini) | Target M2 |
|---|---|---|
| Hit@1 Exact API | 100% | 100% |
| **Hit@5 Conceptual** | ~70% | **> 95%** |
| Empty Rate (Out-of-Scope) | > 80% | > 90% |
| Token Usage (Standard mode) | < 1500 tokens | **< 1000 tokens** (berkat LLM summary yang lebih pendek & akurat) |

---

## 5. Next Action Items
Untuk memulai M2, langkah teknis pertama yang direkomendasikan adalah melakukan **Spike/Riset** pada ekstensi SQLite Vector (`sqlite-vec`) untuk memastikan ekstensi ini bisa berjalan mulus di Windows/Mac/Linux secara *cross-platform* tanpa menyulitkan proses instalasi bagi pengguna.
