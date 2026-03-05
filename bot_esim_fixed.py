# -*- coding: utf-8 -*-
"""
BOT ESIM SUNSET (Multi-user, Multi-number, TANPA active.number)

Perbaikan utama:
- Tiap user Telegram punya "daftar nomor" sendiri + 1 nomor aktif. User A tidak melihat nomor user B.
- User bisa menambahkan banyak nomor, dan switch nomor aktif kapan saja.
- Tidak memakai AuthInstance.set_active_user() => tidak menulis file active.number (menghindari Errno 5).
- Login:
  - Jika nomor sudah ada di refresh-tokens.json -> cukup aktifkan (tanpa OTP).
  - Jika belum -> request OTP -> input OTP -> simpan refresh_token -> aktifkan.
- OTP rate limit:
  - Kalau XL balas "time limit..." bot akan memberitahu user untuk tunggu (tidak error "subscriber_id not found").

File data:
- tg-users.json : mapping chat_id -> {active, numbers[]}
- refresh-tokens.json : list token per nomor

Run:
  cd sunset
  source venv/bin/activate
  export TELEGRAM_TOKEN="xxxx"
  python bot_esim.py   (atau rename file ini jadi bot_esim.py)
"""
import os
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

load_dotenv()

# ---- SUNSET imports ----
from app.util import ensure_api_key
from app.client.ciam import get_new_token, get_otp, submit_otp
from app.client.engsel import (
    send_api_request,
    get_balance,
    get_tiering_info,
    get_family,
    get_package,
    get_profile,
)
from app.menus.util import format_quota_byte
from app.type_dict import PaymentItem
from app.client.purchase.balance import settlement_balance
from app.service.decoy import DecoyInstance

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN env var belum di-set")

API_KEY = ensure_api_key()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot_esim_fixed")

USER_DB_PATH = os.getenv("TG_USER_DB", "tg-users.json")
RT_PATH = os.getenv("SUNSET_REFRESH_TOKENS", "refresh-tokens.json")

