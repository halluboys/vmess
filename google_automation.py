"""Google One automation service."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import platform
import time
import zipfile
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from core.proxy_manager import mask_proxy_url, parse_proxy_parts
from services.device_simulator import DeviceProfile, PIXEL_10_PRO_SPECS as SPECS

logger = logging.getLogger(__name__)


class GoogleAutomationError(Exception):
    """Raised when automation encounters an unrecoverable error."""


# Proxy helpers


def proxy_server_argument(proxy_url: str) -> str:
    """Return a Chrome --proxy-server value without credentials."""
    proxy = parse_proxy_parts(proxy_url)
    return f"{proxy['scheme']}://{proxy['host']}:{proxy['port']}"


def build_proxy_auth_extension(proxy_url: str) -> str | None:
    """Return a base64-encoded Chrome extension for proxy authentication."""
    proxy = parse_proxy_parts(proxy_url)
    username = proxy["username"]
    password = proxy["password"]

    if not username:
        return None

    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "AutoPixel Proxy Auth",
        "permissions": [
            "proxy",
            "storage",
            "tabs",
            "webRequest",
            "webRequestBlocking",
            "<all_urls>",
        ],
        "background": {
            "scripts": ["background.js"],
        },
        "minimum_chrome_version": "88.0.0",
    }

    background = f"""
const config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{
      scheme: "{proxy['scheme']}",
      host: "{proxy['host']}",
      port: parseInt("{proxy['port']}", 10)
    }},
    bypassList: ["localhost", "127.0.0.1"]
  }}
}};

chrome.proxy.settings.set({{ value: config, scope: "regular" }}, function() {{}});

chrome.webRequest.onAuthRequired.addListener(
  function() {{
    return {{
      authCredentials: {{
        username: {json.dumps(username)},
        password: {json.dumps(password or "")}
      }}
    }};
  }},
  {{ urls: ["<all_urls>"] }},
  ["blocking"]
);
""".strip()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("background.js", background)

    return base64.b64encode(buffer.getvalue()).decode("ascii")


# Driver factory


def _detect_chrome_binary() -> Optional[str]:
    """Detect a Chrome/Chromium binary across Linux/macOS/Windows."""
    import shutil

    chrome_bin = (
        os.environ.get("CHROME_BIN")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("chrome")
        or shutil.which("chrome.exe")
    )

    if chrome_bin:
        return chrome_bin

    if platform.system() == "Windows":
        win_candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ]
        for candidate in win_candidates:
            if candidate and os.path.exists(candidate):
                return candidate

    return None


def resolve_browser_binaries() -> tuple[Optional[str], Optional[str]]:
    """Resolve Chrome binary and chromedriver path."""
    import shutil

    chrome_bin = _detect_chrome_binary()
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH") or shutil.which("chromedriver")
    return chrome_bin, chromedriver_path


def build_driver(
    profile: DeviceProfile,
    headless: Optional[bool] = None,
    proxy_url: str | None = None,
) -> webdriver.Chrome:
    """Return a Chrome WebDriver configured for the device profile."""
    options = Options()
    headless_enabled = config.HEADLESS if headless is None else headless

    if headless_enabled:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument(f"--window-size={SPECS['width']},{SPECS['height']}")
    options.add_argument(f"--user-agent={profile.user_agent}")

    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-crash-reporter")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-translate")
    options.add_argument("--no-first-run")
    options.add_argument("--renderer-process-limit=2")
    options.add_argument("--js-flags=--max-old-space-size=512")
    options.add_argument("--disable-ipc-flooding-protection")

    chrome_bin, chromedriver_path = resolve_browser_binaries()

    if chrome_bin:
        options.binary_location = chrome_bin
        logger.info("Using Chrome binary: %s", chrome_bin)
    else:
        logger.warning(
            "CHROME_BIN not found; relying on Selenium Manager/browser defaults."
        )

    mobile_emulation = {
        "deviceMetrics": {
            "width": SPECS["width"],
            "height": SPECS["height"],
            "pixelRatio": SPECS["pixel_ratio"],
            "mobile": True,
            "touch": True,
        },
        "userAgent": profile.user_agent,
    }
    options.add_experimental_option("mobileEmulation", mobile_emulation)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")

    encoded_extension = None
    if proxy_url:
        encoded_extension = build_proxy_auth_extension(proxy_url)
        if encoded_extension:
            options.add_encoded_extension(encoded_extension)
        else:
            options.add_argument(f"--proxy-server={proxy_server_argument(proxy_url)}")
        logger.info("Using proxy: %s", mask_proxy_url(proxy_url))

    if not encoded_extension:
        options.add_argument("--disable-extensions")

    if chromedriver_path:
        logger.info("Using chromedriver: %s", chromedriver_path)
        service = Service(chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        logger.warning(
            "CHROMEDRIVER_PATH not found; using Selenium Manager fallback."
        )
        driver = webdriver.Chrome(options=options)

    setattr(driver, "_autopixel_headless", headless_enabled)

    driver.implicitly_wait(config.IMPLICIT_WAIT)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)

    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": profile.navigator_overrides_js()},
        )
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {
                "userAgent": profile.user_agent,
                "acceptLanguage": profile.accept_language,
                "platform": "Android",
                "userAgentMetadata": profile.user_agent_metadata(),
            },
        )
        driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": profile.as_headers()},
        )
        driver.execute_cdp_cmd(
            "Emulation.setTouchEmulationEnabled",
            {"enabled": True, "maxTouchPoints": SPECS["max_touch_points"]},
        )
        try:
            driver.execute_cdp_cmd(
                "Emulation.setLocaleOverride",
                {"locale": profile.locale},
            )
        except Exception as exc:
            logger.debug("Locale override unavailable for this Chrome build: %s", exc)
        driver.execute_cdp_cmd(
            "Emulation.setTimezoneOverride",
            {"timezoneId": profile.timezone_id},
        )
        driver.execute_cdp_cmd(
            "Emulation.setGeolocationOverride",
            {
                "latitude": profile.geolocation_latitude,
                "longitude": profile.geolocation_longitude,
                "accuracy": profile.geolocation_accuracy,
            },
        )
        logger.info(
            "Device emulation configured: %s (Build %s, Chrome %s)",
            profile.model,
            profile.build_id,
            profile.chrome_version,
        )
    except Exception as exc:
        logger.warning("CDP override injection failed (non-fatal): %s", exc)

    return driver


# Login flow


def _driver_is_headless(driver: webdriver.Chrome) -> bool:
    """Return whether this driver instance is running headless."""
    return bool(getattr(driver, "_autopixel_headless", config.HEADLESS))


def _is_google_challenge_url(url: str) -> bool:
    """Return True when Google is still showing a sign-in challenge page."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    path = parsed.path or ""
    return hostname == "accounts.google.com" and "challenge" in path


