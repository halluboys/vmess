# -*- coding: utf-8 -*-
"""
SUNSET Telegram Bot (Simple Multi-User)

Perubahan utama vs versi simple:
- Tidak ada "nomor aktif global" saat /start. Tiap user Telegram punya akun aktif sendiri.
- Mapping chat_id -> number disimpan di file local: tg-users.json
- Saat login:
  - user input nomor (628xxxx)
  - kalau nomor sudah ada di refresh-tokens.json -> set aktif ke nomor itu (per user) & selesai
  - kalau belum -> request OTP -> input OTP -> simpan refresh token -> set aktif (per user)
- Semua menu 2/3/4 menggunakan nomor aktif milik user Telegram tersebut.

Run:
  cd sunset
  pip install -r requirements.txt
  pip install python-telegram-bot==20.7 python-dotenv
  export TELEGRAM_TOKEN="xxxx"
  python sunset_telegram_bot_simple_multiuser.py
"""
import os
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

from app.service.auth import AuthInstance
from app.client.engsel import (
    send_api_request,
    get_balance,
    get_tiering_info,
    get_family,
    get_package,
)
from app.client.ciam import get_otp, submit_otp
from app.menus.util import format_quota_byte, display_html
from app.type_dict import PaymentItem
from app.client.purchase.balance import settlement_balance
from app.service.decoy import DecoyInstance

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN env var belum di-set")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("sunset_telegram_multiuser")

USER_DB_PATH = os.getenv("TG_USER_DB", "tg-users.json")

# --- Admin / VIP Access Control ---
# Set ADMIN_IDS env to comma-separated Telegram user IDs, e.g. "876081450,12345"
ADMIN_IDS = set()
try:
    _raw_admin = (os.getenv("ADMIN_IDS") or "").strip()
    if _raw_admin:
        ADMIN_IDS = {int(x.strip()) for x in _raw_admin.split(",") if x.strip().isdigit()}
except Exception:
    ADMIN_IDS = set()

def _is_admin(chat_id: int, user_id: int) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False

def _now_ts() -> int:
    return int(datetime.now().timestamp())

def _vip_remaining_seconds(chat_id: int) -> int:
    db = _load_user_db()
    rec = (db.get("users", {}) or {}).get(str(chat_id)) or {}
    if not isinstance(rec, dict):
        return 0
    exp = int(rec.get("expires_at") or 0)
    return max(0, exp - _now_ts())

def _has_vip(chat_id: int) -> bool:
    return _vip_remaining_seconds(chat_id) > 0

def _grant_vip(chat_id: int, days: int) -> int:
    db = _load_user_db()
    db.setdefault("users", {})
    key = str(chat_id)
    rec = db["users"].get(key) or {}
    if not isinstance(rec, dict):
        rec = {}
    current_exp = int(rec.get("expires_at") or 0)
    base = current_exp if current_exp > _now_ts() else _now_ts()
    expires_at = base + int(days) * 86400
    rec["expires_at"] = expires_at
    rec["updated_at"] = _now_ts()
    db["users"][key] = rec
    _save_user_db(db)
    return expires_at

def _revoke_vip(chat_id: int) -> None:
    db = _load_user_db()
    db.setdefault("users", {})
    key = str(chat_id)
    rec = db["users"].get(key) or {}
    if not isinstance(rec, dict):
        rec = {}
    rec["expires_at"] = 0
    rec["updated_at"] = _now_ts()
    db["users"][key] = rec
    _save_user_db(db)

# -------------------------
# Tiny file DB (chat_id -> number)
# -------------------------
def _load_user_db() -> Dict[str, Any]:
    try:
        if os.path.exists(USER_DB_PATH):
            return json.load(open(USER_DB_PATH, "r", encoding="utf-8"))
    except Exception:
        pass
    return {"users": {}}

def _save_user_db(db: Dict[str, Any]) -> None:
    tmp = USER_DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USER_DB_PATH)

