# Mockups — Tampilan GH-DWD (GitHub Web Viewer)

Preview UI dari webapp GitHub Web Viewer (repo: [halluboys/GH-DWD](https://github.com/halluboys/GH-DWD)).
File HTML di folder ini self-contained (cuma butuh internet untuk Tailwind CDN).

## Cara Lihat

### Opsi 1: Preview langsung di browser via HTML preview

Klik link di bawah — masing-masing render file HTML dari repo ini lewat
[html-preview.github.io](https://html-preview.github.io/):

| # | Halaman | Preview |
|---|---|---|
| 1 | Login (GitHub PAT) | [Open](https://html-preview.github.io/?url=https://github.com/halluboys/vmess/blob/main/mockups/01-login.html) |
| 2 | Dashboard (Repo list + search) | [Open](https://html-preview.github.io/?url=https://github.com/halluboys/vmess/blob/main/mockups/02-dashboard.html) |
| 3 | Repo Home (file list + README) | [Open](https://html-preview.github.io/?url=https://github.com/halluboys/vmess/blob/main/mockups/03-repo-home.html) |
| 4 | File Browser (folder navigation) | [Open](https://html-preview.github.io/?url=https://github.com/halluboys/vmess/blob/main/mockups/04-tree.html) |
| 5 | File Viewer (line numbers + syntax) | [Open](https://html-preview.github.io/?url=https://github.com/halluboys/vmess/blob/main/mockups/05-blob.html) |
| 6 | Commit History (per-day grouping) | [Open](https://html-preview.github.io/?url=https://github.com/halluboys/vmess/blob/main/mockups/06-commits.html) |
| 7 | Commit Detail (diff view) | [Open](https://html-preview.github.io/?url=https://github.com/halluboys/vmess/blob/main/mockups/07-commit-detail.html) |

> **Catatan**: Preview hanya jalan kalau repo `halluboys/vmess` **public**.
> Kalau private, pakai Opsi 2 di bawah.

### Opsi 2: Clone dan buka lokal

```bash
git clone https://github.com/halluboys/vmess.git
cd vmess/mockups
# buka file .html di browser
xdg-open 01-login.html       # Linux
open 01-login.html           # macOS
start 01-login.html          # Windows
```

## Catatan

- Mockup pakai **data dummy** (repo `halluboys/dot`, dst). Saat dipakai beneran,
  data datang dari GitHub API real.
- Style pixel-identik dengan rendering production karena pakai CSS yang sama.
- Label hijau di pojok kanan atas hanya muncul di mockup, tidak di webapp asli.
