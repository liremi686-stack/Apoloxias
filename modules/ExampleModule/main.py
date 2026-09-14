import tkinter as tk
from core.module_api import ModuleInterface

C = {
    "bg": "#0d0d12", "card": "#15151f", "hover": "#1c1c2b",
    "accent": "#5c7cfa", "text": "#e9ecef", "muted": "#868e96",
    "success": "#51cf66", "border": "#2c2c3a",
}


class ExampleModule(ModuleInterface):
    NAME = "ExampleModule"
    VERSION = "2.0.0"
    DESCRIPTION = "Демонстрация API и шифрованного хранилища"
    ICON = "icon.ico"

    def __init__(self, api):
        super().__init__(api)
        self._frame = None

    def on_load(self):
        self.api.log("ExampleModule загружен", "INFO")
        self.api.subscribe("chat.message", self._on_chat)

    def on_unload(self):
        self.api.log("ExampleModule выгружен", "INFO")

    def on_activate(self):
        self.api.open_in_main("Example Module", ExampleFrame)

    def on_deactivate(self):
        pass

    def _on_chat(self, data, source):
        self.api.log(f"Сообщение от {source}: {data}", "INFO")


class ExampleFrame(tk.Frame):
    def __init__(self, parent, api):
        super().__init__(parent, bg=C["bg"])
        self.api = api
        self._build_ui()
        self._load_state()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["card"], height=60)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="◈ Example Module", font=("Segoe UI", 16, "bold"),
                 bg=C["card"], fg=C["accent"]).pack(side=tk.LEFT, padx=20, pady=12)

        # Content
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)

        # Info card
        card = tk.Frame(body, bg=C["card"], padx=20, pady=20)
        card.pack(fill=tk.X, pady=(0, 15))
        tk.Label(card, text="Информация", font=("Segoe UI", 12, "bold"),
                 bg=C["card"], fg=C["text"], anchor="w").pack(fill=tk.X)
        for label, val in [
            ("Имя модуля", self.api.get_module_name()),
            ("Путь", str(self.api.get_module_path())),
            ("Ресурсы", str(self.api.get_resources_path())),
        ]:
            row = tk.Frame(card, bg=C["card"])
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=f"{label}:", font=("Segoe UI", 9), bg=C["card"],
                     fg=C["muted"], width=12, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=val, font=("Segoe UI", 9), bg=C["card"],
                     fg=C["text"], anchor="w").pack(side=tk.LEFT)

        # Actions
        actions = tk.Frame(body, bg=C["bg"])
        actions.pack(fill=tk.X, pady=10)

        def mk_btn(text, cmd, primary=False):
            bg = C["accent"] if primary else C["card"]
            fg = "white" if primary else C["text"]
            return tk.Button(actions, text=text, font=("Segoe UI", 10), bg=bg, fg=fg,
                             activebackground=C["hover"], bd=0, padx=18, pady=8,
                             cursor="hand2", command=cmd)

        mk_btn("Отправить событие", self._send_event, True).pack(side=tk.LEFT, padx=(0, 8))
        mk_btn("Тест шифрования", self._test_crypto).pack(side=tk.LEFT, padx=(0, 8))
        mk_btn("Уведомление", self._show_notif).pack(side=tk.LEFT)

        # Vault demo
        vault = tk.LabelFrame(body, text="Шифрованное хранилище (Vault)", bg=C["bg"],
                              fg=C["muted"], font=("Segoe UI", 10), bd=1, relief=tk.FLAT)
        vault.pack(fill=tk.X, pady=15)
        vault_in = tk.Frame(vault, bg=C["bg"])
        vault_in.pack(fill=tk.X, padx=15, pady=10)

        self.vault_entry = tk.Entry(vault_in, font=("Segoe UI", 11), bg=C["card"],
                                    fg=C["text"], insertbackground=C["accent"], bd=0,
                                    highlightthickness=1, highlightcolor=C["accent"],
                                    highlightbackground=C["border"])
        self.vault_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.vault_entry.bind("<Return>", lambda e: self._vault_save())

        tk.Button(vault_in, text="Сохранить", font=("Segoe UI", 9), bg=C["accent"],
                  fg="white", activebackground=C["hover"], bd=0, padx=15, pady=6,
                  cursor="hand2", command=self._vault_save).pack(side=tk.RIGHT, padx=(10, 0))

        self.vault_lbl = tk.Label(vault, text="Значение: —", font=("Segoe UI", 9),
                                  bg=C["bg"], fg=C["text"], anchor="w")
        self.vault_lbl.pack(fill=tk.X, padx=15, pady=(0, 10))

        # Log
        self.log_txt = tk.Text(body, font=("Consolas", 9), bg="#0a0a0f", fg=C["success"],
                               height=8, state=tk.DISABLED, bd=0, padx=10, pady=10)
        self.log_txt.pack(fill=tk.BOTH, expand=True)
        self._log("Модуль инициализирован")

    def _load_state(self):
        val = self.api.vault_get("demo_value", "")
        self.vault_lbl.config(text=f"Значение: {val}")
        self.vault_entry.insert(0, val)

    def _vault_save(self):
        val = self.vault_entry.get().strip()
        self.api.vault_set("demo_value", val)
        self.vault_lbl.config(text=f"Значение: {val}")
        self._log(f"Vault сохранен: {val}")

    def _send_event(self):
        self.api.publish("example.event", {"msg": "Hello!"})
        self._log("Событие отправлено")

    def _test_crypto(self):
        data = b"Secret data 12345"
        enc = self.api.encrypt_data(data)
        dec = self.api.decrypt_data(enc)
        ok = data == dec
        self._log(f"Crypto test: {'✓ OK' if ok else '✗ FAIL'} ({len(enc)} bytes)")

    def _show_notif(self):
        self.api.show_notification("Example", "Тестовое уведомление", "info")
        self._log("Уведомление отправлено")

    def _log(self, msg):
        self.log_txt.configure(state=tk.NORMAL)
        self.log_txt.insert(tk.END, f"> {msg}\n")
        self.log_txt.see(tk.END)
        self.log_txt.configure(state=tk.DISABLED)