def _get_user_number(chat_id: int) -> Optional[int]:
    db = _load_user_db()
    u = db.get("users", {}).get(str(chat_id))
    if isinstance(u, dict):
        n = u.get("number")
        if n is not None:
            try:
                return int(n)
            except Exception:
                return None
    return None

def _touch_user(update: Update) -> None:
    """Persist basic Telegram user info for admin listing."""
    try:
        chat_id = int(update.effective_chat.id)
        u = update.effective_user
        user_id = int(u.id) if u else 0
        username = (u.username or "") if u else ""
        full_name = (u.full_name or "") if u else ""
        db = _load_user_db()
        db.setdefault("users", {})
        rec = db["users"].get(str(chat_id)) or {}
        if not isinstance(rec, dict):
            rec = {}
        rec["tg_user_id"] = user_id
        rec["tg_username"] = username
        rec["tg_name"] = full_name
        rec["last_seen"] = int(datetime.now().timestamp())
        # keep existing keys (number, expires_at)
        db["users"][str(chat_id)] = rec
        _save_user_db(db)
    except Exception:
        pass

async def _send_id_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    chat_id = int(chat.id) if chat else 0
    user_id = int(user.id) if user else 0
    username = (user.username or "").strip() if user else ""
    full_name = (user.full_name or "").strip() if user else ""
    number = _get_user_number(chat_id)

    if not ADMIN_IDS:
        if update.callback_query:
            await update.callback_query.message.edit_text("⚠️ Admin belum dikonfigurasi.", reply_markup=_back_kb())
        else:
            await update.message.reply_text("⚠️ Admin belum dikonfigurasi.", reply_markup=_back_kb())
        return

    msg = (
        "📩 *Permintaan Aktivasi VIP*\n"
        f"- chat_id: `{chat_id}`\n"
        f"- user_id: `{user_id}`\n"
        f"- username: @{username if username else '-'}\n"
        f"- nama: {full_name if full_name else '-'}\n"
        f"- nomor aktif: `{number if number else '-'}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=msg, parse_mode="Markdown")
        except Exception:
            pass

    confirm = "✅ ID kamu sudah dikirim ke admin. Tunggu aktivasi VIP ya."
    if update.callback_query:
        await update.callback_query.message.edit_text(confirm, reply_markup=_back_kb())
    else:
        await update.message.reply_text(confirm, reply_markup=_back_kb())


def _set_user_number(chat_id: int, number: int) -> None:
    db = _load_user_db()
    db.setdefault("users", {})
    key = str(chat_id)
    rec = db["users"].get(key) or {}
    if not isinstance(rec, dict):
        rec = {}
    rec["number"] = int(number)
    rec["updated_at"] = int(datetime.now().timestamp())
    # preserve VIP expiry if exists
    if "expires_at" in rec:
        try:
            rec["expires_at"] = int(rec.get("expires_at") or 0)
        except Exception:
            rec["expires_at"] = 0
    db["users"][key] = rec
    _save_user_db(db)

# -------------------------
# Helpers UI
# -------------------------
def _menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("1️⃣ Login", callback_data="m:login")],
        [InlineKeyboardButton("2️⃣ Cek Paket", callback_data="m:packages")],
        [InlineKeyboardButton("3️⃣ eSIM 5GB", callback_data="m:esim5")],
        [InlineKeyboardButton("4️⃣ eSIM 10GB", callback_data="m:esim10")],
        [InlineKeyboardButton("5️⃣ eSIM 10GB v2", callback_data="m:esim10v2")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("👮 Users", callback_data="m:users")])
    else:
        rows.append([InlineKeyboardButton("📩 Kirim ID saya ke admin", callback_data="m:sendid")])
    return InlineKeyboardMarkup(rows)

def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="m:home")]])

