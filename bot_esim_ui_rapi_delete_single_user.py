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
import zipfile
import tempfile
import shutil
from collections import Counter
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
TX_LOG_PATH = os.getenv("TX_LOG_PATH", "transactions.json")
AUTO_BACKUP_DIR = os.getenv("AUTO_BACKUP_DIR", "backups")

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
    # supaya user tetap muncul di menu Users walau metadata belum lengkap
    if not rec.get("tg_user_id"):
        rec["tg_user_id"] = int(chat_id)
    if rec.get("tg_username") is None:
        rec["tg_username"] = ""
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

def _touch_user_identity(chat_id: int, user_id: int, username: str = "", full_name: str = "") -> None:
    db = _load_user_db()
    db.setdefault("users", {})
    key = str(chat_id)
    rec = db["users"].get(key) or {}
    if not isinstance(rec, dict):
        rec = {}
    rec["chat_id"] = int(chat_id)
    rec["user_id"] = int(user_id)
    rec["username"] = username or rec.get("username", "")
    rec["full_name"] = full_name or rec.get("full_name", "")
    rec["updated_at"] = int(datetime.now().timestamp())
    db["users"][key] = rec
    _save_user_db(db)

def _menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🔐 Login", callback_data="m:login"),
            InlineKeyboardButton("📦 Paket", callback_data="m:packages"),
        ],
        [
            InlineKeyboardButton("5GB", callback_data="m:esim5"),
            InlineKeyboardButton("10GB", callback_data="m:esim10"),
            InlineKeyboardButton("10GB v2", callback_data="m:esim10v2"),
        ],
    ]

    if is_admin:
        rows.extend([
            [
                InlineKeyboardButton("👥 Users", callback_data="m:users"),
                InlineKeyboardButton("📢 Broadcast", callback_data="m:broadcast"),
            ],
            [
                InlineKeyboardButton("💾 Backup", callback_data="m:backupmenu"),
                InlineKeyboardButton("📊 Statistik", callback_data="m:stats"),
            ],
            [
                InlineKeyboardButton("📑 Tx", callback_data="m:txmenu"),
                InlineKeyboardButton("🧹 Hapus User", callback_data="m:cleanusers"),
            ],
        ])
    else:
        rows.append([
            InlineKeyboardButton("📩 Kirim ID", callback_data="m:sendid"),
        ])

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
        "📱 *ISI KUOTA ESIM*\n"
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
    _touch_user_identity(chat_id, update.effective_user.id, update.effective_user.username or "", update.effective_user.full_name or "")
    data = q.data

    if data == "m:home":
        await _show_menu(update, context, edit=True)
        return

    # Admin: Hapus User tertentu
    if data == "m:cleanusers":
        if not _admin_only(chat_id, update.effective_user.id):
            await q.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
            return
        context.user_data["state"] = "wait_delete_user"
        await q.message.edit_text(
            "🧹 *Hapus User*

"
            "Kirim *user_id Telegram* atau *username* user yang ingin dihapus.
"
            "Contoh:
"
            "- `7165758792`
"
            "- `goziit`

"
            "Ketik `batal` untuk membatalkan.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        return
        _clean_all_users()
        await q.message.edit_text(
            "✅ Semua data user berhasil dihapus.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        return

    # Admin: Broadcast
    if data == "m:broadcast":
        await _broadcast_to_all_users(update, context)
        return

    # Admin: Backup/Restore
    if data == "m:backupmenu":
        await _show_backup_restore_menu(update, context)
        return
    if data == "m:stats":
        if not _admin_only(chat_id, update.effective_user.id):
            await q.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
            return
        data_tx = _load_tx_log()
        txs = data_tx.get("transactions", []) or []
        total = len(txs)
        success = sum(1 for t in txs if t.get("status") == "SUCCESS")
        failed = sum(1 for t in txs if t.get("status") == "FAILED")
        today = datetime.now().strftime("%Y-%m-%d")
        today_count = sum(1 for t in txs if str(t.get("time","")).startswith(today))
        users_db = (_load_user_db().get("users", {}) or {})
        vip_active = sum(1 for cid in users_db.keys() if _has_vip(int(cid)))
        await q.message.edit_text(
            "📊 *Statistik Admin*\n"
            f"- Total transaksi: *{total}*\n"
            f"- Transaksi hari ini: *{today_count}*\n"
            f"- Sukses: *{success}*\n"
            f"- Gagal: *{failed}*\n"
            f"- Total user: *{len(users_db)}*\n"
            f"- VIP aktif: *{vip_active}*",
            parse_mode="Markdown", reply_markup=_back_kb())
        return
    if data == "m:txmenu":
        if not _admin_only(chat_id, update.effective_user.id):
            await q.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
            return
        txs = (_load_tx_log().get("transactions", []) or [])
        if not txs:
            await q.message.edit_text("📭 Tidak ada transaksi.", reply_markup=_back_kb())
            return
        lines = ["📑 *Transaction Log*"]
        for t in txs[-50:][::-1]:
            uname = ("@" + t["username"]) if t.get("username") else "-"
            lines.append(f"- `{t.get('time','')}` | `{t.get('number','-')}` | {t.get('package','-')} | *{t.get('status','-')}* | {uname}")
        await _send_long(q.message, "\n".join(lines), parse_mode="Markdown", reply_markup=_back_kb())
        return
    if data == "m:dobackup":
        await _send_backup_zip(update, context)
        return
    if data == "m:dorestore":
        await _prepare_restore(update, context)
        return

    # User: kirim ID ke admin
    if data == "m:sendid":
        await _send_id_to_admin(update, context)
        return

    # Admin: Users
    if data == "m:users":
        if not _admin_only(chat_id, update.effective_user.id):
            await q.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
            return
        await q.message.edit_text("🔄 Mengambil daftar user...", reply_markup=_back_kb())
        await _send_long(q.message, _format_users_list(), parse_mode="Markdown", reply_markup=_back_kb())
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
                _log_transaction(chat_id=chat_id, user_id=update.effective_user.id, username=(update.effective_user.username or ""), full_name=(update.effective_user.full_name or ""), number=str(n), package=label, status="SUCCESS", detail="")
                await q.message.reply_text(f"✅ *SUKSES* dor {label} (Decoy V2).", parse_mode="Markdown", reply_markup=_back_kb())
            else:
                _log_transaction(chat_id=chat_id, user_id=update.effective_user.id, username=(update.effective_user.username or ""), full_name=(update.effective_user.full_name or ""), number=str(n), package=label, status="FAILED", detail=str(pay.get("detail","")))
                detail = pay["detail"]
                body = json.dumps(detail, indent=2)[:3000] if isinstance(detail, dict) else str(detail)
                await _send_long(q.message, f"❌ Gagal dor {label}.\n```json\n{body}\n```", parse_mode="Markdown", reply_markup=_back_kb())
        except Exception as e:
            _log_transaction(chat_id=chat_id, user_id=update.effective_user.id, username=(update.effective_user.username or ""), full_name=(update.effective_user.full_name or ""), number=str(n), package=label, status="FAILED", detail=f"{type(e).__name__}: {e}")
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
    _touch_user_identity(chat_id, update.effective_user.id, update.effective_user.username or "", update.effective_user.full_name or "")

    if not state:
        return

    if text.lower() in ("batal", "cancel", "99"):
        context.user_data.pop("state", None)
        context.user_data.pop("login_number", None)
        context.user_data.pop("otp_subscriber_id", None)
        await update.message.reply_text("✅ Dibatalkan.", reply_markup=_back_kb())
        return

    if state == "wait_broadcast_text":
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        if not _is_admin(chat_id, user_id):
            context.user_data.pop("state", None)
            await update.message.reply_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
            return

        message_text = text.strip()
        if not message_text:
            await update.message.reply_text("❌ Pesan broadcast tidak boleh kosong.", reply_markup=_back_kb())
            return

        db = _load_user_db()
        users = db.get("users", {}) or {}
        targets = []
        for cid, rec in users.items():
            try:
                targets.append(int(cid))
            except Exception:
                continue

        if not targets:
            context.user_data.pop("state", None)
            await update.message.reply_text("❌ Belum ada pengguna untuk dikirimi pesan.", reply_markup=_back_kb())
            return

        sent = 0
        failed = 0
        for target_chat_id in targets:
            try:
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=f"📢 *Broadcast Admin*\n\n{message_text}",
                    parse_mode="Markdown"
                )
                sent += 1
            except Exception:
                failed += 1

        context.user_data.pop("state", None)
        await update.message.reply_text(
            f"✅ Broadcast selesai.\nBerhasil: *{sent}*\nGagal: *{failed}*",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        return

    if state == "wait_delete_user":
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        if not _admin_only(chat_id, user_id):
            context.user_data.pop("state", None)
            await update.message.reply_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
            return

        target = text.strip().lstrip("@")
        if not target:
            await update.message.reply_text("❌ user_id / username tidak boleh kosong.", reply_markup=_back_kb())
            return

        removed = _delete_user_by_id(target)
        context.user_data.pop("state", None)

        if removed:
            await update.message.reply_text(
                f"✅ User berhasil dihapus: `{target}`",
                parse_mode="Markdown",
                reply_markup=_back_kb()
            )
        else:
            await update.message.reply_text(
                f"❌ User tidak ditemukan: `{target}`",
                parse_mode="Markdown",
                reply_markup=_back_kb()
            )
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
    if not _admin_only(chat_id, user_id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    await _send_long(update.message, _format_users_list(), parse_mode="Markdown")

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


async def _broadcast_to_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        if update.callback_query:
            await update.callback_query.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
        else:
            await update.message.reply_text("❌ Kamu bukan admin.")
        return

    context.user_data["state"] = "wait_broadcast_text"
    if update.callback_query:
        await update.callback_query.message.edit_text(
            "📢 *Broadcast Msg*\n\nKirim pesan yang ingin dibroadcast ke semua pengguna bot.\n\nKetik `batal` untuk membatalkan.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
    else:
        await update.message.reply_text(
            "📢 *Broadcast Msg*\n\nKirim pesan yang ingin dibroadcast ke semua pengguna bot.\n\nKetik `batal` untuk membatalkan.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )


def _backup_zip_path() -> str:
    return os.path.join(tempfile.gettempdir(), f"bot_esim_backup_{int(datetime.now().timestamp())}.zip")

async def _send_backup_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        if update.callback_query:
            await update.callback_query.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
        else:
            await update.message.reply_text("❌ Kamu bukan admin.")
        return

    zip_path = _backup_zip_path()
    files_to_backup = []
    for p in ["tg-users.json", "refresh-tokens.json"]:
        full = os.path.join(os.getcwd(), p)
        if os.path.exists(full):
            files_to_backup.append((full, p))

    if not files_to_backup:
        if update.callback_query:
            await update.callback_query.message.edit_text("❌ File user tidak ditemukan untuk dibackup.", reply_markup=_back_kb())
        else:
            await update.message.reply_text("❌ File user tidak ditemukan untuk dibackup.", reply_markup=_back_kb())
        return

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arc in files_to_backup:
            zf.write(full, arcname=arc)

    caption = "✅ Backup berhasil. Silakan download file ZIP ini."
    if update.callback_query:
        await context.bot.send_document(chat_id=chat_id, document=open(zip_path, "rb"), filename=os.path.basename(zip_path), caption=caption)
        await update.callback_query.message.edit_text("✅ Backup selesai.", reply_markup=_back_kb())
    else:
        await context.bot.send_document(chat_id=chat_id, document=open(zip_path, "rb"), filename=os.path.basename(zip_path), caption=caption)

    try:
        os.remove(zip_path)
    except Exception:
        pass

async def _show_backup_restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        await update.callback_query.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Backup", callback_data="m:dobackup")],
        [InlineKeyboardButton("♻️ Restore", callback_data="m:dorestore")],
        [InlineKeyboardButton("⬅️ Kembali", callback_data="m:home")],
    ])
    await update.callback_query.message.edit_text(
        "💾 *Backup/Restore*\n\n"
        "Backup akan membuat ZIP berisi data user & token.\n"
        "Restore akan meminta kamu upload file ZIP backup.",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def _prepare_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        await update.callback_query.message.edit_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
        return
    context.user_data["state"] = "wait_restore_zip"
    await update.callback_query.message.edit_text(
        "♻️ *Restore Backup*\n\nSilakan kirim file `.zip` backup ke chat ini.\n\nKetik `batal` untuk membatalkan.",
        parse_mode="Markdown",
        reply_markup=_back_kb()
    )

async def _handle_restore_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _is_admin(chat_id, user_id):
        context.user_data.pop("state", None)
        await update.message.reply_text("❌ Kamu bukan admin.", reply_markup=_back_kb())
        return

    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ File tidak ditemukan.", reply_markup=_back_kb())
        return
    if not (doc.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text("❌ File harus berformat .zip", reply_markup=_back_kb())
        return

    tmp_dir = tempfile.mkdtemp(prefix="bot_restore_")
    zip_path = os.path.join(tmp_dir, doc.file_name or "backup.zip")
    try:
        tgfile = await context.bot.get_file(doc.file_id)
        await tgfile.download_to_drive(zip_path)

        extract_dir = os.path.join(tmp_dir, "extract")
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        restored = []
        for name in ["tg-users.json", "refresh-tokens.json"]:
            src_file = os.path.join(extract_dir, name)
            if os.path.exists(src_file):
                shutil.copy2(src_file, os.path.join(os.getcwd(), name))
                restored.append(name)

        context.user_data.pop("state", None)

        if not restored:
            await update.message.reply_text("❌ Backup ZIP tidak berisi file yang dikenali.", reply_markup=_back_kb())
            return

        await update.message.reply_text(
            "✅ Restore berhasil.\nFile dipulihkan: " + ", ".join(restored),
            reply_markup=_back_kb()
        )
    except Exception as e:
        context.user_data.pop("state", None)
        await update.message.reply_text(f"❌ Restore gagal: {e}", reply_markup=_back_kb())
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass



async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if state == "wait_restore_zip":
        await _handle_restore_zip(update, context)
        return



# --- Transaction / Backup helpers ---
def _load_tx_log() -> Dict[str, Any]:
    try:
        if os.path.exists(TX_LOG_PATH):
            with open(TX_LOG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("transactions", [])
                    return data
    except Exception:
        pass
    return {"transactions": []}

def _save_tx_log(data: Dict[str, Any]) -> None:
    tmp = TX_LOG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TX_LOG_PATH)

def _log_transaction(*, chat_id: int, user_id: int, username: str, full_name: str, number: str, package: str, status: str, detail: str = "") -> None:
    data = _load_tx_log()
    data.setdefault("transactions", [])
    data["transactions"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chat_id": int(chat_id),
        "user_id": int(user_id),
        "username": username or "",
        "full_name": full_name or "",
        "number": str(number or ""),
        "package": package,
        "status": status,
        "detail": detail[:500],
    })
    # keep last 5000
    data["transactions"] = data["transactions"][-5000:]
    _save_tx_log(data)

def _human_vip(chat_id: int) -> str:
    rem = _vip_remaining_seconds(chat_id)
    if rem <= 0:
        return "OFF"
    days = rem // 86400
    hours = (rem % 86400) // 3600
    return f"{days} hari {hours} jam"

def _format_users_list() -> str:
    """
    Tampilkan user unik untuk admin:
    username telegram - user id - durasi VIP (hari saja)
    Urutan: VIP aktif dulu, lalu VIP OFF.
    """
    db = _load_user_db()
    users = db.get("users", {}) or {}

    merged = {}
    for chat_id_str, rec in users.items():
        if not isinstance(rec, dict):
            continue

        tg_user_id = str(rec.get("tg_user_id") or rec.get("user_id") or chat_id_str).strip()
        tg_username = str(rec.get("tg_username") or rec.get("username") or "").strip()

        key = f"user:{tg_user_id}" if tg_user_id else f"chat:{chat_id_str}"

        exp = int(rec.get("expires_at") or 0)
        rem_days = max(0, (exp - _now_ts()) // 86400)
        vip_text = f"{rem_days} hari" if rem_days > 0 else "OFF"

        item = {
            "user_id": tg_user_id if tg_user_id else "-",
            "username": tg_username if tg_username else "-",
            "vip": vip_text,
            "vip_days": rem_days,
            "updated_at": int(rec.get("updated_at") or rec.get("last_seen") or 0),
        }

        old = merged.get(key)
        if old is None or item["updated_at"] >= old["updated_at"]:
            if old:
                if item["username"] == "-" and old["username"] != "-":
                    item["username"] = old["username"]
                if item["vip_days"] <= 0 and old["vip_days"] > 0:
                    item["vip_days"] = old["vip_days"]
                    item["vip"] = old["vip"]
            merged[key] = item
        else:
            if old["username"] == "-" and item["username"] != "-":
                old["username"] = item["username"]
            if old["vip_days"] <= 0 and item["vip_days"] > 0:
                old["vip_days"] = item["vip_days"]
                old["vip"] = item["vip"]
            merged[key] = old

    rows = list(merged.values())
    # VIP aktif dulu, lalu OFF; di dalamnya urut terbaru
    rows.sort(key=lambda x: (0 if x["vip_days"] > 0 else 1, -x["vip_days"], -x["updated_at"]))

    if not rows:
        return "👮 *Daftar user bot*\n(kosong)"

    lines = ["👮 *Daftar user bot*"]
    for item in rows[:300]:
        uname = f"@{item['username']}" if item["username"] != "-" else "-"
        lines.append(f"- {uname} | user_id: `{item['user_id']}` | VIP: *{item['vip']}*")
    return "\n".join(lines)

async def _auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        target_dir = _ensure_backup_dir()
        zip_path = _create_backup_zip(target_dir)
        # keep only latest 14 backups
        files = sorted(
            [os.path.join(target_dir, x) for x in os.listdir(target_dir) if x.endswith(".zip")],
            key=lambda p: os.path.getmtime(p)
        )
        for old in files[:-14]:
            try:
                os.remove(old)
            except Exception:
                pass
        logger.info(f"Auto backup created: {zip_path}")
    except Exception as e:
        logger.warning(f"Auto backup failed: {e}")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _admin_only(chat_id, user_id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    data = _load_tx_log()
    txs = data.get("transactions", []) or []
    total = len(txs)
    success = sum(1 for t in txs if t.get("status") == "SUCCESS")
    failed = sum(1 for t in txs if t.get("status") == "FAILED")
    today = datetime.now().strftime("%Y-%m-%d")
    today_count = sum(1 for t in txs if str(t.get("time", "")).startswith(today))
    users = (_load_user_db().get("users", {}) or {})
    vip_active = sum(1 for cid in users.keys() if _has_vip(int(cid)))
    await update.message.reply_text(
        "📊 *Statistik Admin*\n"
        f"- Total transaksi: *{total}*\n"
        f"- Transaksi hari ini: *{today_count}*\n"
        f"- Sukses: *{success}*\n"
        f"- Gagal: *{failed}*\n"
        f"- Total user: *{len(users)}*\n"
        f"- VIP aktif: *{vip_active}*",
        parse_mode="Markdown"
    )

async def cmd_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not _admin_only(chat_id, user_id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    data = _load_tx_log()
    txs = data.get("transactions", []) or []
    if context.args:
        needle = context.args[0].strip()
        txs = [t for t in txs if needle in str(t.get("number","")) or needle in str(t.get("chat_id","")) or needle.lower() in str(t.get("username","")).lower()]
    if not txs:
        await update.message.reply_text("📭 Tidak ada transaksi.")
        return
    lines = ["📑 *Transaction Log*"]
    for t in txs[-50:][::-1]:
        uname = ("@" + t["username"]) if t.get("username") else "-"
        lines.append(
            f"- `{t.get('time','')}` | `{t.get('number','-')}` | {t.get('package','-')} | *{t.get('status','-')}* | {uname}"
        )
    await _send_long(update.message, "\n".join(lines), parse_mode="Markdown")



def _delete_user_by_id(target_id: str) -> bool:
    """
    Hapus user tertentu dari tg-users.json.
    target_id bisa berupa user_id telegram atau username (tanpa @).
    """
    db = _load_user_db()
    users = db.get("users", {}) or {}
    target_id = str(target_id).strip().lstrip("@").lower()

    removed = False
    kept = {}
    for cid, rec in users.items():
        if not isinstance(rec, dict):
            kept[cid] = rec
            continue

        tg_user_id = str(rec.get("tg_user_id") or rec.get("user_id") or "").strip().lower()
        tg_username = str(rec.get("tg_username") or rec.get("username") or "").strip().lstrip("@").lower()

        if target_id and (target_id == tg_user_id or target_id == tg_username):
            removed = True
            continue

        kept[cid] = rec

    db["users"] = kept
    _save_user_db(db)
    return removed

def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("vip", cmd_vip))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("tx", cmd_tx))
    app.add_handler(CallbackQueryHandler(on_click, pattern=r"^m:"))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app

def main():
    logger.info("Starting SUNSET Telegram Bot (Simple Multi-User)")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
