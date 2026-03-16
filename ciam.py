import base64
import os
import json
import uuid
import requests
from datetime import datetime, timezone, timedelta

from app.client.encrypt import (
    java_like_timestamp,
    ts_gmt7_without_colon,
    ax_api_signature,
    load_ax_fp,
    ax_device_id,
)

BASE_CIAM_URL = os.getenv("BASE_CIAM_URL")
if not BASE_CIAM_URL:
    raise ValueError("BASE_CIAM_URL environment variable not set")

BASIC_AUTH = os.getenv("BASIC_AUTH")
if not BASIC_AUTH:
    raise ValueError("BASIC_AUTH environment variable not set")

UA = os.getenv("UA")
if not UA:
    raise ValueError("UA environment variable not set")

AX_DEVICE_ID = ax_device_id()
AX_FP = load_ax_fp()


def validate_contact(contact: str) -> bool:
    if not contact.startswith("628") or len(contact) > 14:
        print("Invalid number")
        return False
    return True


def _base_headers(content_type: str) -> dict:
    return {
        "Accept-Encoding": "gzip, deflate, br",
        "Authorization": f"Basic {BASIC_AUTH}",
        "Ax-Device-Id": AX_DEVICE_ID,
        "Ax-Fingerprint": AX_FP,
        "Ax-Request-Device": "samsung",
        "Ax-Request-Device-Model": "SM-N935F",
        "Ax-Request-Id": str(uuid.uuid4()),
        "Ax-Substype": "PREPAID",
        "Content-Type": content_type,
        "Host": BASE_CIAM_URL.replace("https://", ""),
        "User-Agent": UA,
    }


def get_otp(contact: str) -> str | None:
    if not validate_contact(contact):
        return None

    url = BASE_CIAM_URL + "/realms/xl-ciam/auth/otp"
    querystring = {
        "contact": contact,
        "contactType": "SMS",
        "alternateContact": "false",
    }

    now = datetime.now(timezone(timedelta(hours=7)))
    headers = _base_headers("application/json")
    headers["Ax-Request-At"] = java_like_timestamp(now)

    print("Requesting OTP...")
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=30)
        print("OTP status", response.status_code)
        print("OTP response body", response.text)

        try:
            json_body = response.json()
        except ValueError:
            json_body = {}

        if isinstance(json_body, dict) and json_body.get("subscriber_id"):
            return str(json_body["subscriber_id"])

        if response.status_code in (200, 201, 202, 204):
            if isinstance(json_body, dict):
                error_text = str(json_body.get("error") or json_body.get("message") or "").lower()
                if error_text and any(
                    x in error_text
                    for x in ["invalid", "failed", "denied", "forbidden", "unauthorized"]
                ):
                    print(json_body.get("error") or json_body.get("message") or "OTP request rejected")
                    return None
            return contact

        if isinstance(json_body, dict):
            print(json_body.get("error") or json_body.get("message") or f"HTTP {response.status_code}")
        else:
            print(f"OTP request failed with HTTP {response.status_code}")
        return None
    except Exception as e:
        print(f"Error requesting OTP: {e}")
        return None



def extend_session(subscriber_id: str) -> str | None:
    b64_subscriber_id = base64.b64encode(subscriber_id.encode()).decode()
    url = f"{BASE_CIAM_URL}/realms/xl-ciam/auth/extend-session"
    querystring = {
        "contact": b64_subscriber_id,
        "contactType": "DEVICEID",
    }

    now = datetime.now(timezone(timedelta(hours=7)))
    headers = _base_headers("application/json")
    headers["Ax-Request-At"] = java_like_timestamp(now)

    print("Extending session...")
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=30)
        if response.status_code != 200:
            print(f"Failed to extend session: {response.status_code} - {response.text}")
            return None

        data = response.json()
        return data.get("data", {}).get("exchange_code")
    except Exception as e:
        print(f"Error extending session: {e}")
        return None



def submit_otp(api_key: str, contact_type: str, contact: str, code: str):
    if contact_type == "SMS":
        if not validate_contact(contact):
            print("Invalid number")
            return None
        if not code or len(code) != 6:
            print("Invalid OTP code format")
            return None
        final_contact = contact
        final_code = code
    elif contact_type == "DEVICEID":
        final_contact = base64.b64encode(contact.encode()).decode()
        final_code = code
    else:
        print("Unsupported contact type")
        return None

    url = BASE_CIAM_URL + "/realms/xl-ciam/protocol/openid-connect/token"

    now_gmt7 = datetime.now(timezone(timedelta(hours=7)))
    ts_for_sign = ts_gmt7_without_colon(now_gmt7)
    ts_header = ts_gmt7_without_colon(now_gmt7 - timedelta(minutes=5))
    signature = ax_api_signature(api_key, ts_for_sign, final_contact, code, contact_type)

    payload = {
        "contactType": contact_type,
        "code": final_code,
        "grant_type": "password",
        "contact": final_contact,
        "scope": "openid",
    }

    headers = _base_headers("application/x-www-form-urlencoded")
    headers["Ax-Api-Signature"] = signature
    headers["Ax-Request-At"] = ts_header

    print("Submitting OTP...")
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        print("submit_otp status:", response.status_code)
        print("submit_otp content-type:", response.headers.get("Content-Type", ""))
        raw_text = (response.text or "").strip()
        print("submit_otp body:", raw_text if raw_text else "<empty>")

        if response.status_code != 200:
            print("Server reject login")
            return None

        if not raw_text:
            print("Server returned empty response")
            return None

        try:
            data = response.json()
        except Exception:
            print("Response bukan JSON")
            return None

        if not isinstance(data, dict):
            print("Response format tidak valid")
            return None

        if data.get("error"):
            print("Login gagal:", data)
            return None

        if "access_token" in data and "refresh_token" in data:
            print("Login successful.")
            return data

        print("Login gagal:", data)
        return None
    except requests.RequestException as e:
        print(f"[Error submit_otp]: {e}")
        return None
    except Exception as e:
        print(f"[Error submit_otp unexpected]: {type(e).__name__}: {e}")
        return None



def get_new_token(api_key: str, refresh_token: str, subscriber_id: str):
    url = BASE_CIAM_URL + "/realms/xl-ciam/protocol/openid-connect/token"

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    now_gmt7 = datetime.now(timezone(timedelta(hours=7)))
    ts_for_sign = ts_gmt7_without_colon(now_gmt7)
    ts_header = ts_gmt7_without_colon(now_gmt7 - timedelta(minutes=5))
    signature = ax_api_signature(api_key, ts_for_sign, refresh_token)

    headers = _base_headers("application/x-www-form-urlencoded")
    headers["Ax-Api-Signature"] = signature
    headers["Ax-Request-At"] = ts_header

    print("Getting new token via refresh token...")
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        print("refresh_token status", response.status_code)
        print("refresh_token body", response.text)

        data = response.json()
        if data.get("refresh_token"):
            return data

        print("Refresh token invalid, trying extend_session...")
        exchange_code = extend_session(subscriber_id)
        if not exchange_code:
            return None

        return submit_otp(api_key, "DEVICEID", subscriber_id, exchange_code)
    except Exception as e:
        print(f"Error getting new token: {e}")
        return None
