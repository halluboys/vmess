# bot_telegram.py
import os
import json
import logging
import asyncio
import traceback
from io import BytesIO
from functools import wraps
from dotenv import load_dotenv
import time
import datetime

# Import library Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Import library untuk membuat QR Code
import qrcode
import requests

# Muat variabel lingkungan
load_dotenv()

# --- MODIFIKASI: Import dari file database.py yang baru ---
from database import initialize_database, set_user_access, is_user_authorized, get_user_count

# === KONFIGURASI PENTING ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- GANTI DENGAN ID TELEGRAM ANDA! ---
# Untuk mendapatkan ID Anda, chat dengan @userinfobot di Telegram
ADMIN_ID = 876081450

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable not set")

# Nonaktifkan verifikasi SSL
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
original_request = requests.request
def patched_request(method, url, **kwargs):
    kwargs['verify'] = False
    return original_request(method, url, **kwargs)
requests.request = patched_request

# Import modul MyXL
from api_request import get_otp, submit_otp, get_profile, get_balance, get_package, get_family
from auth_helper import AuthInstance
from crypto_helper import load_ax_fp
from my_package import fetch_my_packages
from paket_custom_family import get_packages_by_family
from paket_xut import get_package_xut
from purchase_api import get_payment_methods, settlement_qris, get_qris_code, settlement_multipayment
from util import display_html, ensure_api_key

# Inisialisasi AuthInstance
try:
    AuthInstance.api_key = ensure_api_key()
    load_ax_fp()
    logger.info("Auth initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Auth: {e}")
    raise

# === SISTEM OTORISASI ===
def authorized_only(func):
    """Decorator untuk membatasi akses hanya untuk pengguna yang diotorisasi."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_user_authorized(user_id):
            logger.warning(f"Akses DITOLAK (via decorator) untuk user tidak terdaftar: {user_id}")
            message_text = "⛔ Anda tidak memiliki izin untuk menggunakan bot ini."
            if update.message:
                await update.message.reply_text(message_text)
            elif update.callback_query:
                await update.callback_query.answer(message_text, show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def admin_only(func):
    """Decorator untuk membatasi akses hanya untuk ADMIN."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Perintah ini hanya untuk admin.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# === FUNGSI PEMBANTU ===
def format_benefit(benefit):
    """Format benefit menjadi string yang mudah dibaca"""
    name = benefit['name']
    total = benefit['total']
    if "Call" in name and total > 0:
        minutes = total / 60
        return f"• {name}: {minutes:.0f} menit"
    elif total > 0:
        if total >= 1_000_000_000: value, unit = total / (1024 ** 3), "GB"
        elif total >= 1_000_000: value, unit = total / (1024 ** 2), "MB"
        elif total >= 1_000: value, unit = total / 1024, "KB"
        else: value, unit = total, ""
        return f"• {name}: {value:.2f} {unit}" if unit else f"• {name}: {value}"
    else:
        return f"• {name}: {total}"


# === HANDLER PERINTAH ADMIN ===
@admin_only
async def grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Admin) Memberikan akses ke pengguna."""
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Format salah. Gunakan: `/grant [user_id]`", parse_mode='Markdown')
            return
        user_id_to_grant = int(context.args[0])
        set_user_access(user_id_to_grant, True)
        await update.message.reply_text(f"✅ Akses berhasil diberikan kepada user ID: {user_id_to_grant}")
    except (ValueError):
        await update.message.reply_text("⚠️ ID pengguna harus berupa angka. Format: `/grant [user_id]`", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error di /grant: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Terjadi error: `{e}`", parse_mode='Markdown')

@admin_only
async def revoke_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Admin) Mencabut akses dari pengguna."""
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Format salah. Gunakan: `/revoke [user_id]`", parse_mode='Markdown')
            return
        user_id_to_revoke = int(context.args[0])
        set_user_access(user_id_to_revoke, False)
        await update.message.reply_text(f"❌ Akses berhasil dicabut dari user ID: {user_id_to_revoke}")
    except (ValueError):
        await update.message.reply_text("⚠️ ID pengguna harus berupa angka. Format: `/revoke [user_id]`", parse_mode='Markdown')

@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Admin) Menampilkan jumlah total pengguna."""
    user_count = get_user_count()
    await update.message.reply_text(f"📊 Jumlah total pengguna yang pernah berinteraksi: {user_count} orang.")


# === HANDLER UTAMA & FUNGSI BOT ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk /start. Memeriksa otorisasi sebelum melanjutkan."""
    user = update.effective_user
    logger.info(f"User {user.id} ({user.username}) menjalankan /start.")

    if not is_user_authorized(user.id):
        # Tambahkan pengguna ke DB tapi jangan beri akses, untuk dicatat oleh admin
        set_user_access(user.id, False, user.username, user.first_name)
        logger.warning(f"Akses DITOLAK untuk user baru: {user.id}")
        await update.message.reply_text(
            f"⛔ *Akses Ditolak*\n\n"
            f"Anda tidak terdaftar untuk menggunakan bot ini.\n"
            f"Silakan hubungi admin dan berikan ID Telegram Anda.\n\n"
            f"👤 *ID Telegram Anda:* `{user.id}`",
            parse_mode='Markdown'
        )
        return

    await show_main_menu(update, context)


