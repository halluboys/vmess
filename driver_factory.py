"""Chrome WebDriver factory with mobile emulation setup (FIXED VERSION)."""

import logging
import os
import platform
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

import config
from services.device_simulator import DeviceProfile, PIXEL_10_PRO_SPECS as SPECS

logger = logging.getLogger(__name__)


def _detect_chrome_binary() -> Optional[str]:
    import shutil

    return (
        os.environ.get("CHROME_BIN")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("chrome")
        or shutil.which("chrome.exe")
    )


def build_driver(profile: DeviceProfile) -> webdriver.Chrome:
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    options.add_argument(f"--window-size={SPECS['width']},{SPECS['height']}")
    options.add_argument(f"--user-agent={profile.user_agent}")

    # Mobile emulation
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

    # Anti-detection tweaks
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Detect Chrome binary
    chrome_bin = _detect_chrome_binary()
    if chrome_bin:
        options.binary_location = chrome_bin
        logger.info("Using Chrome binary: %s", chrome_bin)

    # Use Selenium Manager (no manual chromedriver)
    driver = webdriver.Chrome(options=options)

    driver.implicitly_wait(config.IMPLICIT_WAIT)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": profile.navigator_overrides_js()},
        )
    except Exception as exc:
        logger.warning("CDP injection failed: %s", exc)

    return driver