def get_signin_error_text(driver: webdriver.Chrome) -> str | None:
    """Return visible Google sign-in error text when present."""
    selectors = (
        '[jsname="B34EJ"]',
        '[aria-live="assertive"]',
        '[role="alert"]',
    )
    for selector in selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
        except NoSuchElementException:
            continue

        text = " ".join((element.text or "").split())
        if text:
            return text

    return None


def get_login_debug_snapshot(driver: webdriver.Chrome) -> dict[str, str]:
    """Return a small snapshot of the current login page for diagnostics."""
    current_url = ""
    title = ""
    excerpt = ""

    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""

    try:
        title = " ".join((driver.title or "").split())
    except Exception:
        title = ""

    selectors = ("body", "main", '[role="main"]')
    for selector in selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
        except NoSuchElementException:
            continue

        text = " ".join((element.text or "").split())
        if text:
            excerpt = text[:240]
            break

    return {
        "url": current_url,
        "title": title,
        "excerpt": excerpt,
    }


def get_google_login_state(driver: webdriver.Chrome) -> str:
    """Return a coarse login state for the current Google page."""
    parsed = urlparse(driver.current_url)
    hostname = parsed.hostname or ""
    path = parsed.path or ""

    if _is_google_challenge_url(driver.current_url):
        if _is_totp_challenge(driver):
            return "needs_totp"
        return "challenge"

    if hostname == "accounts.google.com" and path.startswith("/signin"):
        return "signin"

    if hostname == "myaccount.google.com":
        return "success"

    if hostname.endswith(".google.com") and "/u/" in path:
        return "success"

    if hostname.endswith(".google.com") and "signin" not in path:
        return "success"

    return "unknown"


def wait_for_login_resolution(driver: webdriver.Chrome, timeout: int = 10) -> str:
    """Wait briefly for Google to leave the sign-in/challenge flow."""
    deadline = time.time() + timeout
    state = get_google_login_state(driver)

    while time.time() < deadline and state in {"challenge", "signin"}:
        time.sleep(0.5)
        state = get_google_login_state(driver)

    return state


