"""Chrome WebDriver factory with mobile emulation setup."""

import logging
import os
import platform
import shutil
import tempfile
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

import config
from services.device_simulator import DeviceProfile, PIXEL_10_PRO_SPECS as SPECS
from services.google_automation_core.errors import GoogleAutomationError

logger = logging.getLogger(__name__)


def _detect_chrome_binary() -> Optional[str]:
    """Detect a Chrome/Chromium binary across Linux/macOS/Windows."""
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
    """Resolve Chrome binary and chromedriver path.

    chromedriver can be None; Selenium Manager fallback will be used.
    """
    chrome_bin = _detect_chrome_binary()
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH") or shutil.which("chromedriver")
    return chrome_bin, chromedriver_path


def _build_chrome_options(profile: DeviceProfile) -> tuple[Options, str]:
    """Build Chrome options and return them with a unique profile directory."""
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")

    profile_dir = tempfile.mkdtemp(prefix="chrome-profile-")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-crash-reporter")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-translate")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--remote-debugging-address=127.0.0.1")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument(f"--window-size={SPECS['width']},{SPECS['height']}")
    options.add_argument(f"--user-agent={profile.user_agent}")
    options.add_argument("--renderer-process-limit=2")
    options.add_argument("--js-flags=--max-old-space-size=512")

    chrome_bin, _ = resolve_browser_binaries()
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

    return options, profile_dir


def build_driver(profile: DeviceProfile) -> webdriver.Chrome:
    """Return a headless Chrome WebDriver configured for the device profile."""
    options, profile_dir = _build_chrome_options(profile)
    _, chromedriver_path = resolve_browser_binaries()

    try:
        if chromedriver_path:
            logger.info("Using chromedriver: %s", chromedriver_path)
            service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=options)
        else:
            logger.warning(
                "CHROMEDRIVER_PATH not found; using Selenium Manager fallback."
            )
            driver = webdriver.Chrome(options=options)
    except SessionNotCreatedException as exc:
        logger.exception("Chrome session creation failed")
        raise GoogleAutomationError(
            "Failed to start Chrome session. On VPS/Docker, verify Chromium and "
            "chromedriver major versions match, increase shared memory, and avoid "
            "running Chrome as root when possible."
        ) from exc
    except Exception:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise

    driver.implicitly_wait(config.IMPLICIT_WAIT)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": profile.navigator_overrides_js()},
        )
        driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": profile.as_headers()},
        )
        driver.execute_cdp_cmd(
            "Emulation.setTouchEmulationEnabled",
            {"enabled": True, "maxTouchPoints": SPECS["max_touch_points"]},
        )
        driver.execute_cdp_cmd(
            "Emulation.setTimezoneOverride",
            {"timezoneId": "America/Los_Angeles"},
        )
        driver.execute_cdp_cmd(
            "Emulation.setGeolocationOverride",
            {"latitude": 37.3861, "longitude": -122.0839, "accuracy": 100},
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
