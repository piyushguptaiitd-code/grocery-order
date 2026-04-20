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

from config import SELENIUM_HEADLESS, SELENIUM_TIMEOUT, ZEPTO_SEARCH_LIMIT

logger = logging.getLogger(__name__)

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

    def login(self, phone: str, otp_callback) -> bool:
        """Login to Zepto via phone + OTP. otp_callback(phone) must return OTP string."""
        try:
            self.start()
            self.driver.get(ZEPTO_BASE_URL)
            time.sleep(2)

            # Click login/profile button
            login_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'login') or contains(text(),'Login') or contains(text(),'Sign')]"))
            )
            login_btn.click()
            time.sleep(1)

            # Enter phone number
            phone_input = self.wait.until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='tel' or @placeholder='Mobile number' or contains(@placeholder,'phone')]"))
            )
            phone_input.clear()
            phone_input.send_keys(phone)
            time.sleep(0.5)

            # Submit phone
            phone_input.send_keys(Keys.RETURN)
            time.sleep(2)

            # Wait for OTP from user via callback
            otp = otp_callback(phone)
            if not otp:
                logger.error("No OTP received")
                return False

            # Enter OTP
            otp_inputs = self.driver.find_elements(By.XPATH, "//input[@type='text' or @type='number' or @maxlength='1']")
            if len(otp_inputs) >= 4:
                for i, digit in enumerate(otp[:len(otp_inputs)]):
                    otp_inputs[i].send_keys(digit)
            else:
                otp_field = otp_inputs[0] if otp_inputs else self.driver.find_element(By.XPATH, "//input[@type='text']")
                otp_field.clear()
                otp_field.send_keys(otp)

            time.sleep(3)
            self.is_logged_in = True
            logger.info("Logged in to Zepto successfully")
            return True

        except Exception as e:
            logger.error(f"Login failed: {e}")
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

    def select_address_and_checkout(self, address_label: str, full_address: str, pin_code: str) -> Optional[ZeptoOrder]:
        """Proceed to checkout, select/set address, and confirm payment."""
        try:
            # Go to checkout
            checkout_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Checkout') or contains(text(),'Proceed') or contains(@class,'checkout')]"))
            )
            checkout_btn.click()
            time.sleep(3)

            # Try selecting saved address matching label or pin
            try:
                saved_addr = self.driver.find_element(
                    By.XPATH,
                    f"//div[contains(text(),'{address_label}') or contains(text(),'{pin_code}')]"
                )
                saved_addr.click()
                logger.info(f"Selected saved address: {address_label}")
            except NoSuchElementException:
                # Add new address
                add_addr_btn = self.driver.find_element(
                    By.XPATH,
                    "//button[contains(text(),'Add') and contains(text(),'address') or contains(@class,'add-address')]"
                )
                add_addr_btn.click()
                time.sleep(1)

                addr_input = self.wait.until(
                    EC.presence_of_element_located((By.XPATH, "//input[contains(@placeholder,'address') or contains(@placeholder,'street')]"))
                )
                addr_input.send_keys(full_address)
                time.sleep(1)

                pin_input = self.driver.find_element(By.XPATH, "//input[contains(@placeholder,'PIN') or contains(@placeholder,'pincode')]")
                pin_input.clear()
                pin_input.send_keys(pin_code)
                time.sleep(2)

                save_btn = self.driver.find_element(By.XPATH, "//button[contains(text(),'Save') or contains(text(),'Confirm')]")
                save_btn.click()
                time.sleep(2)

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
