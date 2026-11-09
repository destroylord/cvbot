# Changelog

## 2026-11-09 (lanjutan): perbaikan "Gagal menyiapkan interview: Expecting value: line ... column ..."

Penyebab: AI sesekali mengembalikan JSON yang tidak valid, paling sering ada koma berlebih sebelum `]` atau `}`,
atau jawabannya terpotong. Ini bisa terjadi di semua fitur yang meminta JSON (scan CV, analisis lowongan,
rekomendasi, penyesuaian CV, perbaikan PAR, persiapan interview, klasifikasi balasan HR), jadi diperbaiki di satu
tempat:

- **Mode JSON bawaan provider**: Gemini (`responseMimeType: application/json`) dan Ollama (`format: json`). Teks
  biasa seperti email lamaran tidak terpengaruh.
- **Perbaikan otomatis** (`_parse_json`): teks/kode pembuka dibuang, koma berlebih dihapus tanpa menyentuh koma di
  dalam teks, dan JSON yang terpotong ditutup. JSON yang sudah valid selalu dibaca apa adanya dulu.
- **Coba ulang sekali** (`_ask_json`): kalau masih rusak, AI diminta mengulang. Kalau gagal lagi, pesannya jelas
  dan awal jawaban AI dicatat di log.
- Batas jawaban persiapan interview dinaikkan ke 6000 token supaya tidak terpotong.

Diuji 13 pengecekan, termasuk mereproduksi error yang sama (`Expecting value`) dari JSON dengan koma berlebih.


## 2026-11-09 (lanjutan): 6 fitur perlindungan & produktivitas

- **🚨 Deteksi loker penipuan** (`job_rules.scam_signals` + field `tanda_penipuan` dari AI): biaya rekrutmen,
  transfer/deposit, data sensitif (KTP/KK/rekening/OTP), janji pasti diterima, modus like/review dibayar, hanya
  WhatsApp, email gratis atas nama PT. Konteks aman ("ditanggung perusahaan", "tidak dipungut biaya") tidak memicu
  peringatan. Tanda serius diulang di layar konfirmasi kirim.
- **🎤 Persiapan interview** (`interview_prep`, `interview_feedback`): tombol di notifikasi undangan interview dan
  di detail lamaran. Berisi pertanyaan, petunjuk jawaban dari pengalaman nyata, pertanyaan balik, dan tips (disimpan
  per lamaran). Ada latihan tanya-jawab dengan umpan balik AI.
- **🎯 Preferensi loker**: kota, mode kerja, gaji minimal, kata posisi yang dihindari. Rekomendasi disaring
  (dengan info jumlah yang disaring), dan lowongan manual diberi peringatan. AI sekarang juga membaca `lokasi`,
  `mode_kerja`, `gaji_min`, `gaji_max`.
- **🔁 Cegah lamaran dobel**: peringatan di ringkasan dan konfirmasi, nama perusahaan dinormalisasi. Lowongan yang
  sudah dilamar tidak direkomendasikan lagi.
- **🕗 Jadwal kirim di jam kerja** (Senin-Jumat 08-17, bisa dimatikan) dan **batas harian per akun**
  (`MAX_SEND_PER_DAY`, default 20). Antrean tersimpan di data pengguna, dijadwalkan ulang saat bot start, dan bisa
  dikirim sekarang atau dibatalkan. Kalau gagal kirim, lamaran tetap di antrean. Pengiriman dirapikan jadi
  `send_and_record`.
- **💾 Backup & pulihkan**: `/backup` mengirim zip (tanpa password/API key). Kirim zip ke bot untuk memulihkan,
  dengan konfirmasi, cadangan otomatis data sebelumnya, path folder disesuaikan, dan zip berbahaya (`../`) ditolak.

Modul baru `job_rules.py` (fungsi murni). Diuji 54 pengecekan dengan waktu & antrean jadwal palsu, termasuk
pengiriman terjadwal yang benar-benar dijalankan, gagal SMTP, batas harian, pulihkan backup dari folder pengguna
lain, dan zip berbahaya.


## 2026-11-09 (lanjutan): pratinjau skor sebelum PDF & 🚀 Tingkatkan lagi

- Setelah penyesuaian CV, bot **menampilkan skor baru dulu** (skor awal → baru, skill cocok, skill yang masih
  kurang, perubahan). **PDF baru dibuat setelah dipilih ✅ Pakai skor ini, buat CV.** Pilihan lain: 🚀 Tingkatkan
  lagi, ↩️ Pakai CV asli (skor kembali ke skor awal), atau ❌ Lewati.
