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
import urllib.request
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HITPAY_CURRENCY = "MYR"

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
ALLOWED_EXT = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}
MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB
PORT = 3000

PKG_NOTE_DEFAULT = "disusun manual, 2x semakan percuma, siap dalam 7 hari bekerja"
PKG_FIELD_DEFAULTS = {
    "category": "", "note": PKG_NOTE_DEFAULT,
    "material": "Art Matte", "size": "12\"x12\"", "pages": "40 muka surat",
}

DEFAULT_DATA = {
    "editors": [],
    "hitpay": {
        "api_key": "test_b483fc92015565bc2915478a63d179da7211e7d811a007c34793478ca689356a",
        "salt":    "6ZFVtkRAi9CWkMJx2ydw3ddTx0gR2GUiOFddFbsukKBK7eqPVXA5fl15C42MXbMp",
        "sandbox": True,
    },
    "media_links": {
        "whatsapp": "",
        "telegram": "",
        "gdrive":   "",
    },
    "vouchers": [],
    "users": [
        {"email": "admin@pixyoprint.com", "password": "admin123",
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
    if "media_links" not in data:
        data["media_links"] = DEFAULT_DATA["media_links"].copy()
        changed = True
    if "hitpay" not in data:
        data["hitpay"] = DEFAULT_DATA["hitpay"].copy()
        changed = True
    if "editors" not in data:
        data["editors"] = []
        changed = True
    if changed:
        save_data(data)
    return data


def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

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
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            return SESSIONS.get(h[7:])
        return None

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
        if self.path == "/api/me":
            u = self._user()
            return self._json({"ok": bool(u), "name": u})
        if self.path == "/api/media-links":
            return self._json(load_data().get("media_links", {}))
        if self.path == "/api/hitpay-config":
            if not self._user():
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
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            orders = load_data().get("orders", [])
            return self._json({"ok": True, "orders": list(reversed(orders))})
        if self.path == "/api/editors":
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            return self._json({"ok": True, "editors": load_data().get("editors", [])})

        if self.path.startswith("/api/order-status"):
            ref = self.path.split("ref=")[-1] if "ref=" in self.path else ""
            orders = load_data().get("orders", [])
            order = next((o for o in orders if o.get("reference") == ref), None)
            return self._json(order or {"status": "not_found"})
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/login":
            b = self._read_body()
            data = load_data()
            for u in data.get("users", []):
                if u["email"] == b.get("email") and u["password"] == b.get("password"):
                    token = secrets.token_hex(16)
                    SESSIONS[token] = u["name"]
                    return self._json({"ok": True, "token": token,
                                       "name": u["name"], "role": u.get("role")})
            return self._json({"ok": False,
                               "error": "Email atau kata laluan salah"}, 401)
        if self.path == "/api/logout":
            h = self.headers.get("Authorization", "")
            if h.startswith("Bearer "):
                SESSIONS.pop(h[7:], None)
            return self._json({"ok": True})
        if self.path == "/api/upload":
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return self._json({"ok": False, "error": "Fail kosong"}, 400)
            if length > MAX_UPLOAD:
                return self._json({"ok": False,
                                   "error": "Fail terlalu besar (maks 200MB)"}, 413)
            raw_name = self.headers.get("X-Filename", "video")
            ext = os.path.splitext(raw_name)[1].lower()
            if ext not in ALLOWED_EXT:
                return self._json({"ok": False,
                                   "error": "Format tidak disokong"}, 415)
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            fname = secrets.token_hex(8) + ext
            with open(os.path.join(UPLOAD_DIR, fname), "wb") as out:
                out.write(self.rfile.read(length))
            return self._json({"ok": True, "url": "uploads/" + fname,
                               "name": raw_name})
        if self.path == "/api/pay":
            b = self._read_body()
            total   = b.get("total", 0)
            name    = b.get("name", "")
            email   = b.get("email", "")
            items   = b.get("items", [])
            if not total or float(total) <= 0:
                return self._json({"ok": False, "error": "Jumlah tidak sah"}, 400)

            reference = secrets.token_hex(8).upper()
            # Simpan order dengan status 'pending'
            d = load_data()
            d["orders"].append({
                "reference":  reference,
                "status":     "pending",
                "total":      total,
                "name":       name,
                "email":      email,
                "created_at": datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
                "items":     items,
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
        if self.path == "/api/media-links":
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            b = self._read_body()
            d = load_data()
            ml = d.get("media_links", {})
            for k in ("whatsapp", "telegram", "gdrive"):
                if k in b: ml[k] = b[k]
            d["media_links"] = ml
            save_data(d)
            return self._json({"ok": True})
        if self.path == "/api/hitpay-config":
            if not self._user():
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
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            d = load_data()
            d["editors"] = self._read_body()
            save_data(d)
            return self._json({"ok": True})
        if self.path.startswith("/api/orders/"):
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            ref = self.path[len("/api/orders/"):]
            b = self._read_body()
            d = load_data()
            for order in d.get("orders", []):
                if order.get("reference") == ref:
                    if "editor" in b: order["editor"] = b["editor"]
                    if "status" in b: order["status"] = b["status"]
                    break
            save_data(d)
            return self._json({"ok": True})
        key_map = {"/api/flow": "flow", "/api/packages": "packages",
                   "/api/categories": "categoryOrder", "/api/vouchers": "vouchers"}
        if self.path in key_map:
            if not self._user():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            d = load_data()
            d[key_map[self.path]] = self._read_body()
            save_data(d)
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass  # senyapkan log konsol


if __name__ == "__main__":
    print(f"PixyoPrint server di http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