def _has_totp_error(driver: webdriver.Chrome) -> bool:
    """Best-effort detection of inline TOTP errors on the Google challenge page."""
    try:
        page_text = driver.page_source.lower()
    except Exception:
        return False

    error_indicators = (
        "wrong code",
        "incorrect code",
        "invalid code",
        "enter a valid code",
        "couldn't verify",
        "could not verify",
        "try again",
        "expired code",
    )
    return any(indicator in page_text for indicator in error_indicators)


def _is_totp_challenge(driver: webdriver.Chrome) -> bool:
    """Return True only when the challenge page really looks like TOTP input."""
    specific_selectors = ('input[name="totpPin"]', "#totpPin")
    for selector in specific_selectors:
        try:
            driver.find_element(By.CSS_SELECTOR, selector)
            return True
        except NoSuchElementException:
            continue

    try:
        driver.find_element(By.CSS_SELECTOR, 'input[type="tel"]')
    except NoSuchElementException:
        return False

    page_text = driver.page_source.lower()
    positive_indicators = (
        "authenticator",
        "google authenticator",
        "verification code",
        "6-digit",
        "6 digit",
        "enter the code",
        "totp",
    )
    negative_indicators = (
        "security key",
        "usb",
        "phone",
        "sms",
        "tap yes",
        "google prompt",
    )

    return (
        any(indicator in page_text for indicator in positive_indicators)
        and not any(indicator in page_text for indicator in negative_indicators)
    )


def _resolve_post_password_state(driver: webdriver.Chrome, email: str) -> str:
    """Resolve the Google login state after password submission with retries."""
    deadline = time.time() + 15
    last_exc: Exception | None = None

    while time.time() < deadline:
        try:
            current_url = driver.current_url
            parsed = urlparse(current_url)
            hostname = parsed.hostname or ""
            path = parsed.path or ""

            challenge_paths = ("/signin/v2/challenge", "/signin/challenge", "/v2/challenge")
            if hostname == "accounts.google.com" and any(p in path for p in challenge_paths):
                if _is_totp_challenge(driver):
                    logger.info("TOTP 2FA challenge confirmed for %s - awaiting code", email)
                    return "needs_totp"

                switched_to_totp = False
                try:
                    for opt_xpath in (
                        '//*[@data-challengetype="6"]',
                        '//div[@data-challengetype="6"]',
                        '//div[contains(text(), "Authenticator")]',
                        '//div[contains(text(), "authenticator")]',
                        '//div[contains(text(), "Google Authenticator")]',
                        '//div[contains(text(), "verification code")]',
                        '//li[contains(., "Authenticator")]',
                        '//li[contains(., "authenticator")]',
                    ):
                        try:
                            driver.find_element(By.XPATH, opt_xpath).click()
                            time.sleep(2)
                            switched_to_totp = True
                            break
                        except NoSuchElementException:
                            continue

                    if not switched_to_totp:
                        for selector in (
                            '//a[contains(text(), "another way")]',
                            '//button[contains(text(), "another way")]',
                            '//a[contains(text(), "other way")]',
                            '//a[contains(text(), "Try another")]',
                            '//span[contains(text(), "another way")]/ancestor::a',
                            '//span[contains(text(), "another way")]/ancestor::button',
                        ):
                            try:
                                try_another = driver.find_element(By.XPATH, selector)
                                try_another.click()
                                time.sleep(2)
                                break
                            except NoSuchElementException:
                                continue

                        for opt_xpath in (
                            '//*[@data-challengetype="6"]',
                            '//div[@data-challengetype="6"]',
                            '//div[contains(text(), "Authenticator")]',
                            '//div[contains(text(), "authenticator")]',
                            '//div[contains(text(), "Google Authenticator")]',
                            '//div[contains(text(), "verification code")]',
                            '//li[contains(., "Authenticator")]',
                        ):
                            try:
                                driver.find_element(By.XPATH, opt_xpath).click()
                                time.sleep(1)
                                switched_to_totp = True
                                break
                            except NoSuchElementException:
                                continue

                    if switched_to_totp and _is_totp_challenge(driver):
                        return "needs_totp"
                except Exception as exc:
                    logger.warning("Error trying alternative 2FA: %s", exc)

                page_text = driver.page_source.lower()
                if "security key" in page_text or "usb" in page_text:
                    challenge_type = "security key"
                elif "phone" in page_text or "sms" in page_text:
                    challenge_type = "SMS / phone verification"
                elif "tap yes" in page_text or "google prompt" in page_text:
                    challenge_type = "Google prompt (tap Yes on your phone)"
                else:
                    challenge_type = "two-step verification"

                if not _driver_is_headless(driver):
                    setattr(driver, "_autopixel_challenge_type", challenge_type)
                    logger.info(
                        "Manual verification required for %s: %s",
                        email,
                        challenge_type,
                    )
                    return "needs_manual_verification"

                raise GoogleAutomationError(
                    f"Your account requires {challenge_type}. "
                    f"No authenticator option found. "
                    f"Please use an App Password instead."
                )

            if hostname == "myaccount.google.com" or (hostname.endswith(".google.com") and "/u/" in path):
                return "success"

            if get_signin_error_text(driver):
                return "failed"

            if not (hostname == "accounts.google.com" and path.startswith("/signin")):
                return "success"

            time.sleep(0.5)
        except StaleElementReferenceException as exc:
            last_exc = exc
            time.sleep(0.5)
        except WebDriverException as exc:
            last_exc = exc
            time.sleep(0.5)

    if last_exc:
        logger.warning("Transient WebDriver issue while resolving login state: %s", last_exc)
    raise GoogleAutomationError(
        "Timed out while waiting for Google sign-in to continue. "
        "This usually points to a proxy/network issue or an unsupported challenge."
    )


