# -*- coding: utf-8 -*-
"""
BOT eSIM SUNSET (Multi-user, TANPA active.number)

- Tidak memakai AuthInstance.set_active_user() -> tidak menulis file `active.number`
- Tiap user Telegram punya nomor aktif sendiri (disimpan di `tg-users.json`)
- Refresh token tetap disimpan di `refresh-tokens.json`

Menu:
1) Login (input nomor; jika sudah ada di refresh-tokens.json -> set aktif untuk user tsb)
   jika belum -> OTP -> simpan refresh token -> set aktif
2) Cek Paket (tampilkan seluruh paket/quota)
3) eSIM 5GB (auto UUID + paket #1 + Pulsa+Decoy V2)
4) eSIM 10GB (auto UUID + paket #1 + Pulsa+Decoy V2)
"""
import os, json, logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

load_dotenv()

from app.util import ensure_api_key
from app.client.ciam import get_new_token, get_otp, submit_otp
from app.client.engsel import send_api_request, get_balance, get_tiering_info, get_family, get_package, get_profile
from app.menus.util import format_quota_byte
from app.type_dict import PaymentItem
from app.client.purchase.balance import settlement_balance
from app.service.decoy import DecoyInstance

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN env var belum di-set")

API_KEY = ensure_api_key()

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("bot_esim_multiuser")

USER_DB_PATH = os.getenv("TG_USER_DB", "tg-users.json")
RT_PATH = os.getenv("SUNSET_REFRESH_TOKENS", "refresh-tokens.json")

# ---- tiny db chat_id -> number ----
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
    if isinstance(u, dict) and u.get("number") is not None:
        try:
            return int(u["number"])
        except Exception:
            return None
    return None

def _set_user_number(chat_id: int, number: int) -> None:
    db = _load_user_db()
    db.setdefault("users", {})
    db["users"][str(chat_id)] = {"number": int(number), "updated_at": int(datetime.now().timestamp())}
    _save_user_db(db)

# ---- refresh-tokens.json helpers ----
def _load_rts() -> List[Dict[str, Any]]:
    if not os.path.exists(RT_PATH):
        with open(RT_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
        return []
    try:
        data = json.load(open(RT_PATH, "r", encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _save_rts(items: List[Dict[str, Any]]) -> None:
    tmp = RT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RT_PATH)

def _find_rt(number: int) -> Optional[Dict[str, Any]]:
    for rt in _load_rts():
        try:
            if int(rt.get("number")) == int(number):
                return rt
        except Exception:
            continue
    return None

def _is_registered(number: int) -> bool:
    return _find_rt(number) is not None

def _upsert_refresh_token(number: int, refresh_token: str) -> None:
    rts = _load_rts()
    existing = None
    for rt in rts:
        try:
            if int(rt.get("number")) == int(number):
                existing = rt
                break
        except Exception:
            continue

    tokens = get_new_token(API_KEY, refresh_token, (existing or {}).get("subscriber_id", "") if existing else "")
    profile = get_profile(API_KEY, tokens["access_token"], tokens["id_token"])
    sub_id = profile["profile"]["subscriber_id"]
    sub_type = profile["profile"]["subscription_type"]

    if existing:
        existing["refresh_token"] = tokens["refresh_token"]
        existing["subscriber_id"] = sub_id
        existing["subscription_type"] = sub_type
    else:
        rts.append({
            "number": int(number),
            "subscriber_id": sub_id,
            "subscription_type": sub_type,
            "refresh_token": tokens["refresh_token"],
        })
    _save_rts(rts)

def _tokens_for(number: int) -> Dict[str, str]:
    rt = _find_rt(number)
    if not rt:
        raise RuntimeError("Refresh token tidak ditemukan untuk nomor ini.")
    tokens = get_new_token(API_KEY, rt["refresh_token"], rt.get("subscriber_id", ""))
    # update refresh_token newest
    try:
        rt["refresh_token"] = tokens.get("refresh_token", rt["refresh_token"])
        _save_rts(_load_rts())
    except Exception:
        pass
    return tokens

# ---- UI helpers ----
def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣ Login", callback_data="m:login")],
        [InlineKeyboardButton("2️⃣ Cek Paket", callback_data="m:packages")],
        [InlineKeyboardButton("3️⃣ eSIM 5GB", callback_data="m:esim5")],
        [InlineKeyboardButton("4️⃣ eSIM 10GB", callback_data="m:esim10")],
    ])

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

def _header(chat_id: int) -> str:
    number = _get_user_number(chat_id)
    if not number:
        return "🔐 *Belum login*\nKlik *1️⃣ Login* lalu masukkan nomor kamu."
    rt = _find_rt(number) or {}
    sub_type = rt.get("subscription_type", "N/A")
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
            "ℹ️ (Gagal load header, tapi nomor sudah tersimpan)"
        )

def _require_login(chat_id: int) -> Tuple[bool, Optional[int], Optional[str]]:
    number = _get_user_number(chat_id)
    if not number:
        return False, None, "❌ Kamu belum login. Klik *1️⃣ Login* dulu."
    if not _find_rt(number):
        return False, None, "❌ Nomor belum terdaftar di server. Login ulang."
    return True, number, None

async def _show_menu(update: Update, *, edit: bool = False):
    chat_id = update.effective_chat.id
    text = _header(chat_id) + "\n\nPilih menu:"
    if update.callback_query and edit:
        await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=_menu_kb())
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=_menu_kb())