- **🚀 Tingkatkan lagi**: skor dinaikkan secara jujur dengan menanyakan setiap skill yang kurang (maksimal 5):
  "Pernah" + cerita singkat, atau "Belum". CV disusun ulang dengan pengalaman yang dikonfirmasi (`tailor_cv`
  parameter `skills`), dinilai ulang, dan bisa diulang. Kalau sisa kekurangan semuanya "belum", bot menyatakan
  itu skor maksimal yang jujur.
- `guard_profile` menerima `extra_skills`: skill yang dikonfirmasi pengguna boleh masuk CV, skill karangan AI
  tetap dibuang.
- Jawaban diingat (`skill_konfirmasi`, `skill_belum`): skill yang "pernah" dipakai untuk lowongan berikutnya,
  yang "belum" tidak ditanya ulang. Setelah konfirmasi, bot bertanya dulu sebelum menambahkan skill ke 📄 CV Saya.

Diuji 24 pengecekan: pratinjau tanpa PDF, tanya-jawab pernah/belum, skill karangan tetap dibuang, tambah ke CV
asli hanya setelah disetujui, skor maksimal jujur, PDF setelah disetujui, kembali ke CV asli, lowongan berikutnya,
dan tombol kedaluwarsa.


## 2026-11-09 (lanjutan): ✨ Perbaiki poin dengan PAR untuk CV asli

- Tombol baru di **📄 CV Saya**. AI (`improve_cv_par`) menulis ulang semua poin pengalaman dan proyek dengan
  metode PAR untuk CV umum. Yang dikembalikan hanya poin per entri, dengan jumlah dan urutan yang sama, jadi
  posisi, perusahaan, dan periode tidak tersentuh.
- Memakai mekanisme tanya-jawab yang sama dengan penyesuaian CV (sekarang punya mode `job` dan `master`).
  Jawaban disimpan ke `hasil_poin`, dan dipakai baik untuk penyesuaian CV maupun perbaikan berikutnya.
- **Pratinjau sebelum → sesudah** (dipecah jadi beberapa pesan kalau panjang), lalu **Simpan** atau **Batal**.
  CV asli baru berubah setelah disetujui, dan lampiran "CV asli" ikut dibuat ulang.
- **Pengaman angka di kode** (`profile_edit.merge_points`): poin baru yang memuat angka yang tidak ada di entri
  aslinya maupun di jawaban pengguna dikembalikan ke poin asli. `50.000` dan `50000` dianggap sama.
- Kalau CV asli diubah lewat menu lain antara pratinjau dan Simpan, hasil PAR tidak disimpan supaya tidak
  menimpa perubahan itu.

Diuji 29 pengecekan (pengaman angka, prompt, alur tanya-jawab, pratinjau, simpan, batal, simpan dua kali,
perubahan di tengah jalan, pratinjau panjang) ditambah uji regresi penyesuaian CV per lowongan.


## 2026-11-09 (lanjutan): 📄 CV Saya, ubah CV asli dari Telegram

CV asli tidak perlu lagi di-upload ulang setiap ada perubahan. Tombol menu **👤 Profil** diganti **📄 CV Saya**
(tombol lama tetap jalan), perintah `/cv` (dan `/profil`).

- Ubah **data diri** (nama, email, telepon, lokasi, tautan) dan **ringkasan**.
- **Pengalaman & proyek**: tambah bertahap (pengalaman baru di urutan paling atas), ubah tiap field, hapus dengan
  konfirmasi, naikkan urutan. Poin bisa ditambah (satu per baris, diingatkan pola PAR), diubah, dihapus, dan
  diurutkan.
- **Skill, pendidikan, sertifikasi, bahasa, tautan**: tambah (skill bisa dipisah koma, duplikat diabaikan), ubah,
  hapus, urutkan.
- **📥 Unduh CV (PDF)**.
- **Lampiran ikut berubah**: setelah CV asli diubah, lampiran "CV asli" saat melamar dibuat ulang dari data
  terbaru dengan font CV kamu, bukan file lama yang dulu di-upload. Upload file CV baru mengembalikan ke hasil
  scan file itu.
- Tombol lama yang menunjuk data yang sudah dihapus atau berubah memberi pesan jelas, tidak crash. Isian bisa
  dibatalkan dengan `batal` atau tombol menu.
