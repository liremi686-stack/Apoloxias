import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Dict, Optional, List
import time
import datetime


C = {
    "bg": "#0d0d12", "card": "#15151f", "hover": "#1c1c2b",
    "accent": "#5c7cfa", "accent_h": "#748ffc", "success": "#51cf66",
    "warning": "#ffd43b", "error": "#ff6b6b", "text": "#e9ecef",
    "muted": "#868e96", "border": "#2c2c3a", "sidebar": "#111118",
    "warning": "#ffd43b",
}


class MainWindow:
    def __init__(self, auth_manager, module_manager):
        self.auth_manager = auth_manager
        self.module_manager = module_manager
        self.window = tk.Tk()
        self.window.title(f"Apoloxias v2.0 — {auth_manager.get_username()}")
        self.window.geometry("1280x800")
        self.window.configure(bg=C["bg"])
        self.window.minsize(1000, 650)
        self.window.state("zoomed")

        self._tabs: Dict[str, dict] = {}
        self._active_tab: Optional[str] = None
        self._tab_counter = 0
        self._notif_queue: List[dict] = []
        self._notif_active = False

        self._build_ui()
        self._load_modules()
        self._start_session_check()

    def _build_ui(self):
        # Sidebar
        self.sidebar = tk.Frame(self.window, bg=C["sidebar"], width=260)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        # User block
        user = tk.Frame(self.sidebar, bg=C["sidebar"], height=90)
        user.pack(fill=tk.X, padx=15, pady=15)
        user.pack_propagate(False)

        avatar = tk.Label(user, text="◈", font=("Segoe UI", 26), bg=C["sidebar"], fg=C["accent"])
        avatar.pack(side=tk.LEFT, padx=(0, 12))

        info = tk.Frame(user, bg=C["sidebar"])
        info.pack(side=tk.LEFT, fill=tk.Y, expand=True)

        tk.Label(info, text=self.auth_manager.get_username(), font=("Segoe UI", 12, "bold"),
                 bg=C["sidebar"], fg=C["text"], anchor="w").pack(fill=tk.X)
        tk.Label(info, text="● Защищено", font=("Segoe UI", 9), bg=C["sidebar"],
                 fg=C["success"], anchor="w").pack(fill=tk.X)

        # Separator
        tk.Frame(self.sidebar, bg=C["border"], height=1).pack(fill=tk.X, padx=15)

        # Modules label
        tk.Label(self.sidebar, text="МОДУЛИ", font=("Segoe UI", 9, "bold"),
                 bg=C["sidebar"], fg=C["muted"]).pack(anchor="w", padx=15, pady=(15, 8))

        # Modules list
        self.modules_canvas = tk.Canvas(self.sidebar, bg=C["sidebar"], highlightthickness=0)
        sb = ttk.Scrollbar(self.sidebar, orient="vertical", command=self.modules_canvas.yview)
        self.modules_container = tk.Frame(self.modules_canvas, bg=C["sidebar"])

        self.modules_container.bind("<Configure>",
            lambda e: self.modules_canvas.configure(scrollregion=self.modules_canvas.bbox("all")))
        self.modules_canvas.create_window((0, 0), window=self.modules_container, anchor="nw", width=240)
        self.modules_canvas.configure(yscrollcommand=sb.set)
        self.modules_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 5))

        # Bottom buttons
        bot = tk.Frame(self.sidebar, bg=C["sidebar"], height=80)
        bot.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=15)
        bot.pack_propagate(False)

        tk.Button(bot, text="🔒  Выйти", font=("Segoe UI", 10), bg=C["card"], fg=C["text"],
                  activebackground=C["hover"], activeforeground="white", bd=0,
                  padx=15, pady=6, cursor="hand2", command=self._logout).pack(fill=tk.X, pady=(0, 6))
        tk.Button(bot, text="🔐  Заблокировать", font=("Segoe UI", 10), bg=C["card"],
                  fg=C["text"], activebackground=C["hover"], activeforeground="white",
                  bd=0, padx=15, pady=6, cursor="hand2", command=self._lock).pack(fill=tk.X)

        # Content area
        self.content = tk.Frame(self.window, bg=C["bg"])
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top bar
        self.top_bar = tk.Frame(self.content, bg=C["card"], height=50)
        self.top_bar.pack(fill=tk.X, side=tk.TOP)
        self.top_bar.pack_propagate(False)

        self.welcome = tk.Label(self.top_bar, text="Добро пожаловать", font=("Segoe UI", 13, "bold"),
                                bg=C["card"], fg=C["text"])
        self.welcome.pack(side=tk.LEFT, padx=20, pady=10)

        self.notif_btn = tk.Label(self.top_bar, text="🔔", font=("Segoe UI", 14),
                                  bg=C["card"], fg=C["muted"], cursor="hand2")
        self.notif_btn.pack(side=tk.RIGHT, padx=20, pady=10)

        # Tab bar
        self.tab_bar = tk.Frame(self.content, bg=C["bg"], height=36)
        self.tab_bar.pack(fill=tk.X, side=tk.TOP)
        self.tab_bar.pack_propagate(False)

        self.tab_inner = tk.Frame(self.tab_bar, bg=C["bg"])
        self.tab_inner.pack(side=tk.LEFT, fill=tk.Y)

        # Tab content
        self.tab_content = tk.Frame(self.content, bg=C["bg"])
        self.tab_content.pack(fill=tk.BOTH, expand=True)

        # Placeholder
        self.placeholder = tk.Frame(self.tab_content, bg=C["bg"])
        self.placeholder.pack(fill=tk.BOTH, expand=True)

        tk.Label(self.placeholder, text="◈", font=("Segoe UI", 80), bg=C["bg"], fg="#1a1a25").place(relx=0.5, rely=0.4, anchor="center")
        tk.Label(self.placeholder, text="Выберите модуль из боковой панели", font=("Segoe UI", 12),
                 bg=C["bg"], fg=C["muted"]).place(relx=0.5, rely=0.55, anchor="center")

        # Notification bar
        self.notif_bar = tk.Frame(self.content, bg=C["accent"], height=0)
        self.notif_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.notif_bar.pack_propagate(False)

    def _load_modules(self):
        manifests = self.module_manager.discover_modules()
        for m in manifests:
            self._create_module_btn(m)

    def _create_module_btn(self, manifest):
        btn = tk.Frame(self.modules_container, bg=C["card"], height=56, cursor="hand2")
        btn.pack(fill=tk.X, pady=2, padx=5)
        btn.pack_propagate(False)

        name = tk.Label(btn, text=manifest.name, font=("Segoe UI", 10, "bold"),
                        bg=C["card"], fg=C["text"], anchor="w", cursor="hand2")
        name.pack(fill=tk.X, padx=12, pady=(7, 0))

        desc = tk.Label(btn, text=manifest.description or "Модуль", font=("Segoe UI", 8),
                        bg=C["card"], fg=C["muted"], anchor="w", cursor="hand2")
        desc.pack(fill=tk.X, padx=12, pady=(0, 5))

        def on_enter(e, b=btn, n=name, d=desc):
            b.config(bg=C["hover"]); n.config(bg=C["hover"]); d.config(bg=C["hover"])
        def on_leave(e, b=btn, n=name, d=desc):
            b.config(bg=C["card"]); n.config(bg=C["card"]); d.config(bg=C["card"])

        for w in [btn, name, desc]:
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", lambda e, m=manifest.name: self._activate_module(m))

    def _activate_module(self, module_name: str, title=None, frame_class=None, **kwargs):
        """Загружает модуль и открывает его UI."""
        if module_name not in self.module_manager.get_loaded_modules():
            if not self.module_manager.load_module(module_name):
                messagebox.showerror("Ошибка", f"Не удалось загрузить '{module_name}'")
                return
        module = self.module_manager.get_module(module_name)
        if not module:
            return

        # Если таб уже открыт — просто переключаемся
        for tid, info in self._tabs.items():
            if info["module"] == module_name:
                self._switch_tab(tid)
                return

        if frame_class:
            # Прямой вызов с UI-классом
            self._open_module_tab(module_name, module, title=title, frame_class=frame_class, **kwargs)
        else:
            # Клик на кнопку — даём модулю самому решать
            try:
                module.on_activate()
            except Exception as e:
                print(f"[MainWindow] Activate error '{module_name}': {e}")
                import traceback
                traceback.print_exc()

    def _open_module_tab(self, module_name: str, module, title=None, frame_class=None, **kwargs):
        self._tab_counter += 1
        tab_id = f"tab_{self._tab_counter}"

        # Hide placeholder
        self.placeholder.pack_forget()

        # Create tab button
        tbtn = tk.Frame(self.tab_inner, bg=C["hover"], padx=12, pady=4, cursor="hand2")
        tbtn.pack(side=tk.LEFT, padx=(0, 2))

        display_title = title or module_name
        tlbl = tk.Label(tbtn, text=display_title, font=("Segoe UI", 9, "bold"),
                        bg=C["hover"], fg=C["text"], cursor="hand2")
        tlbl.pack(side=tk.LEFT)

        tclose = tk.Label(tbtn, text="✕", font=("Segoe UI", 9, "bold"),
                          bg=C["hover"], fg=C["muted"], cursor="hand2")
        tclose.pack(side=tk.LEFT, padx=(8, 0))

        # Create content frame
        frame = tk.Frame(self.tab_content, bg=C["bg"])
        frame.pack(fill=tk.BOTH, expand=True)

        self._tabs[tab_id] = {
            "frame": frame,
            "module": module_name,
            "btn": tbtn,
            "lbl": tlbl,
            "close": tclose,
            "module_obj": module,
        }

        # Создаём UI-фрейм модуля
        if frame_class:
            api = self.module_manager._apis.get(module_name)
            try:
                content = frame_class(frame, api, **kwargs)
                content.pack(fill=tk.BOTH, expand=True)
                self._tabs[tab_id]["content"] = content
            except Exception as e:
                print(f"[MainWindow] Ошибка создания UI для {module_name}: {e}")
                import traceback
                traceback.print_exc()
                messagebox.showerror("Ошибка модуля", f"Не удалось создать интерфейс '{module_name}':\n{e}")
                self._close_tab(tab_id)
                return

        def switch(e, tid=tab_id):
            self._switch_tab(tid)
        def close(e, tid=tab_id):
            self._close_tab(tid)

        tbtn.bind("<Button-1>", switch)
        tlbl.bind("<Button-1>", switch)
        tclose.bind("<Button-1>", close)

        # Переключаемся на новый таб
        self._switch_tab(tab_id)

    def _switch_tab(self, tab_id: str):
        if tab_id == self._active_tab:
            return
        if tab_id not in self._tabs:
            return

        # Deactivate current
        if self._active_tab and self._active_tab in self._tabs:
            old = self._tabs[self._active_tab]
            old["btn"].config(bg=C["card"])
            old["lbl"].config(bg=C["card"])
            old["close"].config(bg=C["card"])
            old["frame"].pack_forget()

        # Activate new
        info = self._tabs[tab_id]
        info["btn"].config(bg=C["hover"])
        info["lbl"].config(bg=C["hover"])
        info["close"].config(bg=C["hover"])
        info["frame"].pack(fill=tk.BOTH, expand=True)
        self._active_tab = tab_id
        self.welcome.config(text=f"Модуль: {info['module']}")

    def _close_tab(self, tab_id: str):
        if tab_id not in self._tabs:
            return
        info = self._tabs[tab_id]
        try:
            info["module_obj"].on_deactivate()
        except Exception:
            pass
        info["btn"].destroy()
        info["frame"].destroy()
        del self._tabs[tab_id]

        if self._active_tab == tab_id:
            self._active_tab = None
            if self._tabs:
                self._switch_tab(list(self._tabs.keys())[-1])
            else:
                self.placeholder.pack(fill=tk.BOTH, expand=True)
                self.welcome.config(text="Добро пожаловать")

    # ─── Public API for modules ───
    def open_module_tab(self, module_name: str, title: str, frame_class=None, **kwargs):
        """Called by modules to open their UI in a tab."""
        for tid, info in self._tabs.items():
            if info["module"] == module_name:
                self._switch_tab(tid)
                return tid
        self._activate_module(module_name, title=title, frame_class=frame_class, **kwargs)
        return self._active_tab

    def close_module_tab(self, tab_id: str):
        self._close_tab(tab_id)

    def show_notification(self, title: str, message: str, msg_type: str = "info"):
        colors = {"info": C["accent"], "success": C["success"],
                  "warning": C["warning"], "error": C["error"]}
        color = colors.get(msg_type, C["accent"])

        self.notif_bar.config(bg=color, height=40)
        for w in self.notif_bar.winfo_children():
            w.destroy()

        tk.Label(self.notif_bar, text=f"{title}: {message}", font=("Segoe UI", 9),
                 bg=color, fg="white", padx=15).pack(side=tk.LEFT, pady=8)
        close = tk.Label(self.notif_bar, text="✕", font=("Segoe UI", 10, "bold"),
                         bg=color, fg="white", cursor="hand2", padx=15)
        close.pack(side=tk.RIGHT, pady=8)
        close.bind("<Button-1>", lambda e: self._hide_notif())
        self.window.after(5000, self._hide_notif)

    def _hide_notif(self):
        self.notif_bar.config(height=0)
        for w in self.notif_bar.winfo_children():
            w.destroy()

    def system_log(self, message: str, level: str = "INFO"):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {message}"
        print(line)
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "apoloxias.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get_setting(self, key: str, default=None):
        profile = self.auth_manager.get_profile()
        if profile and "settings" in profile:
            return profile["settings"].get(key, default)
        return default

    def get_session_token(self) -> str:
        return self.auth_manager.get_session_token() or ""

    def request_permission(self, module_name: str, permission: str) -> bool:
        return messagebox.askyesno("Разрешение",
            f"Модуль '{module_name}' запрашивает: {permission}\n\nРазрешить?")

    def _start_session_check(self):
        self._check_session()

    def _check_session(self):
        if self.auth_manager.check_session_timeout():
            self._lock()
        else:
            self.window.after(30000, self._check_session)

    def _lock(self):
        self.module_manager.unload_all()
        self.auth_manager.logout()
        self.window.destroy()
        import subprocess, sys
        script = Path(__file__).parent.parent / "apoloxias.py"
        subprocess.Popen([sys.executable, str(script)], cwd=script.parent)

    def _logout(self):
        if messagebox.askyesno("Подтверждение", "Выйти из аккаунта?"):
            self._lock()

    def run(self):
        self.window.mainloop()