import base64
from cryptography.fernet import Fernet
from config import ENCRYPTION_KEY

class CredentialManager:
    def __init__(self):
        if not ENCRYPTION_KEY:
            raise ValueError("ENCRYPTION_KEY not set in environment variables")
        self.cipher = Fernet(ENCRYPTION_KEY.encode())

    @staticmethod
    def generate_key():
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: str) -> str:
        return self.cipher.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self.cipher.decrypt(ciphertext.encode()).decode()

    def store_zepto_credentials(self, db, email: str, password: str):
        encrypted_email = self.encrypt(email)
        encrypted_password = self.encrypt(password)

        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM zepto_credentials')
        cursor.execute('''
            INSERT INTO zepto_credentials (email_encrypted, password_encrypted)
            VALUES (?, ?)
        ''', (encrypted_email, encrypted_password))
        conn.commit()
        conn.close()

    def get_zepto_credentials(self, db):
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT email_encrypted, password_encrypted FROM zepto_credentials LIMIT 1')
        result = cursor.fetchone()
        conn.close()

        if not result:
            return None, None

        email = self.decrypt(result['email_encrypted'])
        password = self.decrypt(result['password_encrypted'])
        return email, password