- Modul baru `profile_edit.py`: fungsi murni untuk mengubah profil (tanpa Telegram), tahan data AI yang tidak rapi.

Diuji 40 pengecekan: fungsi `profile_edit`, seluruh alur menu (data diri, ringkasan, daftar, pengalaman bertahap,
poin, hapus dengan konfirmasi), tombol kedaluwarsa, batal, unduh PDF, dan lampiran lamaran memakai CV terbaru.


## 2026-11-09 (lanjutan): metode PAR, font CV, email lamaran yang wajar

### Metode PAR + tanya hasil ke pengguna
- Poin pengalaman dan proyek ditulis dengan metode **PAR** (Problem → Action → Result).
- Hasil hanya dari CV asli atau dari jawaban pengguna. Kalau poin penting belum punya hasil, AI menandainya
  (`perlu_hasil`) dan bot **bertanya dulu** (maksimal 5), dengan tombol **⏭️ Lewati** dan **✅ Cukup, buat CV**.
  Setelah dijawab, CV disusun ulang dengan hasil tersebut.
- Jawaban disimpan (`hasil_poin`) dan dipakai lagi untuk lowongan berikutnya. Poin yang dilewati diingat
  (`hasil_dilewati`), jadi tidak ditanya ulang. Teks poin dari AI yang sedikit berbeda dicocokkan ke poin asli.
- `guard_profile` tetap membuang skill, perusahaan, dan proyek karangan.

### Font CV
- Modul baru `cv_fonts.py`. Font diambil dari Google Fonts (TTF statis, sekali unduh ke `data/fonts/`).
- Default: **font CV asli**, dideteksi saat CV diunggah (`cv_parser.detect_font`). Untuk PDF dipilih font yang
  paling banyak dipakai karakternya (font isi, bukan judul). Untuk DOCX dicek font per teks, lalu gaya Normal,
  lalu font tema dokumen.
- Font Microsoft diganti padanannya yang ukurannya identik (Calibri → Carlito, Arial → Arimo, Times New Roman →
  Tinos, Cambria → Caladea, Georgia → Gelasio). Font Canva/Google dipakai apa adanya.
- **⚙️ Pengaturan > 🔤 Font CV**: 11 font umum + Helvetica, atau ketik nama font Google Fonts mana pun
  (dicek dulu ke Google Fonts). Gagal unduh berarti pakai Helvetica, dan caption PDF memberi tahu.
- Karakter yang tidak ada di font dibuang (bukan kotak kosong). Kutip miring dan karakter non-Latin yang
  didukung font sekarang tetap tampil.

### Email lamaran
- "Cover letter" diganti **email lamaran** yang wajar dan **tidak mengulang isi CV**, mengikuti contoh dari
  pengguna: salam, perkenalan (nama, posisi, perusahaan, sumber lowongan), satu kalimat keahlian yang relevan,
  CV/portofolio terlampir, penutup siap wawancara, tanda tangan (nama, telepon, tautan).
- Gaya: Formal 120-180 kata, Santai 90-140 kata, Singkat 50-80 kata.
- Sumber lowongan diambil dari domain link (misalnya `loker.id`) atau rekomendasi job alert. Portofolio hanya
  disebut kalau ada tautannya di profil. Rincian poin pengalaman tidak lagi dikirim ke prompt email.

Diuji 43 pengecekan: font asli diunduh dari Google Fonts dan tertanam di PDF (Carlito, Poppins, EB Garamond,
Raleway), deteksi font PDF/DOCX/tema, alur tanya-jawab PAR (jawab, lewati, cukup, fakta dipakai ulang, batal
lewat menu), prompt email, dan menu font. Tampilan PDF dicek langsung.


## 2026-11-09 (lanjutan): perbaikan "Tes gagal: Extra data: line 1 column ..."

Error ini muncul karena ada data berisi satu objek JSON yang valid lalu diikuti data lain di belakangnya.
Ada dua kemungkinan sumber, dan keduanya diperbaiki:

- **Jawaban server AI berbentuk streaming** (beberapa potong JSON yang menempel, SSE `data: {...}`, NDJSON,
  atau array JSON), padahal bot mengira satu JSON utuh. Sekarang semua bentuk itu dibaca dan teksnya digabung,
  untuk Gemini, OpenAI-compatible, dan Ollama, termasuk daftar model. Jawaban yang sama sekali bukan JSON
  (misalnya halaman error HTML dari proxy) diberi pesan jelas beserta cuplikannya.
