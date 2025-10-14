from datetime import datetime, timezone, timedelta
import json
import uuid
import base64
import qrcode

import time
import requests
from app.client.engsel import *
from app.client.encrypt import API_KEY, decrypt_xdata, encryptsign_xdata, java_like_timestamp, get_x_signature_payment
from app.type_dict import PaymentItem

def settlement_qris(
    api_key: str,
    tokens: dict,
    token_payment: str,
    ts_to_sign: int,
    payment_target: str,
    price: int,
    item_name: str = "",
    force_amount: bool = False,
):
    """
    Membuat transaksi QRIS.
    """
    path = "payments/api/v8/settlement-multipayment/qris"
    
    # Payload dasar yang lebih lengkap
    settlement_payload = {
        "channel": "MYXL_PWA",
        "payment_method_detail": None,
        "akrab": {
            "akrab_members": [],
            "akrab_parent_alias": "",
            "members": []
        },
        "can_trigger_rating": False,
        "total_discount": 0,
        "coupon": "",
        "payment_for": "BUY_PACKAGE",
        "topup_number": "",
        "is_enterprise": False,
        "autobuy": {
            "is_using_autobuy": False,
            "activated_autobuy_code": "",
            "autobuy_threshold_setting": {
                "label": "",
                "type": "",
                "value": 0
            }
        },
        "access_token": tokens["access_token"],
        "is_myxl_wallet": False,
        "additional_data": {
            "original_price": price,
            "is_spend_limit_temporary": False,
            "migration_type": "",
            "spend_limit_amount": 0,
            "is_spend_limit": False,
            "tax": 0,
            "benefit_type": "",
            "quota_bonus": 0,
            "cashtag": "",
            "is_family_plan": False,
            "combo_details": [],
            "is_switch_plan": False,
            "discount_recurring": 0,
            "has_bonus": False,
            "discount_promo": 0
        },
        "total_amount": price,
        "total_fee": 0,
        "is_use_point": False,
        "lang": "en",
        "items": [{
            "item_code": payment_target,
            "product_type": "",
            "item_price": price,
            "item_name": item_name,
            "tax": 0
        }],
        "verification_token": token_payment,
        "payment_method": "QRIS",
        "timestamp": int(time.time())
    }
    
    if force_amount:
        settlement_payload["total_amount"] = 500
        settlement_payload["additional_data"]["original_price"] = price
        settlement_payload["items"][0]["item_price"] = price

    encrypted_payload = encryptsign_xdata(
        api_key=api_key,
        method="POST",
        path=path,
        id_token=tokens["id_token"],
        payload=settlement_payload
    )
    
    xtime = int(encrypted_payload["encrypted_body"]["xtime"])
    sig_time_sec = (xtime // 1000)
    x_requested_at = datetime.fromtimestamp(sig_time_sec, tz=timezone.utc).astimezone()
    settlement_payload["timestamp"] = ts_to_sign
    
    body = encrypted_payload["encrypted_body"]

    # ================================================================= #
    # ## INI ADALAH PERBAIKAN FINAL YANG SESUAI DENGAN encrypt.py ANDA ##
    # ================================================================= #
    x_sig = get_x_signature_payment(
        api_key=api_key,
        access_token=tokens["access_token"],
        sig_time_sec=ts_to_sign,
        package_code=payment_target,
        token_payment=token_payment,
        payment_method=settlement_payload["payment_method"], # "QRIS"
        payment_for=settlement_payload["payment_for"],   # "BUY_PACKAGE"
        path=path
    )
    # ================================================================= #
    
    headers = {
        "host": BASE_API_URL.replace("https://", ""),
        "content-type": "application/json; charset=utf-8",
        "user-agent": UA,
        "x-api-key": API_KEY,
        "authorization": f"Bearer {tokens['id_token']}",
        "x-hv": "v3",
        "x-signature-time": str(sig_time_sec),
        "x-signature": x_sig,
        "x-request-id": str(uuid.uuid4()),
        "x-request-at": java_like_timestamp(x_requested_at),
        "x-version-app": "8.6.0",
    }
    
    url = f"{BASE_API_URL}/{path}"
    print("Sending settlement request with payload:", json.dumps(settlement_payload))
    resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    
    try:
        decrypted_body = decrypt_xdata(api_key, json.loads(resp.text))
        if decrypted_body["status"] != "SUCCESS":
            print("Failed to initiate settlement.")
            print(f"Error: {decrypted_body}")
            return None
        
        transaction_id = decrypted_body["data"]["transaction_code"]
        
        return transaction_id
    except Exception as e:
        print("[decrypt err]", e)
        return None

def get_qris_code(
    api_key: str,
    tokens: dict,
    transaction_id: str
):
    path = "payments/api/v8/pending-detail"
    payload = {
        "transaction_id": transaction_id,
        "is_enterprise": False,
        "lang": "en",
        "status": ""
    }
    
    res = send_api_request(api_key, path, payload, tokens["id_token"], "POST")
    if res["status"] != "SUCCESS":
        print("Failed to fetch QRIS code.")
        print(f"Error: {res}")
        return None
    
    return res["data"]["qr_code"]

def show_qris_payment(
    api_key: str,
    tokens: dict,
    items: list[PaymentItem],
    payment_for: str,
    ask_overwrite: bool,
    overwrite_amount: int = -1,
    token_confirmation_idx: int = 0,
    amount_idx: int = -1,
):  
    transaction_id = settlement_qris(
        api_key,
        tokens,
        items,
        payment_for,
        ask_overwrite,
        overwrite_amount,
        token_confirmation_idx,
        amount_idx
    )
    
    if not transaction_id:
        print("Failed to create QRIS transaction.")
        return
    
    print("Fetching QRIS code...")
    qris_code = get_qris_code(api_key, tokens, transaction_id)
    if not qris_code:
        print("Failed to get QRIS code.")
        return
    print(f"QRIS data:\n{qris_code}")
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=1,
    )
    qr.add_data(qris_code)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    
    qris_b64 = base64.urlsafe_b64encode(qris_code.encode()).decode()
    qris_url = f"https://ki-ar-kod.netlify.app/?data={qris_b64}"
    
    print(f"Atau buka link berikut untuk melihat QRIS:\n{qris_url}")
    
    return
