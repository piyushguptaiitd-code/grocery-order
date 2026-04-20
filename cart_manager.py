import asyncio
import logging
from typing import List, Optional, Callable

from database import Database

logger = logging.getLogger(__name__)

INACTIVITY_TIMEOUT = 60  # seconds


def parse_items(text: str) -> List[str]:
    """Parse user message into list of items. Handles comma/and/newline separated lists."""
    text = text.strip()
    # Remove common prefixes
    for prefix in ("add ", "order ", "buy ", "get "):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break

    # Split by comma, "and", or newline
    import re
    parts = re.split(r',|\band\b|\n', text, flags=re.IGNORECASE)
    items = [p.strip() for p in parts if p.strip()]
    return items


class CartManager:
    def __init__(self, db: Database, on_timeout: Callable):
        self.db = db
        self.on_timeout = on_timeout  # async callback when cart goes inactive
        self._timeout_task: Optional[asyncio.Task] = None

    def _reset_timer(self):
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = asyncio.create_task(self._inactivity_timer())

    async def _inactivity_timer(self):
        try:
            await asyncio.sleep(INACTIVITY_TIMEOUT)
            logger.info("Cart inactivity timeout reached — triggering address confirmation")
            await self.on_timeout()
        except asyncio.CancelledError:
            pass

    def cancel_timer(self):
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
            self._timeout_task = None

    def add_item(self, product_id: str, product_name: str, price: float, user_id: int, quantity: int = 1):
        self.db.add_to_cart(product_id, product_name, price, user_id, quantity)
        self._reset_timer()
        logger.info(f"Added '{product_name}' to cart — timer reset")

    def get_items(self):
        return self.db.get_cart_items()

    def get_total(self) -> float:
        return self.db.get_cart_total()

    def clear(self):
        self.cancel_timer()
        self.db.clear_cart()
        logger.info("Cart cleared")

    def is_empty(self) -> bool:
        return len(self.get_items()) == 0

    def format_cart(self) -> str:
        items = self.get_items()
        if not items:
            return "Cart is empty."
        lines = ["*Current Cart:*\n"]
        total = 0.0
        for i, item in enumerate(items, 1):
            added_by = item.get("name") or "Someone"
            lines.append(f"{i}. {item['product_name']} — ₹{item['price']:.0f} x{item['quantity']} (by {added_by})")
            total += item['price'] * item['quantity']
        lines.append(f"\n*Total: ₹{total:.0f}*")
        return "\n".join(lines)