# =========================
# JSON helpers (robust save)
# =========================
def _safe_load_json(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _safe_save_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return
    except OSError:
        # fallback direct write
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

# =========================
# User DB: chat_id -> {active, numbers[]}
# =========================
def _load_user_db() -> Dict[str, Any]:
    return _safe_load_json(USER_DB_PATH, {"users": {}})

def _save_user_db(db: Dict[str, Any]) -> None:
    _safe_save_json(USER_DB_PATH, db)

def _get_user_rec(chat_id: int) -> Dict[str, Any]:
    db = _load_user_db()
    users = db.setdefault("users", {})
    rec = users.get(str(chat_id))
    if not isinstance(rec, dict):
        rec = {"active": None, "numbers": [], "updated_at": 0}
        users[str(chat_id)] = rec
        _save_user_db(db)
    # normalize
    if "numbers" not in rec or not isinstance(rec["numbers"], list):
        rec["numbers"] = []
    return rec

def _set_user_rec(chat_id: int, rec: Dict[str, Any]) -> None:
    db = _load_user_db()
    db.setdefault("users", {})[str(chat_id)] = rec
    _save_user_db(db)

def _list_numbers(chat_id: int) -> List[int]:
    rec = _get_user_rec(chat_id)
    out: List[int] = []
    for n in rec.get("numbers", []):
        try:
            out.append(int(n))
        except Exception:
            pass
    # unique preserve order
    seen=set()
    uniq=[]
    for n in out:
        if n not in seen:
            seen.add(n); uniq.append(n)
    return uniq

def _get_active_number(chat_id: int) -> Optional[int]:
    rec = _get_user_rec(chat_id)
    a = rec.get("active")
    if a is None:
        return None
    try:
        return int(a)
    except Exception:
        return None

def _add_number(chat_id: int, number: int, *, set_active: bool = True) -> None:
    rec = _get_user_rec(chat_id)
    nums = _list_numbers(chat_id)
    if int(number) not in nums:
        nums.append(int(number))
    rec["numbers"] = nums
    if set_active:
        rec["active"] = int(number)
    rec["updated_at"] = int(datetime.now().timestamp())
    _set_user_rec(chat_id, rec)

def _set_active(chat_id: int, number: int) -> None:
    _add_number(chat_id, number, set_active=True)

# =========================
# Refresh token DB helpers
# =========================
def _load_rts() -> List[Dict[str, Any]]:
    data = _safe_load_json(RT_PATH, [])
    return data if isinstance(data, list) else []

def _save_rts(items: List[Dict[str, Any]]) -> None:
    _safe_save_json(RT_PATH, items)

def _find_rt(number: int) -> Optional[Dict[str, Any]]:
    for rt in _load_rts():
        try:
            if int(rt.get("number")) == int(number):
                return rt
        except Exception:
            continue
    return None

def _is_registered(number: int) -> bool:
    return _find_rt(number) is not None and bool(_find_rt(number).get("refresh_token"))

def _upsert_refresh_token(number: int, refresh_token: str) -> Dict[str, Any]:
    """
    Simpan refresh token; lengkapi subscriber_id & subscription_type bila bisa.
    """
    rts = _load_rts()
    existing = None
    for rt in rts:
        try:
            if int(rt.get("number")) == int(number):
                existing = rt
                break
        except Exception:
            continue
    subscriber_id = (existing or {}).get("subscriber_id", "") if existing else ""
    tokens = get_new_token(API_KEY, refresh_token, subscriber_id)

    prof = None
    try:
        prof = get_profile(API_KEY, tokens["access_token"], tokens["id_token"])
    except Exception:
        prof = None

    sub_id = ""
    sub_type = ""
    try:
        p = (prof or {}).get("profile") or {}
        sub_id = p.get("subscriber_id", "") or subscriber_id
        sub_type = p.get("subscription_type", "") or (existing or {}).get("subscription_type", "")
    except Exception:
        sub_id = subscriber_id
        sub_type = (existing or {}).get("subscription_type", "")

    if existing:
        existing["refresh_token"] = tokens.get("refresh_token", refresh_token)
        if sub_id:
            existing["subscriber_id"] = sub_id
        if sub_type:
            existing["subscription_type"] = sub_type
        existing["updated_at"] = int(datetime.now().timestamp())
    else:
        existing = {
            "number": int(number),
            "subscriber_id": sub_id,
            "subscription_type": sub_type,
            "refresh_token": tokens.get("refresh_token", refresh_token),
            "updated_at": int(datetime.now().timestamp()),
        }
        rts.append(existing)

    _save_rts(rts)
    return existing

def _tokens_for(number: int) -> Dict[str, str]:
    rt = _find_rt(number)
    if not rt or not rt.get("refresh_token"):
        raise RuntimeError("Nomor belum login (refresh_token tidak ada).")
    tokens = get_new_token(API_KEY, rt["refresh_token"], rt.get("subscriber_id", ""))
    # refresh_token bisa berubah, simpan kembali
    try:
        rt["refresh_token"] = tokens.get("refresh_token", rt["refresh_token"])
        rt["updated_at"] = int(datetime.now().timestamp())
        rts = _load_rts()
        for i, item in enumerate(rts):
            try:
                if int(item.get("number")) == int(number):
                    rts[i] = rt
                    break
            except Exception:
                continue
        _save_rts(rts)
    except Exception:
        pass
    return tokens

# =========================
# UI helpers
# =========================
def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣ Login / Ganti Nomor", callback_data="m:login")],
        [InlineKeyboardButton("2️⃣ Cek Paket", callback_data="m:packages")],
        [InlineKeyboardButton("3️⃣ eSIM 5GB", callback_data="m:esim5")],
        [InlineKeyboardButton("4️⃣ eSIM 10GB", callback_data="m:esim10")],
    ])

def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="m:home")]])

def _login_kb(chat_id: int) -> InlineKeyboardMarkup:
    # tombol switch nomor kalau ada
    nums = _list_numbers(chat_id)
    rows = [[InlineKeyboardButton("➕ Tambah Nomor", callback_data="m:login_add")]]
    if nums:
        rows.append([InlineKeyboardButton("🔁 Pilih Nomor Tersimpan", callback_data="m:login_switch")])
    rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="m:home")])
    return InlineKeyboardMarkup(rows)

def _switch_kb(chat_id: int) -> InlineKeyboardMarkup:
    nums = _list_numbers(chat_id)
    rows = []
    for n in nums[:20]:
        rows.append([InlineKeyboardButton(f"📞 {n}", callback_data=f"m:switch:{n}")])
    rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="m:login")])
    return InlineKeyboardMarkup(rows)

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

