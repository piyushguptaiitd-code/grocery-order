import sqlite3
from datetime import datetime
from config import DATABASE_PATH
from typing import List, Dict, Optional

class Database:
    def __init__(self):
        self.db_path = DATABASE_PATH
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_connection()
        cursor = conn.cursor()

        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE NOT NULL,
                name TEXT NOT NULL,
                phone TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Addresses table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS addresses (
                address_id INTEGER PRIMARY KEY,
                label TEXT NOT NULL,
                full_address TEXT NOT NULL,
                pin_code TEXT NOT NULL,
                is_default BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Zepto credentials (encrypted)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS zepto_credentials (
                id INTEGER PRIMARY KEY,
                email_encrypted TEXT UNIQUE NOT NULL,
                password_encrypted TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Cart items
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cart_items (
                item_id INTEGER PRIMARY KEY,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER DEFAULT 1,
                added_by_user_id INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (added_by_user_id) REFERENCES users(user_id)
            )
        ''')

        # Orders
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY,
                items_json TEXT NOT NULL,
                delivery_address_id INTEGER NOT NULL,
                total REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                zepto_order_id TEXT,
                payment_method_used TEXT,
                FOREIGN KEY (delivery_address_id) REFERENCES addresses(address_id)
            )
        ''')

        conn.commit()
        conn.close()

    # User operations
    def add_user(self, telegram_id: int, name: str, phone: str = None) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO users (telegram_id, name, phone) VALUES (?, ?, ?)',
                      (telegram_id, name, phone))
        conn.commit()
        cursor.execute('SELECT user_id FROM users WHERE telegram_id = ?', (telegram_id,))
        result = cursor.fetchone()
        conn.close()
        return result['user_id'] if result else None

    def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None

    # Address operations
    def add_address(self, label: str, full_address: str, pin_code: str, is_default: bool = False) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        if is_default:
            cursor.execute('UPDATE addresses SET is_default = 0')
        cursor.execute('INSERT INTO addresses (label, full_address, pin_code, is_default) VALUES (?, ?, ?, ?)',
                      (label, full_address, pin_code, is_default))
        conn.commit()
        address_id = cursor.lastrowid
        conn.close()
        return address_id

    def get_all_addresses(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM addresses ORDER BY is_default DESC, address_id DESC')
        results = cursor.fetchall()
        conn.close()
        return [dict(row) for row in results]

    def get_address(self, address_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM addresses WHERE address_id = ?', (address_id,))
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None

    # Cart operations
    def add_to_cart(self, product_id: str, product_name: str, price: float, user_id: int, quantity: int = 1):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO cart_items (product_id, product_name, price, quantity, added_by_user_id) VALUES (?, ?, ?, ?, ?)',
                      (product_id, product_name, price, quantity, user_id))
        conn.commit()
        conn.close()

    def get_cart_items(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ci.*, u.name FROM cart_items ci
            LEFT JOIN users u ON ci.added_by_user_id = u.user_id
            ORDER BY ci.added_at
        ''')
        results = cursor.fetchall()
        conn.close()
        return [dict(row) for row in results]

    def clear_cart(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM cart_items')
        conn.commit()
        conn.close()

    def get_cart_total(self) -> float:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT SUM(price * quantity) as total FROM cart_items')
        result = cursor.fetchone()
        conn.close()
        return result['total'] or 0.0

    # Order operations
    def create_order(self, items_json: str, address_id: int, total: float, zepto_order_id: str = None, payment_method: str = None) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO orders (items_json, delivery_address_id, total, zepto_order_id, payment_method_used, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (items_json, address_id, total, zepto_order_id, payment_method, 'placed'))
        conn.commit()
        order_id = cursor.lastrowid
        conn.close()
        return order_id

    def get_recent_orders(self, limit: int = 5) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT o.*, a.label as address_label FROM orders o
            LEFT JOIN addresses a ON o.delivery_address_id = a.address_id
            ORDER BY o.placed_at DESC LIMIT ?
        ''', (limit,))
        results = cursor.fetchall()
        conn.close()
        return [dict(row) for row in results]
