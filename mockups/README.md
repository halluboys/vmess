# Mockups — GH-DWD Webapp UI Preview

Tampilan UI untuk webapp [GH-DWD](https://github.com/halluboys/GH-DWD) — read-only
GitHub viewer. Dibuat **1:1 mirip GitHub Dark theme** (Primer-inspired tokens,
ikon Octicons, layout Code/Commits, spacing yang sama).

## Cara Lihat

### Cara cepat (clone & buka lokal)

```bash
git clone https://github.com/halluboys/vmess.git
cd vmess/mockups
python3 -m http.server 8080
# buka http://localhost:8080
```

Lalu klik antar halaman untuk navigasi (semua link sudah connected).

### Tanpa server (langsung double-click file)

Cuma buka `01-login.html` dulu, lalu navigate via link. Beberapa fitur (search bar
focus ring) tetap jalan. Kalau pakai `file://`, syntax CDN external (Prism) gak
dipakai di mockup ini — semua styling sudah inline via `_styles.css`.

## Halaman

| # | File | Halaman |
|---|---|---|
| 1 | `01-login.html` | Login page (centered card, GitHub-style) |
| 2 | `02-dashboard.html` | Repo list dengan profile sidebar |
| 3 | `03-repo-home.html` | Repo home — file table + About sidebar + README |
| 4 | `04-tree.html` | File browser dengan latest commit per file |
| 5 | `05-blob.html` | File viewer dengan line numbers + syntax highlight |
| 6 | `06-commits.html` | Commit history dengan timeline + grouping per hari |
| 7 | `07-commit-detail.html` | Commit diff view (line numbers kiri/kanan) |

`_styles.css` — desain system shared (color tokens, button, card, dll).

## Catatan

- Mockup pakai data dummy (repo `halluboys/dot`). Saat dipakai beneran, data dari
  GitHub API real.
- Color palette match GitHub Dark theme:
  - canvas `#0d1117`, overlay `#161b22`, border `#30363d`
  - fg `#e6edf3`, muted `#7d8590`, accent `#2f81f7`
  - tab indicator orange `#f78166`, success green `#238636`
- Hampir semua ikon dari Octicons (GitHub's icon set) — copy SVG paths langsung.