async def _send_long(msg, text: str, *, parse_mode: Optional[str] = None, reply_markup=None):
    if not text:
        text = "✅ Selesai."
    while text:
        chunk = text[:3800]
        text = text[3800:]
        await msg.reply_text(chunk, parse_mode=parse_mode, reply_markup=reply_markup if not text else None)

def _money(v: Any) -> str:
    try:
        return f"{int(v):,}".replace(",", ".")
    except Exception:
        return str(v)

def _is_number_registered(number: int) -> bool:
    try:
        AuthInstance.load_tokens()
    except Exception:
        pass
    for rt in getattr(AuthInstance, "refresh_tokens", []) or []:
        try:
            if int(rt.get("number")) == int(number):
                return True
        except Exception:
            continue
    try:
        if os.path.exists("refresh-tokens.json"):
            data = json.load(open("refresh-tokens.json","r",encoding="utf-8"))
            if isinstance(data, list):
                return any(int(x.get("number")) == int(number) for x in data if isinstance(x, dict) and x.get("number") is not None)
    except Exception:
        pass
    return False

def _activate_for_user(chat_id: int, number: int) -> None:
    """
    Set active user in AuthInstance for THIS REQUEST, and remember mapping for this Telegram user.
    Note: AuthInstance is global, so we ALWAYS set it right before doing an action (menu 2/3/4),
    using the stored mapping, to avoid cross-user confusion.
    """
    AuthInstance.set_active_user(int(number))
    _set_user_number(chat_id, int(number))

def _ensure_active(chat_id: int) -> Tuple[bool, Optional[int], Optional[str]]:
    """Make sure this telegram user has an active number and set it into AuthInstance."""
    n = _get_user_number(chat_id)
    if not n:
        return False, None, "🔐 Kamu belum set nomor aktif.\nPilih *1️⃣ Login* dulu."
    try:
        AuthInstance.set_active_user(int(n))
        return True, int(n), None
    except Exception as e:
        return False, None, f"❌ Gagal mengaktifkan nomor `{n}`: {e}"

def _tokens() -> Optional[Dict[str, str]]:
    try:
        return AuthInstance.get_active_tokens()
    except Exception:
        u = AuthInstance.get_active_user()
        return u.get("tokens") if u else None

def _header(chat_id: int) -> str:
    ok, n, err = _ensure_active(chat_id)
    if not ok:
        return "🔐 *Belum ada nomor aktif*\nSilakan pilih *1️⃣ Login*."
    u = AuthInstance.get_active_user()
    try:
        bal = get_balance(AuthInstance.api_key, u["tokens"]["id_token"])
        remaining = bal.get("remaining", "N/A")
        expired_at = bal.get("expired_at", 0)
        expired_at_dt = datetime.fromtimestamp(expired_at).strftime("%Y-%m-%d") if expired_at else "N/A"
    except Exception:
        remaining, expired_at_dt = "N/A", "N/A"
    point_info = "Points: N/A | Tier: N/A"
    try:
        if u.get("subscription_type") == "PREPAID":
            td = get_tiering_info(AuthInstance.api_key, u["tokens"])
            point_info = f"Points: {td.get('current_point',0)} | Tier: {td.get('tier',0)}"
    except Exception:
        pass
    return (
        "📱 *SUNSET XL BOT*\n"
        f"📞 *Nomor aktif:* `{u.get('number')}` | *Type:* `{u.get('subscription_type')}`\n"
        f"💰 *Pulsa:* Rp {_money(remaining)} | *Aktif s/d:* `{expired_at_dt}`\n"
        f"⭐ *{point_info}*\n"
        f"🎟️ *VIP:* {('AKTIF (sisa ' + str((_vip_remaining_seconds(chat_id)+86399)//86400) + ' hari)') if _has_vip(chat_id) else 'TIDAK AKTIF'}"
    )

