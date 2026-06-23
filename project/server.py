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
import hashlib
import hmac as hmac_lib
import time
import urllib.request
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HITPAY_CURRENCY = "MYR"

# ============================================================
# SECURITY: Password hashing (PBKDF2-SHA256)
# Mencegah: kata laluan bocor jika data.json terdedah
# ============================================================
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
DATA_FILE = os.path.join(BASE, "data.json")
UPLOAD_DIR = os.path.join(BASE, "uploads")
ALLOWED_EXT = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv",
               ".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB
PORT = 3000

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
        {"name": "Design Sahaja",  "desc": "Susun layout digital sahaja",
         "category": "Digital", "note": PKG_NOTE_DEFAULT,
         "material": "Art Matte", "size": "12\"x12\"",
         "price": "280", "strike": "320", "pages": "40 muka surat", "orders": 12},
        {"name": "Design + Cetak", "desc": "Layout + album bercetak 12\"x12\"",
         "category": "Cetak & Album", "note": PKG_NOTE_DEFAULT,
         "material": "Art Matte", "size": "12\"x12\"",
         "price": "680", "strike": "780", "pages": "40 muka surat", "orders": 18},
        {"name": "Pakej Penuh",    "desc": "Design + Cetak + Penghantaran",
         "category": "Cetak & Album", "note": PKG_NOTE_DEFAULT,
         "material": "Art Matte", "size": "12\"x12\"",
         "price": "880", "strike": "990", "pages": "40 muka surat", "orders": 8},
    ],
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


def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
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
    return (datetime.datetime.now() - dt).days

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
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
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

    # --- routes ----------------------------------------------------
    def do_GET(self):
        if self.path == "/api/flow":
            return self._json(load_data().get("flow", []))
        if self.path == "/api/packages":
            return self._json(load_data().get("packages", []))
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
                "created_at": datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
                "created_ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
            save_data(d)
            return self._json({"ok": True})

        if self.path == "/api/pay":
            b = self._read_body()
            name    = str(b.get("name", ""))[:120]
            email   = str(b.get("email", ""))[:254]
            phone   = str(b.get("phone", ""))[:30]
            medium  = str(b.get("medium", ""))[:40]
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
            server_items = []
            subtotal = 0.0
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

            total = round(max(0.0, subtotal - discount), 2)
            if total <= 0:
                return self._json({"ok": False, "error": "Jumlah tidak sah"}, 400)

            reference = secrets.token_hex(8).upper()
            # Simpan order dengan status 'pending'
            d["orders"].append({
                "reference":  reference,
                "status":     "pending",
                "total":      total,
                "subtotal":   round(subtotal, 2),
                "discount":   round(discount, 2),
                "voucher":    applied_voucher,
                "name":       name,
                "email":      email,
                "phone":      phone,
                "medium":     medium,
                "created_at": datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
                "created_ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "items":      server_items,
                # Rekod persetujuan PDPA (s.7/s.40 — bukti kebenaran)
                "consent": {
                    "given":     True,
                    "marketing": consent_marketing,
                    "version":   consent_version,
                    "at":        datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
                },
            })
            save_data(d)

            # Detect host untuk redirect & webhook URL
            host = self.headers.get("Host", f"localhost:{PORT}")
            scheme = "http"
            redirect_url = f"{scheme}://{host}/index.html?payment=return&ref={reference}"
            # Webhook tidak boleh pakai localhost — skip untuk development
            webhook_url  = "" if ("localhost" in host or "127.0.0.1" in host) else f"{scheme}://{host}/api/hitpay-webhook"

            result = hitpay_create_payment(total, reference, redirect_url, webhook_url, name, email)
            if result["ok"]:
                return self._json({"ok": True, "url": result["url"], "reference": reference})
            else:
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

    print(f"PixyoPrint server di http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
