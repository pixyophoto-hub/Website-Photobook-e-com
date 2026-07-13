#!/usr/bin/env python3
"""Backend ringkas untuk PixyoPrint — sajikan fail statik + API JSON dengan login.

Jalankan:  python server.py
Data disimpan dalam data.json (dicipta automatik kali pertama).
Login admin lalai:  admin@pixyoprint.com  /  admin123
"""
import json
import os
import secrets
import datetime
def now_myt():
    """Waktu Malaysia (UTC+8) sebagai datetime naive (wall-clock)."""
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).replace(tzinfo=None)

def is_east_poskod(poskod):
    """Sabah/Sarawak/Labuan: 87-91, 93-98 (ikut 2 digit pertama)."""
    try:
        p = int(str(poskod or "")[:2])
    except (TypeError, ValueError):
        return False
    return (87 <= p <= 91) or (93 <= p <= 98)

def calc_postage(data, total_weight, poskod):
    """Kira postage ikut tetapan admin — mesti sepadan dgn calcPostage() di client."""
    import math
    root = data.get("postage") or {}
    cfg = root.get("east") if (is_east_poskod(poskod) and root.get("east")) else root
    base = float(cfg.get("base", 0) or 0)
    if not base:
        return 0.0
    threshold = float(cfg.get("threshold", 1.5) or 0)
    per_kg = float(cfg.get("per_kg", 0) or 0)
    if total_weight <= 0 or total_weight <= threshold:
        return round(base, 2)
    extra = math.ceil((total_weight - threshold) * 10) / 10
    return round(base + extra * per_kg, 2)
import hashlib
import hmac as hmac_lib
import time
import base64
import urllib.request
import urllib.parse
import smtplib
import threading
from email.message import EmailMessage
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HITPAY_CURRENCY = "MYR"

# ============================================================
# SECURITY: Password hashing (PBKDF2-SHA256)
# Mencegah: kata laluan bocor jika data.json terdedah
# ============================================================
# ID order pendek (6 aksara) — elak huruf keliru (O/0, I/1/L)
_REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
def gen_order_ref(existing=()):
    seen = set(existing)
    for _ in range(30):
        ref = "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))
        if ref not in seen:
            return ref
    return "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))

# ============================================================
# Notifikasi email order baru (SMTP Gmail)
# ============================================================
def _smtp_cfg():
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587") or 587),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASS", "").strip(),
        "to":   (os.environ.get("NOTIFY_TO", "") or os.environ.get("SMTP_USER", "")).strip(),
    }

def _send_order_email(order):
    cfg = _smtp_cfg()
    if not (cfg["user"] and cfg["password"] and cfg["to"]):
        return  # SMTP belum dikonfigurasi — skip senyap
    try:
        items = order.get("items", []) or []
        item_lines = "\n".join(
            f"  - {it.get('name','')} x{it.get('qty',1)} = RM{it.get('price',0)}" for it in items
        ) or "  -"
        addr = " ".join(str(order.get(k, "")).strip() for k in ("alamat", "poskod", "bandar", "negeri")).strip()
        body = (
            "Order baru DIBAYAR! 🎉\n\n"
            f"No. Order : {order.get('reference','')}\n"
            f"Nama      : {order.get('name','')}\n"
            f"Email     : {order.get('email','')}\n"
            f"Telefon   : {order.get('phone','')}\n"
            f"Alamat    : {addr or '-'}\n"
            f"Hantar gambar : {order.get('medium','-')}\n\n"
            f"Item:\n{item_lines}\n\n"
            f"JUMLAH    : RM{order.get('total','')}\n"
            f"Masa      : {order.get('created_at','')}\n\n"
            "— Semak di panel admin PixyoPrint."
        )
        msg = EmailMessage()
        msg["Subject"] = f"[PixyoPrint] Order baru #{order.get('reference','')} — RM{order.get('total','')}"
        msg["From"] = cfg["user"]
        msg["To"] = cfg["to"]
        msg.set_content(body)
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        print(f"[notify] email order {order.get('reference','')} dihantar ke {cfg['to']}")
    except Exception as e:
        print(f"[notify] gagal hantar email: {e}")

def notify_new_order(order):
    # Hantar di thread supaya tak lambatkan respons callback CHIP
    try:
        snapshot = json.loads(json.dumps(order))
    except Exception:
        snapshot = dict(order)
    threading.Thread(target=_send_order_email, args=(snapshot,), daemon=True).start()

# ── Auto peringatan hantar gambar (email ke pelanggan) ───────
def _photo_reminder_cfg():
    c = load_data().get("photo_reminder", {}) or {}
    return {
        "enabled":        bool(c.get("enabled", False)),
        "delay_hours":    float(c.get("delay_hours", 24) or 24),
        "interval_hours": float(c.get("interval_hours", 48) or 48),
        "max":            int(c.get("max", 2) or 2),
    }

def _parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None

def _send_photo_reminder_email(order):
    cfg = _smtp_cfg()
    to = str(order.get("email", "")).strip()
    if not (cfg["user"] and cfg["password"] and to):
        return False
    try:
        ref = order.get("reference", "")
        name = order.get("name", "") or "pelanggan"
        medium = order.get("medium", "")
        body = (
            f"Salam {name},\n\n"
            f"Terima kasih kerana membuat tempahan dengan PixyoPrint (Order #{ref}).\n\n"
            "Kami masih menunggu GAMBAR anda untuk mula proses susun atur & cetakan.\n"
            f"Sila hantar gambar melalui: {medium or 'WhatsApp / Google Drive'}\n\n"
            "Jika anda sudah menghantar gambar, abaikan email ini.\n\n"
            "Sebarang pertanyaan, WhatsApp kami di 013-318 2285.\n\n"
            "Terima kasih,\nPixyoPrint"
        )
        msg = EmailMessage()
        msg["Subject"] = f"[PixyoPrint] Peringatan hantar gambar — Order #{ref}"
        msg["From"] = cfg["user"]
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        print(f"[reminder] peringatan gambar order {ref} -> {to}")
        return True
    except Exception as e:
        print(f"[reminder] gagal hantar: {e}")
        return False

def photo_reminder_tick():
    """Satu pusingan: hantar peringatan kepada order yang belum hantar gambar."""
    cfg = _photo_reminder_cfg()
    if not cfg["enabled"]:
        return
    smtp = _smtp_cfg()
    if not (smtp["user"] and smtp["password"]):
        return
    d = load_data()
    now = now_myt()
    changed = False
    for o in d.get("orders", []):
        # Skip order manual (Telegram/Wedding) — tak perlu peringatan gambar auto
        if o.get("manual"):
            continue
        # Hanya order dah bayar & masih menunggu gambar
        if o.get("status") not in ("completed", "Hantar Gambar"):
            continue
        if not str(o.get("email", "")).strip():
            continue
        created = _parse_ts(o.get("created_ts"))
        if not created:
            continue
        pr = o.get("photo_reminder") or {"count": 0, "last_ts": ""}
        count = int(pr.get("count", 0) or 0)
        if count >= cfg["max"]:
            continue
        if count == 0:
            due = created + datetime.timedelta(hours=cfg["delay_hours"])
        else:
            last = _parse_ts(pr.get("last_ts")) or created
            due = last + datetime.timedelta(hours=cfg["interval_hours"])
        if now < due:
            continue
        if _send_photo_reminder_email(o):
            o["photo_reminder"] = {"count": count + 1,
                                   "last_ts": now.isoformat(timespec="seconds")}
            changed = True
    if changed:
        save_data(d)

def photo_reminder_loop():
    while True:
        try:
            photo_reminder_tick()
        except Exception as e:
            print(f"[reminder] loop error: {e}")
        time.sleep(3600)  # semak setiap jam

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 260000)
    return f"pbkdf2:sha256:260000:{salt}:{key.hex()}"

def verify_password(stored: str, provided: str) -> bool:
    if stored.startswith('pbkdf2:'):
        try:
            _, algo, iters, salt, stored_key = stored.split(':', 4)
            key = hashlib.pbkdf2_hmac(algo, provided.encode('utf-8'), salt.encode('utf-8'), int(iters))
            return hmac_lib.compare_digest(key.hex(), stored_key)
        except Exception:
            return False
    # Legacy plaintext — akan auto-migrate selepas login berjaya
    return hmac_lib.compare_digest(stored, provided)

# ============================================================
# SECURITY: Rate limiting login
# Mencegah: brute-force / credential stuffing
# ============================================================
_LOGIN_ATTEMPTS: dict = {}  # ip -> [count, timestamp]
_MAX_ATTEMPTS = 5
_LOCKOUT_SECS = 300  # 5 minit

def get_hitpay_cfg():
    """Baca config HitPay dari data.json (live, boleh tukar tanpa restart)."""
    cfg = load_data().get("hitpay", {})
    sandbox = cfg.get("sandbox", True)
    return {
        "api_key": cfg.get("api_key", ""),
        "salt":    cfg.get("salt", ""),
        "sandbox": sandbox,
        "base":    "https://api.sandbox.hit-pay.com" if sandbox else "https://api.hit-pay.com",
    }

BASE = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR = lokasi storan kekal (persistent disk di Render). Default = folder app (lokal).
DATA_DIR = os.environ.get("DATA_DIR", BASE)
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "data.json")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
ALLOWED_EXT = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv",
               ".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB
PORT = int(os.environ.get("PORT", 3000))

PKG_NOTE_DEFAULT = "disusun manual, 2x semakan percuma, siap dalam 7 hari bekerja"
PKG_FIELD_DEFAULTS = {
    "category": "", "note": PKG_NOTE_DEFAULT,
    "material": "Art Matte", "size": "12\"x12\"", "pages": "40 muka surat",
}

