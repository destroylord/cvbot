# CV Bot Telegram

Bot Telegram untuk scan CV, cari rekomendasi lowongan dari email job alert, menyesuaikan CV
per lowongan dengan metode PAR (tetap jujur), membuat email lamaran, lalu kirim lamaran via Gmail
**setelah kamu konfirmasi**.

Daftar perubahan terbaru dan cara upgrade: lihat [CHANGELOG.md](CHANGELOG.md).

## Mulai cepat

1. Isi `.env` (lihat [Setup](#setup)), lalu jalankan bot di VPS dengan `./bot.sh install`
   (lihat [Jalan di VPS](#jalan-di-vps-linux-background)).
2. Di Telegram, buka bot kamu dan kirim `/start`. Tombol menu muncul di bawah kolom chat
   (🎯 Rekomendasi, 📂 Lamaran, 📬 Cek Balasan, 📊 Statistik, 📄 CV Saya, ⚙️ Pengaturan).
3. Kirim file CV (PDF/DOCX).
4. Kirim lowongan: link, teks, foto poster, atau forward postingan dari grup lain.
   Atau tekan **🎯 Rekomendasi** untuk cari dari email job alert.
5. Ikuti tombol yang muncul: sesuaikan CV, buat email lamaran, pilih akun Gmail, kirim.
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
   CV otomatis disesuaikan dengan **metode PAR** dan dikirim sebagai PDF baru, lengkap dengan skor
   sebelum/sesudah dan daftar perubahannya. Untuk skor tinggi, ada tombol **Sesuaikan CV** kalau mau.
   Kalau ada poin penting yang belum punya hasil, bot menanyakannya dulu ke kamu (lihat di bawah).
4. Tekan **Buat Email Lamaran**, revisi pakai `/revisi <instruksi>` kalau perlu.
5. Tekan **Kirim via Gmail**, cek konfirmasi, tekan **Ya, kirim** (atau **Kirim dari ...**
   kalau kamu memakai beberapa akun Gmail).
   CV yang dilampirkan otomatis versi yang sudah disesuaikan (kalau ada).

## Soal penyesuaian CV

Bot hanya boleh menyusun ulang, menulis ulang, dan menonjolkan isi CV asli kamu.
Selain instruksi ke AI, ada pengaman di kode (`guard_profile`) yang otomatis membuang skill,
perusahaan, atau proyek yang tidak ada di CV asli, serta mengembalikan nama, kontak, periode kerja,
dan pendidikan ke data asli. Skill yang belum kamu miliki ditampilkan sebagai saran belajar,
bukan dimasukkan ke CV. Tetap baca CV hasilnya sebelum mengirim.

### Metode PAR (Problem → Action → Result)

Setiap poin pengalaman dan proyek ditulis ulang sebagai satu kalimat: masalah atau kebutuhannya, apa yang
kamu lakukan (kata kerja aktif + istilah dari lowongan), dan **hasilnya**. Contoh:

> Antrean kasir sering menumpuk saat jam ramai; membangun sistem kasir berbasis Laravel sehingga waktu antre
> turun dari 10 menit menjadi 3 menit.

Hasil hanya boleh diambil dari CV asli atau dari jawaban kamu, bot **tidak mengarang angka**. Kalau poin penting
belum punya hasil, bot bertanya dulu, maksimal 5 pertanyaan:

- Ketik jawabannya, misalnya "waktu input data turun dari 2 jam jadi 15 menit".
- **⏭️ Lewati** kalau memang tidak ada hasilnya. Poin itu tetap ditulis sebagai masalah + tindakan, dan
  tidak ditanyakan lagi.
- **✅ Cukup, buat CV** untuk langsung membuat CV dengan jawaban yang sudah ada.

Jawaban kamu disimpan, jadi untuk lowongan berikutnya poin yang sama langsung memakai hasil itu tanpa
ditanya ulang.

### Font CV

Secara default, PDF CV yang disesuaikan memakai **font yang sama dengan CV asli kamu** (dideteksi saat CV
diunggah). Font Microsoft diganti padanannya yang ukurannya identik, karena font Microsoft tidak boleh
disebarkan bebas: Calibri → Carlito, Arial → Arimo, Times New Roman → Tinos, Cambria → Caladea,
Georgia → Gelasio. Font dari template Canva (Poppins, Montserrat, Lato, Open Sans, dll.) dipakai apa adanya.

Ganti lewat **⚙️ Pengaturan > 🔤 Font CV**: pilih dari daftar font yang umum dipakai pelamar, atau ketik nama
font apa pun dari [Google Fonts](https://fonts.google.com). Font diunduh sekali dari Google Fonts lalu disimpan
di `data/fonts/`. Kalau VPS tidak bisa mengunduhnya, PDF memakai Helvetica.

### Email lamaran

Karena CV sudah dilampirkan, isi email **tidak mengulang isi CV**: salam, perkenalan singkat (nama, posisi,
perusahaan, dan sumber lowongan kalau diketahui), satu kalimat latar belakang dengan 2-4 keahlian yang
relevan, CV (dan portofolio kalau ada tautannya) terlampir, penutup siap wawancara, lalu tanda tangan
dengan nama, nomor telepon, dan tautan portofolio. Gayanya bisa dipilih di **⚙️ Pengaturan > ✍️ Gaya email
lamaran**: 🎩 Formal (120-180 kata), 😊 Santai (90-140 kata), atau ⚡ Singkat (50-80 kata).

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

## 📄 CV Saya: ubah CV asli

CV asli (hasil scan CV yang kamu kirim) jadi dasar semua penyesuaian CV dan email lamaran. Supaya tidak perlu
upload ulang setiap ada perubahan, ubah langsung dari tombol **📄 CV Saya** (atau `/cv`):

- **👤 Data diri**: nama, email, telepon, lokasi, dan 🔗 tautan (portofolio, LinkedIn, GitHub).
- **📝 Ringkasan**.
- **💼 Pengalaman** dan **🧩 Proyek**: tambah (pengalaman baru ditaruh paling atas), ubah posisi/perusahaan/
  periode, hapus (dengan konfirmasi), dan naikkan urutan. Pilih nomor poin untuk mengubah, menghapus, atau
  mengurutkannya, atau **➕ Tambah poin** (satu per baris, sebaiknya dengan pola PAR).
- **🛠️ Skill**, **🎓 Pendidikan**, **📜 Sertifikasi**, **🌐 Bahasa**: tambah, ubah, hapus, dan urutkan. Skill bisa
  ditambah beberapa sekaligus, dipisah koma.
- **✨ Perbaiki poin dengan PAR**: AI menulis ulang *semua* poin pengalaman dan proyek dengan metode PAR (lihat
  di bawah).
- **📥 Unduh CV (PDF)**: CV asli versi terbaru.

Saat mengisi, ketik `-` untuk mengosongkan bagian yang opsional, atau `batal` untuk membatalkan.

Setelah ada yang diubah, **lampiran "CV asli" saat melamar dibuat ulang dari data terbaru** (dengan font CV
kamu), jadi yang terkirim bukan lagi file lama. Kalau kamu upload file CV baru, data CV asli diganti dengan
hasil scan file itu.

### ✨ Perbaiki poin dengan PAR

Merapikan seluruh poin CV asli sekaligus, untuk CV umum, tidak terikat lowongan tertentu:

1. AI menulis ulang setiap poin dengan pola Problem → Action → Result. Jumlah dan urutan poin dipertahankan,
   dan posisi, perusahaan, serta periode tidak disentuh.
2. Kalau ada poin penting yang belum ada hasilnya, bot **bertanya dulu** (sama seperti saat penyesuaian CV).
   Jawabanmu juga dipakai untuk penyesuaian CV berikutnya.
3. Bot menampilkan **pratinjau sebelum → sesudah**. CV asli baru berubah setelah kamu menekan
   **✅ Simpan ke CV asli**.

Pengaman angka: kalau AI menulis angka yang tidak ada di poin aslinya maupun di jawabanmu (misalnya "bug turun
40%" padahal tidak pernah disebut), poin itu dikembalikan ke versi asli, dan pratinjau memberi tahu berapa poin
yang dikembalikan. Kalau CV asli sempat diubah lewat menu lain sebelum kamu menekan Simpan, hasil PAR tidak
disimpan supaya perubahanmu tidak tertimpa.

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

### Kalau Gemini gagal

Pesan error menyebut alasan untuk **setiap model yang dicoba**:

- **tidak termasuk kuota gratis (limit 0)**: model ini hanya untuk akun berbayar di API key kamu. Model baru
  sering seperti ini. Pilih model lain lewat **🧠 Pilih model**.
- **model tidak ditemukan**: nama model salah ketik atau tidak tersedia untuk API key kamu. Nama model harus
  persis, misalnya `gemini-3.8-flash`, bukan `gemini 3.8`.
- **kuota / batas pemakaian habis**: tunggu sebentar.
- **server sedang sibuk**: coba lagi beberapa menit kemudian.
- **User location is not supported**: Gemini tidak melayani negara lokasi VPS kamu. Pakai provider lain.

Model default: `gemini-3.6-flash`, cadangan `gemini-3.5-flash-lite` lalu `gemini-2.5-flash`. Yang pasti bisa
dipakai adalah model yang muncul di **🧠 Pilih model**, karena daftarnya diambil langsung dari Google untuk
API key kamu.

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
   Kalau skornya tinggi, kamu pilih **Buat Email Lamaran**, **Sesuaikan CV**, atau **Lewati**.
5. **Buat Email Lamaran**, revisi dengan `/revisi` kalau perlu, lalu **📧 Kirim via Gmail**.
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
- `/pengaturan` (atau `/config`) - model AI, gaya email lamaran, font CV, akun Gmail, nyala/matikan cek otomatis
- `/gaya` - atur gaya email lamaran (formal, santai, singkat)
- `/cv` (atau `/profil`) - lihat & ubah CV asli kamu (📄 CV Saya)
- `/revisi <instruksi>` - revisi email lamaran atau email follow-up terakhir

Isi **⚙️ Pengaturan**:

- **✍️ Gaya email lamaran**: formal, santai, atau singkat
- **🔤 Font CV**: sama seperti CV asli (default), pilih dari daftar, atau font Google Fonts lain
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
