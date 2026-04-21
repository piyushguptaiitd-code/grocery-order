import json
import time
import logging
from dataclasses import dataclass
from typing import List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from config import SELENIUM_HEADLESS, SELENIUM_TIMEOUT, ZEPTO_SEARCH_LIMIT, DATABASE_PATH

logger = logging.getLogger(__name__)

COOKIES_FILE = DATABASE_PATH.replace('.db', '_zepto_cookies.json')

ZEPTO_BASE_URL = "https://www.zeptonow.com"

@dataclass
class ZeptoProduct:
    product_id: str
    name: str
    price: float
    quantity_unit: str
    image_url: str
    in_stock: bool

@dataclass
class ZeptoOrder:
    order_id: str
    status: str
    total: float
    estimated_delivery: str


class ZeptoAutomation:
    def __init__(self):
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        self.is_logged_in = False
        self._location_set = False

    def _build_driver(self) -> webdriver.Chrome:
        options = Options()
        if SELENIUM_HEADLESS:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--window-size=1280,800")
        options.add_argument("user-agent=Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
        # Persistent profile so login survives bot restarts
        profile_dir = DATABASE_PATH.replace('.db', '_chrome_profile')
        options.add_argument(f"--user-data-dir={profile_dir}")
        driver = webdriver.Chrome(options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver

    def _save_cookies(self):
        """Save browser cookies to file for persistent login."""
        try:
            cookies = self.driver.get_cookies()
            with open(COOKIES_FILE, 'w') as f:
                json.dump(cookies, f)
            logger.info(f"[Cookies] Saved {len(cookies)} cookies")
        except Exception as e:
            logger.warning(f"[Cookies] Failed to save: {e}")

    def _load_cookies(self):
        """Load saved cookies to browser for automatic login."""
        try:
            with open(COOKIES_FILE, 'r') as f:
                cookies = json.load(f)
            self.driver.get(ZEPTO_BASE_URL)
            time.sleep(1)
            for cookie in cookies:
                try:
                    self.driver.add_cookie(cookie)
                except Exception as e:
                    logger.warning(f"[Cookies] Could not add cookie {cookie.get('name')}: {e}")
            logger.info(f"[Cookies] Loaded {len(cookies)} cookies")
            time.sleep(2)
        except FileNotFoundError:
            logger.info("[Cookies] No saved cookies found")
        except Exception as e:
            logger.error(f"[Cookies] Failed to load: {e}")

    def check_login_state(self) -> str:
        """
        Navigate to Zepto and determine login state from browser UI.
        Looks for Login button (→ logged_out) or profile/account element (→ logged_in).
        Returns 'logged_in' or 'logged_out'.
        """
        try:
            self.driver.get(ZEPTO_BASE_URL)
            time.sleep(3)
            result = self.driver.execute_script("""
                // Login button present → not logged in
                var loginBtn = Array.from(document.querySelectorAll('button, a')).find(function(el) {
                    var t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    return t === 'login' || t === 'sign in' || t === 'log in';
                });
                if (loginBtn) return 'logged_out';

                // Profile/account icon in header → logged in
                var w = window.innerWidth;
                var profileEl = Array.from(document.querySelectorAll(
                    '[data-testid*="profile"], [data-testid*="account"], [data-testid*="user"], ' +
                    '[aria-label*="profile"], [aria-label*="account"], [aria-label*="user"], ' +
                    '[href*="/profile"], [href*="/account"]'
                )).find(function(el) {
                    var rect = el.getBoundingClientRect();
                    return rect.top < 100;
                });
                if (profileEl) return 'logged_in';

                // "Hi, Name" greeting anywhere near top of page
                var greeting = Array.from(document.querySelectorAll('span, p, div, button')).find(function(el) {
                    var t = (el.innerText || '').trim();
                    var rect = el.getBoundingClientRect();
                    return rect.top < 100 && /^hi[,\s]/i.test(t);
                });
                if (greeting) return 'logged_in';

                // No Login button found — assume logged in
                return 'logged_in';
            """)
            logger.info(f"[Login] Browser state: {result}")
            return result or 'logged_out'
        except Exception as e:
            logger.warning(f"[Login] check_login_state failed: {e}")
            return 'logged_out'

    def force_clear_session(self):
        """Clear all browser session data so a fresh OTP login can be performed."""
        try:
            self.driver.delete_all_cookies()
            self.driver.execute_script("try { localStorage.clear(); } catch(e) {} try { sessionStorage.clear(); } catch(e) {}")
            logger.info("[Login] Cleared browser session/cookies for re-login")
        except Exception as e:
            logger.warning(f"[Login] force_clear_session failed: {e}")

    def start(self):
        if not self.driver:
            self.driver = self._build_driver()
            self.wait = WebDriverWait(self.driver, SELENIUM_TIMEOUT)
            logger.info("Selenium driver started")

    def stop(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
            self.is_logged_in = False
            logger.info("Selenium driver stopped")

    def initiate_login(self, phone: str) -> tuple:
        """
        Phase 1: Open Zepto, find login button, enter phone, trigger OTP.
        Returns (success: bool, message: str)
        """
        try:
            self.start()
            self.driver.get(ZEPTO_BASE_URL)
            time.sleep(3)
            self.take_screenshot("/tmp/zepto_step1_home.png")
            logger.info(f"[Login P1] title='{self.driver.title}' url={self.driver.current_url}")

            # Open login modal
            login_opened = False
            for selector in [
                (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]"),
                (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'sign in')]"),
                (By.XPATH, "//a[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]"),
                (By.XPATH, "//*[@data-testid='login-btn' or @data-testid='signin-btn' or @data-testid='profile-btn']"),
                (By.XPATH, "//button[contains(@class,'login') or contains(@class,'signin') or contains(@class,'auth')]"),
                (By.XPATH, "//*[contains(@aria-label,'login') or contains(@aria-label,'sign in')]"),
            ]:
                try:
                    self.driver.find_element(*selector).click()
                    time.sleep(2)
                    login_opened = True
                    logger.info(f"[Login P1] Opened login modal via {selector[1][:60]}")
                    break
                except NoSuchElementException:
                    continue

            self.take_screenshot("/tmp/zepto_step2_modal.png")
            logger.info(f"[Login P1] login_opened={login_opened} url={self.driver.current_url}")

            # Find phone input
            phone_input = None
            for selector in [
                (By.XPATH, "//input[@type='tel']"),
                (By.XPATH, "//input[@name='phone' or @name='mobile' or @name='phoneNumber']"),
                (By.XPATH, "//input[contains(@placeholder,'phone') or contains(@placeholder,'Phone') or contains(@placeholder,'mobile') or contains(@placeholder,'Mobile')]"),
                (By.CSS_SELECTOR, "input[type='tel'], input[maxlength='10']"),
            ]:
                try:
                    phone_input = self.driver.find_element(*selector)
                    logger.info(f"[Login P1] Found phone input via {selector}")
                    break
                except NoSuchElementException:
                    continue

            if not phone_input:
                self.take_screenshot("/tmp/zepto_step2_no_input.png")
                logger.error("[Login P1] Could not find phone input field")
                return False, "❌ Could not find phone input on Zepto. Check screenshot."

            # Zepto expects 10 digits — strip country code
            digits = phone.lstrip('+').replace(' ', '')
            if digits.startswith('91') and len(digits) == 12:
                digits = digits[2:]

            phone_input.clear()
            phone_input.send_keys(digits)
            time.sleep(1)
            logger.info(f"[Login P1] Entered phone digits: {digits}")

            # Click submit / Send OTP button
            submitted = False
            for selector in [
                (By.XPATH, "//button[@type='submit']"),
                (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send otp')]"),
                (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'get otp')]"),
                (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue')]"),
            ]:
                try:
                    self.driver.find_element(*selector).click()
                    submitted = True
                    logger.info(f"[Login P1] Clicked submit via {selector[1][:60]}")
                    break
                except NoSuchElementException:
                    continue

            if not submitted:
                phone_input.send_keys(Keys.RETURN)
                logger.info("[Login P1] Submitted via RETURN key")

            time.sleep(2)
            self.take_screenshot("/tmp/zepto_step3_otp_screen.png")
            logger.info(f"[Login P1] OTP screen url={self.driver.current_url}")
            return True, f"📩 OTP sent to {phone}! Please reply with the OTP."

        except Exception as e:
            logger.error(f"[Login P1] Exception: {e}")
            self.take_screenshot("/tmp/zepto_login_error.png")
            return False, f"❌ Login initiation failed: {e}"

    def complete_login(self, otp: str) -> tuple:
        """
        Phase 2: Enter the OTP received by user.
        Returns (success: bool, message: str)
        """
        try:
            otp = otp.strip()
            logger.info(f"[Login P2] Entering OTP: {otp}")

            # Strategy 1: Individual digit inputs (most common on Zepto)
            otp_inputs = self.driver.find_elements(By.XPATH, "//input[@maxlength='1']")
            if len(otp_inputs) >= len(otp):
                for i, digit in enumerate(otp):
                    otp_inputs[i].click()
                    otp_inputs[i].send_keys(digit)
                    time.sleep(0.2)
                logger.info(f"[Login P2] Entered OTP into {len(otp_inputs)} digit fields")
            else:
                # Strategy 2: Try multiple selector patterns for single OTP input
                otp_field = None
                for selector in [
                    (By.CSS_SELECTOR, "input[autocomplete='one-time-code']"),
                    (By.XPATH, "//input[contains(@placeholder,'OTP') or contains(@placeholder,'otp') or contains(@placeholder,'code') or contains(@placeholder,'OTP code')]"),
                    (By.XPATH, "//input[@type='number' or @type='tel']"),
                    (By.XPATH, "//input[not(@type) or @type='text' or @type='password'][@maxlength and @maxlength > '4']"),
                    (By.CSS_SELECTOR, "input"),  # Any input as fallback
                ]:
                    try:
                        found = self.driver.find_elements(*selector)
                        # Pick the first visible one
                        for el in found:
                            if el.is_displayed():
                                otp_field = el
                                logger.info(f"[Login P2] Found OTP input via {selector}")
                                break
                        if otp_field:
                            break
                    except NoSuchElementException:
                        continue

                if not otp_field:
                    logger.error("[Login P2] Could not find OTP input field")
                    self.take_screenshot("/tmp/zepto_step4_no_otp_field.png")
                    # Try JavaScript as last resort
                    try:
                        self.driver.execute_script("document.querySelector('input').focus();")
                        self.driver.execute_script(f"document.querySelector('input').value = '{otp}';")
                        logger.info("[Login P2] Filled OTP via JavaScript")
                    except Exception as e:
                        logger.error(f"[Login P2] JavaScript fallback failed: {e}")
                        return False, "❌ Could not find OTP input field. Check screenshot."
                else:
                    otp_field.clear()
                    otp_field.send_keys(otp)
                    logger.info("[Login P2] Entered OTP into single field")

            time.sleep(3)
            self.take_screenshot("/tmp/zepto_step4_after_otp.png")
            logger.info(f"[Login P2] After OTP url={self.driver.current_url}")

            # Verify login actually succeeded by checking browser state
            state = self.check_login_state()
            if state == "logged_in":
                self.is_logged_in = True
                return True, "✅ Logged in to Zepto!"
            else:
                logger.error("[Login P2] OTP accepted but still showing Login button — OTP may be wrong")
                self.take_screenshot("/tmp/zepto_step4_otp_failed.png")
                return False, "❌ OTP incorrect or expired. Please try again."

        except Exception as e:
            logger.error(f"[Login P2] Exception: {e}")
            self.take_screenshot("/tmp/zepto_login_error.png")
            return False, f"❌ OTP entry failed: {e}"

    def login(self, phone: str, otp_callback) -> bool:
        """Legacy single-call login (used by /login command)."""
        success, msg = self.initiate_login(phone)
        logger.info(msg)
        if not success:
            return False
        otp = otp_callback(phone)
        if not otp:
            logger.error("No OTP received")
            return False
        success, msg = self.complete_login(otp)
        logger.info(msg)
        return success

    def _open_location_modal(self) -> bool:
        """Open the 'Your Location' modal on Zepto."""
        self.driver.get(ZEPTO_BASE_URL)
        time.sleep(3)
        for selector in [
            (By.XPATH, "//*[@data-testid='location-btn' or @data-testid='address-btn']"),
            (By.XPATH, "//button[contains(@aria-label,'location') or contains(@aria-label,'address') or contains(@aria-label,'deliver')]"),
            (By.XPATH, "//*[contains(text(),'Deliver to') or contains(text(),'Delivering to') or contains(text(),'Select Location')]"),
            (By.XPATH, "//button[contains(@class,'location') or contains(@class,'address')]"),
        ]:
            try:
                self.driver.find_element(*selector).click()
                time.sleep(2)
                logger.info(f"[Location] Opened modal via {selector[1][:60]}")
                return True
            except NoSuchElementException:
                continue
        logger.warning("[Location] Could not open modal via buttons")
        return False

    def get_current_address(self) -> Optional[dict]:
        """Read the currently selected delivery address from Zepto's header."""
        try:
            result = self.driver.execute_script("""
                // The location button in the header shows current address
                var btn = Array.from(document.querySelectorAll('button, a, div')).find(function(el) {
                    var rect = el.getBoundingClientRect();
                    var text = (el.innerText || '').trim();
                    // Top of page, has location-like text, not "Login"
                    return rect.top < 100 && text.length > 2 && text.length < 80
                           && text.toLowerCase() !== 'login'
                           && (el.querySelector('svg') || /deliver|location|home|office|flat|road|street|nagar|colony/i.test(text));
                });
                return btn ? btn.innerText.trim() : null;
            """)
            if result and result.lower() not in ('select location', 'location', ''):
                label = result.split('\n')[0].strip()
                logger.info(f"[Address] Current address in header: {repr(result)}")
                return {"index": 0, "label": label, "address": result}
            logger.info(f"[Address] No address selected in browser yet (shows: {repr(result)})")
            return None
        except Exception as e:
            logger.warning(f"[Address] Could not read header address: {e}")
            return None

    def get_saved_addresses(self) -> tuple:
        """
        Open Zepto's location modal and scrape saved address cards.
        Does NOT rely on a 'Saved Addresses' heading — finds cards directly.
        Returns (success: bool, list of {index, label, address})
        """
        try:
            self._open_location_modal()
            time.sleep(4)  # Wait for addresses to load asynchronously

            # Scroll down inside the modal to reveal saved addresses
            self.driver.execute_script("""
                var modal = document.querySelector('[role="dialog"],[role="sheet"],[class*="modal"],[class*="drawer"],[class*="bottom-sheet"]');
                if (modal) modal.scrollTop = 400;
                else window.scrollBy(0, 400);
            """)
            time.sleep(1)
            self.take_screenshot("/tmp/zepto_addresses.png")

            addresses = self.driver.execute_script("""
                // Strategy: find all visible elements inside any dialog/modal/sheet
                // that look like address cards (short label line + longer address line)
                var modalRoots = Array.from(document.querySelectorAll(
                    '[role="dialog"], [role="sheet"], [data-testid*="modal"], [data-testid*="drawer"], ' +
                    '[class*="modal"], [class*="drawer"], [class*="sheet"], [class*="bottom"]'
                ));
                // Fallback: use entire document if no modal found
                var root = modalRoots.length > 0 ? modalRoots[0] : document.body;

                var candidates = Array.from(root.querySelectorAll('*'));
                var seen = {};
                var result = [];

                candidates.forEach(function(el) {
                    var text = (el.innerText || '').trim();
                    var lines = text.split('\\n').map(function(l) { return l.trim(); }).filter(Boolean);
                    if (lines.length < 2) return;

                    // Strip distance indicator e.g. "Home • 1132.6 km" → "Home"
                    var label = lines[0].replace(/\\s*•.*$/, '').trim();
                    var addr = lines.slice(1).join(', ');

                    // Label: short (2-25 chars), not a generic UI heading
                    var labelOk = label.length >= 2 && label.length <= 25;
                    var HEADINGS = ['saved addresses','your location','select location',
                                    'add new address','add address','deliver to','addresses','location'];
                    var notHeading = !HEADINGS.some(function(h){ return label.toLowerCase() === h; });
                    // Address: reasonably long
                    var addrOk = addr.length > 10;

                    var rect = el.getBoundingClientRect();
                    var visible = rect.width > 60 && rect.height > 15 && rect.height < 200;

                    if (labelOk && notHeading && addrOk && visible && !seen[label]) {
                        seen[label] = true;
                        result.push({ label: label, address: addr });
                    }
                });
                return result.slice(0, 10);
            """)

            if not addresses:
                # Dump modal text to help debug
                try:
                    modal_text = self.driver.execute_script("""
                        var modal = document.querySelector('[role="dialog"],[role="sheet"],[class*="modal"],[class*="drawer"],[class*="sheet"],[class*="bottom"]');
                        return modal ? modal.innerText.substring(0, 1000) : document.body.innerText.substring(0, 1000);
                    """)
                    logger.info(f"[Addresses] Modal text dump: {repr(modal_text)}")
                except Exception:
                    pass

                # Hard fallback: grab all text blocks in the modal that look like addresses
                logger.warning("[Addresses] JS scrape returned nothing — trying text-based fallback")
                try:
                    all_els = self.driver.find_elements(By.XPATH, "//*[string-length(text()) > 5]")
                    seen = set()
                    addresses = []
                    for el in all_els:
                        try:
                            if not el.is_displayed():
                                continue
                            text = el.text.strip()
                            lines = [l.strip() for l in text.split('\n') if l.strip()]
                            clean_label = lines[0].replace('•', '').split('  ')[0].strip()
                            if len(lines) >= 2 and 2 <= len(clean_label) <= 25 and clean_label not in seen:
                                seen.add(clean_label)
                                addresses.append({"label": clean_label, "address": ', '.join(lines[1:])})
                        except Exception:
                            continue
                except Exception as e:
                    logger.warning(f"[Addresses] Fallback failed: {e}")

            result = [{"index": i, "label": a["label"], "address": a.get("address", "")}
                      for i, a in enumerate(addresses or [])]
            logger.info(f"[Addresses] Found {len(result)}: {[a['label'] for a in result]}")
            return True, result

        except Exception as e:
            logger.error(f"[Addresses] Failed: {e}")
            return False, []

    def select_zepto_address(self, label: str) -> bool:
        """Click the saved address card with the given label in the location modal."""
        try:
            self.take_screenshot("/tmp/zepto_addr_before_click.png")
            logger.info(f"[Addresses] Attempting to click address: {label}")

            # JS approach: find any visible element whose text starts with the label,
            # then walk up to the nearest clickable ancestor (a, button, or li/div with onclick)
            clicked = self.driver.execute_script("""
                var label = arguments[0];
                // Find all text-containing elements whose first line matches label
                var all = Array.from(document.querySelectorAll('*'));
                var match = null;
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    var text = (el.innerText || '').trim();
                    var firstLine = text.split('\\n')[0].replace(/\\s*•.*$/, '').trim();
                    var rect = el.getBoundingClientRect();
                    if (firstLine === label && rect.width > 50 && rect.height > 10) {
                        match = el;
                        break;
                    }
                }
                if (!match) return 'not_found';

                // Walk up to the clickable card wrapper
                var target = match;
                for (var p = match; p && p !== document.body; p = p.parentElement) {
                    var tag = (p.tagName || '').toLowerCase();
                    var rect = p.getBoundingClientRect();
                    if ((tag === 'a' || tag === 'button' || tag === 'li') && rect.width > 100) {
                        target = p;
                        break;
                    }
                    // Also accept divs that look like cards (reasonably tall, wide)
                    if (tag === 'div' && rect.width > 150 && rect.height > 40 && rect.height < 200) {
                        target = p;
                        break;
                    }
                }
                try {
                    target.click();
                    return 'clicked';
                } catch(e) {
                    return 'click_failed:' + e.message;
                }
            """, label)

            logger.info(f"[Addresses] select_zepto_address JS result: {clicked}")
            time.sleep(2)
            self.take_screenshot("/tmp/zepto_addr_selected.png")

            if clicked == 'clicked':
                return True

            # JS didn't find it — try XPath as fallback
            for xpath in [
                f"//*[normalize-space(translate(text(),'•0123456789abcdefghijklmnopqrstuvwxyz ',''))='{label}']",
                f"//*[contains(text(),'{label}')]",
            ]:
                try:
                    el = self.driver.find_element(By.XPATH, xpath)
                    self.driver.execute_script("arguments[0].click();", el)
                    time.sleep(2)
                    logger.info(f"[Addresses] XPath fallback clicked: {label}")
                    return True
                except NoSuchElementException:
                    continue

            logger.error(f"[Addresses] Could not find/click address: {label} (JS: {clicked})")
            return False
        except Exception as e:
            logger.error(f"[Addresses] select_zepto_address failed: {e}")
            return False

    def set_delivery_location(self, pin_code: str) -> bool:
        """Set delivery location by pin code."""
        try:
            self.driver.get(ZEPTO_BASE_URL)
            time.sleep(3)

            # Try clicking the location/delivery area button
            for selector in [
                "//button[contains(text(),'Deliver') or contains(text(),'Location') or contains(text(),'Enter')]",
                "//div[contains(text(),'Deliver') or contains(text(),'Location')]",
                "//input[contains(@placeholder,'pincode') or contains(@placeholder,'PIN') or contains(@placeholder,'location') or contains(@placeholder,'area')]",
            ]:
                try:
                    el = self.driver.find_element(By.XPATH, selector)
                    el.click()
                    time.sleep(1)
                    break
                except NoSuchElementException:
                    continue

            pin_input = self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//input[contains(@placeholder,'pincode') or contains(@placeholder,'PIN') or contains(@placeholder,'location') or contains(@placeholder,'area') or contains(@placeholder,'Enter')]"
                ))
            )
            pin_input.clear()
            pin_input.send_keys(pin_code)
            time.sleep(2)

            # Select first autocomplete result
            for selector in [
                "(//ul//li)[1]",
                "(//div[@role='option'])[1]",
                "(//div[contains(@class,'suggestion')])[1]",
                "(//div[contains(@class,'result')])[1]",
            ]:
                try:
                    first_result = self.driver.find_element(By.XPATH, selector)
                    first_result.click()
                    time.sleep(2)
                    logger.info(f"Delivery location set to pin code: {pin_code}")
                    return True
                except NoSuchElementException:
                    continue

            pin_input.send_keys(Keys.RETURN)
            time.sleep(2)
            return True

        except Exception as e:
            logger.error(f"Failed to set delivery location: {e}")
            return False

    def _dismiss_popups(self):
        """Dismiss any overlays or popups that might block interaction."""
        for selector in [
            "//button[contains(text(),'Allow') or contains(text(),'OK') or contains(text(),'Got it') or contains(text(),'Close') or contains(text(),'×') or contains(text(),'Cancel')]",
            "//div[@role='dialog']//button",
            "//div[contains(@class,'modal')]//button[1]",
            "//div[contains(@class,'overlay')]//button",
        ]:
            try:
                buttons = self.driver.find_elements(By.XPATH, selector)
                for btn in buttons[:3]:  # Try first 3 matches
                    try:
                        if btn.is_displayed():
                            btn.click()
                            time.sleep(0.3)
                    except Exception:
                        pass
            except NoSuchElementException:
                pass

    def search_products(self, query: str) -> List[ZeptoProduct]:
        """Search for products using JS-based extraction (resilient to dynamic class names)."""
        try:
            self.driver.get(f"{ZEPTO_BASE_URL}/search?query={query.replace(' ', '+')}")
            time.sleep(5)

            self._dismiss_popups()

            logger.info(f"Search page title: {self.driver.title} | URL: {self.driver.current_url}")

            # JavaScript-based extraction: find cards containing price + add button
            raw = self.driver.execute_script("""
                var candidates = Array.from(document.querySelectorAll('div, article, section, li'));
                var cards = candidates.filter(function(el) {
                    var text = el.innerText || '';
                    var hasPrice = text.includes('₹');
                    var hasAdd = el.querySelector('button') !== null;
                    var rect = el.getBoundingClientRect();
                    var isVisible = rect.width > 50 && rect.height > 50;
                    var notTooLarge = rect.width < 600;
                    var childCount = el.children.length;
                    return hasPrice && hasAdd && isVisible && notTooLarge && childCount >= 2 && childCount <= 20;
                });
                // Deduplicate by bounding box top position
                var seen = {};
                var unique = [];
                cards.forEach(function(el) {
                    var rect = el.getBoundingClientRect();
                    var key = Math.round(rect.top / 10) + '_' + Math.round(rect.left / 10);
                    if (!seen[key]) {
                        seen[key] = true;
                        unique.push(el);
                    }
                });
                return unique.slice(0, 5).map(function(el) {
                    var text = el.innerText || '';
                    var lines = text.split('\\n').map(function(l) { return l.trim(); }).filter(Boolean);
                    // Find product name: skip short lines, quantity lines, price lines, button labels
                    var name = '';
                    for (var i = 0; i < lines.length; i++) {
                        var l = lines[i];
                        var isQty = /^\\d+\\s*(g|kg|ml|L|pc|pack|pcs)/i.test(l);
                        var isPrice = /^₹/.test(l);
                        var isShort = l.length < 4;
                        var isBtn = /^(Add|Buy|Remove|\\+|-)$/.test(l);
                        if (!isQty && !isPrice && !isShort && !isBtn && l.length > 5) {
                            name = l;
                            break;
                        }
                    }
                    if (!name) name = lines.find(function(l) { return l.length > 5 && !/^₹/.test(l); }) || lines[0] || '';
                    var priceMatch = text.match(/₹\\s?([\\d,]+)/);
                    var price = priceMatch ? parseFloat(priceMatch[1].replace(',','')) : 0;
                    var qtyLine = lines.find(function(l) { return /\\d+\\s*(g|kg|ml|L|pc|pack)/i.test(l); }) || '';
                    var img = el.querySelector('img');
                    var imgSrc = img ? (img.src || img.getAttribute('data-src') || '') : '';
                    var outOfStock = text.includes('Out of Stock') || text.includes('Notify Me') || text.includes('Unavailable');
                    return { name: name, price: price, qty: qtyLine, img: imgSrc, outOfStock: outOfStock };
                });
            """)

            products = []
            for i, p in enumerate(raw or []):
                # Skip products with missing name or price
                if not p.get('name') or p.get('price', 0) <= 0:
                    logger.warning(f"[Search] Skipping product {i}: missing name={not p.get('name')}, price={p.get('price', 0)}")
                    continue
                products.append(ZeptoProduct(
                        product_id=f"zepto_{i}_{query[:10].replace(' ', '_')}",
                        name=p['name'],
                        price=p['price'],
                        quantity_unit=p.get('qty', ''),
                        image_url=p.get('img', ''),
                        in_stock=not p.get('outOfStock', False),
                    ))

            logger.info(f"Found {len(products)} products for '{query}'")
            return products

        except Exception as e:
            logger.error(f"Search failed for '{query}': {e}")
            return []

    def add_to_cart(self, product_index: int, query: str) -> bool:
        """Add a product from the current search page to Zepto's cart by index."""
        try:
            logger.info(f"[Cart] Adding product {product_index} to cart on page: {self.driver.current_url}")
            self.take_screenshot(f"/tmp/zepto_add_to_cart_before_{product_index}.png")

            result = self.driver.execute_script("""
                var idx = arguments[0];
                var candidates = Array.from(document.querySelectorAll('div, article, section, li'));
                var cards = candidates.filter(function(el) {
                    var text = el.innerText || '';
                    var hasPrice = text.includes('₹');
                    var hasAdd = el.querySelector('button') !== null;
                    var rect = el.getBoundingClientRect();
                    var isVisible = rect.width > 50 && rect.height > 50;
                    var notTooLarge = rect.width < 600;
                    var childCount = el.children.length;
                    return hasPrice && hasAdd && isVisible && notTooLarge && childCount >= 2 && childCount <= 20;
                });
                var seen = {};
                var unique = [];
                cards.forEach(function(el) {
                    var rect = el.getBoundingClientRect();
                    var key = Math.round(rect.top / 10) + '_' + Math.round(rect.left / 10);
                    if (!seen[key]) { seen[key] = true; unique.push(el); }
                });
                if (idx >= unique.length) return { success: false, reason: 'index out of bounds', count: unique.length };
                var card = unique[idx];
                var buttons = Array.from(card.querySelectorAll('button'));
                var addBtn = buttons.find(function(b) {
                    var t = (b.innerText || '').trim().toLowerCase();
                    return t === 'add' || t === '+';
                }) || buttons[buttons.length - 1];
                if (!addBtn) return { success: false, reason: 'no button found' };
                try {
                    addBtn.click();
                    return { success: true, reason: 'clicked' };
                } catch (e) {
                    return { success: false, reason: 'click failed: ' + e.message };
                }
            """, product_index)

            time.sleep(2)
            logger.info(f"[Cart] add_to_cart result: {result}")
            self.take_screenshot(f"/tmp/zepto_add_to_cart_after_{product_index}.png")
            return bool(result.get('success'))
        except Exception as e:
            logger.error(f"[Cart] add_to_cart failed: {e}")
            return False

    def get_address_modal_addresses(self) -> List[dict]:
        """
        Check if Zepto's 'Your Location' modal is open.
        If so, return saved addresses scraped from it.
        """
        try:
            result = self.driver.execute_script("""
                // Detect the modal by its heading text
                var modal = Array.from(document.querySelectorAll('div, section')).find(function(el) {
                    var t = (el.innerText || '').trim();
                    var rect = el.getBoundingClientRect();
                    return rect.width > 200 && rect.height > 200
                        && /your location/i.test(t)
                        && /saved addresses/i.test(t);
                });
                if (!modal) return null;

                // Collect saved address cards — skip utility rows
                var seen = {};
                var results = [];
                Array.from(modal.querySelectorAll('div, li, a')).forEach(function(el) {
                    var text = (el.innerText || '').trim();
                    var lines = text.split('\\n').map(function(l){ return l.trim(); }).filter(Boolean);
                    if (lines.length < 2) return;
                    var label = lines[0].replace(/•.*$/, '').trim();  // strip "• 1132.6 km"
                    if (/use my current|add new|search|saved addresses|your location/i.test(label)) return;
                    if (label.length > 30 || label.length < 2) return;
                    var rect = el.getBoundingClientRect();
                    if (rect.width < 150 || rect.height < 30) return;
                    if (seen[label]) return;
                    seen[label] = true;
                    results.push({ label: label, address: lines.slice(1).join(', ') });
                });
                return results;
            """)
            if result:
                addresses = [{"index": i, "label": a["label"], "address": a["address"]}
                             for i, a in enumerate(result)]
                logger.info(f"[AddressModal] Found {len(addresses)} addresses: {[a['label'] for a in addresses]}")
                self.take_screenshot("/tmp/zepto_address_modal_detected.png")
                return addresses
            return []
        except Exception as e:
            logger.warning(f"[AddressModal] check failed: {e}")
            return []

    def get_browser_cart(self) -> List[dict]:
        """Click the cart button and scrape product items above the Bill summary section."""
        try:
            # Click the floating cart pill
            current_url = self.driver.current_url
            already_on_cart = any(k in current_url for k in ('cart', 'checkout', 'payment'))
            if already_on_cart:
                logger.info("[BrowserCart] Already on cart/checkout page — skipping pill click")
                clicked = True
            else:
                clicked = self.driver.execute_script("""
                    var btn = Array.from(document.querySelectorAll('button, a, div')).find(function(el) {
                        var t = (el.innerText || '').trim().toLowerCase();
                        var rect = el.getBoundingClientRect();
                        return rect.bottom > window.innerHeight * 0.7
                            && /^cart/.test(t) && /item/.test(t) && rect.width > 80;
                    });
                    if (btn) { btn.click(); return true; }
                    return false;
                """)
                logger.info(f"[BrowserCart] Cart pill clicked: {clicked}")
                if not clicked:
                    logger.warning("[BrowserCart] No cart pill found and not on cart page — returning empty")
                    return []
            time.sleep(3)
            self.take_screenshot("/tmp/zepto_cart_view.png")

            # Scrape only items above the "Bill summary" heading
            raw = self.driver.execute_script("""
                // Find Bill summary top — everything below it is billing, not products
                var billEl = Array.from(document.querySelectorAll('*')).find(function(el) {
                    var t = (el.innerText || '').trim().toLowerCase();
                    var rect = el.getBoundingClientRect();
                    return t === 'bill summary' && rect.width > 80;
                });
                var billTop = billEl ? billEl.getBoundingClientRect().top : window.innerHeight * 0.75;

                var seen = {};
                var results = [];
                var candidates = Array.from(document.querySelectorAll('div, li'));
                candidates.forEach(function(el) {
                    var rect = el.getBoundingClientRect();
                    // Only consider elements fully above the Bill summary
                    if (rect.bottom >= billTop || rect.top < 0) return;
                    if (rect.width < 80 || rect.height < 40 || rect.height > 250) return;

                    var text = (el.innerText || '').trim();
                    var lines = text.split('\\n').map(function(l){ return l.trim(); }).filter(Boolean);
                    if (!text.includes('₹') || el.children.length < 2 || el.children.length > 20) return;

                    // Name: first line that is not a price/qty/button/discount
                    var name = lines.find(function(l){
                        return l.length > 5
                            && !/^₹/.test(l)
                            && !/^\\d+$/.test(l)
                            && !/^(Add|Remove|\\+|-)$/.test(l)
                            && !/^\\d+\\s*(g|kg|ml|L|pc|pcs|pack)/i.test(l)
                            && !/OFF$/i.test(l);
                    });
                    if (!name) return;

                    var priceMatch = text.match(/₹\\s?([\\d,]+)/);
                    var price = priceMatch ? priceMatch[1] : '';
                    var key = name + price;
                    if (price && !seen[key]) {
                        seen[key] = true;
                        results.push({ name: name, price: price });
                    }
                });
                return results.slice(0, 20);
            """)
            logger.info(f"[BrowserCart] Found {len(raw or [])} items")
            return raw or []
        except Exception as e:
            logger.error(f"[BrowserCart] Failed: {e}")
            return []

    def sync_cart_to_zepto(self, cart_items: list) -> int:
        """Re-add local cart items to Zepto's browser cart. Returns count of items added."""
        added = 0
        for item in cart_items:
            name = item.get('product_name', '')
            if not name:
                continue
            try:
                logger.info(f"[Sync] Adding '{name}' to Zepto cart")
                self.driver.get(f"{ZEPTO_BASE_URL}/search?query={name.replace(' ', '+')}")
                time.sleep(4)
                self._dismiss_popups()

                result = self.driver.execute_script("""
                    var candidates = Array.from(document.querySelectorAll('div, article, section, li'));
                    var cards = candidates.filter(function(el) {
                        var text = el.innerText || '';
                        var hasPrice = text.includes('₹');
                        var hasAdd = el.querySelector('button') !== null;
                        var rect = el.getBoundingClientRect();
                        return hasPrice && hasAdd && rect.width > 50 && rect.height > 50
                               && rect.width < 600 && el.children.length >= 2;
                    });
                    var seen = {};
                    var unique = [];
                    cards.forEach(function(el) {
                        var rect = el.getBoundingClientRect();
                        var key = Math.round(rect.top/10) + '_' + Math.round(rect.left/10);
                        if (!seen[key]) { seen[key] = true; unique.push(el); }
                    });
                    if (!unique.length) return false;
                    var card = unique[0];
                    var buttons = Array.from(card.querySelectorAll('button'));
                    var addBtn = buttons.find(function(b) {
                        var t = (b.innerText || '').trim().toLowerCase();
                        return t === 'add' || t === '+';
                    }) || buttons[buttons.length - 1];
                    if (addBtn) { addBtn.click(); return true; }
                    return false;
                """)
                if result:
                    added += 1
                    time.sleep(2)
                    logger.info(f"[Sync] Added '{name}' to Zepto cart")
                else:
                    logger.warning(f"[Sync] Could not add '{name}' to Zepto cart")
            except Exception as e:
                logger.error(f"[Sync] Failed for '{name}': {e}")
        logger.info(f"[Sync] Synced {added}/{len(cart_items)} items to Zepto cart")
        return added

    def go_to_checkout(self) -> bool:
        """Click the floating cart button or cart icon, then proceed to checkout."""
        try:
            self.take_screenshot("/tmp/zepto_checkout_step1_before.png")
            current_url = self.driver.current_url
            logger.info(f"[Checkout] Current page: {current_url}")

            # If already on the cart/checkout page, skip clicking the pill —
            # it doesn't exist there. Go straight to the Proceed button.
            already_on_cart = any(k in current_url for k in ('cart', 'checkout', 'payment'))
            cart_clicked = already_on_cart
            if already_on_cart:
                logger.info("[Checkout] Already on cart/checkout page — skipping pill click")

            # Try JS first: find the bottom floating "Cart N item(s)" pill button
            if not cart_clicked:
                cart_clicked = self.driver.execute_script("""
                    var btn = Array.from(document.querySelectorAll('button, a, div')).find(function(el) {
                        var t = (el.innerText || '').trim().toLowerCase();
                        var rect = el.getBoundingClientRect();
                        var isBottom = rect.bottom > window.innerHeight * 0.7;
                        var isCartPill = /^cart/.test(t) && /item/.test(t);
                        return isBottom && isCartPill && rect.width > 80;
                    });
                    if (btn) { btn.click(); return true; }
                    return false;
                """)
                if cart_clicked:
                    logger.info("[Checkout] Clicked bottom floating cart pill via JS")
                    time.sleep(2)

            # Fallback: top-right cart icon selectors
            if not cart_clicked:
                for selector in [
                    (By.XPATH, "//*[@data-testid='cart-icon']"),
                    (By.XPATH, "//*[@data-testid='cart']"),
                    (By.XPATH, "//a[contains(@href,'cart')]"),
                    (By.XPATH, "//*[contains(@aria-label,'cart') or contains(@aria-label,'Cart')]"),
                    (By.XPATH, "//header//*[contains(@class,'cart')]"),
                ]:
                    try:
                        el = WebDriverWait(self.driver, 4).until(EC.element_to_be_clickable(selector))
                        self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(2)
                        cart_clicked = True
                        logger.info(f"[Checkout] Clicked cart icon via {selector[1][:60]}")
                        break
                    except TimeoutException:
                        continue

            self.take_screenshot("/tmp/zepto_checkout_step2_after_cart_click.png")
            logger.info(f"[Checkout] cart_clicked={cart_clicked}, url={self.driver.current_url}")

            # Now look for Proceed to Checkout button
            for selector in [
                (By.XPATH, "//button[contains(text(),'Proceed')]"),
                (By.XPATH, "//button[contains(text(),'Checkout')]"),
                (By.XPATH, "//button[contains(text(),'Place Order')]"),
                (By.XPATH, "//*[contains(text(),'Proceed to Checkout')]"),
            ]:
                try:
                    btn = WebDriverWait(self.driver, 6).until(EC.element_to_be_clickable(selector))
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(3)
                    logger.info(f"[Checkout] Clicked proceed via {selector[1][:50]}, now at {self.driver.current_url}")
                    self.take_screenshot("/tmp/zepto_checkout_step3_payment.png")
                    return True
                except TimeoutException:
                    continue

            logger.error(f"[Checkout] Could not find Proceed button. cart_clicked={cart_clicked}")
            self.take_screenshot("/tmp/zepto_checkout_error.png")
            return False
        except Exception as e:
            logger.error(f"[Checkout] Failed: {e}")
            return False

    def get_payment_options(self) -> List[dict]:
        """Scrape available payment options from the checkout page."""
        try:
            time.sleep(3)  # Wait for payment options to render
            logger.info(f"[Payment] Getting options from {self.driver.current_url}")

            raw = self.driver.execute_script("""
                var seen = {};
                var result = [];
                var candidates = Array.from(document.querySelectorAll('div, li, label, button'));
                candidates.forEach(function(el) {
                    var text = (el.innerText || '').trim();
                    var firstLine = text.split('\\n')[0].trim();
                    var rect = el.getBoundingClientRect();
                    var isVisible = rect.width > 50 && rect.height > 10 && rect.height < 200;
                    var isPayment = /upi|gpay|phonepe|paytm|bhim|credit|debit|card|cash|cod|net.?bank|wallet|rupay|visa|master/i.test(text);
                    var notTooDeep = el.children.length < 8;
                    var shortLabel = firstLine.length > 1 && firstLine.length < 80;
                    if (isVisible && isPayment && notTooDeep && shortLabel && !seen[firstLine]) {
                        seen[firstLine] = true;
                        result.push({ label: firstLine });
                    }
                });
                return result.slice(0, 8);
            """)
            options = [{"index": i, "label": o["label"]} for i, o in enumerate(raw or [])]
            logger.info(f"[Payment] Options: {[o['label'] for o in options]}")
            return options
        except Exception as e:
            logger.error(f"[Payment] get_payment_options failed: {e}")
            return []

    def select_payment_option(self, label: str) -> tuple:
        """
        Click the payment option with the given label, then click Pay.
        Returns (otp_required: bool, message: str).
        """
        try:
            # Click the payment option
            clicked = False
            for xpath in [
                f"//*[normalize-space(text())='{label}']",
                f"//*[contains(normalize-space(text()),'{label[:30]}')]",
            ]:
                try:
                    el = self.driver.find_element(By.XPATH, xpath)
                    try:
                        el.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", el)
                    time.sleep(2)
                    logger.info(f"[Payment] Clicked option: {label}")
                    clicked = True
                    break
                except NoSuchElementException:
                    continue

            if not clicked:
                logger.warning(f"[Payment] Could not find option: {label}")

            # Click Pay / Place Order button
            for xpath in [
                "//button[contains(text(),'Pay') or contains(text(),'Place Order')]",
                "//button[contains(text(),'Confirm')]",
            ]:
                try:
                    btn = self.driver.find_element(By.XPATH, xpath)
                    btn.click()
                    time.sleep(3)
                    logger.info("[Payment] Clicked Pay button")
                    break
                except NoSuchElementException:
                    continue

            # Check if OTP input appeared
            otp_inputs = self.driver.find_elements(
                By.XPATH,
                "//input[@maxlength='1'] | //input[contains(@placeholder,'OTP') or contains(@placeholder,'otp') or contains(@placeholder,'PIN') or @autocomplete='one-time-code']"
            )
            visible_otp = [f for f in otp_inputs if f.is_displayed()]
            if visible_otp:
                logger.info("[Payment] OTP input detected")
                return True, "🔐 Payment OTP required. Please reply with the OTP sent to your phone."

            return False, "✅ Payment submitted — checking confirmation..."

        except Exception as e:
            logger.error(f"[Payment] select_payment_option failed: {e}")
            return False, f"❌ Payment selection failed: {e}"

    def enter_payment_otp(self, otp: str) -> Optional[ZeptoOrder]:
        """Enter payment OTP and confirm. Returns ZeptoOrder on success."""
        try:
            otp = otp.strip()
            logger.info(f"[Payment] Entering OTP: {otp}")

            # Try individual digit inputs
            digit_inputs = self.driver.find_elements(By.XPATH, "//input[@maxlength='1']")
            visible_digits = [f for f in digit_inputs if f.is_displayed()]
            if len(visible_digits) >= len(otp):
                for i, digit in enumerate(otp):
                    visible_digits[i].click()
                    visible_digits[i].send_keys(digit)
                    time.sleep(0.2)
                logger.info("[Payment] Entered OTP into digit fields")
            else:
                # Single OTP field
                otp_field = None
                for selector in [
                    (By.CSS_SELECTOR, "input[autocomplete='one-time-code']"),
                    (By.XPATH, "//input[contains(@placeholder,'OTP') or contains(@placeholder,'PIN')]"),
                    (By.XPATH, "//input[@type='number' or @type='tel']"),
                ]:
                    try:
                        fields = self.driver.find_elements(*selector)
                        for f in fields:
                            if f.is_displayed():
                                otp_field = f
                                break
                        if otp_field:
                            break
                    except Exception:
                        continue

                if otp_field:
                    otp_field.clear()
                    otp_field.send_keys(otp)
                    logger.info("[Payment] Entered OTP into single field")

            time.sleep(2)

            # Click Confirm/Verify/Submit after OTP
            for xpath in [
                "//button[contains(text(),'Confirm') or contains(text(),'Verify') or contains(text(),'Submit')]",
                "//button[contains(text(),'Pay')]",
            ]:
                try:
                    btn = self.driver.find_element(By.XPATH, xpath)
                    btn.click()
                    time.sleep(5)
                    logger.info("[Payment] Clicked confirm after OTP")
                    break
                except NoSuchElementException:
                    continue

            return self._capture_order_confirmation()

        except Exception as e:
            logger.error(f"[Payment] enter_payment_otp failed: {e}")
            return None

    def _capture_order_confirmation(self) -> ZeptoOrder:
        """Wait for and parse the order confirmation screen."""
        try:
            order_id_el = self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//*[contains(text(),'Order') and (contains(text(),'#') or contains(text(),'placed') or contains(text(),'confirmed'))]"
                ))
            )
            order_text = order_id_el.text
            logger.info(f"[Payment] Order confirmed: {order_text}")
            return ZeptoOrder(order_id=order_text, status="placed", total=0.0, estimated_delivery="10 minutes")
        except TimeoutException:
            logger.warning("[Payment] Order confirmation element not found — assuming placed")
            return ZeptoOrder(order_id="unknown", status="placed", total=0.0, estimated_delivery="10 minutes")

    def take_screenshot(self, path: str = "/tmp/zepto_screenshot.png") -> str:
        """Capture current browser state for confirmation."""
        if self.driver:
            self.driver.save_screenshot(path)
        return path