- **File di `data/` tertimpa sebagian** (misalnya kalau dua proses bot sempat jalan bersamaan). Modul baru
  `storage.py` menulis secara atomik (file sementara lalu `os.replace`) untuk `settings.json`,
  `gmail_accounts.json`, dan data pengguna. File yang terlanjur rusak dipulihkan dari objek JSON pertama
  yang utuh, dan salinan aslinya disimpan sebagai `<nama>.rusak`.
- Kalau 🧪 Tes AI gagal, error lengkapnya sekarang dicatat di log (`./bot.sh log`), termasuk provider dan
  model yang dipakai.

Diuji 15 pengecekan, diawali dengan mereproduksi error aslinya (`Extra data: line 1 column 29xx`) memakai
objek `requests.Response` asli. Ditambah uji bot start dan simpan/baca data pengguna lewat `storage.py`.


## 2026-11-09 (lanjutan): alasan Gemini gagal ditampilkan jelas

Sebelumnya, log dan pesan bot menyebut "tidak tersedia" untuk semua jenis kegagalan (server sibuk, kuota habis,
model tidak ada), jadi penyebab sebenarnya tidak kelihatan.

- Pesan error sekarang merinci **alasan per model** beserta pesan asli dari server, tanpa JSON mentah: tidak
  termasuk kuota gratis (`limit: 0`), model tidak ditemukan, kuota habis sementara, server sibuk.
- Error 429 `limit: 0` (model di luar kuota gratis) tidak lagi dicoba ulang di model yang sama karena menunggu
  tidak membantu, dan langsung pindah ke model cadangan.
- Error 400 (misalnya "User location is not supported") menampilkan pesan aslinya.
- Model default diganti dari `gemini-2.5-flash` (deprecated, akan dialihkan Google pada 1 Desember 2026) ke
  `gemini-3.6-flash` (Stable). Cadangannya `gemini-3.5-flash-lite` lalu `gemini-2.5-flash`. Hanya berlaku kalau
  model belum pernah dipilih di bot atau `.env`.
- **🧠 Pilih model** juga menyaring model Gemini yang bukan untuk teks: transkripsi, terjemahan live, robotik,
  computer-use, dan pembuat gambar (nano-banana).
- Sama untuk provider OpenAI-compatible.

Diuji 10 pengecekan dengan isi error asli Gemini (limit 0, 404, 429, 503, lokasi tidak didukung).


## 2026-11-09 (lanjutan): provider OpenAI-compatible dan login Claude

### Provider OpenAI-compatible
Untuk layanan apa pun yang memakai format API OpenAI, jadi tidak terikat ke satu model atau penyedia.

- Pilihan cepat: OpenRouter, Groq, DeepSeek, Together AI, OpenAI, Ollama `/v1`, LM Studio. Bisa juga ketik
  alamat API sendiri.
- API key opsional, untuk server lokal tanpa key.
- Daftar model diambil dari `/models`. Model gratis OpenRouter (`:free`) ditaruh paling atas. Model apa pun bisa
  diketik manual. Kalau server tidak menyediakan `/models`, key tetap disimpan.
- Coba ulang otomatis untuk 5xx, koneksi putus, **dan 429** (batas per menit, umum di model gratis), lalu
  pindah ke model cadangan. Model cadangan bisa lebih dari satu.
- Bisa membaca foto lowongan (format `image_url`) kalau modelnya mendukung gambar.

### Claude: login tanpa API key
- Pilihan **🔐 Login akun Claude Console**. Bot memakai profil login dari `ant auth login --no-browser` di VPS,
  jadi tidak perlu API key `sk-...`. Tombol **🔄 Cek login** memeriksa login lewat daftar model.
- Pesan jelas untuk: belum login, login kedaluwarsa, dan `ANTHROPIC_API_KEY` di `.env` yang menimpa login.
  Baris `ANTHROPIC_API_KEY=` kosong dari `.env.example` lama dibuang otomatis karena ikut menimpa login.
- Catatan: ini login akun Claude Console (pemakaian API). Kuota langganan Claude Pro/Max tidak bisa dipakai bot.

### Perbaikan
- Claude: `max_tokens` minimal 16.000. Model Claude terbaru berpikir dulu sebelum menjawab, jadi `max_tokens`
  kecil (20 untuk tes, 2.000–4.000 untuk JSON) bisa habis sebelum jawabannya keluar. Ditambah penanganan
  `stop_reason` `refusal` dan jawaban terpotong.
