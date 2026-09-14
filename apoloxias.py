import sys
import os
from pathlib import Path

# Используем тот же BASE_DIR, что и в config.py
from core.config import BASE_DIR, DATA_DIR, MODULES_DIR
sys.path.insert(0, str(BASE_DIR))

from core.auth import AuthManager
from core.module_manager import ModuleManager
from gui.login_window import LoginWindow
from gui.main_window import MainWindow


def main():
    print("=" * 50)
    print("  APOLOXIAS v2.0 — Secure Modular Platform")
    print("=" * 50)
    print(f"[Init] BASE_DIR: {BASE_DIR}")
    print(f"[Init] MODULES_DIR: {MODULES_DIR}")
    print(f"[Init] DATA_DIR: {DATA_DIR}")

    auth = AuthManager(DATA_DIR)

    def on_auth():
        print(f"[Main] Добро пожаловать, {auth.get_username()}!")
        crypto = auth.get_crypto()
        mm = ModuleManager(MODULES_DIR, None, crypto)
        mw = MainWindow(auth, mm)

        class AppProxy:
            def __init__(self, mw, mm):
                self._mw = mw
                self._mm = mm
            def open_module_tab(self, *a, **kw):
                return self._mw.open_module_tab(*a, **kw)
            def close_module_tab(self, *a, **kw):
                return self._mw.close_module_tab(*a, **kw)
            def show_notification(self, *a, **kw):
                return self._mw.show_notification(*a, **kw)
            def system_log(self, *a, **kw):
                return self._mw.system_log(*a, **kw)
            def get_setting(self, *a, **kw):
                return self._mw.get_setting(*a, **kw)
            def get_session_token(self, *a, **kw):
                return self._mw.get_session_token(*a, **kw)
            def request_permission(self, module_name, perm):
                return self._mw.request_permission(module_name, perm)

        mm._app = AppProxy(mw, mm)
        mw.run()

    login = LoginWindow(auth, on_auth)
    login.run()


if __name__ == "__main__":
    main()