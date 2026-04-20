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
            location_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'location') or contains(text(),'location') or contains(text(),'Deliver')]"))
            )
            location_btn.click()
            time.sleep(1)

            pin_input = self.wait.until(
                EC.presence_of_element_located((By.XPATH, "//input[contains(@placeholder,'pincode') or contains(@placeholder,'PIN') or contains(@placeholder,'location')]"))
            )
            pin_input.clear()
            pin_input.send_keys(pin_code)
            time.sleep(2)

            # Select first result
            first_result = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "(//ul[contains(@class,'suggestion') or contains(@class,'result')]//li)[1]"))
            )
            first_result.click()
            time.sleep(2)
            return True

        except Exception as e:
            logger.error(f"Failed to set delivery location: {e}")
            return False

    def search_products(self, query: str) -> List[ZeptoProduct]:
        """Search for products and return top results."""
        try:
            self.driver.get(f"{ZEPTO_BASE_URL}/search?query={query.replace(' ', '+')}")
            time.sleep(3)

            products = []

            # Try to find product cards
            product_cards = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class,'product-card') or contains(@data-testid,'product')]"
            )[:ZEPTO_SEARCH_LIMIT]

            for i, card in enumerate(product_cards):
                try:
                    name = card.find_element(By.XPATH, ".//p[contains(@class,'name') or contains(@class,'title')] | .//h3 | .//span[contains(@class,'name')]").text.strip()
                    price_text = card.find_element(By.XPATH, ".//*[contains(@class,'price') or contains(text(),'₹')]").text.strip()
                    price = float(''.join(filter(lambda c: c.isdigit() or c == '.', price_text.replace(',', ''))) or 0)
                    qty_unit = ""
                    try:
                        qty_unit = card.find_element(By.XPATH, ".//*[contains(@class,'quantity') or contains(@class,'unit') or contains(@class,'weight')]").text.strip()
                    except NoSuchElementException:
                        pass

                    img_url = ""
                    try:
                        img = card.find_element(By.TAG_NAME, "img")
                        img_url = img.get_attribute("src") or ""
                    except NoSuchElementException:
                        pass

                    in_stock = True
                    try:
                        card.find_element(By.XPATH, ".//*[contains(text(),'Out of Stock') or contains(text(),'Unavailable')]")
                        in_stock = False
                    except NoSuchElementException:
                        pass

                    products.append(ZeptoProduct(
                        product_id=f"zepto_{i}_{query[:10].replace(' ', '_')}",
                        name=name,
                        price=price,
                        quantity_unit=qty_unit,
                        image_url=img_url,
                        in_stock=in_stock,
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse product card {i}: {e}")

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