def wait_for(
    driver: webdriver.Chrome,
    by: str,
    value: str,
    timeout: int = config.WEBDRIVER_TIMEOUT,
) -> WebElement:
    """Return element after waiting for it to be clickable."""
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))


def wait_for_any(
    driver: webdriver.Chrome,
    selectors: tuple[tuple[str, str], ...],
    timeout: int = config.WEBDRIVER_TIMEOUT,
) -> WebElement:
    """Return the first visible element that matches any selector."""
    deadline = time.time() + timeout
    last_error: Exception | None = None

    while time.time() < deadline:
        for by, value in selectors:
            try:
                element = driver.find_element(by, value)
            except NoSuchElementException as exc:
                last_error = exc
                continue

            if element.is_displayed():
                return element
        time.sleep(0.5)

    raise TimeoutException(str(last_error) if last_error else "No matching visible element found.")


def gmail_login(driver: webdriver.Chrome, email: str, password: str) -> str:
    """Perform Google login and return status: success, failed, or needs_totp."""
    try:
        driver.implicitly_wait(0)
        driver.get(config.GMAIL_LOGIN_URL)
        time.sleep(3)

        email_selectors = (
            (By.CSS_SELECTOR, 'input[type="email"]'),
            (By.CSS_SELECTOR, 'input[name="identifier"]'),
            (By.CSS_SELECTOR, 'input[autocomplete="username"]'),
        )

        for retry in range(3):
            try:
                email_field = wait_for_any(driver, email_selectors)
                email_field.clear()
                email_field.send_keys(email)
                break
            except StaleElementReferenceException:
                logger.warning("Stale element on email field, retrying (%d/3)", retry + 1)
                time.sleep(1)
        else:
            raise GoogleAutomationError("Email field stale after 3 retries")

        wait_for(driver, By.ID, "identifierNext").click()
        time.sleep(1)

        password_field = wait_for_any(
            driver,
            (
                (By.CSS_SELECTOR, 'input[type="password"]'),
                (By.CSS_SELECTOR, 'input[name="Passwd"]'),
                (By.CSS_SELECTOR, 'input[autocomplete="current-password"]'),
            ),
        )
        password_field.clear()
        password_field.send_keys(password)
        wait_for(driver, By.ID, "passwordNext").click()
        time.sleep(2)
        return _resolve_post_password_state(driver, email)

    except TimeoutException as exc:
        snapshot = get_login_debug_snapshot(driver)
        logger.error(
            "Timeout during login: %s | url=%s | title=%s | excerpt=%s",
            exc,
            snapshot["url"] or "-",
            snapshot["title"] or "-",
            snapshot["excerpt"] or "-",
        )
        detail = ""
        if snapshot["title"] or snapshot["excerpt"]:
            detail = (
                f" Current page: {snapshot['title'] or '(untitled)'}"
                f" | URL: {snapshot['url'] or '-'}"
            )
        raise GoogleAutomationError(
            "Timed out while loading the Google sign-in page. "
            "This usually points to a proxy/network issue or an unexpected Google page."
            f"{detail}"
        ) from exc
    except WebDriverException as exc:
        logger.error("WebDriver error during login: %s", exc)
        raise GoogleAutomationError(
            f"Browser/network error during login: {exc.__class__.__name__}"
        ) from exc


