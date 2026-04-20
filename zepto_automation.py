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

    def _check_session_valid(self) -> bool:
        """Check if already logged in by looking for logged-in indicators on page."""
        try:
            self.driver.get(ZEPTO_BASE_URL)
            time.sleep(2)
            # Check if we see logged-in elements (not login button)
            login_btn = self.driver.find_elements(By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]")
            if login_btn:
                logger.info("[Login] Login button still visible, session expired")
                return False
            # Check for user profile or account elements
            profile = self.driver.find_elements(By.XPATH, "//*[@data-testid='profile'] | //*[contains(@class,'profile')] | //*[contains(text(),'Account')]")
            is_logged = len(profile) > 0
            logger.info(f"[Login] Session check: logged_in={is_logged}")
            return is_logged
        except Exception as e:
            logger.warning(f"[Login] Could not check session: {e}")
            return False

    def start(self):
        if not self.driver:
            self.driver = self._build_driver()
            self.wait = WebDriverWait(self.driver, SELENIUM_TIMEOUT)
            logger.info("Selenium driver started")
            # Try to restore saved session
            self._load_cookies()
            if self._check_session_valid():
                self.is_logged_in = True
                logger.info("[Login] Restored session from saved cookies")
            else:
                logger.info("[Login] Cookies expired or invalid, will need to re-login")

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
            # If cookies restored a valid session, skip OTP entirely
            if self.is_logged_in:
                logger.info("[Login P1] Already logged in via saved cookies — skipping OTP")
                return True, "✅ Already logged in (session restored from cookies)!"
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

            # Debug: log all input fields on page
            all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
            logger.info(f"[Login P2] Found {len(all_inputs)} input fields on page")
            for i, inp in enumerate(all_inputs[:5]):  # Log first 5
                logger.info(f"  Input {i}: type={inp.get_attribute('type')}, placeholder={inp.get_attribute('placeholder')}, maxlength={inp.get_attribute('maxlength')}, visible={inp.is_displayed()}")

            logger.info(f"[Login P2] After OTP url={self.driver.current_url}")

            self.is_logged_in = True
            self._save_cookies()
            return True, "✅ Logged in to Zepto!"

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

    def get_saved_addresses(self) -> tuple:
        """
        Open Zepto's 'Your Location' modal and scrape the Saved Addresses section.
        Returns (success: bool, list of {index, label, address})
        """
        try:
            self._open_location_modal()
            self.take_screenshot("/tmp/zepto_addresses.png")

            # Find the "Saved Addresses" heading, then get all address cards after it
            addresses = self.driver.execute_script("""
                // Find 'Saved Addresses' heading
                var heading = Array.from(document.querySelectorAll('*')).find(function(el) {
                    return el.childElementCount === 0 &&
                           (el.innerText || '').trim() === 'Saved Addresses';
                });
                if (!heading) return [];

                // Walk up to find the container that holds the address cards
                var container = heading.parentElement;
                while (container && container.querySelectorAll('*').length < 5) {
                    container = container.parentElement;
                }
                if (!container) return [];

                // Collect all direct child divs after the heading that look like address cards
                // Each card has a short label line + longer address line
                var allText = Array.from(container.querySelectorAll('*')).filter(function(el) {
                    if (el.childElementCount > 3) return false;
                    var text = (el.innerText || '').trim();
                    var lines = text.split('\\n').map(function(l) { return l.trim(); }).filter(Boolean);
                    var rect = el.getBoundingClientRect();
                    return (
                        lines.length >= 2 &&
                        lines[0].length > 1 && lines[0].length < 30 &&
                        lines[1].length > 10 &&
                        rect.width > 80 && rect.height > 20 && rect.height < 150
                    );
                });

                // Deduplicate by label
                var seen = {};
                var result = [];
                allText.forEach(function(el) {
                    var lines = (el.innerText || '').trim().split('\\n').map(function(l) { return l.trim(); }).filter(Boolean);
                    var label = lines[0];
                    if (!seen[label]) {
                        seen[label] = true;
                        result.push({ label: label, address: lines.slice(1).join(', ') });
                    }
                });
                return result;
            """)

            if not addresses:
                logger.warning("[Addresses] JS scrape returned nothing, falling back to XPath text match")
                # Fallback: find all elements after the "Saved Addresses" heading
                try:
                    heading = self.driver.find_element(By.XPATH, "//*[normalize-space(text())='Saved Addresses']")
                    # Get parent and look for child containers
                    parent = heading.find_element(By.XPATH, "./..")
                    cards = parent.find_elements(By.XPATH, ".//div[.//svg or .//img]")
                    addresses = []
                    seen = set()
                    for card in cards:
                        text = card.text.strip()
                        lines = [l.strip() for l in text.split('\n') if l.strip()]
                        if len(lines) >= 2 and lines[0] not in seen and len(lines[0]) < 30:
                            seen.add(lines[0])
                            addresses.append({"label": lines[0], "address": ', '.join(lines[1:])})
                except Exception as e:
                    logger.warning(f"[Addresses] Fallback also failed: {e}")

            result = [{"index": i, "label": a["label"], "address": a.get("address", "")}
                      for i, a in enumerate(addresses or [])]
            logger.info(f"[Addresses] Found {len(result)}: {[a['label'] for a in result]}")
            return True, result

        except Exception as e:
            logger.error(f"[Addresses] Failed: {e}")
            return False, []

    def select_zepto_address(self, label: str) -> bool:
        """Click the saved address with the given label in the location modal."""
        try:
            # Modal should still be open; if not, re-open it
            for xpath in [
                f"//*[normalize-space(text())='{label}']",
                f"//*[contains(text(),'{label}')]",
            ]:
                try:
                    el = self.driver.find_element(By.XPATH, xpath)
                    # Click the card (may need to go up to the clickable parent)
                    try:
                        el.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", el)
                    time.sleep(2)
                    logger.info(f"[Addresses] Clicked address: {label}")
                    self.take_screenshot("/tmp/zepto_addr_selected.png")
                    return True
                except NoSuchElementException:
                    continue
            logger.error(f"[Addresses] Could not find address with label: {label}")
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
            "//button[contains(text(),'Allow') or contains(text(),'OK') or contains(text(),'Got it') or contains(text(),'Close') or contains(text(),'×')]",
            "//div[@role='dialog']//button",
        ]:
            try:
                btn = self.driver.find_element(By.XPATH, selector)
                btn.click()
                time.sleep(0.5)
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
                    var name = lines[0] || '';
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
                if p.get('name') and p.get('price', 0) > 0:
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
        """Add a product from the current search page to cart by index."""
        try:
            product_cards = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class,'product-card') or contains(@data-testid,'product')]"
            )

            if product_index >= len(product_cards):
                return False

            card = product_cards[product_index]
            add_btn = card.find_element(
                By.XPATH,
                ".//button[contains(text(),'Add') or contains(@class,'add') or contains(@aria-label,'Add')]"
            )
            add_btn.click()
            time.sleep(2)
            logger.info(f"Added product index {product_index} to cart")
            return True

        except Exception as e:
            logger.error(f"Failed to add to cart: {e}")
            return False

    def view_cart(self) -> List[dict]:
        """Return current items in the Zepto cart."""
        try:
            cart_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'cart') or contains(@aria-label,'cart')]"))
            )
            cart_btn.click()
            time.sleep(2)

            items = []
            cart_items = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class,'cart-item') or contains(@data-testid,'cart-item')]"
            )
            for item in cart_items:
                try:
                    name = item.find_element(By.XPATH, ".//*[contains(@class,'name') or contains(@class,'title')]").text
                    price = item.find_element(By.XPATH, ".//*[contains(@class,'price') or contains(text(),'₹')]").text
                    items.append({"name": name, "price": price})
                except Exception:
                    pass
            return items

        except Exception as e:
            logger.error(f"Failed to view cart: {e}")
            return []

    def select_address_and_checkout(self) -> Optional[ZeptoOrder]:
        """Proceed to checkout and confirm payment. Address should already be selected."""
        try:
            # Go to checkout
            checkout_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Checkout') or contains(text(),'Proceed') or contains(@class,'checkout')]"))
            )
            checkout_btn.click()
            time.sleep(3)

            # If address selection/confirmation modal appears, just proceed (address already selected upfront)
            # Try clicking any "Confirm" or "Continue" button for address confirmation
            try:
                confirm_addr_btn = self.driver.find_element(
                    By.XPATH,
                    "//button[contains(text(),'Confirm') or contains(text(),'Continue') or contains(text(),'Proceed')]"
                )
                confirm_addr_btn.click()
                time.sleep(2)
                logger.info("Confirmed pre-selected address at checkout")
            except NoSuchElementException:
                # No confirmation step — address already confirmed
                logger.info("No address confirmation needed — proceeding to payment")

            # Proceed to payment
            pay_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Pay') or contains(text(),'Place Order') or contains(text(),'Confirm')]"))
            )
            pay_btn.click()
            time.sleep(3)

            # Select default saved payment (card/UPI already saved on account)
            try:
                saved_payment = self.driver.find_element(
                    By.XPATH,
                    "(//div[contains(@class,'payment-option') or contains(@class,'saved-card') or contains(@class,'saved-upi')])[1]"
                )
                saved_payment.click()
                time.sleep(1)

                confirm_pay_btn = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Pay') or contains(text(),'Confirm') or contains(text(),'Place')]"))
                )
                confirm_pay_btn.click()
                time.sleep(5)
            except NoSuchElementException:
                logger.warning("Could not find saved payment method — manual intervention needed")
                return None

            # Capture order confirmation
            try:
                order_id_el = self.wait.until(
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Order') and (contains(text(),'#') or contains(text(),'placed'))]"))
                )
                order_text = order_id_el.text
                logger.info(f"Order placed: {order_text}")
                return ZeptoOrder(
                    order_id=order_text,
                    status="placed",
                    total=0.0,
                    estimated_delivery="10 minutes",
                )
            except TimeoutException:
                logger.warning("Order confirmation element not found")
                return ZeptoOrder(order_id="unknown", status="placed", total=0.0, estimated_delivery="10 minutes")

        except Exception as e:
            logger.error(f"Checkout failed: {e}")
            return None

    def take_screenshot(self, path: str = "/tmp/zepto_screenshot.png") -> str:
        """Capture current browser state for confirmation."""
        if self.driver:
            self.driver.save_screenshot(path)
        return path