- Logika coba ulang & model cadangan Gemini dijadikan umum (`_with_fallback`) dan dipakai juga oleh
  OpenAI-compatible.

Diuji 49 pengecekan (termasuk library `anthropic` asli untuk kasus belum login) ditambah 8 pengecekan regresi
Gemini. Pemindaian AST memastikan tidak ada tombol yang terkirim sebagai `parse_mode`.


## 2026-11-09 (lanjutan): atur Model AI dari bot

Provider, model, dan API key AI tidak perlu lagi diatur di `.env`. Semua ada di
**⚙️ Pengaturan > 🤖 Model AI**:

- **Ganti provider** (Gemini / Claude / Ollama). Kalau API key-nya belum ada, bot langsung memintanya.
- **Pilih model dari daftar** yang diambil langsung dari provider, jadi tidak perlu menebak nama model.
  Untuk Gemini, hanya model teks yang ditampilkan (model embedding, TTS, gambar, dan live disaring).
  Bisa juga mengetik nama model sendiri.
- **Model cadangan** Gemini dan **model untuk foto** Ollama juga bisa dipilih dari daftar.
- **API key** dicek dulu ke provider sebelum disimpan. Pesannya dihapus dari chat dan hanya 4 karakter
  terakhir yang ditampilkan. Kalau model yang sedang dipakai tidak tersedia untuk key tersebut, bot memberi
  peringatan.
- **🧪 Tes AI** untuk memastikan pengaturan jalan, lengkap dengan waktu respons.
- Disimpan di `data/settings.json` (`chmod 600`) dan langsung berlaku tanpa restart. Urutannya: pengaturan
  dari bot, lalu `.env`, lalu nilai default. Jadi `.env` lama tetap jalan.
- Menu hanya aktif kalau `ALLOWED_USER_IDS` diisi.
- `.env` sekarang cukup berisi `TELEGRAM_BOT_TOKEN` dan `ALLOWED_USER_IDS`. Bagian AI di `.env.example`
  dikomentari karena opsional.

Diuji 35 pengecekan: urutan sumber pengaturan, pemanggilan AI memakai model & key dari bot, penyaringan
daftar model, key salah, Ollama mati, library `anthropic` belum ter-install, seluruh alur menu, batal,
indeks kedaluwarsa, tes AI, dan penolakan tanpa `ALLOWED_USER_IDS`. Ditemukan dan diperbaiki sebelum rilis:
tombol sempat dikirim sebagai `parse_mode`, bukan `reply_markup`. Seluruh `bot.py` sudah dicek dan tidak ada
panggilan lain dengan masalah yang sama.


## 2026-11-09 (lanjutan): Gemini sibuk (503) tidak langsung gagal

Sebelumnya, saat server Gemini sedang ramai, bot langsung menampilkan error mentah
`Gemini error 503: {...}` dan lowongan harus dikirim ulang.

- **Coba ulang otomatis**: error 500/502/503/504 dan koneksi putus dicoba ulang sampai 4 kali di model
  yang sama, dengan jeda 2, 5, lalu 10 detik.
- **Model cadangan**: kalau model utama tetap sibuk, kuotanya habis (429), atau namanya tidak ada (404),
  bot pindah ke `GEMINI_FALLBACK_MODELS` (default `gemini-2.5-flash-lite`, punya kuota sendiri).
  Kosongkan nilainya kalau tidak mau memakai model cadangan.
- **Pesan error ramah**: tidak ada lagi JSON mentah. Pesannya dibedakan antara server sibuk, kuota habis,
  dan nama model salah.
- **Tombol 🔄 Coba lagi** saat analisis lowongan gagal, memakai teks lowongan yang sama (termasuk hasil baca
  foto), jadi tidak perlu kirim ulang.
- Error yang bukan sementara (misalnya API key salah) tetap langsung ditampilkan tanpa coba ulang.

Diuji 19 pengecekan dengan server Gemini palsu: sibuk lalu pulih, koneksi putus, sibuk terus, kuota habis,
model tidak ada, API key salah, urutan model cadangan dari `.env`, dan tombol Coba lagi.


## 2026-11-09 (lanjutan): modul yang hilang ditulis ulang, bot bisa jalan