async def _show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False):
    chat_id = update.effective_chat.id
    text = _header(chat_id) + "\n\nPilih menu:"
    if update.callback_query and edit:
        await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=_menu_kb(is_admin=_is_admin(chat_id, update.effective_user.id)))
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=_menu_kb(is_admin=_is_admin(chat_id, update.effective_user.id)))

# -------------------------
# Purchase helpers
# -------------------------
def _pick_option_code_from_family(fam: Dict[str, Any], target_no: int = 1) -> Optional[Dict[str, Any]]:
    n = 1
    for v in fam.get("package_variants", []):
        for opt in v.get("package_options", []):
            if n == target_no:
                return {
                    "option_code": opt.get("package_option_code"),
                    "name": opt.get("name",""),
                    "price": int(opt.get("price",0) or 0),
                }
            n += 1
    return None

def _pay_pulsa_decoy_v2(tokens: Dict[str, str], option_code: str, name: str, price: int, token_confirmation: str) -> Dict[str, Any]:
    items: List[PaymentItem] = [{
        "item_code": option_code,
        "product_type": "PACKAGE",
        "item_price": price,
        "item_name": name,
        "tax": 0,
        "token_confirmation": token_confirmation,
    }]

    decoy = DecoyInstance.get_decoy("balance")
    if not decoy:
        return {"ok": False, "detail": "Decoy config tidak ditemukan."}

    decoy_detail = get_package(AuthInstance.api_key, tokens, decoy["option_code"])
    if not decoy_detail:
        return {"ok": False, "detail": "Gagal load detail paket decoy."}

    decoy_price = int((decoy_detail.get("package_option") or {}).get("price", 0) or 0)
    decoy_name = (decoy_detail.get("package_option") or {}).get("name", "Decoy")
    decoy_code = (decoy_detail.get("package_option") or {}).get("package_option_code", decoy.get("option_code"))

    items2: List[PaymentItem] = list(items)
    items2.append({
        "item_code": decoy_code,
        "product_type": "",
        "item_price": decoy_price,
        "item_name": decoy_name,
        "tax": 0,
        "token_confirmation": decoy_detail.get("token_confirmation", ""),
    })

    overwrite_amount = price + decoy_price

    res = settlement_balance(
        AuthInstance.api_key,
        tokens,
        items2,
        "🤫",
        ask_overwrite=False,
        overwrite_amount=overwrite_amount,
        token_confirmation_idx=1
    )

    if isinstance(res, dict) and res.get("status") != "SUCCESS":
        msg = res.get("message", "") or ""
        if "Bizz-err.Amount.Total" in msg:
            try:
                valid_amount = int(msg.split("=")[1].strip())
                res2 = settlement_balance(
                    AuthInstance.api_key,
                    tokens,
                    items2,
                    "🤫",
                    ask_overwrite=False,
                    overwrite_amount=valid_amount,
                    token_confirmation_idx=-1
                )
                if not (isinstance(res2, dict) and res2.get("status") != "SUCCESS"):
                    return {"ok": True, "detail": "Adjusted OK"}
                return {"ok": False, "detail": res2}
            except Exception:
                return {"ok": False, "detail": res}
        return {"ok": False, "detail": res}

    return {"ok": True, "detail": "OK"}

# -------------------------
# Handlers
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _touch_user(update)
    await _show_menu(update, context, edit=False)