DEFAULT_VA_FAQ = [
    {"q": "Berapa harga pakej?", "a": "Pakej Photobook bermula dari RM65. Lihat seksyen Terokai di laman utama untuk semua pakej & harga."},
    {"q": "Berapa lama siap?", "a": "Anggaran 30 hari bekerja selepas kami terima gambar lengkap anda."},
    {"q": "Macam mana nak hantar gambar?", "a": "Selepas pembayaran, anda boleh hantar gambar melalui WhatsApp, Telegram atau Google Drive — ikut pilihan semasa checkout."},
    {"q": "Berapa kali boleh semak/edit?", "a": "Setiap pakej termasuk 1× semakan PERCUMA sebelum cetakan/finalisasi."},
    {"q": "Cara pembayaran?", "a": "Pembayaran selamat melalui FPX, Kad Kredit/Debit & e-Wallet (dikuasakan oleh HitPay)."},
    {"q": "Penghantaran ke mana?", "a": "Kami hantar ke seluruh Malaysia. Caj penghantaran dikira automatik ikut berat & zon (Semenanjung / Sabah & Sarawak) semasa checkout."},
]

DEFAULT_REVIEWS = [
    {"name": "Aisyah & Haziq",     "pkg": "Album Warisan",  "date": "Jun 2026", "rating": 5,
     "quote": "Album kami sangat cantik & tersusun. Designer faham betul vibe yang kami nak. Semak 2 kali je terus sempurna!"},
    {"name": "Nurul & Amirul",     "pkg": "Design + Cetak", "date": "Mei 2026", "rating": 5,
     "quote": "Proses sangat mudah. Hantar gambar pagi, esok designer dah hubungi. Layout siap dalam 5 hari. Terbaik!"},
    {"name": "Farah & Danial",     "pkg": "Pakej Penuh",    "date": "Apr 2026", "rating": 5,
     "quote": "Packaging pun cantik, album sampai dalam keadaan sempurna. Harga berbaloi sangat dengan kualiti yang dapat!"},
    {"name": "Hafiz & Liyana",     "pkg": "Album Warisan",  "date": "Apr 2026", "rating": 5,
     "quote": "Tak sangka gambar telefon pun boleh jadi album secantik ni. Designer pandai susun ikut cerita majlis."},
    {"name": "Syafiq & Aina",      "pkg": "Design Sahaja",  "date": "Mac 2026", "rating": 5,
     "quote": "Komunikasi sangat baik, setiap detail diambil berat. Hasil akhir buat kami menangis bahagia."},
    {"name": "Imran & Balqis",     "pkg": "Pakej Penuh",    "date": "Mac 2026", "rating": 5,
     "quote": "Dari mula sampai siap semua smooth. Kualiti cetakan tajam, warna cantik. Recommended!"},
    {"name": "Danish & Maisarah",  "pkg": "Design + Cetak", "date": "Feb 2026", "rating": 5,
     "quote": "Album untuk hadiah ibu bapa. Mereka suka sangat! Terima kasih designer PixyoPrint."},
    {"name": "Adam & Qistina",     "pkg": "Album Warisan",  "date": "Feb 2026", "rating": 5,
     "quote": "Layout kemas, font cantik, susunan gambar nampak profesional. Memang puas hati 100%."},
    {"name": "Zikri & Nadia",      "pkg": "Pakej Penuh",    "date": "Jan 2026", "rating": 5,
     "quote": "Fast response, hasil melebihi jangkaan. Album jadi memori paling berharga kami."},
    {"name": "Iskandar & Sofea",   "pkg": "Design + Cetak", "date": "Jan 2026", "rating": 5,
     "quote": "Setiap helaian disusun dengan teliti. Nampak betul usaha designer. Sangat berpuas hati!"},
]

DEFAULT_ANNOUNCEMENTS = [
    {"emoji": "🌷", "text": "Tote Bag percuma untuk setiap order RM79 & ke atas.", "cta": "Tempah Sekarang", "active": True},
    {"emoji": "✨", "text": "Guna kod FIFA10 untuk diskaun 10% semasa checkout.",   "cta": "Tempah",          "active": True},
    {"emoji": "📦", "text": "Album siap disusun dalam 7 hari — design manual oleh designer kami.", "cta": "", "active": True},
]

DEFAULT_EXPLORE = [
    {"name": "Album Hardcover",     "desc": "Kemas, tahan lama & premium", "icon": "ph ph-book-bookmark", "cat": "HARDCOVER"},
    {"name": "Photobook Softcover", "desc": "Ringan & bergaya",            "icon": "ph ph-book-open",      "cat": "SOFTCOVER"},
    {"name": "Crystal Album",       "desc": "Kulit kristal mewah",         "icon": "ph ph-diamond",        "cat": "CRYSTAL ALBUM"},
    {"name": "Add-on & Hadiah",     "desc": "Tote bag, mug & lain-lain",   "icon": "ph ph-gift",           "cat": ""},
]

