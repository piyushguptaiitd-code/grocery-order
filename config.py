import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_GROUP_ID = int(os.getenv('TELEGRAM_GROUP_ID', 0))

# Zepto Account Credentials (encrypted in database)
ZEPTO_PHONE = os.getenv('ZEPTO_PHONE', '')
ZEPTO_EMAIL = os.getenv('ZEPTO_EMAIL')
ZEPTO_PASSWORD = os.getenv('ZEPTO_PASSWORD')
ZEPTO_PIN = os.getenv('ZEPTO_PIN', '')

# Database
DATABASE_PATH = os.getenv('DATABASE_PATH', 'grocery_bot.db')

# Cart Configuration
CART_INACTIVITY_TIMEOUT = 60  # seconds
ZEPTO_SEARCH_LIMIT = 5  # Show top 5 results

# Selenium Configuration
SELENIUM_HEADLESS = os.getenv('SELENIUM_HEADLESS', 'True').lower() == 'true'
SELENIUM_TIMEOUT = 30  # seconds

# Security
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')  # 32-byte base64 encoded key for credential encryption