@authorized_only
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk /menu, hanya untuk pengguna terotorisasi."""
    await show_main_menu(update, context)


@authorized_only
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menampilkan menu utama dengan pengecekan yang lebih aman."""
    active_user = None # Inisialisasi dengan None untuk keamanan
    try:
        active_user = AuthInstance.get_active_user()
    except Exception as e:
        logger.error(f"Gagal mendapatkan active_user dari AuthInstance: {e}")

    # --- Sisa kode Anda tetap sama ---
    main_buttons = [
        InlineKeyboardButton("Cek Login", callback_data='switch_account_menu'),
        InlineKeyboardButton("BIZ Starter", callback_data='buy_biz_starter_direct'),
        InlineKeyboardButton("BIZ Manufacture", callback_data='buy_biz_manufacture_direct'),
        InlineKeyboardButton("Lihat Paket Saya", callback_data='view_packages'),
      #  InlineKeyboardButton("MasTif 1Bln", callback_data='buy_hot_package_menu'),
    ]
    
    keyboard = [main_buttons[i:i + 2] for i in range(0, len(main_buttons), 2)]
    if active_user:
        keyboard.append([InlineKeyboardButton("Akun Saya", callback_data='account_info')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_message = "              *TEMBAK PAKET XL DAR DER DOR*\n"
    
    if active_user and 'number' in active_user:
        welcome_message += f"✅ *Nomor Aktif: *`{active_user['number']}`\n"
    else:
        welcome_message += "🔐 *Status:* Belum login\n"
        # Jika tidak ada user aktif, coba set dari yang pertama jika ada token
        if not active_user and AuthInstance.refresh_tokens:
            first_number = AuthInstance.refresh_tokens[0].get('number')
            if first_number:
                logger.info(f"Tidak ada user aktif, mencoba set ke user pertama: {first_number}")
                AuthInstance.set_active_user(first_number)
                welcome_message = "🔄 Sesi di-refresh. Silakan coba lagi."

    welcome_message += "Silakan pilih menu di bawah ini:"

    if update.message:
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        try:
            await update.callback_query.message.edit_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception:
            await update.callback_query.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')

# --- TAMBAHKAN FUNGSI BARU INI ---
async def buy_hot_package_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menampilkan dan memproses pembelian dari menu Paket Hot 2."""
    query = update.callback_query
    await query.answer()

    try:
        await query.message.edit_text("🔥 Mengambil daftar Paket Hot 2...")
        
        # Ambil data dari JSON eksternal
        url = "https://me.mashu.lol/pg-hot2.json"
        response = requests.get(url, timeout=15)
        response.raise_for_status() # Ini akan error jika status bukan 200
        hot_packages = response.json()

        if not hot_packages:
            await query.message.edit_text("📭 Saat ini tidak ada Paket Hot 2 yang tersedia.")
            return

        # Buat tombol untuk setiap paket
        keyboard = []
        for idx, pkg in enumerate(hot_packages):
            # Simpan detail paket sementara di context untuk diakses nanti
            callback_data = f"hotpkg_{idx}"
            context.bot_data[callback_data] = pkg
            keyboard.append([InlineKeyboardButton(f"{pkg['name']} - Rp {pkg.get('price', 'N/A')}", callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data='main_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text("🔥 **Paket Hot 2 Tersedia** 🔥\n\nPilih paket promo di bawah ini:", reply_markup=reply_markup, parse_mode='Markdown')

    except requests.exceptions.RequestException as e:
        logger.error(f"Gagal mengambil data Paket Hot 2: {e}")
        await query.message.edit_text("❌ Gagal mengambil data Paket Hot 2. Silakan coba lagi nanti.")
    except Exception as e:
        logger.error(f"Error di buy_hot_package_menu: {e}", exc_info=True)
        await query.message.edit_text("❌ Terjadi kesalahan saat menampilkan Paket Hot 2.")

# GANTI SELURUH FUNGSI INI DI bot_telegram.py ANDA

async def process_hot_package_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memproses setelah pengguna memilih salah satu paket dari menu Hot 2."""
    query = update.callback_query
    await query.answer()
    
    selected_package_data = context.bot_data.get(query.data)
    if not selected_package_data:
        await query.message.edit_text("❌ Paket promo tidak valid atau sudah kedaluwarsa. Silakan kembali ke menu.")
        return

    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
        return

    await query.message.edit_text(f"🔄 Mempersiapkan paket *{selected_package_data['name']}*...", parse_mode='Markdown')

    try:
        payment_items = []
        api_key = AuthInstance.api_key
        
        # --- FUNGSI BANTU UNTUK MEMBERSIHKAN HARGA ---
        def clean_price(price_str):
            if isinstance(price_str, (int, float)):
                return price_str # Jika sudah angka, kembalikan langsung
            # Hapus "Rp", titik, dan spasi, lalu ubah ke integer
            return int(''.join(filter(str.isdigit, str(price_str))))
        # --- AKHIR FUNGSI BANTU ---

        for package_item in selected_package_data.get("packages", []):
            family_code = package_item.get("family_code")
            variant_name_to_find = package_item.get("variant_name")
            order_to_find = package_item.get("order")
            is_enterprise = package_item.get("is_enterprise", False)
            
            if not all([family_code, variant_name_to_find, order_to_find is not None]):
                raise ValueError("Item paket di JSON tidak lengkap.")

            family_data = get_family(api_key, tokens, family_code, is_enterprise)
            if not family_data or "package_variants" not in family_data:
                raise ValueError(f"Gagal mengambil data family untuk {family_code}")

            found_option_code = None
            for variant in family_data["package_variants"]:
                if variant.get("name") == variant_name_to_find:
                    for option in variant.get("package_options", []):
                        if option.get("order") == order_to_find:
                            found_option_code = option.get("package_option_code")
                            break
                if found_option_code:
                    break
            
            if not found_option_code:
                raise ValueError(f"Tidak dapat menemukan paket yang cocok untuk {variant_name_to_find}")

            package_details = get_package(api_key, tokens, found_option_code)
            if not package_details:
                raise ValueError(f"Gagal mengambil detail untuk item: {found_option_code}")

            payment_items.append({
                'item_code': package_details["package_option"]["package_option_code"],
                'item_price': package_details["package_option"]["price"], # Harga dari API sudah pasti angka
                'item_name': package_details["package_option"]["name"],
                'token_confirmation': package_details["token_confirmation"],
            })

        if not payment_items:
            await query.message.edit_text("❌ Tidak ada item paket yang valid dalam promo ini.")
            return

        # Simpan item pembayaran di context untuk digunakan oleh fungsi QRIS
        context.user_data['payment_items'] = payment_items
        context.user_data['promo_name'] = selected_package_data['name']
        
        # --- PERBAIKAN: BERSIHKAN HARGA PROMO DARI JSON ---
        promo_price_cleaned = clean_price(selected_package_data['price'])
        context.user_data['promo_price'] = promo_price_cleaned
        
        # Tampilkan detail akhir dan tombol bayar
        message = (
            f"📦 *Konfirmasi Pembelian Promo*\n\n"
            f"🏷️ *Nama Promo:* {selected_package_data['name']}\n"
            f"💰 *Harga Total:* Rp {promo_price_cleaned:,}\n" # Gunakan harga yang sudah bersih
            f"📄 *Detail:* {selected_package_data.get('detail', '-')}\n\n"
            f"Tekan tombol di bawah untuk melanjutkan pembayaran."
        )
        keyboard = [
            [InlineKeyboardButton("📲 Bayar dengan QRIS", callback_data='pay_hot_package_qris')],
            [InlineKeyboardButton("🔙 Kembali", callback_data='buy_hot_package_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error di process_hot_package_selection: {e}", exc_info=True)
        await query.message.edit_text(f"❌ Terjadi kesalahan saat mempersiapkan paket promo: {e}")

# GANTI SELURUH FUNGSI INI DI bot_telegram.py ANDA

async def pay_hot_package_with_qris(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memproses pembayaran QRIS untuk paket bundling Hot 2 dalam satu transaksi."""
    query = update.callback_query
    await query.answer()

    # Ambil data pembayaran dari context
    payment_items_full = context.user_data.get('payment_items')
    promo_name = context.user_data.get('promo_name', 'Paket Promo')
    promo_price = context.user_data.get('promo_price', 0)

    if not payment_items_full:
        await query.message.edit_text("❌ Informasi pembayaran tidak ditemukan. Silakan ulangi dari awal.")
        return

    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Sesi berakhir. Silakan login kembali.")
        return

    await query.message.edit_text("🔄 Memproses pembayaran QRIS untuk paket promo...")
    
    try:
        api_key = AuthInstance.api_key
        
        # Ambil detail dari item PERTAMA hanya untuk mendapatkan token pembayaran
        primary_item = payment_items_full[0]
        
        # 1. Dapatkan metode pembayaran (cukup sekali menggunakan item pertama)
        payment_methods_data = get_payment_methods(api_key, tokens, primary_item['token_confirmation'], primary_item['item_code'])
        if not payment_methods_data:
            raise ValueError("Gagal mendapatkan metode pembayaran.")

        token_payment = payment_methods_data["token_payment"]
        ts_to_sign = payment_methods_data["timestamp"]
        
        # 2. Susun ulang 'items' untuk dikirim ke settlement_qris
        items_for_payload = []
        for item in payment_items_full:
            items_for_payload.append({
                "item_code": item['item_code'],
                "item_price": item['item_price'],
                "item_name": item['item_name'],
                "product_type": "",
                "tax": 0
            })
        
        # 3. Panggil settlement_qris SATU KALI dengan semua item dan harga promo
        transaction_id = settlement_qris(
            api_key=api_key,
            tokens=tokens,
            token_payment=token_payment,
            ts_to_sign=ts_to_sign,
            items=items_for_payload, # <-- Kirim semua item
            total_amount=promo_price, # <-- Kirim harga promo
            promo_name=promo_name
        )
        if not transaction_id:
            raise ValueError("Gagal membuat transaksi QRIS. Periksa log untuk detail dari server.")
        
        # 4. Dapatkan dan kirim QR Code seperti biasa
        qris_data = get_qris_code(api_key, tokens, transaction_id)
        if not qris_data:
            raise ValueError("Gagal mendapatkan data QRIS.")

        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(qris_data)
        img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        
        caption = (
            f"📲 *Pembayaran Promo: {promo_name}*\n\n"
            f"Silakan scan QR Code untuk membayar **Rp {promo_price:,}**.\n\n"
            f"Setelah pembayaran berhasil, semua paket dalam promo akan diaktifkan."
        )
        await query.message.reply_photo(photo=img_buffer, caption=caption, parse_mode='Markdown')
        await query.message.edit_text("✅ QR Code pembayaran telah dikirim!")

    except Exception as e:
        logger.error(f"Error saat pembayaran QRIS paket Hot: {e}", exc_info=True)
        await query.message.edit_text(f"❌ Terjadi kesalahan saat memproses pembayaran QRIS: {e}")

async def buy_xut_vidio_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Langsung tampilkan detail paket XUT Unlimited Turbo Vidio (nomor 11)"""
    query = update.callback_query
    await query.answer()
    try:
        # 1. Pastikan pengguna sudah login
        tokens = AuthInstance.get_active_tokens()
        if not tokens:
            await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
            return
        await query.message.edit_text("🔄 Mengambil detail paket XUT Vidio...")
        # 2. Dapatkan API key
        api_key = AuthInstance.api_key
        # 3. Panggil fungsi get_package_xut() untuk mendapatkan daftar paket
        packages = get_package_xut()
        if not packages:
            await query.message.edit_text("❌ Gagal mengambil data paket XUT.")
            return
        # 4. Cari paket dengan nomor 11
        target_package = None
        for pkg in packages:
            if pkg.get('number') == 11:  # Cari paket nomor 11
                target_package = pkg
                break
        if not target_package:
            await query.message.edit_text("❌ Paket XUT Unlimited Turbo Vidio (nomor 11) tidak ditemukan.")
            return
        # 5. Ambil detail paket lengkap untuk mendapatkan token_confirmation
        package_code = target_package['code']
        package_details = get_package(api_key, tokens, package_code)
        if not package_details:
            await query.message.edit_text("❌ Gagal mengambil detail paket XUT Vidio.")
            return
        # 6. Ekstrak informasi yang dibutuhkan
        package_name = target_package['name']
        package_price = target_package['price']
        token_confirmation = package_details["token_confirmation"]
        # 7. Simpan informasi paket di context.user_data dengan KEY YANG SESUAI
        # Gunakan 'selected_package' agar bisa dibaca oleh buy_xut_with_qris
        context.user_data['selected_package'] = {
            'code': package_code,
            'name': package_name,
            'price': package_price,
            'token_confirmation': token_confirmation,
            # Tambahkan field lain jika diperlukan
            'validity': package_details["package_option"]["validity"],
            'benefits': package_details["package_option"]["benefits"],
            'tnc': package_details["package_option"]["tnc"]
        }
        # 8. Tampilkan detail paket dan opsi pembayaran
        message = (
            f"📦 *Detail Paket*\n"
            f"🏷 *Nama:* {package_name}\n"
            f"💰 *Harga:* Rp {package_price}\n"
            "Pilih metode pembayaran:"
        )
        keyboard = [
            [InlineKeyboardButton("📲 Beli dengan QRIS", callback_data='buy_xut_qris')], 
            [InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error fetching XUT Vidio package: {e}", exc_info=True)
        await query.message.edit_text("❌ Terjadi kesalahan saat mengambil detail paket XUT Vidio.")

# --- FITUR BARU: PEMBELIAN LANGSUNG BIZ STARTER ---
async def buy_biz_starter_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Membeli paket enterprise 'BIZ Starter' secara langsung."""
    query = update.callback_query
    await query.answer()

    # 1. Pastikan pengguna sudah login
    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
        return

    await query.message.edit_text("🔄 Memproses paket BIZ Starter...")

    try:
        # --- DATA PAKET ENTERPRISE ---
        FAMILY_CODE_BIZ = "20342db0-e03e-4dfd-b2d0-cd315d7ddc36"
        TARGET_PACKAGE_NUMBER = 1
        TARGET_PACKAGE_NAME = "BIZ Starter"
        # --- AKHIR DATA ---

        api_key = AuthInstance.api_key
        
        # 2. Dapatkan daftar paket dari family code (is_enterprise=True)
        family_data = get_family(api_key, tokens, FAMILY_CODE_BIZ, is_enterprise=True)
        if not family_data or "package_variants" not in family_data:
            await query.message.edit_text(f"❌ Gagal mengambil data untuk family code `{FAMILY_CODE_BIZ}`.")
            return

        # 3. Temukan paket dengan nomor yang sesuai (nomor 1)
        all_packages_list = []
        option_number = 1
        for variant in family_data["package_variants"]:
            for option in variant["package_options"]:
                all_packages_list.append({
                    "number": option_number,
                    "name": option["name"],
                    "price": option["price"],
                    "code": option["package_option_code"]
                })
                option_number += 1
        
        target_package_info = next((p for p in all_packages_list if p["number"] == TARGET_PACKAGE_NUMBER), None)

        if not target_package_info:
            await query.message.edit_text(f"❌ Paket nomor {TARGET_PACKAGE_NUMBER} ({TARGET_PACKAGE_NAME}) tidak ditemukan.")
            return

        # 4. Ambil detail paket lengkap untuk mendapatkan token_confirmation
        package_code = target_package_info['code']
        package_details = get_package(api_key, tokens, package_code)
        if not package_details:
            await query.message.edit_text("❌ Gagal mengambil detail lengkap paket BIZ Starter.")
            return

        # 5. Siapkan data untuk proses pembayaran (disimpan di context.user_data)
        context.user_data['selected_package'] = {
            'code': package_code,
            'name': target_package_info['name'],
            'price': target_package_info['price'],
            'token_confirmation': package_details.get("token_confirmation", ""),
            'validity': package_details.get("package_option", {}).get("validity", "N/A"),
            'benefits': package_details.get("package_option", {}).get("benefits", []),
            'tnc': package_details.get("package_option", {}).get("tnc", "")
        }

        # 6. Langsung panggil fungsi pembayaran QRIS (kita gunakan kembali fungsi yang sudah ada)
        await buy_xut_with_qris(update, context)

    except Exception as e:
        logger.error(f"[BIZ STARTER] Error: {e}", exc_info=True)
        await query.message.edit_text("❌ Terjadi kesalahan saat memproses paket BIZ Starter.")

# --- TAMBAHKAN FUNGSI BARU INI ---

async def buy_biz_manufacture_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Membeli paket enterprise 'BIZ Manufacture' secara langsung."""
    query = update.callback_query
    await query.answer()

    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
        return

    await query.message.edit_text("🔄 Memproses paket BIZ Manufacture...")

    try:
        # --- DATA PAKET ENTERPRISE ---
        FAMILY_CODE_BIZ = "f3303d95-8454-4e80-bb25-38513d358a11"
        TARGET_PACKAGE_NUMBER = 1
        TARGET_PACKAGE_NAME = "BIZ Lite"
        # --- AKHIR DATA ---

        api_key = AuthInstance.api_key
        
        family_data = get_family(api_key, tokens, FAMILY_CODE_BIZ, is_enterprise=True)
        if not family_data or "package_variants" not in family_data:
            await query.message.edit_text(f"❌ Gagal mengambil data untuk family code `{FAMILY_CODE_BIZ}`.")
            return

        all_packages_list = []
        option_number = 1
        for variant in family_data["package_variants"]:
            for option in variant["package_options"]:
                all_packages_list.append({
                    "number": option_number,
                    "name": option["name"],
                    "price": option["price"],
                    "code": option["package_option_code"]
                })
                option_number += 1
        
        target_package_info = next((p for p in all_packages_list if p["number"] == TARGET_PACKAGE_NUMBER), None)

        if not target_package_info:
            await query.message.edit_text(f"❌ Paket nomor {TARGET_PACKAGE_NUMBER} ({TARGET_PACKAGE_NAME}) tidak ditemukan.")
            return

        package_code = target_package_info['code']
        package_details = get_package(api_key, tokens, package_code)
        if not package_details:
            await query.message.edit_text("❌ Gagal mengambil detail lengkap paket BIZ Manufacture.")
            return

        context.user_data['selected_package'] = {
            'code': package_code,
            'name': target_package_info['name'],
            'price': target_package_info['price'],
            'token_confirmation': package_details.get("token_confirmation", ""),
            'validity': package_details.get("package_option", {}).get("validity", "N/A"),
            'benefits': package_details.get("package_option", {}).get("benefits", []),
            'tnc': package_details.get("package_option", {}).get("tnc", "")
        }

        await buy_xut_with_qris(update, context)

    except Exception as e:
        logger.error(f"[BIZ MANUFACTURE] Error: {e}", exc_info=True)
        await query.message.edit_text("❌ Terjadi kesalahan saat memproses paket BIZ Manufacture.")

async def initiate_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses login - meminta nomor HP"""
    query = update.callback_query
    if query:
        await query.answer()
    message = (
        "📱 *Login ke MyXL*\n"
        "Silakan kirimkan nomor telepon Anda.\n"
        "Format: `628XXXXXXXXXX` (awali dengan 62)\n"
    )
    # Simpan state bahwa user sedang menunggu input nomor untuk login
    context.user_data['state'] = 'waiting_phone_number_login'
    keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # Untuk MessageHandler
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def initiate_switch_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses ganti akun - meminta nomor HP"""
    query = update.callback_query
    if query:
        await query.answer()
    message = (
        "🔄 *Cek Login*\n"
        "Silakan kirimkan nomor telepon yang ingin diaktifkan.\n"
        "Format: `628XXXXXXXXXX` (awali dengan 62)\n"
    )
    # Simpan state bahwa user sedang menunggu input nomor untuk ganti akun
    context.user_data['state'] = 'waiting_phone_number_switch'
    keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # Untuk MessageHandler
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_phone_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Menangani input nomor. Jika refresh token gagal, tetap lanjutkan ke menu utama
    sesuai permintaan, namun catat sebagai warning.
    """
    state = context.user_data.get('state')
    if state not in ['waiting_phone_number_login', 'waiting_phone_number_switch']:
        return

    phone_number = update.message.text.strip()
    
    # --- (Bagian validasi nomor telepon tidak diubah) ---
    original_input = phone_number
    if phone_number.startswith("08"):
        phone_number = "62" + phone_number[1:]
    elif not phone_number.startswith("628"):
        await update.message.reply_text("❌ Format nomor telepon tidak valid. Awali dengan `628` atau `08`.")
        return
    if not phone_number.isdigit() or len(phone_number) < 11 or len(phone_number) > 15:
        await update.message.reply_text("❌ Panjang nomor telepon tidak valid.")
        return
    # --- (Akhir bagian validasi) ---

    context.user_data.pop('state', None)
    status_message = await update.message.reply_text(f"🔄 Memeriksa sesi untuk `{phone_number}`...", parse_mode='Markdown')

    AuthInstance.load_tokens()
    user_exists = any(str(user.get('number')) == phone_number for user in AuthInstance.refresh_tokens)

    if not user_exists:
        # Jika nomor belum pernah login sama sekali, selalu minta OTP.
        await status_message.edit_text(f"Nomor `{phone_number}` belum terdaftar. Memulai proses login baru...", parse_mode='Markdown')
        context.user_data['temp_phone'] = phone_number
        context.user_data['state'] = 'waiting_otp'
        await request_and_send_otp(update, phone_number)
        await status_message.delete()
        return

    # Jika nomor SUDAH ADA, coba aktifkan sesi.
    success = AuthInstance.set_active_user(int(phone_number))
    
    if not success:
        # Gagal refresh, tapi kita paksakan lanjut sesuai permintaan.
        # Ini adalah bagian yang diubah.
        logger.warning(
            f"Gagal menyegarkan sesi untuk {phone_number}, "
            "namun melanjutkan ke menu utama sesuai permintaan. "
            "Fungsi bot mungkin akan gagal nanti jika access token kedaluwarsa."
        )

    # Baik sukses maupun gagal (tapi ada user), tetap lanjutkan ke menu utama.
    await status_message.edit_text(f"✅ Sesi untuk `{phone_number}` telah diaktifkan. Menampilkan menu...", parse_mode='Markdown')
    await asyncio.sleep(1)
    await status_message.delete()
    await show_main_menu(update, context)


async def request_and_send_otp(update: Update, phone_number: str) -> None:
    """Meminta OTP dan mengirimkannya ke pengguna"""
    await update.message.reply_text("🔄 Mengirimkan permintaan OTP...")
    try:
        subscriber_id = get_otp(phone_number)
        if not subscriber_id:
            await update.message.reply_text("❌ Gagal mengirim OTP.")
            return
        await update.message.reply_text(
            f"✅ OTP telah dikirim ke nomor {phone_number}.\n"
            "Silakan kirimkan kode OTP 6 digit yang Anda terima:"
        )
    except Exception as e:
        logger.error(f"Error requesting OTP for {phone_number}: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat meminta OTP. Silakan coba lagi.")

async def handle_otp_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menangani input OTP"""
    if context.user_data.get('state') != 'waiting_otp':
        return
    otp = update.message.text.strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text(
            "❌ Kode OTP tidak valid.\n"
            "Pastikan OTP terdiri dari 6 digit angka.\n"
            "Silakan kirimkan OTP yang benar:"
        )
        return
    phone_number = context.user_data.get('temp_phone')
    if not phone_number:
        await update.message.reply_text("❌ Terjadi kesalahan. Silakan mulai proses login dari awal.")
        context.user_data.clear()
        return
    await update.message.reply_text("🔄 Memverifikasi OTP...")
    try:
        tokens = submit_otp(AuthInstance.api_key, phone_number, otp)
        if not tokens:
            await update.message.reply_text("❌ OTP salah atau telah kedaluwarsa. Silakan coba lagi.")
            context.user_data['state'] = 'waiting_phone_number_login'
            return
        AuthInstance.add_refresh_token(int(phone_number), tokens["refresh_token"])
        AuthInstance.set_active_user(int(phone_number))
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Login berhasil!\n"
            "Anda sekarang dapat menggunakan semua fitur bot."
        )
        await show_main_menu(update, context)
    except Exception as e:
        logger.error(f"Error submitting OTP for {phone_number}: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat memverifikasi OTP. Silakan coba lagi.")

async def view_packages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View user's packages"""
    query = update.callback_query
    await query.answer()
    try:
        tokens = AuthInstance.get_active_tokens()
        if not tokens:
            await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
            return
        await query.message.edit_text("🔄 Mengambil daftar paket Anda...")
        api_key = AuthInstance.api_key
        id_token = tokens.get("id_token")
        from api_request import send_api_request
        path = "api/v8/packages/quota-details"
        payload = {"is_enterprise": False, "lang": "en", "family_member_id": ""}
        res = send_api_request(api_key, path, payload, id_token, "POST")
        if res.get("status") != "SUCCESS":
            await query.message.edit_text("❌ Gagal mengambil data paket.")
            return
        quotas = res["data"]["quotas"]
        if not quotas:
            await query.message.edit_text("📭 Anda tidak memiliki paket aktif.")
            return
        message = "*📦 Paket Saya:*\n"
        for i, quota in enumerate(quotas, 1):
            quota_code = quota["quota_code"]
            name = quota["name"]
            group_code = quota["group_code"]
            from api_request import get_package
            package_details = get_package(api_key, tokens, quota_code)
            family_code = "N/A"
            if package_details:
                family_code = package_details["package_family"]["package_family_code"]
            message += (
                f"📦 *Paket {i}*\n"
                f"   Nama: {name}\n"
                f"   Kode Kuota: `{quota_code}`\n"
                f"   Kode Family: `{family_code}`\n"
                f"   Kode Grup: `{group_code}`\n"
            )
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error viewing packages: {e}")
        await query.message.edit_text("❌ Terjadi kesalahan saat mengambil data paket.")

async def buy_xut_packages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display XUT packages"""
    query = update.callback_query
    await query.answer()
    try:
        tokens = AuthInstance.get_active_tokens()
        if not tokens:
            await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
            return
        await query.message.edit_text("🔄 Mengambil daftar paket XUT...")
        packages = get_package_xut()
        if not packages:
            await query.message.edit_text("❌ Gagal mengambil data paket XUT.")
            return
        context.user_data['xut_packages'] = packages
        message = "*🛒 Paket XUT (Unli Turbo)*\n"
        keyboard = []
        for index, pkg in enumerate(packages):
            message += f"{pkg['number']}. {pkg['name']} - Rp {pkg['price']}\n"
            keyboard.append([InlineKeyboardButton(
                f"{pkg['number']}. {pkg['name']} (Rp {pkg['price']})",
                callback_data=f'xut_select_{index}'
            )])
        keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error fetching XUT packages: {e}")
        await query.message.edit_text("❌ Terjadi kesalahan saat mengambil data paket XUT.")

async def show_xut_package_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menampilkan detail paket XUT yang dipilih"""
    query = update.callback_query
    await query.answer()
    try:
        _, _, index_str = query.data.split('_')
        index = int(index_str)
        packages = context.user_data.get('xut_packages', [])
        if not packages or index >= len(packages):
            await query.message.edit_text("❌ Paket tidak ditemukan.")
            return
        package_info = packages[index]
        package_code = package_info['code']
        await query.message.edit_text("🔄 Mengambil detail paket...")
        tokens = AuthInstance.get_active_tokens()
        api_key = AuthInstance.api_key
        package_details = get_package(api_key, tokens, package_code)
        if not package_details:
            await query.message.edit_text("❌ Gagal mengambil detail paket.")
            return
        name1 = package_details.get("package_family", {}).get("name", "")
        name2 = package_details.get("package_detail_variant", {}).get("name", "")
        name3 = package_details.get("package_option", {}).get("name", "")
        package_name = f"{name1} {name2} {name3}".strip()
        price = package_details["package_option"]["price"]
        validity = package_details["package_option"]["validity"]
        tnc = display_html(package_details["package_option"]["tnc"])
        token_confirmation = package_details["token_confirmation"]
        benefits = package_details["package_option"]["benefits"]
        context.user_data['selected_package'] = {
            'code': package_code, 'name': package_name, 'price': price,
            'validity': validity, 'tnc': tnc, 'token_confirmation': token_confirmation,
            'benefits': benefits
        }
        benefits_text = "\n".join([format_benefit(b) for b in benefits]) if benefits else "Tidak ada informasi benefit."
        message = (
            f"📦 *Detail Paket XUT*\n"
            f"🏷 *Nama:* {package_name}\n"
            f"💰 *Harga:* Rp {price}\n"
            f"📅 *Masa Aktif:* {validity} hari\n"
            f"🔷 *Benefits:*\n{benefits_text}\n"
            f"📝 *Syarat & Ketentuan:*\n{tnc[:300]}..."
        )
        keyboard = [
            [InlineKeyboardButton("📲 Beli dengan QRIS", callback_data='buy_xut_qris')],
            [InlineKeyboardButton("🔙 Kembali", callback_data='buy_xut')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except (ValueError, IndexError):
        await query.message.edit_text("❌ Data paket tidak valid.")
    except Exception as e:
        logger.error(f"Error showing XUT package details: {e}")
        await query.message.edit_text("❌ Terjadi kesalahan saat menampilkan detail paket.")

async def buy_xut_with_pulsa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses pembelian paket XUT dengan Pulsa"""
    query = update.callback_query
    await query.answer()
    package_info = context.user_data.get('selected_package')
    if not package_info:
        await query.message.edit_text("❌ Informasi paket tidak ditemukan. Silakan pilih paket kembali.")
        return
    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Anda belum login. Silakan login terlebih dahulu.")
        return
    api_key = AuthInstance.api_key
    package_code = package_info['code']
    await query.message.edit_text("🔄 Memproses pembelian dengan Pulsa...")
    try:
        from api_request import purchase_package
        result = purchase_package(api_key, tokens, package_code)
        if result and result.get("status") == "SUCCESS":
            await query.message.edit_text(
                "✅ Pembelian paket dengan Pulsa berhasil diinisiasi!\n"
                "Silakan cek hasil pembelian di aplikasi MyXL."
            )
        else:
            await query.message.edit_text(
                "❌ Gagal membeli paket dengan Pulsa.\n"
                "Silakan coba lagi atau gunakan metode pembayaran lain."
            )
    except Exception as e:
        logger.error(f"Error processing Pulsa payment: {e}")
        await query.message.edit_text(
            "❌ Terjadi kesalahan saat memproses pembelian dengan Pulsa.\n"
            "Silakan coba lagi."
        )

async def buy_xut_with_ewallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses pembelian paket XUT dengan E-Wallet (simulasi)"""
    query = update.callback_query
    await query.answer()
    package_info = context.user_data.get('selected_package')
    if not package_info:
        await query.message.edit_text("❌ Informasi paket tidak ditemukan. Silakan pilih paket kembali.")
        return
    message = (
        "💳 *Pembelian dengan E-Wallet*\n"
        "Untuk menyelesaikan pembelian dengan E-Wallet:\n"
        "1. Buka aplikasi pembayaran Anda (DANA, OVO, GoPay, ShopeePay)\n"
        "2. Pilih menu Bayar atau Scan QR\n"
        "3. Gunakan kode pembayaran berikut:\n"
        f"   `EW-{package_info['code']}-{int(package_info['price'])}`\n"
        f"4. Konfirmasi pembayaran sebesar Rp {package_info['price']}\n"
        "Setelah pembayaran berhasil, paket akan otomatis masuk ke akun Anda."
    )
    keyboard = [[InlineKeyboardButton("🏠 Menu Utama", callback_data='main_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def buy_xut_with_qris(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses pembelian paket dengan QRIS"""
    query = update.callback_query
    await query.answer()
    package_info = context.user_data.get('selected_package')
    if not package_info:
        await query.message.edit_text("❌ Informasi paket tidak ditemukan. Silakan pilih paket kembali.")
        return
    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Anda belum login. Silakan login terlebih dahulu.")
        return
    api_key = AuthInstance.api_key
    package_code = package_info['code']
    price = package_info['price']
    package_name = package_info['name']
    token_confirmation = package_info['token_confirmation']
    await query.message.edit_text("🔄 Memproses pembayaran QRIS...")
    try:
        payment_methods_data = get_payment_methods(api_key, tokens, token_confirmation, package_code)
        if not payment_methods_data:
            await query.message.edit_text("❌ Gagal mendapatkan metode pembayaran QRIS.")
            return
        token_payment = payment_methods_data["token_payment"]
        ts_to_sign = payment_methods_data["timestamp"]
        transaction_id = settlement_qris(api_key, tokens, token_payment, ts_to_sign, package_code, price, package_name)
        if not transaction_id:
            await query.message.edit_text("❌ Gagal membuat transaksi QRIS.")
            return
        qris_data = get_qris_code(api_key, tokens, transaction_id)
        if not qris_data:
            await query.message.edit_text("❌ Gagal mendapatkan data QRIS.")
            return
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(qris_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        caption = (
            f"📲 *Pembayaran QRIS*\n"
            f"Silakan scan QR Code di bawah ini untuk menyelesaikan pembayaran.\n"
            f"📦 *Paket:* {package_name}\n"
            f"💰 *Harga:* Rp {price}\n"
            f"Setelah pembayaran berhasil, paket akan otomatis masuk ke akun Anda."
        )
        await query.message.reply_photo(photo=img_buffer, caption=caption, parse_mode='Markdown')
        await query.message.edit_text(
            "✅ QR Code pembayaran telah dikirim!\n"
            "Silakan scan QR Code yang dikirim di atas untuk menyelesaikan pembayaran."
        )
        if 'selected_package' in context.user_data:
            del context.user_data['selected_package']
    except Exception as e:
        logger.error(f"Error processing QRIS payment: {e}", exc_info=True)
        await query.message.edit_text(
            "❌ Terjadi kesalahan saat memproses pembayaran QRIS.\n"
            "Silakan coba lagi atau hubungi administrator jika masalah berlanjut."
        )

async def request_family_code(update: Update, context: ContextTypes.DEFAULT_TYPE, is_enterprise: bool) -> None:
    """Minta Family Code dari pengguna"""
    query = update.callback_query
    await query.answer()
    context.user_data['state'] = 'waiting_family_code'
    context.user_data['enterprise'] = is_enterprise
    message = "🔍 Silakan kirimkan Family Code"
    if is_enterprise:
        message += " (Enterprise)"
    message += ":"
    keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(message, reply_markup=reply_markup)

async def handle_family_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menangani input Family Code"""
    if context.user_data.get('state') != 'waiting_family_code':
        return
    family_code = update.message.text.strip()
    is_enterprise = context.user_data.get('enterprise', False)
    context.user_data['selected_family_code'] = family_code
    await update.message.reply_text("🔄 Mengambil daftar paket...")
    await show_family_packages(update, context, family_code, is_enterprise)

async def show_family_packages(update: Update, context: ContextTypes.DEFAULT_TYPE, family_code: str, is_enterprise: bool) -> None:
    """Display packages for a specific family code"""
    try:
        tokens = AuthInstance.get_active_tokens()
        if not tokens:
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
            else:
                await update.message.reply_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
            return
        api_key = AuthInstance.api_key
        data = get_family(api_key, tokens, family_code, is_enterprise)
        if not data:
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.message.edit_text("❌ Gagal memuat data family.")
            else:
                await update.message.reply_text("❌ Gagal memuat data family.")
            return
        package_variants = data["package_variants"]
        if not package_variants:
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.message.edit_text("📭 Tidak ada paket tersedia untuk family ini.")
            else:
                await update.message.reply_text("📭 Tidak ada paket tersedia untuk family ini.")
            return
        context.user_data['family_data'] = data
        context.user_data['family_packages'] = []
        message = f"*Family Name:* {data['package_family']['name']}\n"
        keyboard = []
        option_number = 1
        for variant in package_variants:
            variant_name = variant["name"]
            message += f"🔹 *Variant:* {variant_name}\n"
            for option in variant["package_options"]:
                option_name = option["name"]
                price = option["price"]
                code = option["package_option_code"]
                context.user_data['family_packages'].append({
                    "number": option_number, "name": option_name,
                    "price": price, "code": code
                })
                message += f"{option_number}. {option_name} - Rp {price}\n"
                keyboard.append([InlineKeyboardButton(
                    f"{option_number}. {option_name} (Rp {price})", 
                    callback_data=f'family_pkg_{option_number}'
                )])
                option_number += 1
        message += "\n00. Kembali ke menu sebelumnya"
        keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error fetching family packages: {e}")
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.edit_text("❌ Terjadi kesalahan saat mengambil data paket family.")
        else:
            await update.message.reply_text("❌ Terjadi kesalahan saat mengambil data paket family.")

async def show_family_package_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menampilkan detail paket family yang dipilih"""
    query = update.callback_query
    await query.answer()
    try:
        _, _, pkg_number_str = query.data.split('_')
        pkg_number = int(pkg_number_str)
        packages = context.user_data.get('family_packages', [])
        selected_pkg = next((p for p in packages if p["number"] == pkg_number), None)
        if not selected_pkg:
            await query.message.edit_text("❌ Paket tidak ditemukan.")
            return
        package_code = selected_pkg['code']
        await query.message.edit_text("🔄 Mengambil detail paket...")
        tokens = AuthInstance.get_active_tokens()
        api_key = AuthInstance.api_key
        package_details = get_package(api_key, tokens, package_code)
        if not package_details:
            await query.message.edit_text("❌ Gagal mengambil detail paket.")
            return
        name1 = package_details.get("package_family", {}).get("name", "")
        name2 = package_details.get("package_detail_variant", {}).get("name", "")
        name3 = package_details.get("package_option", {}).get("name", "")
        package_name = f"{name1} {name2} {name3}".strip()
        price = package_details["package_option"]["price"]
        validity = package_details["package_option"]["validity"]
        tnc = display_html(package_details["package_option"]["tnc"])
        token_confirmation = package_details["token_confirmation"]
        benefits = package_details["package_option"]["benefits"]
        context.user_data['selected_package'] = {
            'code': package_code, 'name': package_name, 'price': price,
            'validity': validity, 'tnc': tnc,
            'token_confirmation': token_confirmation, 'benefits': benefits
        }
        benefits_text = "\n".join([format_benefit(b) for b in benefits]) if benefits else "Tidak ada informasi benefit."
        message = (
            f"📦 *Detail Paket Family*\n"
            f"🏷 *Nama:* {package_name}\n"
            f"💰 *Harga:* Rp {price}\n"
            f"📅 *Masa Aktif:* {validity} hari\n"
            f"🔷 *Benefits:*\n{benefits_text}\n"
            f"📝 *Syarat & Ketentuan:*\n{tnc[:300]}..."
        )
        keyboard = [
            [InlineKeyboardButton("💳 Beli dengan Pulsa", callback_data='buy_family_pulsa')],
            [InlineKeyboardButton("💳 Beli dengan E-Wallet", callback_data='buy_family_ewallet')],
            [InlineKeyboardButton("📲 Beli dengan QRIS", callback_data='buy_family_qris')],
            [InlineKeyboardButton("🔙 Kembali", callback_data='buy_family')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except (ValueError, IndexError):
        await query.message.edit_text("❌ Data paket tidak valid.")
    except Exception as e:
        logger.error(f"Error showing family package details: {e}")
        await query.message.edit_text("❌ Terjadi kesalahan saat menampilkan detail paket.")

async def buy_family_with_pulsa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses pembelian paket family dengan Pulsa"""
    await buy_xut_with_pulsa(update, context)

async def buy_family_with_ewallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses pembelian paket family dengan E-Wallet (simulasi)"""
    await buy_xut_with_ewallet(update, context)

async def buy_family_with_qris(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memulai proses pembelian paket family dengan QRIS"""
    await buy_xut_with_qris(update, context)

async def show_account_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menampilkan informasi akun dengan format tanggal yang benar."""
    query = update.callback_query
    await query.answer()
    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
        return
    try:
        await query.message.edit_text("🔄 Mengambil informasi akun...")
        api_key = AuthInstance.api_key
        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token")
        
        profile_data = get_profile(api_key, access_token, id_token)
        if not profile_data:
            await query.message.edit_text("❌ Gagal mengambil data profil.")
            return
        
        # Mengambil nomor dari profile_data, bukan dari active_user
        msisdn = profile_data.get("msisdn", "N/A") 
        
        balance_data = get_balance(api_key, id_token)
        if not balance_data:
            await query.message.edit_text("❌ Gagal mengambil data saldo.")
            return
            
        remaining = balance_data.get("remaining", 0)
        
        # --- BAGIAN YANG DIPERBAIKI ---
        expired_timestamp = balance_data.get("expired_at")
        masa_aktif_str = "N/A"  # Nilai default
        try:
            # Konversi timestamp (dalam detik) ke format tanggal
            if isinstance(expired_timestamp, (int, float)):
                dt_object = datetime.datetime.fromtimestamp(expired_timestamp)
                masa_aktif_str = dt_object.strftime("%d %B %Y")  # Format: 28 Oktober 2025
            msisdn = profile_data.get("profile", {}).get("msisdn", "N/A")
        except (ValueError, TypeError):
            # Jika formatnya sudah string atau tidak valid, tampilkan apa adanya
            masa_aktif_str = str(expired_timestamp)
        # --- AKHIR BAGIAN PERBAIKAN ---

        message = (
            f"👤 *Informasi Akun MyXL*\n"
            f"📱 *Nomor:* {msisdn}\n"
            f"💰 *Pulsa:* Rp {remaining:,}\n"
            f"📅 *Masa Aktif:* {masa_aktif_str}" # Gunakan string yang sudah diformat
        )
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data='main_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error showing account info: {e}")
        await query.message.edit_text("❌ Terjadi kesalahan saat mengambil informasi akun.")


async def buy_aniv_package_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Membeli paket "BONUS #TraktiranXL 28th Anniversary" secara langsung."""
    query = update.callback_query
    await query.answer()

    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        await query.message.edit_text("❌ Tidak ada akun aktif. Silakan login terlebih dahulu.")
        return

    await query.message.edit_text("🔄 Memproses pembelian paket Aniv...")

    try:
        FAMILY_CODE_ANIV = "6fda76ee-e789-4897-89fb-9114da47b805"
        PACKAGE_NUMBER_ANIV = 7
        PACKAGE_NAME_ANIV = "BONUS #TraktiranXL 28th Anniversary"
        AMOUNT_ANIV = 500
        
        api_key = AuthInstance.api_key
        family_data = get_family(api_key, tokens, FAMILY_CODE_ANIV, is_enterprise=False)
        if not family_data:
             await query.message.edit_text("❌ Gagal mengambil data paket Aniv.")
             return
        package_variants = family_data.get("package_variants", [])
        if not package_variants:
             await query.message.edit_text("📭 Tidak ada paket tersedia untuk Family Code Aniv.")
             return

        all_packages_list = []
        option_number = 1
        for variant in package_variants:
            for option in variant["package_options"]:
                code = option["package_option_code"]
                all_packages_list.append({"number": option_number, "code": code})
                option_number += 1
        target_package_info = next((p for p in all_packages_list if p["number"] == PACKAGE_NUMBER_ANIV), None)
        if not target_package_info:
            await query.message.edit_text(
                f"❌ Paket nomor {PACKAGE_NUMBER_ANIV} ({PACKAGE_NAME_ANIV}) tidak ditemukan "
                f"pada Family Code `{FAMILY_CODE_ANIV}`."
            )
            return

        PACKAGE_CODE_ANIV = target_package_info['code']
        package_details = get_package(api_key, tokens, PACKAGE_CODE_ANIV)
        if not package_details:
             await query.message.edit_text("❌ Gagal mengambil detail paket Aniv.")
             return
        TOKEN_CONFIRMATION_ANIV = package_details.get("token_confirmation", "")
        if not TOKEN_CONFIRMATION_ANIV:
             await query.message.edit_text("❌ Gagal mendapatkan token konfirmasi untuk paket Aniv.")
             return
        logger.info(f"[ANIV DIRECT] Paket ditemukan: {PACKAGE_NAME_ANIV} ({PACKAGE_CODE_ANIV})")
        context.user_data['tmp_direct_aniv_data'] = {
            'package_code': PACKAGE_CODE_ANIV,
            'package_name': PACKAGE_NAME_ANIV,
            'token_confirmation': TOKEN_CONFIRMATION_ANIV,
            'confirmed_price': AMOUNT_ANIV
        }
        await _process_direct_aniv_qris_payment(query.message, context, api_key, tokens)
    except Exception as e:
        logger.error(f"[ANIV DIRECT] Error memulai pembelian: {e}", exc_info=True)
        await query.message.edit_text(
            "❌ Terjadi kesalahan saat memulai pembelian paket Aniv.\n"
            "Silakan coba lagi atau hubungi administrator."
        )

async def _process_direct_aniv_qris_payment(
    main_message, context: ContextTypes.DEFAULT_TYPE,
    api_key: str, tokens: dict
):
    """Fungsi internal untuk memproses pembayaran QRIS paket Aniv dengan amount otomatis."""
    tmp_data = context.user_data.get('tmp_direct_aniv_data')
    if not tmp_data:
        await main_message.edit_text("❌ Data paket Aniv tidak ditemukan. Silakan ulangi proses.")
        return

    package_code = tmp_data['package_code']
    package_name = tmp_data['package_name']
    token_confirmation = tmp_data['token_confirmation']
    confirmed_price = tmp_data['confirmed_price']

    try:
        await main_message.edit_text(f"🔄 Memproses pembayaran QRIS untuk paket:\n`{package_name}`", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"[ANIV QRIS] Gagal mengedit pesan utama: {e}")
        return

    try:
        payment_methods_data = get_payment_methods(api_key, tokens, token_confirmation, package_code)
        if not payment_methods_data:
            await main_message.edit_text("❌ Gagal mendapatkan metode pembayaran QRIS untuk paket Aniv.")
            return

        token_payment = payment_methods_data["token_payment"]
        ts_to_sign = payment_methods_data["timestamp"]

        transaction_id = settlement_qris(api_key, tokens, token_payment, ts_to_sign, package_code, 0, package_name, force_amount=True)
        if not transaction_id:
            error_msg = (
                f"❌ Gagal membuat transaksi QRIS untuk paket Aniv (`{package_name}`).\n\n"
                f"*Harga yang dikirim:* Rp {confirmed_price}\n\n"
                "*Penyebab yang Mungkin:*\n"
                "• Jumlah pembayaran (Rp 500) tidak dikenali oleh API MyXL.\n"
                "• Token konfirmasi mungkin sudah kadaluarsa.\n\n"
                "*Solusi:*\n"
                "1. Coba lagi dalam beberapa menit.\n"
                "2. Hubungi administrator jika masalah berlanjut."
            )
            await main_message.edit_text(error_msg, parse_mode='Markdown')
            logger.error(f"[ANIV QRIS] Gagal membuat settlement. Price yang dikirim: {confirmed_price}")
            return

        qris_data = get_qris_code(api_key, tokens, transaction_id)
        if not qris_data:
            await main_message.edit_text("❌ Gagal mendapatkan data QRIS untuk paket Aniv.")
            return

        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(qris_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        caption = (
            f"🎉 *Pembayaran QRIS (Paket Bonus)*\n"
            f"Silakan scan QR Code di bawah ini untuk menyelesaikan pembayaran.\n"
            f"📦 *Paket:* Bonus Kuota 2.8 GB\n"
            f"Setelah pembayaran berhasil, paket akan otomatis masuk ke akun Anda."
        )
        await main_message.reply_photo(photo=img_buffer, caption=caption, parse_mode='Markdown')
        await main_message.edit_text(
            f"✅ QR Code pembayaran untuk paket Aniv (`{package_name}`) telah dikirim!\n"
            "Silakan scan QR Code yang dikirim di atas."
        )
        context.user_data.pop('tmp_direct_aniv_data', None)
    except Exception as e:
        logger.error(f"[ANIV QRIS] Error processing payment: {e}", exc_info=True)
        try:
            await main_message.edit_text("❌ Terjadi kesalahan saat memproses pembayaran QRIS untuk paket Aniv.")
        except:
            pass
        context.user_data.pop('tmp_direct_aniv_data', None)




@authorized_only
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menangani semua callback dari tombol. Dilindungi oleh otorisasi."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.info(f"User {query.from_user.id} pressed button: {data}")

    if data == 'main_menu': await show_main_menu(update, context)
    elif data == 'login_menu': await initiate_login(update, context)
    elif data == 'switch_account_menu': await initiate_switch_account(update, context)
    elif data == 'view_packages': await view_packages(update, context)
    elif data == 'buy_xut': await buy_xut_packages(update, context)
    elif data == 'buy_xut_vidio_direct': await buy_xut_vidio_direct(update, context)
    
    # --- TAMBAHKAN BARIS DI BAWAH INI ---
    elif data == 'buy_biz_starter_direct': await buy_biz_starter_direct(update, context)
    
    elif data.startswith('xut_select_'): await show_xut_package_details(update, context)
    elif data == 'buy_xut_pulsa': await buy_xut_with_pulsa(update, context)
    elif data == 'buy_xut_ewallet': await buy_xut_with_ewallet(update, context)
    elif data == 'buy_xut_qris': await buy_xut_with_qris(update, context)
    elif data == 'buy_family': await request_family_code(update, context, is_enterprise=False)
    elif data == 'buy_family_enterprise': await request_family_code(update, context, is_enterprise=True)
    elif data.startswith('family_pkg_'): await show_family_package_details(update, context)
    elif data == 'buy_family_pulsa': await buy_family_with_pulsa(update, context)
    elif data == 'buy_family_ewallet': await buy_family_with_ewallet(update, context)
    elif data == 'buy_family_qris': await buy_family_with_qris(update, context)
    elif data == 'buy_aniv_direct': await buy_aniv_package_direct(update, context)
    elif data == 'buy_aniv_direct': await buy_aniv_package_direct(update, context) # Baris ini tetap ada jika Anda masih ingin menggunakannya  
    elif data == 'buy_hot_package_menu': await buy_hot_package_menu(update, context)
    elif data.startswith('hotpkg_'): await process_hot_package_selection(update, context)
    elif data == 'pay_hot_package_qris': await pay_hot_package_with_qris(update, context)
    elif data == 'buy_biz_starter_direct': await buy_biz_starter_direct(update, context)
    elif data == 'buy_biz_manufacture_direct': await buy_biz_manufacture_direct(update, context)
    elif data == 'account_info': await show_account_info(update, context)
    else: await query.message.edit_text("❌ Fitur belum diimplementasikan.")


# === MAIN FUNCTION ===
def main() -> None:
    """Fungsi utama untuk menjalankan bot."""
    initialize_database()

    logger.info(f"Memastikan admin ({ADMIN_ID}) memiliki akses...")
    set_user_access(ADMIN_ID, True, username="BotAdmin", first_name="Admin")

    logger.info("Memulai MyXL Telegram Bot...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("grant", grant_access))
    application.add_handler(CommandHandler("revoke", revoke_access))
    application.add_handler(CommandHandler("stats", stats))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^\d{6}$') & ~filters.COMMAND, handle_otp_input))
    application.add_handler(MessageHandler(filters.Regex(r'^(08|628)\d{8,12}$') & ~filters.COMMAND, handle_phone_number_input))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_family_code_input))

    logger.info("Bot sedang berjalan...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
