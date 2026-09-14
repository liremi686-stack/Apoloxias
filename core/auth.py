import secrets
import time
from pathlib import Path
from typing import Optional, Dict
from core.crypto import CryptoEngine, SecureStorage


class AuthManager:
    """Улучшенный менеджер аутентификации с rate limiting и secure memory."""

    def __init__(self, data_dir: Path):
        self._storage = SecureStorage(data_dir)
        self._crypto: Optional[CryptoEngine] = None
        self._profile: Optional[Dict] = None
        self._authenticated = False
        self._failed_attempts = 0
        self._lockout_until = 0
        self._session_start = 0

    def is_first_run(self) -> bool:
        return not self._storage.exists("profile")

    def register(self, username: str, password: str) -> bool:
        if not self.is_first_run():
            return False
        if len(password) < 8:
            return False
        salt = secrets.token_bytes(32)
        crypto = CryptoEngine(password, salt)
        profile = {
            "username": username,
            "password_hash": crypto.hash_password(password),
            "settings": {
                "theme": "dark",
                "language": "ru",
                "auto_lock": 300,
                "network_encryption": True,
                "stealth_mode": True,
            },
            "modules_enabled": [],
            "session_token": secrets.token_hex(32),
            "created_at": time.time(),
        }
        encrypted = crypto.encrypt_profile(profile)
        self._storage.save("profile", encrypted, encrypt=False)
        self._storage.save("salt", salt, encrypt=False)
        self._crypto = crypto
        self._profile = profile
        self._authenticated = True
        self._session_start = time.time()
        return True

    def authenticate(self, username: str, password: str) -> bool:
        if time.time() < self._lockout_until:
            return False
        if self.is_first_run():
            return False
        encrypted_data = self._storage.load("profile", decrypt=False)
        salt = self._storage.load("salt", decrypt=False)
        if not encrypted_data or not salt:
            return False
        try:
            crypto = CryptoEngine(password, salt)
            profile = crypto.decrypt_profile(encrypted_data)
            if profile.get("username") != username:
                raise ValueError("username mismatch")
            if profile.get("password_hash") != crypto.hash_password(password):
                raise ValueError("password mismatch")
            self._crypto = crypto
            self._profile = profile
            self._authenticated = True
            self._failed_attempts = 0
            self._session_start = time.time()
            profile["session_token"] = secrets.token_hex(32)
            profile["last_login"] = time.time()
            self._save_profile()
            return True
        except Exception:
            self._failed_attempts += 1
            if self._failed_attempts >= 5:
                self._lockout_until = time.time() + 300
            return False

    def change_password(self, old_password: str, new_password: str) -> bool:
        if not self._authenticated:
            return False
        try:
            old_crypto = CryptoEngine(old_password, self._crypto.get_salt())
            encrypted_data = self._storage.load("profile", decrypt=False)
            profile = old_crypto.decrypt_profile(encrypted_data)
            if profile.get("password_hash") != old_crypto.hash_password(old_password):
                return False
            new_salt = secrets.token_bytes(32)
            new_crypto = CryptoEngine(new_password, new_salt)
            profile["password_hash"] = new_crypto.hash_password(new_password)
            encrypted = new_crypto.encrypt_profile(profile)
            self._storage.save("profile", encrypted, encrypt=False)
            self._storage.save("salt", new_salt, encrypt=False)
            self._crypto = new_crypto
            self._profile = profile
            return True
        except Exception:
            return False

    def get_crypto(self) -> Optional[CryptoEngine]:
        return self._crypto

    def get_profile(self) -> Optional[Dict]:
        return self._profile.copy() if self._profile else None

    def get_username(self) -> Optional[str]:
        return self._profile.get("username") if self._profile else None

    def is_authenticated(self) -> bool:
        return self._authenticated

    def logout(self):
        self._crypto = None
        self._profile = None
        self._authenticated = False
        self._failed_attempts = 0
        self._lockout_until = 0

    def _save_profile(self):
        if self._crypto and self._profile:
            encrypted = self._crypto.encrypt_profile(self._profile)
            self._storage.save("profile", encrypted, encrypt=False)

    def update_settings(self, settings: Dict):
        if self._profile:
            self._profile["settings"].update(settings)
            self._save_profile()

    def get_session_token(self) -> Optional[str]:
        return self._profile.get("session_token") if self._profile else None

    def check_session_timeout(self) -> bool:
        if not self._authenticated or not self._profile:
            return False
        timeout = self._profile.get("settings", {}).get("auto_lock", 300)
        return (time.time() - self._session_start) > timeout

    def touch_session(self):
        self._session_start = time.time()
