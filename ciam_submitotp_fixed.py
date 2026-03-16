import base64
import os
import json
import uuid
import requests
from urllib.parse import urlparse

from datetime import datetime, timezone, timedelta

from app.client.encrypt import (
    java_like_timestamp,
    ts_gmt7_without_colon,
    ax_api_signature,
    load_ax_fp,
    ax_device_id
)

BASE_CIAM_URL = os.getenv("BASE_CIAM_URL")
if not BASE_CIAM_URL:
    raise ValueError("BASE_CIAM_URL environment variable not set")

BASIC_AUTH = os.getenv("BASIC_AUTH")
AX_DEVICE_ID = ax_device_id()
AX_FP = load_ax_fp()
UA = os.getenv("UA")

def validate_contact(contact: str) -> bool:
    if not contact.startswith("628") or len(contact) > 14:
        print("Invalid number")
        return False
    return True

def get_otp(contact: str) -> str:
    if not validate_contact(contact):
        return None

    url = BASE_CIAM_URL + "/realms/xl-ciam/auth/otp"

    querystring = {
        "contact": contact,
        "contactType": "SMS",
        "alternateContact": "false"
    }

    now = datetime.now(timezone(timedelta(hours=7)))
    ax_request_at = java_like_timestamp(now)  # format: "2023-10-20T12:34:56.78+07:00"
    ax_request_id = str(uuid.uuid4())

    payload = ""
    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Authorization": f"Basic {BASIC_AUTH}",
        "Ax-Device-Id": AX_DEVICE_ID,
        "Ax-Fingerprint": AX_FP,
        "Ax-Request-At": ax_request_at,
        "Ax-Request-Device": "samsung",
        "Ax-Request-Device-Model": "SM-N935F",
        "Ax-Request-Id": ax_request_id,
        "Ax-Substype": "PREPAID",
        "Content-Type": "application/json",
        "Host": BASE_CIAM_URL.replace("https://", ""),
        "User-Agent": UA,
    }

    print("Requesting OTP...")
    try:
        response = requests.request("GET", url, data=payload, headers=headers, params=querystring, timeout=30)
        print("OTP status", response.status_code)
        print("OTP response body", response.text)

        try:
            json_body = response.json()
        except ValueError:
            json_body = {}

        # Respons lama: subscriber_id tersedia langsung
        if isinstance(json_body, dict) and json_body.get("subscriber_id"):
            return str(json_body["subscriber_id"])

        # Respons baru kadang tidak membawa subscriber_id, tapi OTP tetap terkirim.
        # Selama status HTTP sukses dan tidak ada error eksplisit, lanjutkan login memakai nomor input.
        if response.status_code in (200, 201, 202, 204):
            if isinstance(json_body, dict):
                error_text = str(json_body.get("error") or json_body.get("message") or "").lower()
                if error_text and any(x in error_text for x in ["invalid", "failed", "denied", "forbidden", "unauthorized"]):
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

def extend_session(subscriber_id: str) -> str:
    b64_subscriber_id = base64.b64encode(subscriber_id.encode()).decode()
    url = f"{BASE_CIAM_URL}/realms/xl-ciam/auth/extend-session"

    querystring = {
        "contact": b64_subscriber_id,
        "contactType": "DEVICEID"
    }
    
    now = datetime.now(timezone(timedelta(hours=7)))
    ax_request_at = java_like_timestamp(now)  # format: "2023-10-20T12:34:56.78+07:00"
    ax_request_id = str(uuid.uuid4())
    
    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Authorization": f"Basic {BASIC_AUTH}",
        "Ax-Device-Id": AX_DEVICE_ID,
        "Ax-Fingerprint": AX_FP,
        "Ax-Request-At": ax_request_at,
        "Ax-Request-Device": "samsung",
        "Ax-Request-Device-Model": "SM-N935F",
        "Ax-Request-Id": ax_request_id,
        "Ax-Substype": "PREPAID",
        "Content-Type": "application/json",
        "Host": BASE_CIAM_URL.replace("https://", ""),
        "User-Agent": UA,
    }
    
    print("Extending session...")
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=30)
        if response.status_code != 200:
            print(f"Failed to extend session: {response.status_code} - {response.text}")
            return None
        
        data = response.json()
        exchange_code = data.get("data", {}).get("exchange_code")
        
        return exchange_code
    except Exception as e:
        print(f"Error extending session: {e}")
        return None

