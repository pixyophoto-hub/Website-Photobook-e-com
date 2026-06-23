# Panduan Deploy ke Render — PixyoPrint

Semua fail dah disediakan (`render.yaml`, `requirements.txt`, kod sokong persistent disk).
Ikut langkah ini sekali sahaja. Selepas ni, setiap kali Claude push ke GitHub, laman update automatik.

---

## Langkah 1 — Daftar Render
1. Pergi ke **https://render.com** → **Get Started** / Sign Up.
2. Pilih **Sign up with GitHub** (paling senang — terus sambung repo).
3. Benarkan Render akses akaun GitHub anda.

## Langkah 2 — Deploy guna Blueprint
1. Di dashboard Render, klik **New +** → **Blueprint**.
2. Pilih repo **Website-Photobook-e-com**.
3. Render akan baca fail `render.yaml` automatik dan tunjuk servis **pixyoprint**.
4. Klik **Apply** / **Create**.

## Langkah 3 — Isi rahsia (env vars)
Render akan minta nilai untuk yang ditanda rahsia. Isi:

| Kunci | Nilai |
|---|---|
| **ADMIN_PASSWORD** | (kata laluan admin baru anda — JANGAN guna admin123) |
| **HITPAY_API_KEY** | (kunci API HitPay anda — boleh isi kemudian via panel admin) |
| **HITPAY_SALT** | (salt HitPay anda — boleh isi kemudian) |

> ADMIN_EMAIL dah ditetapkan `admin@pixyoprint.com`. Boleh tukar kalau nak.
> HITPAY boleh dikosongkan dulu dan diisi kemudian dalam Tetapan → Pembayaran.

## Langkah 4 — Tunggu deploy
- Render akan build & start (~2-3 minit).
- Bila siap, anda dapat URL: **https://pixyoprint.onrender.com** (atau nama yang Render bagi).

## Langkah 5 — Setup awal di laman live
Buka URL → laman dah hidup dengan pakej photobook lengkap. Kemudian:
1. **Log Masuk Admin** (guna ADMIN_PASSWORD yang anda set).
2. **Tetapan → Produk** → upload semula gambar produk (gambar tak ikut deploy).
3. **Tetapan → Umum** → isi pautan WhatsApp & Email "Hubungi Kami".
4. **Tetapan → Pembayaran** → isi link cara hantar gambar + HitPay (jika belum).
5. Semak harga, postage, FAQ — laraskan jika perlu.

---

## Selepas deploy — macam mana nak update?
Tak perlu buat apa-apa di Render. Cukup:
```
Minta Claude ubah → Claude push ke GitHub → Render auto-update (~1-2 min)
```
Data anda (order, gambar, tetapan) **kekal selamat** di persistent disk walau berapa kali update.

## Nota penting
- **Data tak hilang** — `data.json` & gambar disimpan di persistent disk (/var/data).
- **Free tier** — kalau guna pelan 'free', laman tidur bila idle (loading ~30s). Untuk perniagaan sebenar, kekal 'starter' (~RM33/bulan).
- **Domain sendiri** — boleh tambah domain (cth pixyoprint.com) nanti di Render → Settings → Custom Domain.
- **Backup** — sekali-sekala muat turun CSV order (admin → Senarai Order → Muat Turun CSV).
