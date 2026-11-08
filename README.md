# CV Bot Telegram

Bot Telegram untuk scan CV, cari rekomendasi lowongan dari email job alert, menyesuaikan CV
per lowongan (tetap jujur), membuat cover letter, lalu kirim lamaran via Gmail
**setelah kamu konfirmasi**.

## Alur

1. Kirim CV (PDF/DOCX) ke bot. CV dibaca dan diringkas jadi profil.
2. Dapatkan lowongan dengan salah satu cara:
   - ketik `/rekomendasi` (atau tunggu cek otomatis harian), bot membaca email job alert
     dari JobStreet/Glints/LinkedIn/Kalibrr di Gmail kamu, lalu tekan **Proses** di lowongan yang menarik, atau
   - kirim link lowongan / paste teks deskripsinya.
3. Bot memberi skor kecocokan. Kalau skor di bawah `CV_TAILOR_THRESHOLD` (default 70),
   CV otomatis disesuaikan dan dikirim sebagai PDF baru, lengkap dengan skor sebelum/sesudah
   dan daftar perubahannya. Untuk skor tinggi, ada tombol **Sesuaikan CV** kalau mau.
4. Tekan **Buat Cover Letter**, revisi pakai `/revisi <instruksi>` kalau perlu.
5. Tekan **Kirim via Gmail**, cek konfirmasi, tekan **Ya, kirim**.
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
4. **Pilih AI** (isi `AI_PROVIDER` di `.env`):
   - `gemini` (disarankan, ada kuota gratis): login dengan akun Google di
     https://aistudio.google.com/apikey, klik buat API key, salin ke `GEMINI_API_KEY`.
     Kalau model default sudah tidak tersedia, ganti `GEMINI_MODEL` dengan nama model
     yang tertera di AI Studio.
   - `ollama` (gratis, offline): install dari https://ollama.com, lalu jalankan
     `ollama pull qwen2.5:7b`. Butuh RAM sekitar 16 GB agar lancar, dan hasilnya
     biasanya kurang bagus dibanding Gemini/Claude.
   - `claude` (berbayar): key dari https://console.anthropic.com, isi `ANTHROPIC_API_KEY`.
     Langganan Claude.ai tidak bisa dipakai untuk bot ini.
5. **Gmail App Password** (dipakai untuk kirim lamaran dan membaca job alert):
   - Aktifkan verifikasi 2 langkah di akun Google kamu.
   - Buka https://myaccount.google.com/apppasswords, buat app password baru, salin 16 karakternya.
   - Kalau login IMAP gagal, cek pengaturan Gmail > Lihat semua setelan > Penerusan dan POP/IMAP.
6. **Aktifkan job alert** di JobStreet, Glints, LinkedIn, dll. dengan email Gmail yang sama.
   Atur kata kunci dan lokasi sesuai posisi yang kamu incar.
7. Salin `.env.example` menjadi `.env`, lalu isi semua nilainya.
8. Jalankan:
   ```
   python bot.py
   ```
   Bot harus tetap berjalan supaya cek harian bisa jalan.

## Perintah

- `/rekomendasi` - cek email job alert sekarang
- `/lamaran` - daftar lamaran, ubah status, buat follow-up
- `/statistik` - statistik lamaran (tingkat respons, interview, per minggu)
- `/cekbalasan` - cek balasan HR di Gmail sekarang
- `/gaya` - atur gaya cover letter (formal, santai, singkat)
- `/profil` - lihat hasil scan CV
- `/revisi <instruksi>` - revisi cover letter atau email follow-up terakhir

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

## Catatan

- Isi `ALLOWED_USER_IDS`, kalau tidak siapa pun yang menemukan bot kamu bisa memakai Gmail kamu.
- Bot hanya membaca email job alert (mode read-only, email tidak ditandai sudah dibaca).
- Halaman lowongan JobStreet/LinkedIn sering diblokir untuk bot. Kalau tombol **Proses** gagal,
  buka link-nya lalu copy-paste deskripsi lowongan ke bot.
- PDF hasil scan (gambar) tidak bisa dibaca. Pakai PDF yang di-export dari Word/Canva/Google Docs.
- Semua data (CV, profil, riwayat) tersimpan di folder `data/`.