def submit_otp(
    api_key: str,
    contact_type: str,
    contact: str,
    code: str
):
    final_contact = ""
    final_code = ""

    if contact_type == "SMS":
        if not validate_contact(contact):
            print("Invalid number")
            return None
        final_contact = contact
    
        if not code or len(code) != 6:
            print("Invalid OTP code format")
            return None
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

    payload = f"contactType={contact_type}&code={final_code}&grant_type=password&contact={final_contact}&scope=openid"

    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Authorization": f"Basic {BASIC_AUTH}",
        "Ax-Api-Signature": signature,
        "Ax-Device-Id": AX_DEVICE_ID,
        "Ax-Fingerprint": AX_FP,
        "Ax-Request-At": ts_header,
        "Ax-Request-Device": "samsung",
        "Ax-Request-Device-Model": "SM-N935F",
        "Ax-Request-Id": str(uuid.uuid4()),
        "Ax-Substype": "PREPAID",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": UA,
    }

    print("Submitting OTP...")
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        print("submit_otp status", response.status_code)
        print("submit_otp content-type", response.headers.get("Content-Type", ""))
        raw_text = (response.text or "").strip()
        print("submit_otp response body", raw_text if raw_text else "<empty>")

        json_body = None
        if raw_text:
            try:
                json_body = response.json()
            except ValueError:
                try:
                    json_body = json.loads(raw_text)
                except ValueError:
                    json_body = None

        if response.status_code >= 400:
            if isinstance(json_body, dict):
                print(f"[Error submit_otp]: {json_body}")
            else:
                print(f"[Error submit_otp]: HTTP {response.status_code} - {raw_text or '<empty>'}")
            return None

        if not raw_text:
            print("[Error submit_otp]: empty response body from server")
            return None

        if not isinstance(json_body, dict):
            print(f"[Error submit_otp]: non-JSON response - {raw_text}")
            return None

        if "error" in json_body:
            print(f"[Error submit_otp]: {json_body}")
            return None

        if not json_body.get("refresh_token"):
            print(f"[Error submit_otp]: refresh_token not found in response - {json_body}")
            return None

        print("Login successful.")
        return json_body
    except requests.RequestException as e:
        print(f"[Error submit_otp]: {e}")
        return None
    except Exception as e:
        print(f"[Error submit_otp unexpected]: {type(e).__name__}: {e}")
        return None

def get_new_token(api_key: str, refresh_token: str, subscriber_id: str) -> str:
    url = BASE_CIAM_URL + "/realms/xl-ciam/protocol/openid-connect/token"

    now = datetime.now(timezone(timedelta(hours=7)))
    ax_request_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+0700"
    ax_request_id = str(uuid.uuid4())

    headers = {
        "Host": BASE_CIAM_URL.replace("https://", ""),
        "ax-request-at": ax_request_at,
        "ax-device-id": AX_DEVICE_ID,
        "ax-request-id": ax_request_id,
        "ax-request-device": "samsung",
        "ax-request-device-model": "SM-N935F",
        "ax-fingerprint": AX_FP,
        "authorization": f"Basic {BASIC_AUTH}",
        "user-agent": UA,
        "ax-substype": "PREPAID",
        "content-type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    print("Refreshing token...")
    resp = requests.post(url, headers=headers, data=data, timeout=30)
    if resp.status_code == 400:
        if resp.json().get("error_description") != "Session not active":
            print(f"Failed to refresh token: {resp.status_code} - {resp.text}")
            return None

        if subscriber_id == "":
            raise ValueError("Subscriber ID is missing")
        
        exchange_code = extend_session(subscriber_id)
        if exchange_code is None:
            raise ValueError("Failed to get exchange code")
        
        extend_result = submit_otp(
            api_key,
            "DEVICEID",
            subscriber_id,
            exchange_code
        )
        
        if extend_result is None:
            if "Invalid refresh token" in resp.text:
                raise ValueError("Refresh token is invalid or expired. Please login again.")

            raise ValueError("Failed to submit OTP after extending session")
        
        return extend_result

    resp.raise_for_status()

    body = resp.json()
    
    if "id_token" not in body:
        raise ValueError("ID token not found in response")
    if "error" in body:
        raise ValueError(f"Error in response: {body['error']} - {body.get('error_description', '')}")
    
    return body

def get_auth_code(tokens: dict, pin: str, msisdn: str):
    url = BASE_CIAM_URL + "/ciam/auth/authorization-token/generate"

    parsed = urlparse(BASE_CIAM_URL)
    host_header = parsed.netloc or BASE_CIAM_URL.replace("https://", "")

    now = datetime.now(timezone(timedelta(hours=7)))
    ax_request_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+0700"
    ax_request_id = str(uuid.uuid4())

    headers = {
        "Host": host_header,
        "Ax-Request-At": ax_request_at,
        "Ax-Device-Id": AX_DEVICE_ID,
        "Ax-Request-Id": ax_request_id,
        "Ax-Request-Device": "samsung",
        "Ax-Request-Device-Model": "SM-N935F",
        "Ax-Fingerprint": AX_FP,
        "Authorization": f"Bearer {tokens['access_token']}",
        "User-Agent": UA,
        "Ax-Substype": "PREPAID",
        "Content-Type": "application/json",
    }

    pin_b64 = base64.b64encode(pin.encode("utf-8")).decode("utf-8")

    body = {
        "pin": pin_b64,
        "transaction_type": "SHARE_BALANCE",
        "receiver_msisdn": msisdn,
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
    except requests.RequestException as e:
        print(f"[get_auth_code] Request error: {e}")
        return None


    if resp.status_code != 200:
        print(f"Failed to get auth code: {resp.status_code} - {resp.text}")
        return None

    try:
        data = resp.json()
    except ValueError:
        print(f"Invalid JSON response: {resp.text}")
        return None

    if not isinstance(data, dict):
        print(f"Unexpected response format: {data!r}")
        return None
    
    status = data.get("status", "")
    if status != "Success":
        print(f"Error getting authorization code: {status}")
        return None

    authorization_code = data.get("data", {}).get("authorization_code")
    if not authorization_code:
        print(f"Authorization code not found in response: {data}")
        return None

    return authorization_code
