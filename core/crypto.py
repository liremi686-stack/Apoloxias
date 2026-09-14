import os
import base64
import hashlib
import secrets
import json
from pathlib import Path
from typing import Optional, Dict, Tuple

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend


class CryptoEngine:
    """Улучшенное шифрование с PBKDF2 + AES-GCM + Fernet."""

    def __init__(self, master_password: str, salt: bytes = None):
        self._password = master_password.encode("utf-8")
        self._salt = salt or os.urandom(32)
        self._key = self._derive_key()
        self._fernet = Fernet(self._key)
        self._session_key = hashlib.sha3_256(self._key + b"session_v2").digest()
        self._module_keys: Dict[str, bytes] = {}

    def _derive_key(self) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA3_256(),
            length=32,
            salt=self._salt,
            iterations=800000,
            backend=default_backend()
        )
        return base64.urlsafe_b64encode(kdf.derive(self._password))

    def encrypt_data(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt_data(self, ciphertext: bytes) -> bytes:
        return self._fernet.decrypt(ciphertext)

    def encrypt_profile(self, data: dict) -> bytes:
        plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return self.encrypt_data(plaintext)

    def decrypt_profile(self, ciphertext: bytes) -> dict:
        plaintext = self.decrypt_data(ciphertext)
        return json.loads(plaintext.decode("utf-8"))

    def get_module_key(self, module_name: str) -> bytes:
        if module_name not in self._module_keys:
            mk = hashlib.sha3_256(self._key + module_name.encode()).digest()
            self._module_keys[module_name] = mk
        return self._module_keys[module_name]

    def encrypt_module_data(self, module_name: str, data: bytes) -> bytes:
        key = self.get_module_key(module_name)
        aesgcm = AESGCM(key[:32])
        nonce = os.urandom(12)
        return nonce + aesgcm.encrypt(nonce, data, None)

    def decrypt_module_data(self, module_name: str, ciphertext: bytes) -> bytes:
        key = self.get_module_key(module_name)
        aesgcm = AESGCM(key[:32])
        nonce = ciphertext[:12]
        return aesgcm.decrypt(nonce, ciphertext[12:], None)

    def encrypt_session_packet(self, data: bytes) -> bytes:
        aesgcm = AESGCM(self._session_key[:32])
        nonce = os.urandom(12)
        return nonce + aesgcm.encrypt(nonce, data, None)

    def decrypt_session_packet(self, ciphertext: bytes) -> bytes:
        aesgcm = AESGCM(self._session_key[:32])
        nonce = ciphertext[:12]
        return aesgcm.decrypt(nonce, ciphertext[12:], None)

    def hash_password(self, password: str) -> str:
        return hashlib.sha3_256(self._salt + password.encode()).hexdigest()

    def get_salt(self) -> bytes:
        return self._salt

    def get_identity(self) -> bytes:
        return hashlib.sha3_256(self._key + b"identity").digest()[:16]


class SecureStorage:
    """Защищенное хранилище с обфускацией имен файлов."""

    def __init__(self, data_dir: Path, crypto: Optional[CryptoEngine] = None):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._crypto = crypto
        self._index_file = self.data_dir / ".idx"
        self._index: Dict[str, str] = {}
        self._load_index()

    def _obfuscate_name(self, name: str) -> str:
        h = hashlib.sha3_256(name.encode()).hexdigest()[:24]
        return f".{h}.bin"

    def _load_index(self):
        if self._index_file.exists():
            try:
                with open(self._index_file, "r", encoding="utf-8") as f:
                    self._index = json.load(f)
            except Exception:
                self._index = {}

    def _save_index(self):
        with open(self._index_file, "w", encoding="utf-8") as f:
            json.dump(self._index, f)

    def save(self, name: str, data: bytes, encrypt: bool = True):
        obf = self._obfuscate_name(name)
        self._index[obf] = name
        self._save_index()
        filepath = self.data_dir / obf
        if encrypt and self._crypto:
            data = self._crypto.encrypt_data(data)
        with open(filepath, "wb") as f:
            f.write(data)

    def load(self, name: str, decrypt: bool = True) -> Optional[bytes]:
        obf = self._obfuscate_name(name)
        filepath = self.data_dir / obf
        if not filepath.exists():
            return None
        with open(filepath, "rb") as f:
            data = f.read()
        if decrypt and self._crypto:
            try:
                return self._crypto.decrypt_data(data)
            except Exception:
                return None
        return data

    def delete(self, name: str):
        obf = self._obfuscate_name(name)
        filepath = self.data_dir / obf
        if filepath.exists():
            size = filepath.stat().st_size
            with open(filepath, "wb") as f:
                f.write(os.urandom(size))
            filepath.unlink()
        self._index.pop(obf, None)
        self._save_index()

    def exists(self, name: str) -> bool:
        return (self.data_dir / self._obfuscate_name(name)).exists()

    def wipe_all(self):
        for f in self.data_dir.iterdir():
            if f.is_file():
                size = f.stat().st_size
                with open(f, "wb") as fh:
                    fh.write(os.urandom(size))
                f.unlink()
        self._index.clear()
        self._save_index()