def _header(chat_id: int) -> str:
    number = _get_active_number(chat_id)
    if not number:
        return "🔐 *Belum ada nomor aktif*\nKlik *1️⃣ Login / Ganti Nomor* untuk menambahkan nomor kamu."
    rt = _find_rt(number) or {}
    sub_type = rt.get("subscription_type", "N/A") or "N/A"
    try:
        tokens = _tokens_for(number)
        bal = get_balance(API_KEY, tokens["id_token"])
        remaining = bal.get("remaining", "N/A")
        expired_at = bal.get("expired_at", 0)
        expired_at_dt = datetime.fromtimestamp(expired_at).strftime("%Y-%m-%d") if expired_at else "N/A"

        point_info = "Points: N/A | Tier: N/A"
        try:
            if sub_type == "PREPAID":
                td = get_tiering_info(API_KEY, tokens)
                point_info = f"Points: {td.get('current_point',0)} | Tier: {td.get('tier',0)}"
        except Exception:
            pass

        return (
            "📱 *SUNSET XL BOT*\n"
            f"📞 *Nomor aktif:* `{number}` | *Type:* `{sub_type}`\n"
            f"💰 *Pulsa:* Rp {_money(remaining)} | *Aktif s/d:* `{expired_at_dt}`\n"
            f"⭐ *{point_info}*"
        )
    except Exception:
        return (
            "📱 *SUNSET XL BOT*\n"
            f"📞 *Nomor aktif:* `{number}` | *Type:* `{sub_type}`\n"
            "ℹ️ (Header gagal load, tapi nomor sudah tersimpan)"
        )

def _require_login(chat_id: int) -> Tuple[bool, Optional[int], Optional[str]]:
    number = _get_active_number(chat_id)
    if not number:
        return False, None, "❌ Kamu belum punya nomor aktif. Klik *1️⃣ Login / Ganti Nomor*."
    if not _is_registered(number):
        return False, None, "❌ Nomor aktif belum login (refresh_token belum ada). Login dulu."
    return True, number, None

# =========================
# Purchase helpers (Decoy V2)
# =========================
def _pick_option_code_from_family(fam: Dict[str, Any], target_no: int = 1) -> Optional[Dict[str, Any]]:
    n = 1
    for v in fam.get("package_variants", []):
        for opt in v.get("package_options", []):
            if n == target_no:
                return {
                    "option_code": opt.get("package_option_code"),
                    "name": opt.get("name", ""),
                    "price": int(opt.get("price", 0) or 0),
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

    decoy_detail = get_package(API_KEY, tokens, decoy["option_code"])
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
        API_KEY, tokens, items2, "🤫",
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
                    API_KEY, tokens, items2, "🤫",
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

# =========================
# Telegram handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.effective_message.reply_text(
        _header(chat_id) + "\n\nPilih menu:",
        parse_mode="Markdown",
        reply_markup=_menu_kb()
    )