Sejak commit pertama, `bot.py` memanggil empat modul yang tidak pernah ada di repo, sehingga bot
langsung berhenti saat start (`ModuleNotFoundError`). Keempatnya sekarang ditulis ulang:

| File | Fungsi |
| --- | --- |
| `cv_parser.py` | Baca teks CV dari PDF (`pypdf`) dan DOCX (`python-docx`), termasuk isi tabel dan header DOCX. PDF terkunci tanpa password tetap dicoba dibuka. |
| `cv_builder.py` | Buat PDF CV yang sudah disesuaikan (`reportlab`): satu kolom dan ramah ATS, judul bagian Indonesia/Inggris sesuai bahasa lowongan. Tahan data AI yang tidak rapi (null, teks biasa padahal seharusnya daftar, emoji, kutip miring). |
| `job_fetcher.py` | Buka link lowongan. Data terstruktur `JobPosting` (dipakai banyak situs lowongan) dibaca lebih dulu, lalu teks halaman tanpa menu/footer. Pesan error jelas kalau situs memblokir bot. |
| `job_alerts.py` | Baca email job alert dari tiap akun Gmail (IMAP read-only + `BODY.PEEK`, jadi email tidak ditandai sudah dibaca). Pengirim diatur lewat `ALERT_SENDERS`, maksimal 10 email terbaru per pengirim supaya kuota AI aman. Link diganti penanda `[L1]`, `[L2]`, dan link berhenti-berlangganan dibuang. |
| `requirements.txt` | Daftar library. `anthropic` hanya perlu kalau `AI_PROVIDER=claude`. |

Diuji dengan 38 pengecekan memakai library asli: CV PDF/DOCX sungguhan, server web lokal, email MIME
tiruan (HTML, teks biasa, subjek ter-encode, charset lain, lampiran), IMAP palsu, bot start sungguhan
(13 handler + jadwal otomatis), dan alur end-to-end: upload DOCX → profil → lowongan skor rendah →
PDF CV disesuaikan. Selama pengujian ditemukan dan diperbaiki: URL di akhir kalimat ikut membawa tanda
titik.

Bagian "Belum beres" di entri sebelumnya soal file yang hilang sudah selesai. Yang belum pernah dicoba
hanya koneksi ke layanan sungguhan (Telegram, Gmail, dan AI), karena butuh token dan akun kamu.


## 2026-11-09 (lanjutan): kelola akun Gmail dari bot

Akun Gmail tidak perlu lagi ditulis di `.env`. Semua bisa dari Telegram:
**⚙️ Pengaturan > 📧 Akun Gmail**.

- **Tambah akun**: ketik alamat, lalu app password. Bot mencoba login IMAP dan SMTP dulu, dan akun
  hanya disimpan kalau login berhasil. Pesan berisi password langsung dihapus dari chat, dan password
  tidak disimpan di state percakapan bot.
- **Ganti app password**, **hapus akun** (dengan konfirmasi), dan **jadikan default**.
- Disimpan di `data/gmail_accounts.json` dengan izin file `600` di Linux.
- Akun di `.env` tetap jalan (ditandai `(.env)`), tapi hanya bisa diubah lewat `.env`.
- Kelola akun hanya aktif kalau `ALLOWED_USER_IDS` diisi, supaya orang lain tidak bisa melihat
  atau mengubah akun kamu.