DEFAULT_DATA = {
    "editors": [],
    "reviews": DEFAULT_REVIEWS,
    "announcements": DEFAULT_ANNOUNCEMENTS,
    "explore": DEFAULT_EXPLORE,
    "hitpay": {
        # SECURITY: Baca dari environment variable — jangan simpan secret dalam kod/Git.
        # Set HITPAY_API_KEY & HITPAY_SALT sebelum jalankan, atau isi via panel admin.
        "api_key": os.environ.get("HITPAY_API_KEY", ""),
        "salt":    os.environ.get("HITPAY_SALT", ""),
        "sandbox": os.environ.get("HITPAY_SANDBOX", "true").lower() != "false",
    },
    "chip": {
        "secret_key": os.environ.get("CHIP_SECRET_KEY", ""),
        "brand_id":   os.environ.get("CHIP_BRAND_ID", ""),
    },
    "payment_gateway": os.environ.get("PAYMENT_GATEWAY", "hitpay"),
    "tracking": {
        "meta_pixel_id": "",
        "ga4_id": "",
        "tiktok_pixel_id": "",
    },
    "addons": [
        {"name": "Add-On Layout 6x6 (10 foto)",       "price": "2",  "weight": 0, "category": "ADDON"},
        {"name": "Add-On Editor Pilih Gambar 6x6",     "price": "20", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Layout 6x8 (10 foto)",       "price": "2",  "weight": 0, "category": "ADDON"},
        {"name": "Add-On Editor Pilih Gambar 6x8",     "price": "20", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Layout 8x8 (10 foto)",       "price": "4",  "weight": 0, "category": "ADDON"},
        {"name": "Add-On Editor Pilih Gambar 8x8",     "price": "40", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Layout 11x8.5 (10 foto)",    "price": "4",  "weight": 0, "category": "ADDON"},
        {"name": "Add-On Editor Pilih Gambar 11x8.5",  "price": "40", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Tambah Ayat 6x6",     "price": "2", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Tambah Ayat 6x8",     "price": "2", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Tambah Ayat 8x8",     "price": "2", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Tambah Ayat 11x8.5",  "price": "2", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Edit Cerah Tone 6x6",     "price": "50",  "weight": 0, "category": "ADDON"},
        {"name": "Add-On Edit Cerah Tone 6x8",     "price": "75",  "weight": 0, "category": "ADDON"},
        {"name": "Add-On Edit Cerah Tone 8x8",     "price": "100", "weight": 0, "category": "ADDON"},
        {"name": "Add-On Edit Cerah Tone 11x8.5",  "price": "125", "weight": 0, "category": "ADDON"},
        {"name": "Photobook 6x6 (Softcover)",     "price": "65",  "weight": 0.5, "category": "PHOTOBOOK"},
        {"name": "Photobook 6x6 (Hardcover)",     "price": "68",  "weight": 0.5, "category": "PHOTOBOOK"},
        {"name": "Photobook 6x8 (Softcover)",     "price": "68",  "weight": 0.5, "category": "PHOTOBOOK"},
        {"name": "Photobook 6x8 (Hardcover)",     "price": "78",  "weight": 0.5, "category": "PHOTOBOOK"},
        {"name": "Photobook 8x8 (Softcover)",     "price": "85",  "weight": 0.5, "category": "PHOTOBOOK"},
        {"name": "Photobook 8x8 (Hardcover)",     "price": "115", "weight": 0.5, "category": "PHOTOBOOK"},
        {"name": "Photobook 11x8.5 (Softcover)",  "price": "95",  "weight": 0.5, "category": "PHOTOBOOK"},
        {"name": "Photobook 11x8.5 (Hardcover)",  "price": "135", "weight": 0.5, "category": "PHOTOBOOK"},
    ],
    "media_links": {
        "whatsapp": "",
        "telegram": "",
        "gdrive":   "",
    },
    "contact_links": {
        "whatsapp": "",
        "email": "",
    },
    "vouchers": [],
    "users": [
        {"email": os.environ.get("ADMIN_EMAIL", "admin@pixyoprint.com"),
         "password": os.environ.get("ADMIN_PASSWORD", "admin123"),
         "name": "Azlinda", "role": "admin"}
    ],
    "flow": [
        {"n": "01", "cap": "Pilih pakej & tema",    "url": ""},
        {"n": "02", "cap": "Hantar gambar majlis",  "url": ""},
        {"n": "03", "cap": "Designer susun layout", "url": ""},
        {"n": "04", "cap": "Semak & terima album",  "url": ""},
    ],
    "packages": [
        {"name": "PHOTOBOOK HARDCOVER 6x6", "category": "HARDCOVER", "material": "Lustre Paper",
         "size": "6\"×6\"", "price": "68", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 100 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "PHOTOBOOK HARDCOVER 6x8", "category": "HARDCOVER", "material": "Lustre Paper",
         "size": "6\"×8\"", "price": "78", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 150 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "PHOTOBOOK HARDCOVER 8x8", "category": "HARDCOVER", "material": "Lustre Paper",
         "size": "8\"×8\"", "price": "115", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 150 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "PHOTOBOOK HARDCOVER 11x8.5", "category": "HARDCOVER", "material": "Lustre Paper",
         "size": "11\"×8.5\"", "price": "135", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 250 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "PHOTOBOOK SOFTCOVER 6X6", "category": "SOFTCOVER", "material": "Lustre Paper",
         "size": "6\"×6\"", "price": "65", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 100 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "PHOTOBOOK SOFTCOVER 6X8", "category": "SOFTCOVER", "material": "Lustre Paper",
         "size": "6\"×8\"", "price": "70", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 150 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "PHOTOBOOK SOFTCOVER 8X8", "category": "SOFTCOVER", "material": "Lustre Paper",
         "size": "8\"×8\"", "price": "80", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 150 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "PHOTOBOOK SOFTCOVER 11X8.5", "category": "SOFTCOVER", "material": "Lustre Paper",
         "size": "11\"×8.5\"", "price": "100", "pages": "40", "weight": 0.5,
         "desc": "Susun layout digital sahaja. Gambar Max 250 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
        {"name": "CRYSTAL ALBUM 12x8", "category": "CRYSTAL ALBUM", "material": "Glossy",
         "size": "12\"×8\"", "price": "240", "pages": "20", "weight": 2.3,
         "desc": "Susun layout digital sahaja. Gambar Max 100 pcs. Editor susunkan, 1× semakan PERCUMA. Siap dalam 30 hari bekerja."},
    ],
    "categoryOrder": ["HARDCOVER", "SOFTCOVER", "CRYSTAL ALBUM"],
}

# token -> nama pengguna (sesi dalam ingatan; hilang bila server restart)
SESSIONS = {}


# ============================================================
# HITPAY HELPERS
# ============================================================
def hitpay_create_payment(amount, reference, redirect_url, webhook_url, name="", email=""):
    """Cipta payment request di HitPay. Pulangkan dict {ok, url, payment_id} atau {ok:False, error}."""
    cfg = get_hitpay_cfg()
    if not cfg["api_key"]:
        return {"ok": False, "error": "HitPay API Key belum dikonfigurasi"}
    params = {
        "amount":           f"{float(amount):.2f}",
        "currency":         HITPAY_CURRENCY,
        "reference_number": reference,
        "redirect_url":     redirect_url,
    }
    if webhook_url: params["webhook"] = webhook_url
    if name:        params["name"]    = name
    if email:       params["email"]   = email

    body = urllib.parse.urlencode(params).encode("utf-8")
    req  = urllib.request.Request(
        f"{cfg['base']}/v1/payment-requests",
        data=body,
        headers={
            "X-BUSINESS-API-KEY": cfg["api_key"],
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "PixyoPrint/1.0",
            "Accept": "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "url": data.get("url"), "payment_id": data.get("id")}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        return {"ok": False, "error": err}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def hitpay_verify_webhook(payload: dict, hmac_received: str) -> bool:
    """Verify HMAC signature dari HitPay webhook."""
    salt = get_hitpay_cfg()["salt"]
    sorted_str = "\n".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
    computed = hmac_lib.new(
        salt.encode("utf-8"),
        sorted_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return hmac_lib.compare_digest(computed, hmac_received)


# ============================================================
# CHIP COLLECT HELPERS (gate.chip-in.asia)
# ============================================================
CHIP_BASE = "https://gate.chip-in.asia/api/v1"

def get_chip_cfg():
    """Baca config CHIP dari data.json (live, boleh tukar tanpa restart)."""
    cfg = load_data().get("chip", {})
    return {
        "secret_key": cfg.get("secret_key", "") or os.environ.get("CHIP_SECRET_KEY", ""),
        "brand_id":   cfg.get("brand_id", "") or os.environ.get("CHIP_BRAND_ID", ""),
    }

def chip_request(method, path, body=None):
    """Panggil API CHIP. Pulangkan (data, error)."""
    cfg = get_chip_cfg()
    if not cfg["secret_key"]:
        return None, "CHIP Secret Key belum dikonfigurasi"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        CHIP_BASE + path, data=data,
        headers={
            "Authorization": "Bearer " + cfg["secret_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "PixyoPrint/1.0",
        }, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode("utf-8")
    except Exception as e:
        return None, str(e)

def chip_create_purchase(total, reference, success_redirect, failure_redirect, success_callback, name, email, whitelist=None):
    """Cipta purchase di CHIP. Amaun dalam SEN (integer). Pulangkan {ok, url, id}."""
    cfg = get_chip_cfg()
    if not cfg["secret_key"] or not cfg["brand_id"]:
        return {"ok": False, "error": "CHIP Secret Key / Brand ID belum dikonfigurasi"}
    body = {
        "brand_id": cfg["brand_id"],
        "client": {"email": email or "noemail@pixyoprint.com", "full_name": name or "Pelanggan"},
        "purchase": {
            "currency": "MYR",
            "products": [{"name": "Pesanan PixyoPrint #" + reference,
                          "price": int(round(float(total) * 100)), "quantity": "1"}],
        },
        "reference": reference,
        "success_redirect": success_redirect,
        "failure_redirect": failure_redirect,
    }
    if success_callback:
        body["success_callback"] = success_callback
    if whitelist:
        body["payment_method_whitelist"] = whitelist
    data, err = chip_request("POST", "/purchases/", body)
    # Fallback selamat: jika whitelist ditolak (kaedah tak aktif untuk brand), cuba tanpa whitelist
    if err and whitelist:
        body.pop("payment_method_whitelist", None)
        data, err = chip_request("POST", "/purchases/", body)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "url": data.get("checkout_url"), "id": data.get("id")}

def chip_get_purchase(purchase_id):
    """Ambil semula purchase dari CHIP (sahkan status guna secret key)."""
    data, _ = chip_request("GET", "/purchases/" + str(purchase_id) + "/")
    return data


# ── EasyParcel Open API (OAuth 2.0) ──────────────────────────
EP_API_BASE = "https://api.easyparcel.com"
EP_OPENAPI  = EP_API_BASE + "/open_api/2026-06"

# Nama negeri Malaysia → kod ISO 3166-2 (subdivision_code)
MY_STATE_CODES = {
    "johor": "MY-01", "kedah": "MY-02", "kelantan": "MY-03", "melaka": "MY-04",
    "malacca": "MY-04", "negeri sembilan": "MY-05", "pahang": "MY-06",
    "pulau pinang": "MY-07", "penang": "MY-07", "perak": "MY-08", "perlis": "MY-09",
    "selangor": "MY-10", "terengganu": "MY-11", "sabah": "MY-12", "sarawak": "MY-13",
    "kuala lumpur": "MY-14", "wp kuala lumpur": "MY-14", "wilayah persekutuan kuala lumpur": "MY-14",
    "labuan": "MY-15", "wp labuan": "MY-15", "putrajaya": "MY-16", "wp putrajaya": "MY-16",
}

def ep_state_code(negeri):
    key = str(negeri or "").strip().lower()
    key = key.replace("w.p. ", "wp ").replace("w.p ", "wp ")
    return MY_STATE_CODES.get(key, "")

def get_easyparcel_cfg():
    """Config EasyParcel Open API dari data.json."""
    cfg = load_data().get("easyparcel", {}) or {}
    return {
        "client_id":     cfg.get("client_id", "") or os.environ.get("EASYPARCEL_CLIENT_ID", ""),
        "client_secret": cfg.get("client_secret", "") or os.environ.get("EASYPARCEL_CLIENT_SECRET", ""),
        "access_token":  cfg.get("access_token", ""),
        "refresh_token": cfg.get("refresh_token", ""),
        "token_expiry":  int(cfg.get("token_expiry", 0) or 0),
        "oauth_state":   cfg.get("oauth_state", ""),
        "pick_name":     cfg.get("pick_name", ""),
        "pick_contact":  cfg.get("pick_contact", ""),
        "pick_email":    cfg.get("pick_email", ""),
        "pick_addr1":    cfg.get("pick_addr1", ""),
        "pick_addr2":    cfg.get("pick_addr2", ""),
        "pick_city":     cfg.get("pick_city", ""),
        "pick_code":     cfg.get("pick_code", ""),
        "pick_state":    cfg.get("pick_state", ""),
        "content":       cfg.get("content", "Photobook"),
        "def_weight":    float(cfg.get("def_weight", 0.5) or 0.5),
        "width":         cfg.get("width", 25),
        "length":        cfg.get("length", 20),
        "height":        cfg.get("height", 5),
    }

def ep_set_cfg(**kv):
    """Kemas kini sebahagian config easyparcel dalam data.json."""
    d = load_data()
    ep = d.get("easyparcel", {}) or {}
    ep.update(kv)
    d["easyparcel"] = ep
    save_data(d)

def ep_oauth_token(grant_type, code=None, refresh_token=None, redirect_uri=None):
    """Tukar code/refresh_token → access_token. Pulangkan (data, error)."""
    cfg = get_easyparcel_cfg()
    if not cfg["client_id"] or not cfg["client_secret"]:
        return None, "Client ID / Secret EasyParcel belum ditetapkan"
    basic = base64.b64encode(
        (cfg["client_id"] + ":" + cfg["client_secret"]).encode("utf-8")).decode("ascii")
    body = {"grant_type": grant_type}
    if code:          body["code"] = code
    if refresh_token: body["refresh_token"] = refresh_token
    if redirect_uri:  body["redirect_uri"] = redirect_uri
    req = urllib.request.Request(
        EP_API_BASE + "/oauth/token",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Basic " + basic,
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "PixyoPrint/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode("utf-8")
    except Exception as e:
        return None, str(e)

def ep_store_token(data):
    """Simpan access/refresh token dari respons OAuth."""
    ep_set_cfg(
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token", "") or get_easyparcel_cfg()["refresh_token"],
        token_expiry=int(time.time()) + int(data.get("expires_in", 36000) or 36000) - 120,
    )

def ep_get_access_token():
    """Access token sah — refresh automatik jika tamat tempoh. None jika belum connect."""
    cfg = get_easyparcel_cfg()
    if not cfg["access_token"]:
        return None
    if time.time() < cfg["token_expiry"]:
        return cfg["access_token"]
    if not cfg["refresh_token"]:
        return None
    data, err = ep_oauth_token("refresh_token", refresh_token=cfg["refresh_token"])
    if err or not data or not data.get("access_token"):
        return None
    ep_store_token(data)
    return data.get("access_token")

def ep_api(method, path, body=None):
    """Panggil Open API dgn Bearer token. Pulangkan (data, error)."""
    token = ep_get_access_token()
    if not token:
        return None, "EasyParcel belum disambung (OAuth). Sila Sambung Akaun di Tetapan."
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        EP_OPENAPI + path, data=data,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "PixyoPrint/1.0"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode("utf-8")
    except Exception as e:
        return None, str(e)

def ep_order_weight(order):
    """Berat order: guna 'weight' tersimpan, atau kira dari item ikut pakej semasa."""
    w = order.get("weight")
    if w:
        try:
            return max(0.1, float(w))
        except (TypeError, ValueError):
            pass
    d = load_data()
    by_name = {p.get("name"): p for p in d.get("packages", [])}
    for a in d.get("addons", []):
        by_name[a.get("name")] = a
    total = 0.0
    for it in order.get("items", []):
        pk = by_name.get(it.get("name")) or {}
        try:
            total += float(pk.get("weight", 0) or 0) * int(it.get("qty", 1))
        except (TypeError, ValueError):
            pass
    return max(0.1, total or get_easyparcel_cfg()["def_weight"])

def ep_sender(cfg):
    return {
        "name":          cfg["pick_name"],
        "phone_number":  cfg["pick_contact"],
        "email":         cfg["pick_email"],
        "address_1":     cfg["pick_addr1"],
        "address_2":     cfg["pick_addr2"],
        "city":          cfg["pick_city"],
        "postcode":      cfg["pick_code"],
        "subdivision_code": ep_state_code(cfg["pick_state"]),
        "country_code":  "MY",
    }

def ep_receiver(order):
    return {
        "name":          order.get("name", "") or "Pelanggan",
        "phone_number":  order.get("phone", ""),
        "email":         order.get("email", ""),
        "address_1":     order.get("alamat", ""),
        "address_2":     "",
        "city":          order.get("bandar", ""),
        "postcode":      order.get("poskod", ""),
        "subdivision_code": ep_state_code(order.get("negeri", "")),
        "country_code":  "MY",
    }

def ep_check_rates(order):
    """Semak kadar (quotations) untuk order. Pulangkan {ok, rates:[...], weight}."""
    cfg = get_easyparcel_cfg()
    weight = ep_order_weight(order)
    snd, rcv = ep_sender(cfg), ep_receiver(order)
    body = {"shipment": [{
        "sender":       {"postcode": snd["postcode"], "subdivision_code": snd["subdivision_code"], "country": "MY"},
        "receiver":     {"postcode": rcv["postcode"], "subdivision_code": rcv["subdivision_code"], "country": "MY"},
        "parcel_value": float(order.get("subtotal", order.get("total", 1)) or 1),
        "weight":       weight,
        "width":        cfg["width"], "length": cfg["length"], "height": cfg["height"],
    }]}
    data, err = ep_api("POST", "/shipment/quotations", body)
    if err:
        return {"ok": False, "error": err}
    try:
        block = (data.get("data") or [{}])[0]
        quotes = block.get("quotations") or []
        out = []
        for q in quotes:
            c = q.get("courier", {}) or {}
            p = q.get("pricing", {}) or {}
            out.append({
                "service_id":   c.get("service_id"),
                "courier_name": c.get("courier_name") or c.get("service_name"),
                "service_name": c.get("service_name"),
                "price":        float(p.get("total_amount") or p.get("shipment_price") or 0),
                "delivery":     c.get("delivery_duration") or "",
            })
        return {"ok": True, "rates": out, "weight": weight}
    except Exception as e:
        return {"ok": False, "error": "Respons quotation tak dijangka: " + str(e)}

def ep_book(order, service_id):
    """Submit shipment (auto-tolak wallet). Pulangkan {ok, awb, order_no, tracking_url, awb_link, cost, courier}."""
    cfg = get_easyparcel_cfg()
    weight = ep_order_weight(order)
    body = {"shipment": [{
        "sender":   ep_sender(cfg),
        "receiver": ep_receiver(order),
        "service_id":      service_id,
        "collection_date": now_myt().strftime("%Y-%m-%d"),
        "reference":       order.get("reference", ""),
        "weight": weight, "width": cfg["width"], "length": cfg["length"], "height": cfg["height"],
        "items": [{
            "content":       cfg["content"],
            "quantity":      1,
            "value":         float(order.get("subtotal", order.get("total", 1)) or 1),
            "currency_code": "MYR",
            "weight": weight, "width": cfg["width"], "length": cfg["length"], "height": cfg["height"],
        }],
    }]}
    data, err = ep_api("POST", "/shipment/submit", body)
    if err:
        return {"ok": False, "error": err}
    try:
        block = (data.get("data") or [{}])[0]
        if block.get("status") and block.get("status") != "success":
            return {"ok": False, "error": block.get("message") or "Gagal submit shipment"}
        return {
            "ok": True,
            "order_no":     block.get("order_number") or block.get("shipment_id") or "",
            "awb":          block.get("awb") or block.get("tracking_number") or "",
            "awb_link":     block.get("awb_url") or block.get("label_url") or "",
            "tracking_url": block.get("tracking_url") or "",
            "courier":      block.get("courier_name") or "",
            "cost":         float((block.get("pricing") or {}).get("total_amount") or block.get("price") or 0),
        }
    except Exception as e:
        return {"ok": False, "error": "Respons submit tak dijangka: " + str(e)}


def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        # data.json rosak — backup & seed semula supaya laman PULIH (bukan crash 502)
        try:
            os.replace(DATA_FILE, DATA_FILE + ".corrupt")
        except OSError:
            pass
        print(f"[load_data] data.json rosak ({e}); seed semula DEFAULT_DATA")
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    if not isinstance(data, dict):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    try:
        return _migrate_data(data)
    except Exception as e:
        # Migrasi gagal — jangan crash; pulangkan data sedia ada
        print(f"[load_data] migrasi gagal: {e}")
        return data


def _migrate_data(data):
    # Migrasi: backfill medan pakej yang belum wujud (tanpa reset data)
    changed = False
    for p in data.get("packages", []):
        for key, val in PKG_FIELD_DEFAULTS.items():
            if key not in p:
                p[key] = val
                changed = True
        # Migrasi: tukar 'img' tunggal -> 'imgs' (senarai, maks 5)
        if "imgs" not in p:
            old = p.get("img", "")
            p["imgs"] = [old] if old else []
            changed = True
    # Migrasi: pastikan categoryOrder selari dengan kategori dalam pakej
    cats_in_pkgs = []
    for p in data.get("packages", []):
        c = (p.get("category") or "").strip()
        if c and c not in cats_in_pkgs:
            cats_in_pkgs.append(c)
    existing = data.get("categoryOrder", [])
    new_order = [c for c in existing if c in cats_in_pkgs] + \
                [c for c in cats_in_pkgs if c not in existing]
    if new_order != existing:
        data["categoryOrder"] = new_order
        changed = True
    if "vouchers" not in data:
        data["vouchers"] = []
        changed = True
    if "orders" not in data:
        data["orders"] = []
        changed = True
    if "data_requests" not in data:
        data["data_requests"] = []
        changed = True
    if "va_faq" not in data:
        data["va_faq"] = json.loads(json.dumps(DEFAULT_VA_FAQ))
        changed = True
    if "media_links" not in data:
        data["media_links"] = DEFAULT_DATA["media_links"].copy()
        changed = True
    if "contact_links" not in data:
        data["contact_links"] = DEFAULT_DATA["contact_links"].copy()
        changed = True
    if "chip" not in data:
        data["chip"] = DEFAULT_DATA["chip"].copy()
        changed = True
    if "payment_gateway" not in data:
        data["payment_gateway"] = DEFAULT_DATA.get("payment_gateway", "hitpay")
        changed = True
    if "tracking" not in data:
        data["tracking"] = DEFAULT_DATA["tracking"].copy()
        changed = True
    if "addons" not in data:
        data["addons"] = json.loads(json.dumps(DEFAULT_DATA["addons"]))
        changed = True
    else:
        # Merge: tambah add-on lalai yang belum wujud (ikut nama) — tanpa ubah harga sedia ada
        _have = {a.get("name") for a in data["addons"]}
        for _a in DEFAULT_DATA["addons"]:
            if _a.get("name") not in _have:
                data["addons"].append(json.loads(json.dumps(_a)))
                changed = True
    if "hitpay" not in data:
        data["hitpay"] = DEFAULT_DATA["hitpay"].copy()
        changed = True
    if "editors" not in data:
        data["editors"] = []
        changed = True
    if "reviews" not in data:
        data["reviews"] = json.loads(json.dumps(DEFAULT_REVIEWS))
        changed = True
    if "announcements" not in data:
        data["announcements"] = json.loads(json.dumps(DEFAULT_ANNOUNCEMENTS))
        changed = True
    if "explore" not in data:
        data["explore"] = json.loads(json.dumps(DEFAULT_EXPLORE))
        changed = True
    # Migrate plaintext passwords → hashed (PBKDF2)
    for u in data.get("users", []):
        if not u.get("password", "").startswith("pbkdf2:"):
            u["password"] = hash_password(u["password"])
            changed = True
    if changed:
        save_data(data)
    return data


def save_data(d):
    import tempfile, os
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


# PDPA s.10 (Prinsip Penyimpanan): padam order melebihi tempoh simpan.
RETENTION_DAYS = 730  # 2 tahun

def _order_age_days(order):
    """Pulangkan umur order dalam hari, atau None jika tarikh tak boleh dibaca."""
    ts = order.get("created_ts")
    dt = None
    if ts:
        try:
            dt = datetime.datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            dt = None
    if dt is None:
        # Fallback: parse created_at ("22 Jun 2026, 23:56") — singkatan bulan Inggeris
        try:
            dt = datetime.datetime.strptime(order.get("created_at", ""), "%d %b %Y, %H:%M")
        except (ValueError, TypeError):
            return None
    return (now_myt() - dt).days

def purge_old_orders(d):
    """Buang order yang melebihi RETENTION_DAYS. Pulangkan bilangan dipadam.
    Order tanpa tarikh sah dikekalkan (selamat — tidak dipadam secara silap)."""
    orders = d.get("orders", [])
    kept = []
    removed = 0
    for o in orders:
        age = _order_age_days(o)
        if age is not None and age > RETENTION_DAYS:
            removed += 1
        else:
            kept.append(o)
    if removed:
        d["orders"] = kept
    return removed

def run_retention_purge():
    """Jalankan purge dan simpan jika ada perubahan. Pulangkan bilangan dipadam."""
    d = load_data()
    removed = purge_old_orders(d)
    if removed:
        save_data(d)
        print(f"[Retention] {removed} order melebihi {RETENTION_DAYS} hari dipadam (PDPA s.10).")
    return removed


_SECURITY_HEADERS = [
    ("X-Content-Type-Options",  "nosniff"),
    ("X-Frame-Options",         "DENY"),
    ("X-XSS-Protection",        "1; mode=block"),
    ("Referrer-Policy",         "strict-origin-when-cross-origin"),
    ("Permissions-Policy",      "camera=(), microphone=(), geolocation=()"),
    ("Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://connect.facebook.net https://www.googletagmanager.com https://www.google-analytics.com https://analytics.tiktok.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob: https://www.facebook.com https://www.google-analytics.com https://*.googletagmanager.com https://analytics.tiktok.com; "
        "media-src 'self' blob:; "
        "connect-src 'self' https://www.facebook.com https://connect.facebook.net https://www.google-analytics.com https://*.analytics.google.com https://*.google-analytics.com https://www.googletagmanager.com https://analytics.tiktok.com https://*.tiktok.com; "
        "frame-ancestors 'none'"),
]

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    # SECURITY: inject security headers on every response
    def end_headers(self):
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)
        super().end_headers()

    # --- utilities -------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _user(self):
        """Pulang sesi {name, role, email} atau None."""
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            return SESSIONS.get(h[7:])
        return None

    def _is_admin(self):
        """Pulang sesi jika pengguna admin, jika tidak None."""
        u = self._user()
        return u if (u and u.get("role") == "admin") else None

    def _can_access_order(self, order):
        """True jika admin, atau editor yang order ini ditugaskan kepadanya."""
        u = self._user()
        if not u:
            return False
        if u.get("role") == "admin":
            return True
        if u.get("role") == "editor":
            first = u["name"].split(" ")[0]
            return order.get("editor") in (first, u["name"])
        return False

    def _serve_upload(self, name):
        """Hidangkan fail dari UPLOAD_DIR dengan selamat (cegah path traversal)."""
        import mimetypes
        safe = os.path.basename(name)  # buang sebarang path
        path = os.path.join(UPLOAD_DIR, safe)
        if not os.path.abspath(path).startswith(os.path.abspath(UPLOAD_DIR)) or not os.path.isfile(path):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    # --- routes ----------------------------------------------------
    def do_GET(self):
        # Hidangkan fail upload dari persistent disk (UPLOAD_DIR mungkin di luar BASE)
        if self.path.startswith("/uploads/"):
            return self._serve_upload(self.path[len("/uploads/"):].split("?")[0])
        if self.path == "/api/flow":
            return self._json(load_data().get("flow", []))
        if self.path == "/api/packages":
            return self._json(load_data().get("packages", []))
        if self.path == "/api/addons":
            return self._json(load_data().get("addons", []))
        if self.path == "/api/categories":
            return self._json(load_data().get("categoryOrder", []))
        if self.path == "/api/vouchers":
            return self._json(load_data().get("vouchers", []))
        if self.path == "/api/reviews":
            return self._json(load_data().get("reviews", []))
        if self.path == "/api/announcements":
            return self._json(load_data().get("announcements", []))
        if self.path == "/api/explore":
            return self._json(load_data().get("explore", []))
        if self.path == "/api/va-faq":
            return self._json(load_data().get("va_faq", []))
        if self.path == "/api/me":
            u = self._user()
            return self._json({"ok": bool(u),
                               "name": u["name"] if u else None,
                               "role": u.get("role") if u else None})
        if self.path == "/api/postage":
            return self._json(load_data().get("postage", {"base":8,"threshold":1.5,"per_kg":2,"east":{"base":15,"threshold":1.0,"per_kg":10}}))
        if self.path.startswith("/api/orders/") and self.path.endswith("/chip"):
            # Admin: ambil jumlah sebenar dibayar dari CHIP untuk 1 order
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            ref = self.path[len("/api/orders/"):-len("/chip")]
            d = load_data()
            order = next((o for o in d.get("orders", []) if o.get("reference") == ref), None)
            if not order:
                return self._json({"ok": False, "error": "Order tidak dijumpai"}, 404)
            pid = order.get("chip_id") or order.get("payment_id")
            if not pid:
                return self._json({"ok": False, "error": "Order ini tiada rekod CHIP"}, 404)
            p = chip_get_purchase(pid)
            if not p or not isinstance(p, dict):
                return self._json({"ok": False, "error": "Gagal hubungi CHIP"}, 502)
            pur = p.get("purchase", {}) or {}
            pay = p.get("transaction_data", {}) or {}
            amount_cents = pur.get("total")
            paid_on = p.get("paid_on") or p.get("updated_on")
            paid_str = ""
            if paid_on:
                try:
                    paid_str = datetime.datetime.fromtimestamp(
                        int(paid_on), datetime.timezone(datetime.timedelta(hours=8))
                    ).strftime("%d %b %Y, %H:%M")
                except (ValueError, TypeError, OSError):
                    paid_str = ""
            return self._json({
                "ok": True,
                "status": p.get("status", ""),
                "amount": round(float(amount_cents) / 100.0, 2) if amount_cents is not None else None,
                "currency": pur.get("currency", "MYR"),
                "method": pay.get("payment_method", "") or (pay.get("extra", {}) or {}).get("payment_method", ""),
                "paid_on": paid_str,
                "chip_id": str(pid),
            })
        if self.path == "/api/media-links":
            return self._json(load_data().get("media_links", {}))
        if self.path == "/api/contact-links":
            return self._json(load_data().get("contact_links", {}))
        if self.path == "/api/hitpay-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            cfg = load_data().get("hitpay", {})
            # Mask API key dan salt — hantar sebahagian sahaja untuk paparan
            ak = cfg.get("api_key", "")
            sl = cfg.get("salt", "")
            return self._json({
                "api_key": ak[:8] + "..." + ak[-6:] if len(ak) > 14 else ak,
                "api_key_set": bool(ak),
                "salt_set": bool(sl),
                "sandbox": cfg.get("sandbox", True),
            })
        if self.path == "/api/chip-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            d = load_data()
            cfg = d.get("chip", {})
            sk = cfg.get("secret_key", "")
            return self._json({
                "secret_key": sk[:6] + "..." + sk[-4:] if len(sk) > 12 else sk,
                "secret_key_set": bool(sk),
                "brand_id": cfg.get("brand_id", ""),
                "gateway": d.get("payment_gateway", "chip"),
            })
        if self.path == "/api/easyparcel-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            cfg = load_data().get("easyparcel", {}) or {}
            cid = cfg.get("client_id", "")
            csec = cfg.get("client_secret", "")
            connected = bool(cfg.get("refresh_token")) and int(cfg.get("token_expiry", 0) or 0) > 0
            return self._json({
                "client_id": cid,
                "client_secret_set": bool(csec),
                "connected": connected,
                "pick_name": cfg.get("pick_name", ""),
                "pick_contact": cfg.get("pick_contact", ""),
                "pick_email": cfg.get("pick_email", ""),
                "pick_addr1": cfg.get("pick_addr1", ""),
                "pick_addr2": cfg.get("pick_addr2", ""),
                "pick_city": cfg.get("pick_city", ""),
                "pick_code": cfg.get("pick_code", ""),
                "pick_state": cfg.get("pick_state", ""),
                "content": cfg.get("content", "Photobook"),
                "width": cfg.get("width", 25),
                "length": cfg.get("length", 20),
                "height": cfg.get("height", 5),
            })
        if self.path == "/api/photo-reminder-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            c = load_data().get("photo_reminder", {}) or {}
            smtp = _smtp_cfg()
            return self._json({
                "enabled":        bool(c.get("enabled", False)),
                "delay_hours":    c.get("delay_hours", 24),
                "interval_hours": c.get("interval_hours", 48),
                "max":            c.get("max", 2),
                "smtp_ready":     bool(smtp["user"] and smtp["password"]),
            })
        if self.path == "/api/easyparcel/auth-url":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            cfg = get_easyparcel_cfg()
            if not cfg["client_id"]:
                return self._json({"ok": False, "error": "Client ID belum ditetapkan"}, 400)
            host = self.headers.get("Host", f"localhost:{PORT}")
            scheme = "http" if ("localhost" in host or "127.0.0.1" in host) else "https"
            redirect_uri = f"{scheme}://{host}/api/easyparcel-oauth-callback"
            state = secrets.token_hex(16)
            ep_set_cfg(oauth_state=state, oauth_redirect=redirect_uri)
            url = EP_API_BASE + "/oauth/login?" + urllib.parse.urlencode({
                "client_id": cfg["client_id"],
                "redirect_uri": redirect_uri,
                "state": state,
            })
            return self._json({"ok": True, "url": url})
        if self.path.startswith("/api/easyparcel-oauth-callback"):
            # Callback OAuth EasyParcel — tukar code → token
            q = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(q))
            code = params.get("code", "")
            state = params.get("state", "")
            cfg = load_data().get("easyparcel", {}) or {}
            def _html(msg, ok=True):
                color = "#2e7d32" if ok else "#c0392b"
                body = ("<!doctype html><meta charset='utf-8'><title>EasyParcel</title>"
                        "<div style=\"font-family:system-ui;max-width:460px;margin:80px auto;text-align:center;\">"
                        f"<div style='font-size:46px'>{'✓' if ok else '✕'}</div>"
                        f"<h2 style='color:{color}'>{msg}</h2>"
                        "<p style='color:#666'>Anda boleh tutup tetingkap ini dan kembali ke admin.</p></div>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
            if not code:
                return _html("Tiada kod kebenaran diterima.", False)
            if not state or state != cfg.get("oauth_state"):
                return _html("State tidak sah (kemungkinan sesi tamat). Cuba sambung semula.", False)
            redirect_uri = cfg.get("oauth_redirect", "")
            data, err = ep_oauth_token("authorization_code", code=code, redirect_uri=redirect_uri)
            if err or not data or not data.get("access_token"):
                return _html("Gagal tukar token: " + str(err or "tiada access_token"), False)
            ep_store_token(data)
            ep_set_cfg(oauth_state="")
            return _html("EasyParcel berjaya disambung!", True)
        if self.path == "/api/tracking":
            # Awam — laman perlu tahu pixel mana nak dimuat (ID pixel memang awam di sisi klien)
            t = load_data().get("tracking", {})
            return self._json({
                "meta_pixel_id": t.get("meta_pixel_id", ""),
                "ga4_id": t.get("ga4_id", ""),
                "tiktok_pixel_id": t.get("tiktok_pixel_id", ""),
            })
        if self.path == "/api/orders":
            u = self._user()
            if not u:
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            orders = load_data().get("orders", [])
            # Editor hanya nampak order yang ditugaskan kepadanya
            if u.get("role") == "editor":
                first = u["name"].split(" ")[0]
                orders = [o for o in orders
                          if o.get("editor") in (first, u["name"])]
            return self._json({"ok": True, "orders": list(reversed(orders))})
        if self.path == "/api/data-requests":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            reqs = load_data().get("data_requests", [])
            return self._json({"ok": True, "requests": list(reversed(reqs))})
        if self.path == "/api/editors":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            return self._json({"ok": True, "editors": load_data().get("editors", [])})

        if self.path.startswith("/api/order-status"):
            ref = self.path.split("ref=")[-1] if "ref=" in self.path else ""
            orders = load_data().get("orders", [])
            order = next((o for o in orders if o.get("reference") == ref), None)
            # SECURITY: Dedah status sahaja — bukan nama/email/items pelanggan
            if not order:
                return self._json({"status": "not_found"})
            return self._json({"reference": order.get("reference"),
                               "status": order.get("status")})

        # SECURITY: Blok akses fail sensitif (data.json, kod sumber, dotfiles)
        if not self._is_safe_static_path(self.path):
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    # SECURITY: Hanya benarkan static serving fail "selamat"
    def _is_safe_static_path(self, path):
        clean = path.split("?")[0].split("#")[0].lstrip("/").lower()
        blocked_ext = (".py", ".pyc", ".json", ".env", ".log", ".db", ".sqlite")
        base = clean.rsplit("/", 1)[-1]
        if base.startswith("."):       # dotfiles spt .gitignore, .env
            return False
        if clean.endswith(blocked_ext):  # data.json, server.py, dll
            return False
        if "__pycache__" in clean:
            return False
        return True

    def do_POST(self):
        if self.path == "/api/login":
            # SECURITY: Rate limiting — cegah brute-force
            client_ip = self.client_address[0]
            now = time.time()
            att_count, att_time = _LOGIN_ATTEMPTS.get(client_ip, [0, 0])
            if now - att_time > _LOCKOUT_SECS:
                att_count = 0
            if att_count >= _MAX_ATTEMPTS:
                remaining = int(_LOCKOUT_SECS - (now - att_time))
                return self._json({"ok": False,
                    "error": f"Terlalu banyak percubaan. Cuba lagi dalam {remaining//60+1} minit."}, 429)

            b = self._read_body()
            email = str(b.get("email", ""))[:254]
            password = str(b.get("password", ""))[:128]
            data = load_data()
            for u in data.get("users", []):
                if u["email"] == email and verify_password(u["password"], password):
                    _LOGIN_ATTEMPTS.pop(client_ip, None)
                    # SECURITY: auto-upgrade legacy plaintext hash selepas login
                    if not u["password"].startswith("pbkdf2:"):
                        u["password"] = hash_password(password)
                        save_data(data)
                    token = secrets.token_hex(32)
                    SESSIONS[token] = {"name": u["name"],
                                       "role": u.get("role", ""),
                                       "email": u.get("email", "")}
                    return self._json({"ok": True, "token": token,
                                       "name": u["name"], "role": u.get("role")})
            _LOGIN_ATTEMPTS[client_ip] = [att_count + 1, now]
            return self._json({"ok": False,
                               "error": "Email atau kata laluan salah"}, 401)
        if self.path == "/api/logout":
            h = self.headers.get("Authorization", "")
            if h.startswith("Bearer "):
                SESSIONS.pop(h[7:], None)
            return self._json({"ok": True})
        if self.path == "/api/upload":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return self._json({"ok": False, "error": "Fail kosong"}, 400)
            if length > MAX_UPLOAD:
                return self._json({"ok": False,
                                   "error": "Fail terlalu besar (maks 200MB)"}, 413)
            # SECURITY: Sanitize filename — ambil extension sahaja, buang path
            raw_name = os.path.basename(self.headers.get("X-Filename", "video"))[:200]
            ext = os.path.splitext(raw_name)[1].lower()
            # SECURITY: Strict allowlist — bukan sekadar extension check
            if ext not in ALLOWED_EXT or len(ext) > 5 or '/' in ext or '\\' in ext:
                return self._json({"ok": False,
                                   "error": "Format tidak disokong"}, 415)
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            # SECURITY: Random filename — cegah overwrite & enumeration
            fname = secrets.token_hex(16) + ext
            dest = os.path.join(UPLOAD_DIR, fname)
            # SECURITY: Verify path dalam UPLOAD_DIR sahaja (cegah traversal)
            if not os.path.abspath(dest).startswith(os.path.abspath(UPLOAD_DIR)):
                return self._json({"ok": False, "error": "Laluan tidak sah"}, 400)
            with open(dest, "wb") as out:
                out.write(self.rfile.read(length))
            return self._json({"ok": True, "url": "uploads/" + fname,
                               "name": raw_name})
        if self.path == "/api/data-request":
            # PDPA: permintaan subjek data (akses/betul/padam/tarik kebenaran/unsubscribe)
            b = self._read_body()
            VALID_TYPES = {"akses", "betulkan", "padam", "tarik_kebenaran", "unsubscribe"}
            rtype = str(b.get("type", "")).strip()[:30]
            if rtype not in VALID_TYPES:
                return self._json({"ok": False, "error": "Jenis permintaan tidak sah"}, 400)
            name  = str(b.get("name", "")).strip()[:120]
            email = str(b.get("email", "")).strip()[:254]
            phone = str(b.get("phone", "")).strip()[:30]
            if not name or not (email or phone):
                return self._json({"ok": False, "error": "Sila isi nama dan email/telefon"}, 400)
            d = load_data()
            d.setdefault("data_requests", []).append({
                "id":         secrets.token_hex(6),
                "type":       rtype,
                "name":       name,
                "email":      email,
                "phone":      phone,
                "order_ref":  str(b.get("order_ref", "")).strip()[:40],
                "message":    str(b.get("message", "")).strip()[:1000],
                "status":     "open",
                "created_at": now_myt().strftime("%d %b %Y, %H:%M"),
                "created_ts": now_myt().isoformat(timespec="seconds"),
            })
            save_data(d)
            return self._json({"ok": True})

        if self.path == "/api/pay":
            b = self._read_body()
            name    = str(b.get("name", ""))[:120]
            email   = str(b.get("email", ""))[:254]
            phone   = str(b.get("phone", ""))[:30]
            alamat  = str(b.get("alamat", ""))[:300]
            poskod  = str(b.get("poskod", ""))[:10]
            bandar  = str(b.get("bandar", ""))[:120]
            negeri  = str(b.get("negeri", ""))[:120]
            medium  = str(b.get("medium", ""))[:40]
            pay_method = str(b.get("pay_method", ""))[:20]
            req_items = b.get("items", [])
            voucher_code = str(b.get("voucher", "")).strip().upper()[:40]
            consent_marketing = bool(b.get("consent_marketing", False))
            consent_version = str(b.get("consent_version", "1.0"))[:10]

            if not isinstance(req_items, list) or not req_items:
                return self._json({"ok": False, "error": "Troli kosong"}, 400)

            d = load_data()
            # SECURITY: Kira semula harga dari pakej tersimpan di server —
            # JANGAN percaya 'total' atau 'price' yang dihantar client.
            pkg_by_name = {p.get("name"): p for p in d.get("packages", [])}
            for _a in d.get("addons", []):
                pkg_by_name[_a.get("name")] = _a
            server_items = []
            subtotal = 0.0
            total_weight = 0.0
            for it in req_items:
                nm = str(it.get("name", ""))
                try:
                    qty = int(it.get("qty", 1))
                except (TypeError, ValueError):
                    qty = 1
                qty = max(1, min(qty, 99))  # had munasabah
                pkg = pkg_by_name.get(nm)
                if not pkg:
                    return self._json({"ok": False,
                        "error": f"Pakej tidak sah: {nm}"}, 400)
                price = float(pkg.get("price", 0) or 0)
                subtotal += price * qty
                total_weight += float(pkg.get("weight", 0) or 0) * qty
                # Snapshot spesifikasi pakej waktu beli (kekal walau pakej diubah kemudian)
                server_items.append({
                    "name":     nm,
                    "price":    price,
                    "qty":      qty,
                    "desc":     pkg.get("desc", ""),
                    "pages":    pkg.get("pages", ""),
                    "material": pkg.get("material", ""),
                    "size":     pkg.get("size", ""),
                })

            # SECURITY: Sahkan baucar di server (jangan percaya diskaun client)
            discount = 0.0
            applied_voucher = ""
            if voucher_code:
                v = next((v for v in d.get("vouchers", [])
                          if str(v.get("code", "")).upper() == voucher_code
                          and v.get("active")), None)
                if v:
                    rate = float(v.get("discount", 0) or 0)
                    if v.get("type") == "rm":
                        discount = min(rate, subtotal)
                    else:
                        discount = subtotal * (rate / 100.0)
                    applied_voucher = voucher_code

            # Postage — dikira di server (sumber kebenaran) ikut berat & zon poskod
            postage = calc_postage(d, total_weight, poskod)
            total = round(max(0.0, subtotal - discount) + postage, 2)
            if total <= 0:
                return self._json({"ok": False, "error": "Jumlah tidak sah"}, 400)

            reference = gen_order_ref({o.get("reference") for o in d.get("orders", [])})
            # Simpan order dengan status 'pending'
            d["orders"].append({
                "reference":  reference,
                "status":     "pending",
                "total":      total,
                "subtotal":   round(subtotal, 2),
                "discount":   round(discount, 2),
                "postage":    round(postage, 2),
                "weight":     round(total_weight, 2),
                "voucher":    applied_voucher,
                "name":       name,
                "email":      email,
                "phone":      phone,
                "alamat":     alamat,
                "poskod":     poskod,
                "bandar":     bandar,
                "negeri":     negeri,
                "medium":     medium,
                "created_at": now_myt().strftime("%d %b %Y, %H:%M"),
                "created_ts": now_myt().isoformat(timespec="seconds"),
                "items":      server_items,
                # Rekod persetujuan PDPA (s.7/s.40 — bukti kebenaran)
                "consent": {
                    "given":     True,
                    "marketing": consent_marketing,
                    "version":   consent_version,
                    "at":        now_myt().strftime("%d %b %Y, %H:%M"),
                },
            })
            save_data(d)

            # Detect host & scheme untuk redirect/callback
            host = self.headers.get("Host", f"localhost:{PORT}")
            is_local = ("localhost" in host or "127.0.0.1" in host)
            scheme = "http" if is_local else "https"

            # Gerbang pembayaran: CHIP
            success_redirect = f"{scheme}://{host}/index.html?payment=return&ref={reference}&status=completed"
            failure_redirect = f"{scheme}://{host}/index.html?payment=return&ref={reference}&status=failed"
            # Callback server-ke-server tak boleh ke localhost
            callback_url = "" if is_local else f"{scheme}://{host}/api/chip-callback"
            # Whitelist DIMATIKAN buat masa ini — kod kaedah perlu disahkan dgn akaun CHIP
            # (whitelist salah kod -> CHIP jana invoice terkunci yg tak boleh bayar).
            # Auto-pilih kaedah dibuat melalui Direct Post (preferred=...) di sisi client.
            wl_map = {}
            whitelist = wl_map.get(pay_method)
            result = chip_create_purchase(total, reference, success_redirect, failure_redirect, callback_url, name, email, whitelist)
            if result["ok"]:
                for o in d["orders"]:
                    if o.get("reference") == reference:
                        o["chip_id"] = result.get("id")
                        break
                save_data(d)
                return self._json({"ok": True, "url": result["url"], "reference": reference})
            return self._json({"ok": False, "error": result.get("error", "Gagal cipta pembayaran")}, 502)

        if self.path == "/api/hitpay-webhook":
            # Baca form-encoded body dari HitPay
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            params = dict(urllib.parse.parse_qsl(raw))
            hmac_received = params.pop("hmac", "")

            salt = get_hitpay_cfg()["salt"]
            if salt:
                if not hitpay_verify_webhook(params, hmac_received):
                    return self._json({"ok": False, "error": "Invalid HMAC"}, 403)

            reference = params.get("reference_number", "")
            status    = params.get("status", "")
            d = load_data()
            for order in d.get("orders", []):
                if order.get("reference") == reference:
                    order["status"]     = status
                    order["payment_id"] = params.get("payment_id", "")
                    break
            save_data(d)
            # HitPay expects HTTP 200 plaintext
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if self.path.startswith("/api/orders/") and self.path.endswith("/repay"):
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            ref = self.path[len("/api/orders/"):-len("/repay")]
            d = load_data()
            order = next((o for o in d.get("orders", []) if o.get("reference") == ref), None)
            if not order:
                return self._json({"ok": False, "error": "Order tidak dijumpai"}, 404)
            total = float(order.get("total", 0) or 0)
            if total <= 0:
                return self._json({"ok": False, "error": "Jumlah tidak sah"}, 400)
            host = self.headers.get("Host", f"localhost:{PORT}")
            is_local = ("localhost" in host or "127.0.0.1" in host)
            scheme = "http" if is_local else "https"
            success_redirect = f"{scheme}://{host}/index.html?payment=return&ref={ref}&status=completed"
            failure_redirect = f"{scheme}://{host}/index.html?payment=return&ref={ref}&status=failed"
            callback_url = "" if is_local else f"{scheme}://{host}/api/chip-callback"
            result = chip_create_purchase(total, ref, success_redirect, failure_redirect, callback_url,
                                          order.get("name", ""), order.get("email", ""))
            if result["ok"]:
                order["chip_id"] = result.get("id")
                order["chip_url"] = result.get("url")
                save_data(d)
                return self._json({"ok": True, "url": result["url"]})
            return self._json({"ok": False, "error": result.get("error", "Gagal jana link")}, 502)

        if self.path.startswith("/api/orders/") and self.path.endswith("/ep-rates"):
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            ref = self.path[len("/api/orders/"):-len("/ep-rates")]
            d = load_data()
            order = next((o for o in d.get("orders", []) if o.get("reference") == ref), None)
            if not order:
                return self._json({"ok": False, "error": "Order tidak dijumpai"}, 404)
            if not self._can_access_order(order):
                return self._json({"ok": False, "error": "forbidden"}, 403)
            return self._json(ep_check_rates(order))

        if self.path.startswith("/api/orders/") and self.path.endswith("/ep-book"):
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            ref = self.path[len("/api/orders/"):-len("/ep-book")]
            b = self._read_body()
            service_id = str(b.get("service_id", "")).strip()
            if not service_id:
                return self._json({"ok": False, "error": "service_id diperlukan"}, 400)
            d = load_data()
            order = next((o for o in d.get("orders", []) if o.get("reference") == ref), None)
            if not order:
                return self._json({"ok": False, "error": "Order tidak dijumpai"}, 404)
            if not self._can_access_order(order):
                return self._json({"ok": False, "error": "forbidden"}, 403)
            res = ep_book(order, service_id)
            if res.get("ok"):
                order["easyparcel"] = {
                    "order_no":     res.get("order_no", ""),
                    "awb":          res.get("awb", ""),
                    "awb_link":     res.get("awb_link", ""),
                    "tracking_url": res.get("tracking_url", ""),
                    "courier":      res.get("courier", ""),
                    "cost":         res.get("cost", 0),
                    "service_id":   service_id,
                    "booked_at":    now_myt().strftime("%d %b %Y, %H:%M"),
                }
                save_data(d)
            return self._json(res)

        if self.path == "/api/orders/manual":
            # Tambah order manual (Telegram / Wedding-Crystal) — rekod sahaja, dah bayar luar
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            source = str(b.get("source", "manual")).strip()[:20] or "manual"
            name = str(b.get("name", "")).strip()[:120]
            if not name:
                return self._json({"ok": False, "error": "Nama pelanggan diperlukan"}, 400)
            raw_items = b.get("items", [])
            items, subtotal = [], 0.0
            if isinstance(raw_items, list):
                for it in raw_items:
                    nm = str(it.get("name", "")).strip()[:160]
                    if not nm:
                        continue
                    try:
                        qty = max(1, min(999, int(it.get("qty", 1))))
                    except (TypeError, ValueError):
                        qty = 1
                    try:
                        price = max(0.0, float(it.get("price", 0) or 0))
                    except (TypeError, ValueError):
                        price = 0.0
                    subtotal += price * qty
                    items.append({"name": nm, "qty": qty, "price": price})
            if not items:
                return self._json({"ok": False, "error": "Sekurang-kurangnya 1 item diperlukan"}, 400)
            d = load_data()
            ref = gen_order_ref({o.get("reference") for o in d.get("orders", [])})
            order = {
                "reference": ref, "source": source, "manual": True,
                "status": str(b.get("status", "completed")).strip()[:30] or "completed",
                "total": round(subtotal, 2), "subtotal": round(subtotal, 2),
                "discount": 0, "postage": 0, "voucher": "",
                "name": name,
                "email": str(b.get("email", "")).strip()[:254],
                "phone": str(b.get("phone", "")).strip()[:30],
                "alamat": str(b.get("alamat", "")).strip()[:300],
                "poskod": str(b.get("poskod", "")).strip()[:10],
                "bandar": str(b.get("bandar", "")).strip()[:120],
                "negeri": str(b.get("negeri", "")).strip()[:120],
                "medium": str(b.get("medium", "")).strip()[:40],
                "note": str(b.get("note", "")).strip()[:500],
                "editor": "—",
                "created_at": now_myt().strftime("%d %b %Y, %H:%M"),
                "created_ts": now_myt().isoformat(timespec="seconds"),
                "items": items,
            }
            d.setdefault("orders", []).append(order)
            save_data(d)
            return self._json({"ok": True, "reference": ref})

        if self.path == "/api/chip-callback":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = {}
            purchase_id = payload.get("id", "")
            # SECURITY: jangan percaya body — sahkan dengan re-fetch guna secret key
            verified = chip_get_purchase(purchase_id) if purchase_id else None
            if verified and verified.get("status") == "paid":
                ref = verified.get("reference", "")
                d = load_data()
                for order in d.get("orders", []):
                    if order.get("reference") == ref:
                        _was_completed = (order.get("status") == "completed")
                        order["status"]     = "completed"
                        order["payment_id"] = str(purchase_id)
                        # Notifikasi email — sekali sahaja (bila pertama kali jadi paid)
                        if not _was_completed and not order.get("notified"):
                            order["notified"] = True
                            notify_new_order(order)
                        break
                save_data(d)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/api/orders/"):
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            ref = self.path[len("/api/orders/"):]
            d = load_data()
            before = len(d.get("orders", []))
            d["orders"] = [o for o in d.get("orders", []) if o.get("reference") != ref]
            save_data(d)
            return self._json({"ok": before != len(d["orders"])})
        return self._json({"error": "not found"}, 404)

    def do_PUT(self):
        if self.path == "/api/account":
            sess = self._user()
            if not sess:
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            for u in d.get("users", []):
                if u.get("email") == sess["email"]:
                    if b.get("name","").strip():
                        u["name"] = b["name"].strip()
                    if b.get("password","").strip():
                        u["password"] = hash_password(b["password"].strip())
                    # update session name
                    tok = self.headers.get("Authorization","").replace("Bearer ","").strip()
                    if tok and tok in SESSIONS:
                        SESSIONS[tok]["name"] = u["name"]
                    save_data(d)
                    return self._json({"ok": True, "name": u["name"]})
            return self._json({"ok": False, "error": "user not found"}, 404)

        if self.path == "/api/postage":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            e = b.get("east", {}) or {}
            d["postage"] = {
                "base": float(b.get("base", 8)),
                "threshold": float(b.get("threshold", 1.5)),
                "per_kg": float(b.get("per_kg", 2)),
                "east": {
                    "base": float(e.get("base", 15)),
                    "threshold": float(e.get("threshold", 1.0)),
                    "per_kg": float(e.get("per_kg", 10)),
                },
            }
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/easyparcel-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            ep = d.get("easyparcel", {}) or {}
            cid = str(b.get("client_id", "")).strip()
            if cid:
                ep["client_id"] = cid[:120]
            csec = str(b.get("client_secret", "")).strip()
            if csec and "..." not in csec:
                ep["client_secret"] = csec[:200]
            ep["pick_name"]    = str(b.get("pick_name", ep.get("pick_name", "")))[:120]
            ep["pick_contact"] = str(b.get("pick_contact", ep.get("pick_contact", "")))[:30]
            ep["pick_email"]   = str(b.get("pick_email", ep.get("pick_email", "")))[:120]
            ep["pick_addr1"]   = str(b.get("pick_addr1", ep.get("pick_addr1", "")))[:300]
            ep["pick_addr2"]   = str(b.get("pick_addr2", ep.get("pick_addr2", "")))[:300]
            ep["pick_city"]    = str(b.get("pick_city", ep.get("pick_city", "")))[:120]
            ep["pick_code"]    = str(b.get("pick_code", ep.get("pick_code", "")))[:10]
            ep["pick_state"]   = str(b.get("pick_state", ep.get("pick_state", "")))[:120]
            ep["content"]      = str(b.get("content", ep.get("content", "Photobook")))[:120]
            for k in ("width", "length", "height"):
                try:
                    ep[k] = int(float(b.get(k, ep.get(k, 0))))
                except (TypeError, ValueError):
                    pass
            d["easyparcel"] = ep
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/photo-reminder-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            pr = d.get("photo_reminder", {}) or {}
            pr["enabled"] = bool(b.get("enabled", False))
            try: pr["delay_hours"] = max(1, float(b.get("delay_hours", 24)))
            except (TypeError, ValueError): pr["delay_hours"] = 24
            try: pr["interval_hours"] = max(1, float(b.get("interval_hours", 48)))
            except (TypeError, ValueError): pr["interval_hours"] = 48
            try: pr["max"] = max(1, min(5, int(float(b.get("max", 2)))))
            except (TypeError, ValueError): pr["max"] = 2
            d["photo_reminder"] = pr
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/media-links":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            ml = d.get("media_links", {})
            for k in ("whatsapp", "telegram", "gdrive"):
                if k in b: ml[k] = b[k]
            d["media_links"] = ml
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/contact-links":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            cl = d.get("contact_links", {})
            for k in ("whatsapp", "email"):
                if k in b: cl[k] = b[k]
            d["contact_links"] = cl
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/hitpay-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            cfg = d.get("hitpay", {})
            if "api_key" in b and b["api_key"] and not b["api_key"].endswith("..."):
                cfg["api_key"] = b["api_key"]
            if "salt" in b and b["salt"] and not b["salt"].endswith("..."):
                cfg["salt"] = b["salt"]
            if "sandbox" in b:
                cfg["sandbox"] = bool(b["sandbox"])
            d["hitpay"] = cfg
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/chip-config":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            cfg = d.get("chip", {})
            if "secret_key" in b and b["secret_key"] and not b["secret_key"].endswith("..."):
                cfg["secret_key"] = b["secret_key"].strip()
            if "brand_id" in b:
                cfg["brand_id"] = b["brand_id"].strip()
            d["chip"] = cfg
            if "gateway" in b and b["gateway"] in ("chip", "hitpay"):
                d["payment_gateway"] = b["gateway"]
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/tracking":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            t = d.get("tracking", {}) or {}
            if "meta_pixel_id" in b:
                t["meta_pixel_id"] = str(b["meta_pixel_id"]).strip()
            if "ga4_id" in b:
                t["ga4_id"] = str(b["ga4_id"]).strip()
            if "tiktok_pixel_id" in b:
                t["tiktok_pixel_id"] = str(b["tiktok_pixel_id"]).strip()
            d["tracking"] = t
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/editors":
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            new_editors = self._read_body()
            if not isinstance(new_editors, list):
                return self._json({"ok": False, "error": "format tidak sah"}, 400)
            d = load_data()
            # Sesi editor sedia ada (untuk kekalkan kata laluan)
            editor_users = {str(u.get("email", "")).lower(): u
                            for u in d.get("users", []) if u.get("role") == "editor"}
            cleaned = []
            for ed in new_editors:
                email = str(ed.get("email", "")).strip()
                pw    = str(ed.get("password", "")).strip()
                # Rekod editor disimpan TANPA kata laluan
                cleaned.append({
                    "id":    ed.get("id"),
                    "name":  ed.get("name", ""),
                    "email": email,
                    "color": ed.get("color", ""),
                })
                if not email:
                    continue
                key = email.lower()
                if key in editor_users:
                    u = editor_users[key]
                    u["name"] = ed.get("name", u.get("name"))
                    if pw:  # tukar kata laluan hanya jika diberi
                        u["password"] = hash_password(pw)
                else:
                    # Akaun login editor baharu (role: editor)
                    d.setdefault("users", []).append({
                        "email":    email,
                        "name":     ed.get("name", ""),
                        "role":     "editor",
                        "password": hash_password(pw) if pw else hash_password(secrets.token_hex(12)),
                    })
            # Buang akaun editor yang tiada lagi dalam senarai
            keep = {str(ed.get("email", "")).strip().lower()
                    for ed in new_editors if ed.get("email")}
            d["users"] = [u for u in d.get("users", [])
                          if u.get("role") != "editor"
                          or str(u.get("email", "")).lower() in keep]
            d["editors"] = cleaned
            save_data(d)
            return self._json({"ok": True})
        if self.path.startswith("/api/orders/"):
            u = self._user()
            if not u:
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            ref = self.path[len("/api/orders/"):]
            b = self._read_body()
            d = load_data()
            for order in d.get("orders", []):
                if order.get("reference") == ref:
                    if u.get("role") == "editor":
                        # Editor hanya boleh kemaskini status order miliknya sendiri
                        first = u["name"].split(" ")[0]
                        if order.get("editor") not in (first, u["name"]):
                            return self._json({"ok": False, "error": "forbidden"}, 403)
                        if "status" in b: order["status"] = b["status"]
                    else:
                        if "editor" in b: order["editor"] = b["editor"]
                        if "status" in b: order["status"] = b["status"]
                    break
            save_data(d)
            return self._json({"ok": True})
        if self.path.startswith("/api/data-requests/"):
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            rid = self.path[len("/api/data-requests/"):]
            b = self._read_body()
            d = load_data()
            for r in d.get("data_requests", []):
                if r.get("id") == rid:
                    if "status" in b: r["status"] = str(b["status"])[:20]
                    break
            save_data(d)
            return self._json({"ok": True})
        key_map = {"/api/flow": "flow", "/api/packages": "packages",
                   "/api/categories": "categoryOrder", "/api/vouchers": "vouchers",
                   "/api/reviews": "reviews", "/api/announcements": "announcements",
                   "/api/explore": "explore", "/api/va-faq": "va_faq"}
        if self.path in key_map:
            if not self._is_admin():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            d = load_data()
            d[key_map[self.path]] = self._read_body()
            save_data(d)
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass  # senyapkan log konsol


if __name__ == "__main__":
    # SECURITY: Amaran jika kredential / secret default masih digunakan
    _d = load_data()
    # RESET admin sekali guna: set ADMIN_RESET=1 + ADMIN_PASSWORD di env, deploy, login,
    # kemudian BUANG ADMIN_RESET & deploy semula supaya ia tak reset setiap kali start.
    if os.environ.get("ADMIN_RESET", "").strip().lower() in ("1", "true", "yes"):
        _email = os.environ.get("ADMIN_EMAIL", "admin@pixyoprint.com")
        _newpw = os.environ.get("ADMIN_PASSWORD", "admin123")
        _u = next((u for u in _d.get("users", [])
                   if u.get("role") == "admin" or u.get("email") == _email), None)
        if _u:
            _u["email"] = _email
            _u["password"] = hash_password(_newpw)
        else:
            _d.setdefault("users", []).append({"email": _email, "password": hash_password(_newpw),
                                                "name": "Admin", "role": "admin"})
        save_data(_d)
        print("ADMIN_RESET: kata laluan admin telah diset semula ikut ADMIN_PASSWORD.")
    _admin = next((u for u in _d.get("users", []) if u.get("role") == "admin"), None)
    if _admin and verify_password(_admin.get("password", ""), "admin123"):
        print("AMARAN: Kata laluan admin masih 'admin123' (default).")
        print("   Tukar segera sebelum guna untuk produksi — set ADMIN_PASSWORD env var")
        print("   atau kemaskini dalam data.json.")
    if not _d.get("hitpay", {}).get("api_key"):
        print("ℹ  HitPay belum dikonfigurasi — set via panel admin atau HITPAY_API_KEY env var.")

    # PDPA s.10: padam order lama waktu mula, kemudian setiap 24 jam.
    run_retention_purge()
    import threading
    def _retention_loop():
        while True:
            time.sleep(86400)  # 24 jam
            try:
                run_retention_purge()
            except Exception as e:
                print(f"[Retention] ralat: {e}")
    threading.Thread(target=_retention_loop, daemon=True).start()
    threading.Thread(target=photo_reminder_loop, daemon=True).start()

    print(f"PixyoPrint server di http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