async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = update.effective_chat.id
    data = q.data

    if data == "m:home":
        await q.message.edit_text(_header(chat_id) + "\n\nPilih menu:", parse_mode="Markdown", reply_markup=_menu_kb())
        return

    # ---- Login hub ----
    if data == "m:login":
        await q.message.edit_text(
            "🔐 *Login / Ganti Nomor*\n\nPilih aksi:",
            parse_mode="Markdown",
            reply_markup=_login_kb(chat_id)
        )
        return

    if data == "m:login_add":
        context.user_data["state"] = "wait_msisdn"
        await q.message.edit_text(
            "📲 *Tambah Nomor*\n\nKirim nomor format `628xxxx`.\n\nKetik `batal` untuk membatalkan.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        return

    if data == "m:login_switch":
        nums = _list_numbers(chat_id)
        if not nums:
            await q.message.edit_text("📭 Belum ada nomor tersimpan. Pilih *Tambah Nomor* dulu.", parse_mode="Markdown", reply_markup=_login_kb(chat_id))
            return
        await q.message.edit_text("🔁 *Pilih nomor yang ingin dijadikan aktif:*", parse_mode="Markdown", reply_markup=_switch_kb(chat_id))
        return

    if data.startswith("m:switch:"):
        try:
            number = int(data.split(":", 2)[2])
            if number not in _list_numbers(chat_id):
                await q.message.edit_text("❌ Nomor tidak ada di daftar kamu.", reply_markup=_login_kb(chat_id))
                return
            _set_active(chat_id, number)
            await q.message.edit_text(f"✅ Nomor aktif sekarang: `{number}`", parse_mode="Markdown", reply_markup=_back_kb())
        except Exception as e:
            await q.message.edit_text(f"❌ Gagal switch nomor: {e}", reply_markup=_login_kb(chat_id))
        return

    # ---- Protected menus ----
    ok, number, err = _require_login(chat_id)
    if not ok:
        await q.message.edit_text(err, parse_mode="Markdown", reply_markup=_back_kb())
        return

    # 2) Cek paket
    if data == "m:packages":
        await q.message.edit_text("🔄 Mengambil seluruh paket/quota...", reply_markup=_back_kb())
        try:
            tokens = _tokens_for(number)
            path = "api/v8/packages/quota-details"
            payload = {"is_enterprise": False, "lang": "en", "family_member_id": ""}
            res = send_api_request(API_KEY, path, payload, tokens["id_token"], "POST")
            if res.get("status") != "SUCCESS":
                await _send_long(q.message, f"❌ Gagal ambil paket.\n```json\n{json.dumps(res, indent=2)[:3000]}\n```", parse_mode="Markdown", reply_markup=_back_kb())
                return

            quotas = (res.get("data", {}) or {}).get("quotas", []) or []
            lines = [_header(chat_id), "", "📦 *My Packages (Full)*"]
            if not quotas:
                lines.append("📭 Tidak ada data quota.")
                await _send_long(q.message, "\n".join(lines), parse_mode="Markdown", reply_markup=_back_kb())
                return

            for i, qt in enumerate(quotas, start=1):
                group = qt.get("group_name", "")
                name = qt.get("name", "")
                benefits = qt.get("benefits") or []
                if benefits:
                    b0 = benefits[0]
                    rem = b0.get("remaining")
                    tot = b0.get("total")
                    rems = format_quota_byte(rem) if isinstance(rem, (int, float)) else str(rem)
                    tots = format_quota_byte(tot) if isinstance(tot, (int, float)) else str(tot)
                    lines.append(f"{i}. *{group}* - {name} — {rems}/{tots}")
                else:
                    lines.append(f"{i}. *{group}* - {name}")
            await _send_long(q.message, "\n".join(lines), parse_mode="Markdown", reply_markup=_back_kb())
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {type(e).__name__}: {e}", reply_markup=_back_kb())
        return

    async def _dor_esim(uuid: str, label: str):
        await q.message.edit_text(f"⚡ Dor {label}: memproses...", reply_markup=_back_kb())
        try:
            tokens = _tokens_for(number)
            fam = get_family(API_KEY, tokens, uuid, None, None)
            picked = _pick_option_code_from_family(fam, 1)
            if not picked or not picked.get("option_code"):
                await q.message.reply_text("❌ Paket #1 tidak ditemukan.", reply_markup=_back_kb()); return

            pkg = get_package(API_KEY, tokens, picked["option_code"])
            token_confirmation = pkg.get("token_confirmation", "")
            if not token_confirmation:
                await q.message.reply_text("❌ token_confirmation tidak ditemukan.", reply_markup=_back_kb()); return

            pay = _pay_pulsa_decoy_v2(
                tokens,
                picked["option_code"],
                picked["name"] or (pkg.get("package_option") or {}).get("name",""),
                picked["price"],
                token_confirmation
            )
            if pay["ok"]:
                await q.message.reply_text(f"✅ *SUKSES* dor {label} (Decoy V2).", parse_mode="Markdown", reply_markup=_back_kb())
            else:
                detail = pay["detail"]
                body = json.dumps(detail, indent=2)[:3000] if isinstance(detail, dict) else str(detail)
                await _send_long(q.message, f"❌ Gagal dor {label}.\n```json\n{body}\n```", parse_mode="Markdown", reply_markup=_back_kb())
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {type(e).__name__}: {e}", reply_markup=_back_kb())

    if data == "m:esim5":
        await _dor_esim("0d9d072c-3dab-4f72-88cd-31c5b8dc8c6b", "eSIM 5GB")
        return

    if data == "m:esim10":
        await _dor_esim("162d261b-05c7-450c-9b7a-1873b160140c", "eSIM 10GB")
        return

    await q.message.edit_text("❌ Menu tidak dikenali.", reply_markup=_back_kb())

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    state = context.user_data.get("state")
    chat_id = update.effective_chat.id

    if not state:
        return

    if text.lower() in ("batal", "cancel", "99"):
        context.user_data.clear()
        await update.message.reply_text("✅ Dibatalkan.", reply_markup=_back_kb())
        return

    if state == "wait_msisdn":
        if not text.startswith("628") or not text.isdigit() or len(text) > 14:
            await update.message.reply_text("❌ Format nomor salah. Harus `628xxxx`.", reply_markup=_back_kb())
            return

        number = int(text)
        context.user_data["login_number"] = number

        # Kalau sudah ada refresh_token => langsung aktifkan untuk user ini (tanpa OTP)
        if _is_registered(number):
            try:
                _set_active(chat_id, number)
                context.user_data.clear()
                await update.message.reply_text("✅ Nomor sudah terdaftar. Nomor aktif diset untuk akun Telegram kamu.", reply_markup=_back_kb())
                return
            except Exception as e:
                context.user_data.clear()
                await update.message.reply_text(f"❌ Gagal menyimpan nomor aktif: {e}", reply_markup=_back_kb())
                return

        # Request OTP
        await update.message.reply_text("📨 Meminta OTP...", reply_markup=_back_kb())
        try:
            subscriber_id = get_otp(str(number))
            if not subscriber_id:
                context.user_data.clear()
                await update.message.reply_text("❌ Gagal meminta OTP. Coba lagi nanti.", reply_markup=_back_kb())
                return
            context.user_data["otp_subscriber_id"] = subscriber_id
            context.user_data["state"] = "wait_otp"
            await update.message.reply_text("✅ OTP diminta. Kirim kode OTP 6 digit:", reply_markup=_back_kb())
        except Exception as e:
            # Handle rate limit message nicely
            msg = str(e)
            if "time limit" in msg.lower() or "not permitted to request an otp again" in msg.lower():
                await update.message.reply_text(
                    "⏳ OTP sedang dibatasi (cooldown). Tunggu ±60-120 detik lalu coba lagi.",
                    reply_markup=_back_kb()
                )
            else:
                await update.message.reply_text(f"❌ Error request OTP: {type(e).__name__}: {e}", reply_markup=_back_kb())
            context.user_data.clear()
        return

    if state == "wait_otp":
        otp = text
        if not otp.isdigit() or len(otp) != 6:
            await update.message.reply_text("❌ OTP harus 6 digit angka.", reply_markup=_back_kb())
            return

        number = context.user_data.get("login_number")
        subscriber_id = context.user_data.get("otp_subscriber_id")
        if not number or not subscriber_id:
            context.user_data.clear()
            await update.message.reply_text("❌ State login hilang. Ulangi login.", reply_markup=_back_kb())
            return

        await update.message.reply_text("🔄 Verifikasi OTP...", reply_markup=_back_kb())
        try:
            # submit_otp returns dict with refresh_token
            result = submit_otp(API_KEY, "SMS", str(number), otp)
            if not result or "refresh_token" not in result:
                await update.message.reply_text("❌ OTP salah / login gagal.", reply_markup=_back_kb())
                return

            _upsert_refresh_token(int(number), result["refresh_token"])
            _set_active(chat_id, int(number))
            context.user_data.clear()
            await update.message.reply_text("✅ Login sukses. Nomor aktif diset untuk akun Telegram kamu.", reply_markup=_back_kb())
        except Exception as e:
            context.user_data.clear()
            await update.message.reply_text(f"❌ Error submit OTP: {type(e).__name__}: {e}", reply_markup=_back_kb())
        return

# Extra command: list numbers
async def numbers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    nums = _list_numbers(chat_id)
    active = _get_active_number(chat_id)
    if not nums:
        await update.effective_message.reply_text("📭 Kamu belum menyimpan nomor. Klik menu Login.", reply_markup=_back_kb())
        return
    lines = ["📚 *Nomor tersimpan:*"]
    for n in nums:
        mark = "✅" if active == n else "▫️"
        lines.append(f"{mark} `{n}`")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=_back_kb())

def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("numbers", numbers_cmd))
    app.add_handler(CallbackQueryHandler(on_click, pattern=r"^m:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app

def main():
    logger.info("Starting bot_esim_fixed (multi-user + multi-number, no active.number)")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