- Pesan error yang dulu menyuruh cek `.env` sekarang menunjuk ke menu yang tepat. Lamaran dari akun yang
  sudah dihapus memberi pesan jelas saat follow-up.
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` di `.env.example` sekarang opsional (dikomentari).
- README: bagian baru **Kelola akun Gmail** dan **Alur foto lowongan**.

Diuji dengan 31 pengecekan otomatis memakai Telegram dan Gmail palsu: tambah, salah format, login gagal,
duplikat, batal, default, ganti password, akun `.env` terlindungi, kirim lamaran dari akun baru, hapus,
follow-up setelah akun dihapus, dan penolakan tanpa `ALLOWED_USER_IDS`.

**Upgrade**: `git pull && ./bot.sh restart`. `.env` lama tetap jalan tanpa diubah.


## 2026-11-09

### Ringkasan

| Fitur | Sebelumnya | Sekarang |
| --- | --- | --- |
| Akun Gmail | Hanya 1 akun | Bisa 2 akun atau lebih, pengirim dipilih saat konfirmasi |
| Cara pakai bot | Harus mengetik perintah | Tombol menu di bawah chat + tombol **Menu** Telegram |
| Pengaturan | Hanya `/gaya` | `/pengaturan`: gaya, akun Gmail default, nyala/matikan cek otomatis |
| Sumber lowongan | Job alert, link, teks | + foto/poster lowongan, + forward dari grup/channel lain |
| Email tujuan | Tidak bisa diganti di konfirmasi | Tombol **✏️ Ganti email tujuan** |
| Menjalankan bot | `python bot.py`, harus tetap terbuka | `./bot.sh install`: service systemd di VPS, jalan di background |
| Log | Hanya di terminal | Juga di `data/bot.log` dan `journalctl` |
| Rekomendasi harian | 1 pesan per lowongan (bisa 8+ pesan) | 1 pesan berisi semua lowongan, tombol per lowongan |
| Pengingat follow-up | 1 pesan per lamaran | 1 pesan berisi semua lamaran yang perlu di-follow-up |
| Notifikasi | Semua berbunyi | Hanya yang penting yang berbunyi |

### 1. Banyak akun Gmail

Tambahkan akun lain di `.env` dengan akhiran `_2`, `_3`, dst. Setiap akun butuh App Password sendiri.

```
GMAIL_ADDRESS=utama@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
GMAIL_ADDRESS_2=kedua@gmail.com
GMAIL_APP_PASSWORD_2=yyyy yyyy yyyy yyyy
```

- **Rekomendasi**: job alert dibaca dari semua akun. Email yang sama di dua akun hanya diproses sekali.
  Kalau satu akun gagal login, akun lain tetap dicek.
- **Kirim lamaran**: layar konfirmasi menampilkan tombol **Kirim dari ...** untuk tiap akun.
  Akun default (⭐) ada di paling atas. Dengan satu akun, tampilannya tetap **Ya, kirim**.
- **Balasan HR & follow-up**: tiap lamaran mencatat akun pengirimnya (field `dari`). Balasan HR dicek
  di inbox akun itu, dan follow-up dikirim dari akun itu supaya masuk ke thread email yang sama.
- Akun pengirim tampil di detail lamaran (`/lamaran`).

File terkait: `gmail_accounts.py` (baru), `mailer.py`, `gmail_replies.py`, `bot.py`.

### 2. Menu dan pengaturan

- Setelah `/start`, muncul tombol menu tetap di bawah kolom chat:
  🎯 Rekomendasi, 📂 Lamaran, 📬 Cek Balasan, 📊 Statistik, 👤 Profil, ⚙️ Pengaturan.
- Tombol **Menu** bawaan Telegram (di samping kolom chat) berisi daftar semua perintah. Daftar ini
  dipasang otomatis setiap bot start.
- **⚙️ Pengaturan** (`/pengaturan` atau `/config`):
  - gaya cover letter (formal, santai, singkat)
  - akun Gmail default (hanya muncul kalau akun lebih dari satu)
  - rekomendasi harian: ON/OFF
  - cek balasan HR otomatis: ON/OFF

  Jam cek, ambang skor, dan daftar akun Gmail tetap diatur di `.env`.

### 3. Foto lowongan dan forward dari grup

- Kirim foto/screenshot poster lowongan (sebagai foto, atau file JPG/PNG/WEBP maks. 5 MB).
  AI menyalin isi gambar, termasuk email HR, lalu alurnya sama seperti lowongan biasa:
  skor, penyesuaian CV, cover letter, kirim.
- Teks di bawah foto (caption) ikut dibaca.
- Lowongan dari grup/channel Telegram lain: tekan lama postingannya, **Forward**, pilih bot kamu.
  Teks maupun foto yang di-forward diproses otomatis. Bot tidak memantau grup sendiri, supaya akun
  Telegram kamu aman dari banned.
- AI bisa salah membaca huruf di gambar. Cek alamat tujuan di layar konfirmasi dan pakai
  **✏️ Ganti email tujuan** kalau keliru.
- Didukung Gemini dan Claude. Untuk Ollama, isi `OLLAMA_VISION_MODEL` (default `qwen2.5vl:7b`)
  lalu jalankan `ollama pull qwen2.5vl:7b`.

File terkait: `ai_helper.py` (`read_job_image`, dukungan gambar di semua provider), `bot.py`.

### 4. Jalan di background (VPS Linux)

`bot.sh` memasang bot sebagai service systemd: jalan di background, start otomatis saat VPS boot,
dan restart sendiri 10 detik setelah crash. Langkah lengkapnya ada di
[README > Jalan di VPS](README.md#jalan-di-vps-linux-background).

```
./bot.sh install     # pasang & jalankan
./bot.sh status      # cek status + log terakhir
./bot.sh log         # log langsung (Ctrl+C keluar)
./bot.sh restart     # setelah ubah .env atau git pull
./bot.sh uninstall   # hentikan & hapus service
```

### 5. Chat lebih rapi

- **Rekomendasi**: semua lowongan dikirim dalam satu pesan bernomor. Tiap lowongan punya satu baris
  tombol: **🔍 Proses** dan **🌐 Buka**.
- **Pengingat follow-up**: semua lamaran yang sudah `FOLLOWUP_DAYS` hari tanpa kabar digabung dalam
  satu pesan, dengan tombol **📨 Follow-up #id** dan **👻 #id tidak ada kabar** per lamaran.
  Menekan tombolnya mengirim pesan baru, jadi daftar aslinya tetap utuh.
- **Pesan "⏳ Mengecek ..."** dari `/rekomendasi` dan `/cekbalasan` diganti hasilnya atau dihapus
  setelah selesai, jadi tidak menumpuk.
- **Notifikasi tanpa suara** untuk yang rutin: rekomendasi harian, pengingat follow-up, dan balasan HR
  berupa penolakan atau konfirmasi otomatis. Yang tetap **berbunyi**: undangan interview,
  tawaran kerja, dan HR meminta info (karena perlu kamu tindak lanjuti).
  Daftarnya diatur di `LOUD_REPLIES` di `bot.py`.

### 6. Refactor struktur kode (tanpa perubahan perilaku)

Mengikuti prinsip *python-design-patterns* (satu tanggung jawab per fungsi, pisahkan I/O dari logika, KISS):

- `handle_button` (250 baris, rantai `if` untuk ~20 tombol) dipecah menjadi satu fungsi `btn_*` per
  tombol, dipetakan lewat dictionary `BUTTONS`. Tombol yang butuh lowongan aktif didaftarkan di
  `NEEDS_JOB`. Menambah tombol baru cukup: buat fungsi `btn_*`, daftarkan di `BUTTONS`.
- `send_recommendations` (95 baris) dipisah menjadi: baca Gmail (`fetch_new_alert_emails`),
  AI (`jobs_from_emails`), logika murni (`dedupe_jobs`, `top_jobs`, `rec_id`), dan format pesan
  (`recommendations_message`).
- `check_replies` (80 baris) dipisah menjadi: `apps_by_account`, simpan data (`record_reply`),
  dan format pesan (`reply_message`).
- Diverifikasi dengan tes golden master: 45 skenario (semua tombol, rekomendasi, pengingat, cek
  balasan) dijalankan dengan Telegram/Gmail/AI palsu. Output sebelum dan sesudah refactor identik.

### 7. Lain-lain

- `.gitignore`: `.env` (password & token) dan `data/` (CV & data lamaran) tidak ikut ter-commit.
- `.gitattributes`: `*.sh` selalu LF supaya bisa jalan di Linux walau di-commit dari Windows.

### Cara upgrade dari versi sebelumnya

1. `git pull`
2. `.env` lama tetap jalan tanpa diubah. Opsional: tambah `GMAIL_ADDRESS_2` / `GMAIL_APP_PASSWORD_2`,
   dan `OLLAMA_VISION_MODEL` kalau pakai Ollama.
3. Data lamaran lama tetap terbaca. Lamaran lama yang belum punya field `dari` dianggap dikirim
   dari akun utama (`GMAIL_ADDRESS`).
4. Restart bot (`./bot.sh restart`, atau `./bot.sh install` kalau belum pernah dipasang), lalu
   kirim `/start` sekali supaya tombol menu muncul.

### Belum beres

- File `job_alerts.py`, `job_fetcher.py`, `cv_parser.py`, `cv_builder.py`, dan `requirements.txt`
  belum ada di repo, jadi bot belum bisa dijalankan dari hasil clone.
- Setelah `job_alerts.py` ditambahkan, `fetch_alert_emails(seen, days, account)` harus menerima
  parameter `account` dan login memakai `account.address` / `account.password`, bukan
  `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` dari environment.
- Belum diuji langsung di Telegram maupun di VPS. Yang sudah dicek baru: semua file Python lolos
  compile, `bot.sh` lolos cek sintaks, dan pembacaan akun Gmail dari `.env`.