def submit_totp_code(driver: webdriver.Chrome, code: str) -> bool:
    """Enter TOTP/authenticator code and return True when accepted."""
    try:
        totp_field = None
        for selector in (
            'input[type="tel"]',
            'input[name="totpPin"]',
            '#totpPin',
            'input[type="text"]',
        ):
            try:
                totp_field = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                if totp_field:
                    break
            except TimeoutException:
                continue

        if not totp_field:
            return False

        totp_field.clear()
        totp_field.send_keys(code)
        time.sleep(0.5)

        for btn_selector in (
            "#totpNext",
            'button[jsname="LgbsSe"]',
            '[data-action="verify"]',
            'button[type="submit"]',
        ):
            try:
                driver.find_element(By.CSS_SELECTOR, btn_selector).click()
                break
            except NoSuchElementException:
                continue

        deadline = time.time() + 15
        while time.time() < deadline:
            current_url = driver.current_url
            if not _is_google_challenge_url(current_url):
                return True
            if _has_totp_error(driver):
                return False
            time.sleep(0.5)

        return not _is_google_challenge_url(driver.current_url)
    except Exception as exc:
        logger.error("Error submitting TOTP code: %s", exc)
        return False


# Offer scanning


def diagnose_google_one_page(driver: webdriver.Chrome) -> str | None:
    """Return a short diagnosis string for the current Google One page."""
    try:
        page_source = driver.page_source.lower()
    except Exception:
        return None

    paid_ai_markers = (
        "google ai pro",
        "ai premium",
        "g1.2tb.ai",
        "g1.2tb.ai.annual",
    )
    free_offer_markers = (
        "partner-eft-onboard",
        "bard_advanced",
        "claim offer",
        "redeem",
        "free trial",
        "12-month",
        "12 month",
    )

    if any(marker in page_source for marker in paid_ai_markers):
        if any(marker in page_source for marker in free_offer_markers):
            return (
                "Google One shows AI-related products, but the promo state is mixed "
                "and needs manual review."
            )
        return (
            "Google One shows regular paid Google AI Pro plans for this account, "
            "but no free promo claim link was present."
        )

    if "paket anda saat ini" in page_source or "your current plan" in page_source:
        return "Google One loaded your normal account plan page, but no promo card was present."

    return None


def is_correct_offer_url(url: str) -> bool:
    """Return True for expected Pixel Gemini offer claim URL pattern."""
    return bool(url) and "partner-eft-onboard" in url


def extract_payment_link(driver: webdriver.Chrome) -> Optional[str]:
    """Scan current page for Gemini Pro offer activation link."""
    all_links = driver.find_elements(By.TAG_NAME, "a")

    for link in all_links:
        try:
            href = link.get_attribute("href") or ""
            if "LOCKED" in href and "BARD_ADVANCED" in href:
                old_url = driver.current_url
                driver.execute_script("arguments[0].click();", link)
                time.sleep(5)
                current_url = driver.current_url

                if is_correct_offer_url(current_url):
                    return current_url
                if "LOCKED" in current_url:
                    return None

                if current_url != old_url:
                    new_links = driver.find_elements(By.TAG_NAME, "a")
                    for new_link in new_links:
                        try:
                            next_href = new_link.get_attribute("href") or ""
                            if is_correct_offer_url(next_href):
                                return next_href
                        except Exception:
                            continue

                    if is_correct_offer_url(current_url):
                        return current_url

                return None
        except Exception as exc:
            logger.warning("Error clicking LOCKED link: %s", exc)
            return None

    for link in all_links:
        try:
            href = link.get_attribute("href") or ""
            if is_correct_offer_url(href):
                return href
        except Exception:
            continue

    keywords = config.GEMINI_OFFER_KEYWORDS
    for link in all_links:
        try:
            text = (link.text + " " + (link.get_attribute("aria-label") or "")).lower()
            href = link.get_attribute("href") or ""
            if "LOCKED" in href:
                continue
            if any(keyword in text for keyword in keywords) and is_correct_offer_url(href):
                return href
        except Exception:
            continue

    for link in all_links:
        try:
            href = link.get_attribute("href") or ""
            if is_correct_offer_url(href):
                return href
        except Exception:
            continue

    return None