async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _touch_user(update)
    q = update.callback_query
    await q.answer()
    chat_id = update.effective_chat.id
    data = q.data

    if data == "m:home":
        await _show_menu(update, context, edit=True)
        return

    # User: kirim ID ke admin
    if data == "m:sendid":
        await _send_id_to_admin(update, context)
        return

    # Admin: Users
    if data == "m:users":
        if not _is_admin(chat_id, update.effective_user.id):
            await q.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
            return

        db = _load_user_db()
        users = db.get("users", {}) or {}
        if not users:
            await q.message.edit_text("👮 *Daftar user bot*\n(kosong)", parse_mode="Markdown", reply_markup=_back_kb())
            return

        lines = ["👮 *Daftar user bot*"]
        count = 0
        for cid, rec in users.items():
            if not isinstance(rec, dict):
                continue
            num = rec.get("number", "-")
            uname = rec.get("tg_username") or "-"
            uid = rec.get("tg_user_id") or "-"
            exp = int(rec.get("expires_at") or 0)
            rem = max(0, exp - _now_ts())
            days_left = (rem + 86399)//86400 if rem > 0 else 0
            vip = "OFF" if days_left <= 0 else f"AKTIF ({days_left} hari)"
            lines.append(f"- chat_id: `{cid}` | user_id: `{uid}` | @{uname} | nomor: `{num}` | VIP: *{vip}*")
            count += 1
            if count >= 300:
                break

        await _send_long(q.message, "\n".join(lines), parse_mode="Markdown", reply_markup=_back_kb())
        return

    # 1) Login
    if data == "m:login":
        context.user_data["state"] = "wait_msisdn"
        await q.message.edit_text(
            "📲 *Login*\n\nKirim nomor format `628xxxx`.\n\nKetik `batal` untuk membatalkan.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        return

    # All other menus require per-user active number
    ok, n, err = _ensure_active(chat_id)
    if not ok:
        await q.message.edit_text(err, parse_mode="Markdown", reply_markup=_back_kb())
        return

    # 2) Cek paket (FULL)
    if data == "m:packages":
        await q.message.edit_text("🔄 Mengambil seluruh paket/quota...", reply_markup=_back_kb())
        try:
            tokens = _tokens()
            path = "api/v8/packages/quota-details"
            payload = {"is_enterprise": False, "lang": "en", "family_member_id": ""}
            res = send_api_request(AuthInstance.api_key, path, payload, tokens["id_token"], "POST")
            if res.get("status") != "SUCCESS":
                await _send_long(q.message, f"❌ Gagal ambil paket.\n```json\n{json.dumps(res, indent=2)[:3000]}\n```", parse_mode="Markdown", reply_markup=_back_kb())
                return

            quotas = res.get("data", {}).get("quotas", []) or []
            lines = [_header(chat_id), "", "📦 *My Packages (Full)*"]
            if not quotas:
                lines.append("📭 Tidak ada data quota.")
                await _send_long(q.message, "\n".join(lines), parse_mode="Markdown", reply_markup=_back_kb())
                return

            for i, qt in enumerate(quotas, start=1):
                group = qt.get("group_name","")
                name = qt.get("name","")
                benefits = qt.get("benefits") or []
                if benefits:
                    b0 = benefits[0]
                    rem = b0.get("remaining")
                    tot = b0.get("total")
                    rems = format_quota_byte(rem) if isinstance(rem,(int,float)) else str(rem)
                    tots = format_quota_byte(tot) if isinstance(tot,(int,float)) else str(tot)
                    lines.append(f"{i}. *{group}* - {name} — {rems}/{tots}")
                else:
                    lines.append(f"{i}. *{group}* - {name}")
            await _send_long(q.message, "\n".join(lines), parse_mode="Markdown", reply_markup=_back_kb())
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {type(e).__name__}: {e}", reply_markup=_back_kb())
        return

    async def _dor_esim(uuid: str, label: str, package_no: int = 1):
        await q.message.edit_text(f"⚡ Dor {label}: memproses...", reply_markup=_back_kb())
        try:
            tokens = _tokens()
            fam = get_family(AuthInstance.api_key, tokens, uuid, None, None)
            picked = _pick_option_code_from_family(fam, package_no)
            if not picked or not picked.get("option_code"):
                await q.message.reply_text("❌ Paket #1 tidak ditemukan.", reply_markup=_back_kb()); return
            pkg = get_package(AuthInstance.api_key, tokens, picked["option_code"])
            token_confirmation = pkg.get("token_confirmation","")
            if not token_confirmation:
                await q.message.reply_text("❌ token_confirmation tidak ditemukan.", reply_markup=_back_kb()); return
            pay = _pay_pulsa_decoy_v2(tokens, picked["option_code"], picked["name"] or (pkg.get("package_option") or {}).get("name",""), picked["price"], token_confirmation)
            if pay["ok"]:
                await q.message.reply_text(f"✅ *SUKSES* dor {label} (Decoy V2).", parse_mode="Markdown", reply_markup=_back_kb())
            else:
                detail = pay["detail"]
                body = json.dumps(detail, indent=2)[:3000] if isinstance(detail, dict) else str(detail)
                await _send_long(q.message, f"❌ Gagal dor {label}.\n```json\n{body}\n```", parse_mode="Markdown", reply_markup=_back_kb())
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {type(e).__name__}: {e}", reply_markup=_back_kb())

    # 3) eSIM 5GB
    if data == "m:esim5":
        if not _has_vip(chat_id):
            await q.message.edit_text("⛔ Fitur tembak paket hanya untuk VIP.\nKetik /vip untuk cek status.", reply_markup=_back_kb())
            return
        await _dor_esim("0d9d072c-3dab-4f72-88cd-31c5b8dc8c6b", "eSIM 5GB")
        return

    # 4) eSIM 10GB
    if data == "m:esim10":
        if not _has_vip(chat_id):
            await q.message.edit_text("⛔ Fitur tembak paket hanya untuk VIP.\nKetik /vip untuk cek status.", reply_markup=_back_kb())
            return
        await _dor_esim("162d261b-05c7-450c-9b7a-1873b160140c", "eSIM 10GB")
        return

    # 5) eSIM 10GB v2
    if data == "m:esim10v2":
        if not _has_vip(chat_id):
            await q.message.edit_text("⛔ Fitur tembak paket hanya untuk VIP.\nKetik /vip untuk cek status.", reply_markup=_back_kb())
            return
        await _dor_esim("162d261b-05c7-450c-9b7a-1873b160140c", "eSIM 10GB v2", 2)
        return

    await q.message.edit_text("❌ Menu tidak dikenali.", reply_markup=_back_kb())

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _touch_user(update)
    text = (update.message.text or "").strip()
    state = context.user_data.get("state")
    chat_id = update.effective_chat.id

    if not state:
        return

    if text.lower() in ("batal", "cancel", "99"):
        context.user_data.pop("state", None)
        context.user_data.pop("login_number", None)
        context.user_data.pop("otp_subscriber_id", None)
        await update.message.reply_text("✅ Dibatalkan.", reply_markup=_back_kb())
        return

    if state == "wait_msisdn":
        if not text.startswith("628") or not text.isdigit() or len(text) > 14:
            await update.message.reply_text("❌ Format nomor salah. Harus `628xxxx`.", reply_markup=_back_kb())
            return

        number = int(text)
        context.user_data["login_number"] = number

        # if registered -> activate for this telegram user, then show menu
        if _is_number_registered(number):
            try:
                _activate_for_user(chat_id, number)
                context.user_data.pop("state", None)
                await update.message.reply_text("✅ Nomor sudah terdaftar. Nomor aktif diset untuk akun Telegram kamu.", reply_markup=_back_kb())
                return
            except Exception as e:
                context.user_data.pop("state", None)
                await update.message.reply_text(f"❌ Gagal set aktif: {e}", reply_markup=_back_kb())
                return

        # request OTP
        await update.message.reply_text("📨 Meminta OTP...", reply_markup=_back_kb())
        try:
            subscriber_id = get_otp(str(number))
            if not subscriber_id:
                context.user_data.pop("state", None)
                await update.message.reply_text("❌ Gagal meminta OTP. Coba lagi.", reply_markup=_back_kb())
                return
            context.user_data["otp_subscriber_id"] = subscriber_id
            context.user_data["state"] = "wait_otp"
            await update.message.reply_text("✅ OTP terkirim. Kirim kode OTP 6 digit:", reply_markup=_back_kb())
        except Exception as e:
            context.user_data.pop("state", None)
            await update.message.reply_text(f"❌ Error request OTP: {type(e).__name__}: {e}", reply_markup=_back_kb())
        return

    if state == "wait_otp":
        otp = text
        if not otp.isdigit() or len(otp) != 6:
            await update.message.reply_text("❌ OTP harus 6 digit angka.", reply_markup=_back_kb())
            return

        number = context.user_data.get("login_number")
        if not number:
            context.user_data.pop("state", None)
            await update.message.reply_text("❌ State login hilang. Ulangi login.", reply_markup=_back_kb())
            return

        await update.message.reply_text("🔄 Verifikasi OTP...", reply_markup=_back_kb())
        try:
            # submit_otp returns dict with refresh_token
            result = submit_otp(AuthInstance.api_key, "SMS", str(number), otp)
            if not result or "refresh_token" not in result:
                await update.message.reply_text("❌ OTP salah / login gagal.", reply_markup=_back_kb())
                return

            AuthInstance.add_refresh_token(int(number), result["refresh_token"])
            _activate_for_user(chat_id, int(number))

            context.user_data.pop("state", None)
            context.user_data.pop("otp_subscriber_id", None)

            await update.message.reply_text("✅ Login sukses. Nomor aktif diset untuk akun Telegram kamu.", reply_markup=_back_kb())
        except Exception as e:
            context.user_data.pop("state", None)
            await update.message.reply_text(f"❌ Error submit OTP: {type(e).__name__}: {e}", reply_markup=_back_kb())
        return



# --- Admin/VIP Commands ---
async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rem = _vip_remaining_seconds(chat_id)
    if rem <= 0:
        await update.message.reply_text("🎟️ VIP kamu: *TIDAK AKTIF*\nHubungi admin untuk aktivasi.", parse_mode="Markdown")
        return
    days = rem // 86400
    hours = (rem % 86400) // 3600
    await update.message.reply_text(f"🎟️ VIP kamu: *AKTIF*\nSisa: *{days} hari {hours} jam*", parse_mode="Markdown")

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    db = _load_user_db()
    users = db.get("users", {}) or {}
    lines = ["👮 *Daftar user bot*"]
    if not users:
        lines.append("(kosong)")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    count = 0
    for cid, rec in users.items():
        if not isinstance(rec, dict):
            continue
        num = rec.get("number", "-")
        exp = int(rec.get("expires_at") or 0)
        rem = max(0, exp - _now_ts())
        vip = "OFF" if exp <= 0 else f"{rem//86400}h {((rem%86400)//3600)}j"
        lines.append(f"- chat_id: `{cid}` | nomor: `{num}` | VIP: *{vip}*")
        count += 1
        if count >= 200:
            break
    await _send_long(update.message, "\n".join(lines), parse_mode="Markdown")

async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Format: /grant <chat_id> <hari>\nContoh: /grant 123456789 30")
        return
    try:
        target_chat_id = int(context.args[0])
        days = int(context.args[1])
        if days <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("❌ Format salah. Contoh: /grant 123456789 30")
        return
    exp = _grant_vip(target_chat_id, days)
    dt = datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"✅ VIP diberikan/ditambah untuk `{target_chat_id}` sampai: {dt}", parse_mode="Markdown")

async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /revoke <chat_id>\nContoh: /revoke 123456789")
        return
    try:
        target_chat_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("❌ chat_id tidak valid.")
        return
    _revoke_vip(target_chat_id)
    await update.message.reply_text(f"✅ VIP dicabut untuk `{target_chat_id}`", parse_mode="Markdown")

def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("vip", cmd_vip))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CallbackQueryHandler(on_click, pattern=r"^m:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app

def main():
    logger.info("Starting SUNSET Telegram Bot (Simple Multi-User)")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
