# docctx — Panduan Pengguna (User Guide)

Selamat datang di `docctx`! Tool ini dirancang agar Coding Agent AI (seperti Claude Code, Cursor, atau Windsurf) berhenti berhalusinasi saat menggunakan *tech stack* atau library versi terbaru. 

Panduan ini akan menjelaskan cara mengisi "otak" agent-mu dengan dokumentasi yang presisi menggunakan versi M1 ini.

---

## 1. Instalasi

Karena `docctx` dibangun menggunakan Python secara *local-first*, pastikan kamu sudah menginstal `uv` atau `pip`.

```bash
# Clone repository ini (jika belum)
git clone <repo-url>
cd local-mcp

# Install menggunakan uv (sangat direkomendasikan)
uv pip install -e .

# Jalankan health check untuk memastikan semuanya berjalan lancar
uv run docctx doctor
```
Jika `doctor` menampilkan tulisan **"All checks passed. docctx is healthy"**, kamu siap lanjut ke tahap berikutnya!

---

## 2. Menambahkan Dokumentasi (Ingestion)

Agent AI-mu tidak akan tahu apapun sampai kamu memberikan dokumentasinya. Kamu bisa meng-*crawl* dokumentasi dari URL resmi.

### A. Meng-*crawl* Satu Halaman Spesifik
Gunakan flag `--scope page-only` jika kamu hanya butuh 1 halaman itu saja (misalnya halaman tentang *useEffect* di React).
```bash
uv run docctx add "https://react.dev/reference/react/useEffect" --scope page-only --as "react-useeffect"
```

### B. Meng-*crawl* Seluruh Halaman yang Berdekatan (Siblings)
Secara default, jika kamu memasukkan sebuah URL panjang (mendalam), `docctx` akan men-download URL tersebut beserta URL lain di sebelahnya (satu kategori).
```bash
uv run docctx add "https://react.dev/reference/react/useEffect"
```

### C. Meng-*crawl* Satu Sub-kategori Penuh (Subtree)
Gunakan `--scope subtree` jika kamu ingin men-download URL itu beserta semua anak/sub-halamannya.
```bash
uv run docctx add "https://react.dev/reference/react" --scope subtree --as "react-reference"
```

### D. Melihat Daftar Dokumentasi
Untuk mengecek dokumentasi apa saja yang sudah berhasil di-*crawl* dan tersedia untuk agent-mu:
```bash
uv run docctx list
```

---

## 3. Integrasi dengan Coding Agent (MCP)

Agar agent AI-mu bisa membaca dokumentasi yang baru saja kamu tambahkan, kamu harus menyambungkan `docctx` ke dalam agent-mu via **Model Context Protocol (MCP)**.

File konfigurasi otomatis bernama `mcp_config.json` sudah dibuat di folder proyekmu. Isinya seperti ini:
```json
{
  "mcpServers": {
    "docctx": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "D:/local-mcp",
        "docctx",
        "serve"
      ]
    }
  }
}
```

### Cara Menyambungkan:
- **Untuk Claude Desktop / Claude Code:** Salin isi konfigurasi di atas ke dalam file konfigurasi Claude-mu (biasanya berada di `~/.claude_desktop_config.json` atau via menu Settings).
- **Untuk Cursor:** Buka Settings > Features > MCP. Tambahkan server baru dengan tipe `command`, lalu masukkan perintah eksekusinya: `uv run --project D:/local-mcp docctx serve`.
- **Untuk Windsurf:** Masukkan konfigurasi JSON di atas ke file konfigurasi MCP Windsurf.

Setelah tersambung, coba buka chat di agent-mu dan katakan:
*"Gunakan tool `search_docs` dari docctx untuk mencari tahu cara menggunakan useEffect cleanup!"*

---

## 4. Menguji Pencarian Secara Manual (Debugging)

Kalau kamu merasa agent-mu gagal menemukan jawaban yang tepat, kamu bisa menguji mesin pencari `docctx` secara manual lewat terminalmu:

### A. Melakukan Query
Mencari tahu apakah keyword tertentu membuahkan hasil:
```bash
uv run docctx query "useEffect cleanup"
```

### B. Menganalisis Kenapa Sebuah Dokumen Terpilih (Explain)
Jika kamu bingung kenapa hasil X muncul di nomor 1, gunakan perintah `explain` untuk melihat alasan *scoring* dan *confidence* secara transparan:
```bash
uv run docctx explain "useEffect cleanup"
```

### C. Memeriksa Kualitas Ekstraksi (Inspect)
Ingin tahu bagaimana bentuk teks asli sebelum dipotong-potong menjadi *chunk*?
```bash
uv run docctx inspect "https://react.dev/reference/react/useEffect"
```

---

## 5. Maintenance (Perawatan)

Dokumentasi web bisa kedaluwarsa atau diperbarui oleh pembuatnya. Kamu bisa memperbaruinya di komputermu dengan mudah:

**Memperbarui Dokumentasi (Refresh)**
Hanya akan men-download halaman yang berubah dari situs aslinya.
```bash
uv run docctx refresh react-useeffect
```

**Menghapus Dokumentasi (Remove)**
Jika suatu library sudah tidak kamu pakai lagi di proyekmu:
```bash
uv run docctx remove react-useeffect
```

---
**Tips Pro:** Masukkan dokumentasi dengan spesifik! Jangan meng-index seluruh situs web jika kamu hanya menggunakan sebagian fiturnya. Semakin sedikit dan spesifik dokumen yang kamu *add*, semakin cerdas dan fokus Agent-mu dalam memberikan solusi koding.
