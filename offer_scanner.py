"""Google One offer discovery helpers."""

import logging
import time
from typing import Optional
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

import config

logger = logging.getLogger(__name__)


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
        "one.google.com/offer",
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
            "but no free promo claim link was present."
        )

    if "paket anda saat ini" in page_source or "your current plan" in page_source:
        return "Google One loaded your normal account plan page, but no promo card was present."

    return None


def is_correct_offer_url(url: str) -> bool:
    """Return True for supported Google offer claim URL patterns."""
    if not url:
        return False

    url = url.strip()
    lowered = url.lower()

    patterns = (
        "one.google.com/partner-eft-onboard/",
        "one.google.com/offer/",
        "pixel.google.com/head-start",
    )
    if any(pattern in lowered for pattern in patterns):
        return True

    # Fallback: only allow Google domains and obvious claim/offer keywords.
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
    except Exception:
        return False

    if not host.endswith("google.com"):
        return False

    keyword_hints = ("offer", "redeem", "claim", "head-start", "partner-eft-onboard")
    return any(hint in path or hint in query for hint in keyword_hints)


def extract_payment_link(driver: webdriver.Chrome) -> Optional[str]:
    """Scan current page for Gemini Pro offer activation link."""
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

    # Text-assisted match for ambiguous buttons/cards.
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
    """Navigate Google One pages and attempt to find the offer link."""
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
