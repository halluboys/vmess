"""Google One offer discovery helpers with strict claim-link filtering."""

import logging
import re
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

import config

logger = logging.getLogger(__name__)

_BLOCKED_OFFER_SEGMENTS = {
    "freetrial",
    "free-trial",
    "trial",
    "plans",
    "plan",
    "explore",
    "about",
    "homepage",
    "welcome",
    "landing",
    "promo",
    "offers",
}
_CODE_RE = re.compile(r"[A-Za-z0-9_-]{8,}")


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
        "one.google.com/offer/",
        "pixel.google.com/head-start",
    )

    if any(marker in page_source for marker in paid_ai_markers):
        if any(marker in page_source for marker in free_offer_markers):
            return (
                "Google One shows AI-related products, but the promo state is mixed "
                "and needs manual review."
            )
        return (
            "Google One shows regular paid Google AI Pro plans for this account, "
            "but no claim-style promo link was present."
        )

    if "paket anda saat ini" in page_source or "your current plan" in page_source:
        return "Google One loaded your normal account plan page, but no promo card was present."

    return None


def _is_valid_code(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and _CODE_RE.fullmatch(value) is not None


def is_correct_offer_url(url: str) -> bool:
    """
    Return True only for strict claim/code URLs.

    Accepted examples:
    - https://one.google.com/partner-eft-onboard/1A4DXKS0UFCC5SGCYUNQ
    - https://one.google.com/offer/56L3MR7CWXEKWFVEB1EF?g1_landing_page=0
    - https://pixel.google.com/head-start?data=AZ1Ta0xN...

    Rejected examples:
    - https://one.google.com/offer/freetrial
    - https://one.google.com/offer/trial
    """
    if not url:
        return False

    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False

    host = (parsed.netloc or "").lower()
    parts = [p.strip() for p in (parsed.path or "").strip("/").split("/") if p.strip()]
    query = parse_qs(parsed.query or "")

    if host == "one.google.com":
        if len(parts) >= 2 and parts[0] == "partner-eft-onboard":
            return _is_valid_code(parts[1])

        if len(parts) >= 2 and parts[0] == "offer":
            code = parts[1].strip()
            if code.lower() in _BLOCKED_OFFER_SEGMENTS:
                return False
            return _is_valid_code(code)

        return False

    if host == "pixel.google.com":
        if len(parts) >= 1 and parts[0] == "head-start":
            data = (query.get("data", [""])[0] or "").strip()
            return len(data) >= 20
        return False

    return False


def extract_payment_link(driver: webdriver.Chrome) -> Optional[str]:
    """Scan current page for a strict Gemini/Google One claim link."""
    all_links = driver.find_elements(By.TAG_NAME, "a")

    # Some Google pages expose a LOCKED/BARD_ADVANCED launcher that navigates to the
    # final claim page only after clicking. Support that flow first.
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

    # Direct href match.
    for link in all_links:
        try:
            href = link.get_attribute("href") or ""
            if is_correct_offer_url(href):
                return href
        except Exception:
            continue

    # Text-assisted match for buttons/cards that already point to a strict claim URL.
    keywords = config.GEMINI_OFFER_KEYWORDS
    extra_keywords = (
        "claim offer",
        "redeem",
        "activate",
        "get offer",
        "head start",
        "gemini",
        "google ai pro",
        "12 month",
        "12-month",
    )
    for link in all_links:
        try:
            text = (link.text + " " + (link.get_attribute("aria-label") or "")).lower()
            href = link.get_attribute("href") or ""
            if "LOCKED" in href:
                continue
            if any(keyword in text for keyword in (*keywords, *extra_keywords)) and is_correct_offer_url(href):
                return href
        except Exception:
            continue

    # Last fallback: sometimes the current page itself is already the claim page.
    current_url = driver.current_url or ""
    if is_correct_offer_url(current_url):
        return current_url

    return None


def navigate_google_one(driver: webdriver.Chrome) -> Optional[str]:
    """Navigate Google One pages and attempt to find a strict claim link."""
    urls_to_try = []
    seen = set()
    for url in (
        config.GOOGLE_ONE_URL,
        config.GOOGLE_ONE_OFFERS_URL,
        "https://one.google.com/offers",
        "https://pixel.google.com/head-start",
    ):
        if url and url not in seen:
            urls_to_try.append(url)
            seen.add(url)

    for url in urls_to_try:
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
