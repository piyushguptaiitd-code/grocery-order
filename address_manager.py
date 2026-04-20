from typing import List, Optional, Dict
from database import Database


class AddressManager:
    def __init__(self, db: Database):
        self.db = db

    def add_address(self, label: str, full_address: str, pin_code: str, is_default: bool = False) -> int:
        return self.db.add_address(label, full_address, pin_code, is_default)

    def get_all(self) -> List[Dict]:
        return self.db.get_all_addresses()

    def get(self, address_id: int) -> Optional[Dict]:
        return self.db.get_address(address_id)

    def has_addresses(self) -> bool:
        return len(self.get_all()) > 0

    def format_address_list(self) -> str:
        addresses = self.get_all()
        if not addresses:
            return "No saved addresses. Use /save_address to add one."
        lines = ["*Saved Addresses:*\n"]
        for addr in addresses:
            default_tag = " ⭐" if addr["is_default"] else ""
            lines.append(f"[{addr['address_id']}] *{addr['label']}*{default_tag}\n    {addr['full_address']} — {addr['pin_code']}")
        return "\n".join(lines)
