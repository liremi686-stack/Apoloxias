import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass, field


@dataclass
class ModuleManifest:
    name: str
    version: str = "1.0.0"
    author: str = "Unknown"
    description: str = ""
    icon: str = "icon.ico"
    entry_point: str = "main.py"
    requires: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)


class EventBus:
    def __init__(self):
        self._subs: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: str, callback: Callable):
        with self._lock:
            self._subs.setdefault(event_type, []).append(callback)

    def unsubscribe(self, event_type: str, callback: Callable):
        with self._lock:
            if event_type in self._subs and callback in self._subs[event_type]:
                self._subs[event_type].remove(callback)

    def publish(self, event_type: str, data: Any = None, source: str = None):
        with self._lock:
            cbs = self._subs.get(event_type, []).copy()
        for cb in cbs:
            try:
                cb(data, source)
            except Exception as e:
                print(f"[EventBus] {event_type} error: {e}")


class ModuleAPI:
    """API для модулей — все данные шифруются прозрачно."""

    def __init__(self, module_name: str, module_path: Path, event_bus: EventBus,
                 app_reference, crypto_engine):
        self._name = module_name
        self._path = module_path
        self._event_bus = event_bus
        self._app = app_reference
        self._crypto = crypto_engine
        self._res_path = module_path / "resources"
        self._data_path = module_path / "module_data"
        self._data_path.mkdir(exist_ok=True)
        self._secure_file = self._data_path / ".vault"
        self._vault: Dict[str, bytes] = {}
        self._load_vault()

    def _load_vault(self):
        if self._secure_file.exists():
            try:
                with open(self._secure_file, "rb") as f:
                    encrypted = f.read()
                raw = self._crypto.decrypt_module_data(self._name, encrypted)
                self._vault = json.loads(raw.decode("utf-8"))
            except Exception:
                self._vault = {}

    def _save_vault(self):
        raw = json.dumps(self._vault, ensure_ascii=False).encode("utf-8")
        encrypted = self._crypto.encrypt_module_data(self._name, raw)
        with open(self._secure_file, "wb") as f:
            f.write(encrypted)

    # ─── Data ───
    def vault_get(self, key: str, default=None) -> Any:
        val = self._vault.get(key)
        if val is None:
            return default
        return json.loads(val)

    def vault_set(self, key: str, value: Any):
        self._vault[key] = json.dumps(value, ensure_ascii=False)
        self._save_vault()

    def vault_delete(self, key: str):
        self._vault.pop(key, None)
        self._save_vault()

    # ─── Paths ───
    def get_module_name(self) -> str:
        return self._name

    def get_module_path(self) -> Path:
        return self._path

    def get_resources_path(self) -> Path:
        return self._res_path

    def get_data_path(self) -> Path:
        return self._data_path

    # ─── Events ───
    def subscribe(self, event_type: str, callback: Callable):
        self._event_bus.subscribe(event_type, callback)

    def unsubscribe(self, event_type: str, callback: Callable):
        self._event_bus.unsubscribe(event_type, callback)

    def publish(self, event_type: str, data: Any = None):
        self._event_bus.publish(event_type, data, source=self._name)

    # ─── Crypto helpers ───
    def encrypt_data(self, data: bytes) -> bytes:
        return self._crypto.encrypt_module_data(self._name, data)

    def decrypt_data(self, ciphertext: bytes) -> bytes:
        return self._crypto.decrypt_module_data(self._name, ciphertext)

    def hash_data(self, data: bytes) -> str:
        import hashlib
        return hashlib.sha3_256(data).hexdigest()

    # ─── UI / App ───
    def open_in_main(self, title: str, frame_class, **kwargs):
        """Открыть модуль во вкладке главного окна."""
        return self._app.open_module_tab(self._name, title, frame_class, **kwargs)

    def close_tab(self, tab_id: str):
        self._app.close_module_tab(tab_id)

    def show_notification(self, title: str, message: str, msg_type: str = "info"):
        self._app.show_notification(title, message, msg_type)

    def request_permission(self, permission: str) -> bool:
        return self._app.request_permission(self._name, permission)

    def log(self, message: str, level: str = "INFO"):
        self._app.system_log(f"[{self._name}] {message}", level)

    def get_setting(self, key: str, default=None):
        return self._app.get_setting(key, default)

    def get_session_token(self) -> str:
        return self._app.get_session_token()


class ModuleInterface:
    NAME = "BaseModule"
    VERSION = "1.0.0"
    DESCRIPTION = ""
    ICON = "icon.ico"

    def __init__(self, api: ModuleAPI):
        self.api = api
        self._running = False

    def on_load(self):
        pass

    def on_unload(self):
        pass

    def on_activate(self):
        pass

    def on_deactivate(self):
        pass

    def get_info(self) -> Dict:
        return {
            "name": self.NAME,
            "version": self.VERSION,
            "description": self.DESCRIPTION,
            "icon": self.ICON,
        }