def navigate_google_one(driver: webdriver.Chrome) -> Optional[str]:
    """Navigate Google One pages and attempt to find the offer link."""
    for url in (config.GOOGLE_ONE_URL, config.GOOGLE_ONE_OFFERS_URL):
        try:
            logger.info("Navigating to %s", url)
            driver.get(url)
            time.sleep(3)

            for selector in (
                '[aria-label="Accept all"]',
                'button[jsname="higCR"]',
                '[data-action="accept"]',
            ):
                try:
                    driver.find_element(By.CSS_SELECTOR, selector).click()
                    time.sleep(1)
                    break
                except NoSuchElementException:
                    continue

            link = extract_payment_link(driver)
            if link:
                return link
        except (TimeoutException, WebDriverException) as exc:
            logger.warning("Error accessing %s: %s", url, exc)

    return None


# Public API


def dump_offer_debug_artifacts(
    driver,
    chat_id: int,
    attempt: int | None = None,
    session_id: str | None = None,
) -> dict[str, str]:
    """Persist screenshot + page HTML for a no-offer debugging snapshot."""
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    dump_dir = os.path.join(project_root, "logs", "offer_debug", f"chat_{chat_id}")
    os.makedirs(dump_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    token = (session_id or "nosession").replace("-", "")[:8]
    suffix = f"_attempt{attempt}" if attempt is not None else ""
    basename = f"{timestamp}_session_{token}{suffix}"

    screenshot_path = os.path.join(dump_dir, f"{basename}.png")
    html_path = os.path.join(dump_dir, f"{basename}.html")

    artifacts: dict[str, str] = {}
    current_url = ""
    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""

    try:
        driver.save_screenshot(screenshot_path)
        artifacts["screenshot"] = screenshot_path
    except Exception as exc:
        logger.warning("Failed to save no-offer screenshot for chat %s: %s", chat_id, exc)

    try:
        page_source = driver.page_source
        with open(html_path, "w", encoding="utf-8") as handle:
            if current_url:
                handle.write(f"<!-- URL: {current_url} -->\n")
            handle.write(page_source)
        artifacts["html"] = html_path
    except Exception as exc:
        logger.warning("Failed to save no-offer HTML for chat %s: %s", chat_id, exc)

    if artifacts:
        logger.info("Saved no-offer debug artifacts for chat %s", chat_id)

    return artifacts


def start_login(
    email: str,
    password: str,
    device: DeviceProfile,
    headless: bool | None = None,
    proxy_url: str | None = None,
) -> tuple:
    """Start login process and return (driver, status)."""
    effective_headless = config.HEADLESS if headless is None else headless
    logger.info(
        "Starting WebDriver for session %s (headless=%s)",
        device.session_id,
        effective_headless,
    )
    driver = build_driver(
        device,
        headless=effective_headless,
        proxy_url=proxy_url,
    )

    try:
        status = gmail_login(driver, email, password)
        if status == "failed":
            detail = get_signin_error_text(driver)
            driver.quit()
            if detail:
                raise GoogleAutomationError(f"Google sign-in rejected the login: {detail}")
            raise GoogleAutomationError(
                "Google sign-in rejected the login. "
                "This can be caused by invalid credentials, account protection, or proxy issues."
            )
        return driver, status
    except GoogleAutomationError:
        driver.quit()
        raise
    except Exception:
        driver.quit()
        raise


def submit_2fa_code(driver, code: str) -> bool:
    """Submit TOTP code on a driver that is on the 2FA challenge page."""
    return submit_totp_code(driver, code)


def resolve_manual_login(driver, timeout: int = 10) -> str:
    """Wait briefly for a manual Google verification step to finish."""
    return wait_for_login_resolution(driver, timeout=timeout)


def check_offer_with_driver(driver) -> Optional[str]:
    """Navigate to Google One and find the Gemini Pro offer link."""
    return navigate_google_one(driver)


def diagnose_offer_page(driver) -> str | None:
    """Return a short diagnosis for the currently loaded Google One page."""
    return diagnose_google_one_page(driver)


def close_driver(driver) -> None:
    """Safely close WebDriver instance."""
    if driver:
        try:
            driver.quit()
        except Exception:
            pass


__all__ = [
    "GoogleAutomationError",
    "start_login",
    "submit_2fa_code",
    "resolve_manual_login",
    "check_offer_with_driver",
    "diagnose_offer_page",
    "dump_offer_debug_artifacts",
    "close_driver",
]
