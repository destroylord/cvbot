# CV Bot Telegram

Bot Telegram untuk scan CV, cari rekomendasi lowongan dari email job alert, menyesuaikan CV
per lowongan (tetap jujur), membuat cover letter, lalu kirim lamaran via Gmail
**setelah kamu konfirmasi**.

Daftar perubahan terbaru dan cara upgrade: lihat [CHANGELOG.md](CHANGELOG.md).

## Mulai cepat

1. Isi `.env` (lihat [Setup](#setup)), lalu jalankan bot di VPS dengan `./bot.sh install`
   (lihat [Jalan di VPS](#jalan-di-vps-linux-background)).
2. Di Telegram, buka bot kamu dan kirim `/start`. Tombol menu muncul di bawah kolom chat.
3. Kirim file CV (PDF/DOCX).
4. Kirim lowongan: link, teks, foto poster, atau forward postingan dari grup lain.
   Atau tekan **🎯 Rekomendasi** untuk cari dari email job alert.
5. Ikuti tombol yang muncul: sesuaikan CV, buat cover letter, pilih akun Gmail, kirim.
6. Pantau lewat **📂 Lamaran**, atur preferensi di **⚙️ Pengaturan**.

## Alur

1. Kirim CV (PDF/DOCX) ke bot. CV dibaca dan diringkas jadi profil.
2. Dapatkan lowongan dengan salah satu cara:
   - ketik `/rekomendasi` (atau tunggu cek otomatis harian), bot membaca email job alert
     dari JobStreet/Glints/LinkedIn/Kalibrr di Gmail kamu, lalu tekan **Proses** di lowongan yang menarik, atau
   - kirim link lowongan / paste teks deskripsinya, atau
   - kirim **foto/screenshot poster lowongan**. Bot membaca isinya (termasuk email HR) pakai AI.
     Lowongan dari grup/channel lain cukup di-**forward** ke bot.
3. Bot memberi skor kecocokan. Kalau skor di bawah `CV_TAILOR_THRESHOLD` (default 70),
   CV otomatis disesuaikan dan dikirim sebagai PDF baru, lengkap dengan skor sebelum/sesudah
   dan daftar perubahannya. Untuk skor tinggi, ada tombol **Sesuaikan CV** kalau mau.
4. Tekan **Buat Cover Letter**, revisi pakai `/revisi <instruksi>` kalau perlu.
5. Tekan **Kirim via Gmail**, cek konfirmasi, tekan **Ya, kirim** (atau **Kirim dari ...**
   kalau kamu memakai beberapa akun Gmail).
   CV yang dilampirkan otomatis versi yang sudah disesuaikan (kalau ada).

## Soal penyesuaian CV

Bot hanya boleh menyusun ulang, menulis ulang, dan menonjolkan isi CV asli kamu.
Selain instruksi ke AI, ada pengaman di kode (`guard_profile`) yang otomatis membuang skill,
perusahaan, atau proyek yang tidak ada di CV asli, serta mengembalikan nama, kontak, periode kerja,
dan pendidikan ke data asli. Skill yang belum kamu miliki ditampilkan sebagai saran belajar,
bukan dimasukkan ke CV. Tetap baca CV hasilnya sebelum mengirim.

## Setup

1. **Python 3.10+**, lalu install dependensi:
   ```
   pip install -r requirements.txt
   ```
2. **Token bot**: chat @BotFather di Telegram, ketik `/newbot`, salin token-nya.
3. **User ID kamu**: chat @userinfobot, salin angka ID-nya.
4. **AI**: tidak perlu diisi di `.env`. Setelah bot jalan, buka **⚙️ Pengaturan > 🤖 Model AI**
   (lihat [Pengaturan Model AI](#pengaturan-model-ai)). Pilihan provider:
   - **Gemini** (disarankan, ada kuota gratis): API key dari https://aistudio.google.com/apikey.
   - **OpenAI-compatible**: layanan apa pun yang memakai format API OpenAI, misalnya OpenRouter (banyak model
     gratis), Groq, DeepSeek, Together AI, OpenAI, atau server lokal seperti LM Studio. Bebas memilih model.
   - **Claude** (berbayar): login akun Claude Console lewat `ant auth login`, atau API key. Install juga
     library-nya di VPS: `.venv/bin/pip install -U anthropic`. Kuota langganan Claude Pro/Max tidak bisa
     dipakai untuk bot ini.
   - **Ollama** (gratis, lokal): install dari https://ollama.com lalu `ollama pull qwen2.5:7b`. Butuh RAM
     sekitar 16 GB, dan hasilnya biasanya kurang bagus dibanding Gemini/Claude.
5. **Gmail App Password** (dipakai untuk kirim lamaran dan membaca job alert):
   - Aktifkan verifikasi 2 langkah di akun Google kamu.
   - Buka https://myaccount.google.com/apppasswords, buat app password baru, salin 16 karakternya.
   - Kalau login IMAP gagal, cek pengaturan Gmail > Lihat semua setelan > Penerusan dan POP/IMAP.
   - Akun Gmail **tidak perlu** diisi di `.env`. Setelah bot jalan, buka **⚙️ Pengaturan > 📧 Akun Gmail**
     (lihat [Kelola akun Gmail](#kelola-akun-gmail)).
6. **Aktifkan job alert** di JobStreet, Glints, LinkedIn, dll. dengan email Gmail yang sama.
   Atur kata kunci dan lokasi sesuai posisi yang kamu incar.
7. Salin `.env.example` menjadi `.env`, lalu isi semua nilainya.
8. Jalankan `python bot.py` untuk mencoba. Bot harus tetap berjalan supaya cek harian bisa jalan,
   jadi untuk pemakaian sehari-hari jalankan di VPS (lihat bagian berikut).

## Jalan di VPS (Linux, background)

Di VPS (contoh Ubuntu/Debian), sekali saja:

```
sudo apt install -y python3 python3-venv git
git clone https://github.com/destroylord/cvbot.git
cd cvbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # cukup isi TELEGRAM_BOT_TOKEN dan ALLOWED_USER_IDS
.venv/bin/python bot.py                # tes dulu di depan; kalau sudah jalan, Ctrl+C
chmod +x bot.sh
./bot.sh install
```

`./bot.sh install` memasang bot sebagai service systemd: jalan di background, otomatis start
setiap VPS reboot, dan restart sendiri kalau crash. Terminal SSH boleh ditutup. Perintah lain:

- `./bot.sh status` - cek bot jalan atau tidak + 10 baris log terakhir
- `./bot.sh log` - lihat log langsung (Ctrl+C untuk keluar); log juga tersimpan di `data/bot.log`
- `./bot.sh restart` - wajib setelah mengubah `.env` atau kode
- `./bot.sh stop` / `./bot.sh start`
- `./bot.sh uninstall` - hentikan dan hapus service

Update ke versi terbaru: `git pull && .venv/bin/pip install -r requirements.txt && ./bot.sh restart`.

Jangan jalankan `python bot.py` manual saat service aktif, dan jangan jalankan bot yang sama
di laptop sekaligus. Dua bot dengan token yang sama akan saling bentrok.

## Pengaturan Model AI

Buka **⚙️ Pengaturan > 🤖 Model AI**:

- **🔁 Ganti provider**: Gemini, OpenAI-compatible, Claude, atau Ollama. Kalau API key provider itu belum
  ada, bot langsung memintanya.
- **🌐 Ganti alamat API** (OpenAI-compatible): pilih OpenRouter, Groq, DeepSeek, Together AI, OpenAI, Ollama,
  atau LM Studio, atau ketik alamat sendiri. Server lokal bisa dipakai tanpa API key.
- **🧠 Pilih model**: daftar model diambil langsung dari provider, jadi yang muncul pasti tersedia untuk
  API key kamu. Untuk OpenRouter, model gratis (`:free`) ditaruh paling atas. Bisa juga **✏️ Ketik nama
  model** secara manual, untuk model apa pun.
- **🛟 Model cadangan** (Gemini, OpenAI-compatible): dipakai otomatis kalau model utama sedang sibuk (503)
  atau kena batas pemakaian (429). Bisa lebih dari satu (ketik, pisahkan koma). Pilih **🚫 Tanpa cadangan**
  untuk mematikannya.
- **🖼️ Model untuk foto** (Ollama): model vision untuk membaca poster lowongan.
- **🔑 Ganti API key**: key dicek dulu ke provider sebelum disimpan. Pesan berisi key langsung dihapus
  dari chat, dan yang ditampilkan hanya 4 karakter terakhirnya.
- **🧪 Tes AI**: kirim pertanyaan singkat ke AI untuk memastikan pengaturannya jalan, lengkap dengan waktu
  responsnya.

### Claude: login atau API key

Pilih **🔐 Login akun Claude Console** supaya tidak perlu API key `sk-...`. Caranya, sekali saja di VPS,
sebagai user yang menjalankan bot:

1. Install CLI `ant` dari https://github.com/anthropics/anthropic-cli/releases (file `linux_amd64` atau
   `linux_arm64`), lalu taruh `ant` di `/usr/local/bin`.
2. Jalankan `ant auth login --no-browser`, buka link yang muncul, login, lalu tempel kodenya ke terminal.
3. Kalau ada baris `ANTHROPIC_API_KEY` di `.env`, hapus lalu `./bot.sh restart`. Key di environment selalu
   menimpa login, bahkan kalau isinya kosong (baris kosong dibuang otomatis oleh bot).
4. Di bot, tekan **🔄 Cek login**.

Ini login ke akun **Claude Console** (pemakaian API, bayar sesuai pemakaian), bukan kuota langganan Claude
Pro/Max. Login kedaluwarsa berkala. Kalau bot bilang login kedaluwarsa, ulangi langkah 2.

Disimpan di `data/settings.json` (hanya bisa dibaca pemilik file) dan langsung berlaku tanpa restart.
Pengaturan dari bot mengalahkan nilai di `.env`. Menu ini hanya aktif kalau `ALLOWED_USER_IDS` diisi.

## Kelola akun Gmail

Buka **⚙️ Pengaturan > 📧 Akun Gmail**:

- **➕ Tambah akun**: ketik alamat Gmail, lalu app password-nya. Bot langsung mencoba login
  (baca dan kirim email). Akun baru disimpan hanya kalau login berhasil. Pesan berisi password
  langsung dihapus dari chat.
- Pilih salah satu akun untuk **⭐ Jadikan default**, **🔑 Ganti app password**, atau **🗑️ Hapus akun**.
- Ketik `batal`, atau tekan tombol menu mana pun, untuk membatalkan isian.

Job alert dibaca dari semua akun. Saat konfirmasi kirim, kamu memilih akun pengirimnya (akun default
ada di paling atas). Balasan HR dicek dan follow-up dikirim dari akun yang sama dengan lamarannya.

Akun yang ditambahkan lewat bot disimpan di `data/gmail_accounts.json` (hanya bisa dibaca pemilik file).
Akun yang diisi di `.env` tetap jalan dan ditandai `(.env)`, tapi hanya bisa diubah atau dihapus
lewat `.env`. Menu ini hanya aktif kalau `ALLOWED_USER_IDS` sudah diisi.

## Alur foto lowongan

Saat kamu mengirim atau mem-forward foto poster lowongan:

1. Bot mengecek CV kamu sudah ada, lalu mengunduh foto (resolusi terbesar).
2. **AI membaca gambar** dan menyalin semua teksnya: posisi, kualifikasi, cara melamar, dan email HR.
   Caption foto ikut dibaca. Kalau gambarnya bukan lowongan atau tidak terbaca, bot memberi tahu
   dan berhenti di sini.
3. **AI mencocokkan** lowongan dengan CV kamu: skor, skill yang cocok dan yang kurang, dan email HR.
4. Kalau skor di bawah `CV_TAILOR_THRESHOLD`, CV otomatis disesuaikan dan PDF baru dikirim untuk kamu cek.
   Kalau skornya tinggi, kamu pilih **Buat Cover Letter**, **Sesuaikan CV**, atau **Lewati**.
5. **Buat Cover Letter**, revisi dengan `/revisi` kalau perlu, lalu **📧 Kirim via Gmail**.
6. **Layar konfirmasi**: tujuan (email HR dari gambar), subjek, lampiran CV, lalu kamu pilih akun
   pengirimnya. Kalau email di gambar tidak terbaca, bot menanyakannya. Kalau terbaca tapi salah,
   tekan **✏️ Ganti email tujuan**.
7. Setelah terkirim, lamaran tercatat di **📂 Lamaran** dan balasan HR dicek otomatis.

Tidak ada email yang terkirim tanpa kamu menekan tombol kirim di langkah 6.

Satu foto diproses sebagai satu lowongan. Kalau poster terdiri dari beberapa foto (album), tiap foto
diproses terpisah. Untuk poster seperti itu, lebih baik kirim satu foto yang paling lengkap, atau
paste teksnya.

## Perintah

Tombol menu muncul di bawah kolom chat setelah `/start`, dan daftar perintah ada di tombol **Menu**
di samping kolom chat, jadi tidak perlu hafal perintahnya.

- `/rekomendasi` - cek email job alert sekarang
- `/lamaran` - daftar lamaran, ubah status, buat follow-up
- `/statistik` - statistik lamaran (tingkat respons, interview, per minggu)
- `/cekbalasan` - cek balasan HR di Gmail sekarang
- `/pengaturan` (atau `/config`) - model AI, gaya cover letter, akun Gmail, nyala/matikan cek otomatis
- `/gaya` - atur gaya cover letter (formal, santai, singkat)
- `/profil` - lihat hasil scan CV
- `/revisi <instruksi>` - revisi cover letter atau email follow-up terakhir

Isi **⚙️ Pengaturan**:

- **Gaya cover letter**: formal, santai, atau singkat
- **📧 Akun Gmail**: tambah, ganti app password, hapus, dan pilih akun default (⭐)
- **Rekomendasi harian** dan **Cek balasan otomatis**: ON/OFF

## Tracker lamaran & balasan HR

- Setiap lamaran yang terkirim lewat bot otomatis tercatat dengan status 📤 Terkirim.
- Bot mengecek inbox Gmail tiap `REPLY_CHECK_MINUTES` menit untuk email dari alamat HR
  (dan dari domain perusahaannya, kecuali domain umum seperti gmail.com). Balasan diringkas
  dan dikategorikan: undangan interview, penolakan, tawaran kerja, permintaan info, atau
  konfirmasi otomatis. Status lamaran naik otomatis (tidak pernah turun), dan bisa kamu ubah
  manual lewat `/lamaran`.
- Kalau `FOLLOWUP_DAYS` hari belum ada kabar, bot mengingatkan sekali dan bisa membuatkan
  draft email follow-up. Follow-up dikirim sebagai balasan di thread email lamaran yang sama,
  dan tetap harus kamu konfirmasi dulu.
- Lamaran yang kamu kirim di luar bot tidak tercatat.

## Notifikasi

- Rekomendasi harian dan pengingat follow-up masing-masing dikirim dalam **satu pesan**, tanpa suara.
- Balasan HR dikirim satu pesan per balasan. Yang **berbunyi** hanya undangan interview, tawaran kerja,
  dan permintaan info dari HR. Penolakan dan konfirmasi otomatis masuk tanpa suara.
- Pesan "⏳ ..." otomatis diganti hasilnya atau dihapus setelah selesai.

## Catatan

- Isi `ALLOWED_USER_IDS`, kalau tidak siapa pun yang menemukan bot kamu bisa memakai Gmail kamu.
- Bot hanya membaca email job alert (mode read-only, email tidak ditandai sudah dibaca).
- Halaman lowongan JobStreet/LinkedIn sering diblokir untuk bot. Kalau tombol **Proses** gagal,
  buka link-nya lalu copy-paste deskripsi lowongan ke bot.
- Hasil baca foto lowongan bisa salah, terutama alamat email. Cek alamat tujuan di layar konfirmasi
  dan pakai **Ganti email tujuan** kalau keliru. Untuk Ollama, foto butuh model vision (`OLLAMA_VISION_MODEL`).
- PDF CV hasil scan (gambar) tidak bisa dibaca. Pakai PDF yang di-export dari Word/Canva/Google Docs.
- Semua data (CV, profil, riwayat) tersimpan di folder `data/`.