# ---- purchase helpers ----
def _pick_option_code_from_family(fam: Dict[str, Any], target_no: int = 1) -> Optional[Dict[str, Any]]:
    n = 1
    for v in fam.get("package_variants", []):
        for opt in v.get("package_options", []):
            if n == target_no:
                return {"option_code": opt.get("package_option_code"), "name": opt.get("name",""), "price": int(opt.get("price",0) or 0)}
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
    res = settlement_balance(API_KEY, tokens, items2, "🤫", ask_overwrite=False, overwrite_amount=overwrite_amount, token_confirmation_idx=1)

    if isinstance(res, dict) and res.get("status") != "SUCCESS":
        msg = res.get("message", "") or ""
        if "Bizz-err.Amount.Total" in msg:
            try:
                valid_amount = int(msg.split("=")[1].strip())
                res2 = settlement_balance(API_KEY, tokens, items2, "🤫", ask_overwrite=False, overwrite_amount=valid_amount, token_confirmation_idx=-1)
                if not (isinstance(res2, dict) and res2.get("status") != "SUCCESS"):
                    return {"ok": True, "detail": "Adjusted OK"}
                return {"ok": False, "detail": res2}
            except Exception:
                return {"ok": False, "detail": res}
        return {"ok": False, "detail": res}
    return {"ok": True, "detail": "OK"}

# ---- telegram handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_menu(update, edit=False)

async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = update.effective_chat.id
    data = q.data

    if data == "m:home":
        await _show_menu(update, edit=True)
        return

    if data == "m:login":
        context.user_data["state"] = "wait_msisdn"
        await q.message.edit_text(
            "📲 *Login*\n\nKirim nomor format `628xxxx`.\n\nKetik `batal` untuk membatalkan.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        return

    ok, number, err = _require_login(chat_id)
    if not ok:
        await q.message.edit_text(err, parse_mode="Markdown", reply_markup=_back_kb())
        return

    if data == "m:packages":
        await q.message.edit_text("🔄 Mengambil seluruh paket/quota...", reply_markup=_back_kb())
        try:
            tokens = _tokens_for(number)
            res = send_api_request(API_KEY, "api/v8/packages/quota-details",
                                   {"is_enterprise": False, "lang": "en", "family_member_id": ""},
                                   tokens["id_token"], "POST")
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

    async def _dor(uuid: str, label: str):
        await q.message.edit_text(f"⚡ Dor {label}: memproses...", reply_markup=_back_kb())
        try:
            tokens = _tokens_for(number)
            fam = get_family(API_KEY, tokens, uuid, None, None)
            picked = _pick_option_code_from_family(fam, 1)
            if not picked or not picked.get("option_code"):
                await q.message.reply_text("❌ Paket #1 tidak ditemukan.", reply_markup=_back_kb()); return
            pkg = get_package(API_KEY, tokens, picked["option_code"])
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

    if data == "m:esim5":
        await _dor("0d9d072c-3dab-4f72-88cd-31c5b8dc8c6b", "eSIM 5GB")
        return
    if data == "m:esim10":
        await _dor("162d261b-05c7-450c-9b7a-1873b160140c", "eSIM 10GB")
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
        if not text.startswith("628") or (not text.isdigit()) or len(text) > 14:
            await update.message.reply_text("❌ Format nomor salah. Harus `628xxxx`.", reply_markup=_back_kb())
            return
        number = int(text)
        context.user_data["login_number"] = number

        if _is_registered(number):
            _set_user_number(chat_id, number)
            context.user_data.pop("state", None)
            await update.message.reply_text("✅ Nomor sudah terdaftar. Nomor aktif diset untuk akun Telegram kamu.", reply_markup=_back_kb())
            return

        await update.message.reply_text("📨 Meminta OTP...", reply_markup=_back_kb())
        try:
            get_otp(str(number))
            context.user_data["state"] = "wait_otp"
            await update.message.reply_text("✅ OTP terkirim. Kirim kode OTP 6 digit:", reply_markup=_back_kb())
        except Exception as e:
            context.user_data.pop("state", None)
            await update.message.reply_text(f"❌ Error request OTP: {type(e).__name__}: {e}", reply_markup=_back_kb())
        return

    if state == "wait_otp":
        otp = text
        number = context.user_data.get("login_number")
        if (not otp.isdigit()) or len(otp) != 6:
            await update.message.reply_text("❌ OTP harus 6 digit angka.", reply_markup=_back_kb())
            return
        if not number:
            context.user_data.clear()
            await update.message.reply_text("❌ State login hilang. Ulangi login.", reply_markup=_back_kb())
            return

        await update.message.reply_text("🔄 Verifikasi OTP...", reply_markup=_back_kb())
        try:
            result = submit_otp(API_KEY, "SMS", str(number), otp)
            if not result or "refresh_token" not in result:
                await update.message.reply_text("❌ OTP salah / login gagal.", reply_markup=_back_kb())
                return
            _upsert_refresh_token(int(number), result["refresh_token"])
            _set_user_number(chat_id, int(number))
            context.user_data.clear()
            await update.message.reply_text("✅ Login sukses. Nomor aktif diset untuk akun Telegram kamu.", reply_markup=_back_kb())
        except Exception as e:
            context.user_data.clear()
            await update.message.reply_text(f"❌ Error submit OTP: {type(e).__name__}: {e}", reply_markup=_back_kb())

def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CallbackQueryHandler(on_click, pattern=r"^m:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app

def main():
    logger.info("Starting BOT eSIM (Multi-user, no active.number)")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
