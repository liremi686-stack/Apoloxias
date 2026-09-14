import tkinter as tk
from tkinter import messagebox
from typing import Callable
import time


class LoginWindow:
    def __init__(self, auth_manager, on_success: Callable):
        self.auth_manager = auth_manager
        self.on_success = on_success
        self.window = tk.Tk()
        self.window.title("Apoloxias — Аутентификация")
        self.window.geometry("420x520")
        self.window.resizable(False, False)
        self.window.configure(bg="#0d0d12")
        self.window.overrideredirect(False)

        self._center_window(420, 520)
        self._failed = 0
        self._lockout = 0
        self._register_mode = False

        self._build_ui()
        if self.auth_manager.is_first_run():
            self._switch_register()

        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Return>", lambda e: self._action())

    def _center_window(self, w, h):
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() // 2) - (w // 2)
        y = (self.window.winfo_screenheight() // 2) - (h // 2)
        self.window.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self):
        # === ИСПРАВЛЕНИЕ: сохраняем палитру в self для доступа из других методов ===
        self.C = {"bg": "#0d0d12", "card": "#15151f", "accent": "#5c7cfa",
             "text": "#e9ecef", "muted": "#868e96", "error": "#ff6b6b",
             "border": "#2c2c3a", "hover": "#1c1c2b"}
        C = self.C

        frame = tk.Frame(self.window, bg=C["bg"])
        frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=40)

        tk.Label(frame, text="◈", font=("Segoe UI", 48), bg=C["bg"], fg=C["accent"]).pack()
        tk.Label(frame, text="APOLOXIAS", font=("Segoe UI", 20, "bold"), bg=C["bg"], fg=C["text"]).pack()
        tk.Label(frame, text="Secure Modular Platform v2.0", font=("Segoe UI", 9), bg=C["bg"], fg=C["muted"]).pack(pady=(0, 25))

        self.mode_lbl = tk.Label(frame, text="ВХОД", font=("Segoe UI", 12, "bold"), bg=C["bg"], fg=C["text"])
        self.mode_lbl.pack(pady=(0, 15))

        def mk_entry(show=None):
            e = tk.Entry(frame, font=("Segoe UI", 11), bg=C["card"], fg=C["text"],
                         insertbackground=C["accent"], bd=0, relief=tk.FLAT, show=show or "",
                         highlightthickness=1, highlightcolor=C["accent"], highlightbackground=C["border"])
            e.pack(fill=tk.X, ipady=8, pady=4)
            return e

        tk.Label(frame, text="Логин", font=("Segoe UI", 9), bg=C["bg"], fg=C["muted"], anchor="w").pack(fill=tk.X)
        self.user_entry = mk_entry()

        tk.Label(frame, text="Пароль", font=("Segoe UI", 9), bg=C["bg"], fg=C["muted"], anchor="w").pack(fill=tk.X, pady=(10, 0))
        self.pass_entry = mk_entry("•")

        self.conf_lbl = tk.Label(frame, text="Подтвердите пароль", font=("Segoe UI", 9), bg=C["bg"], fg=C["muted"], anchor="w")
        self.conf_entry = mk_entry("•")
        self.conf_lbl.pack_forget()
        self.conf_entry.pack_forget()

        self.err_lbl = tk.Label(frame, text="", font=("Segoe UI", 9), bg=C["bg"], fg=C["error"], wraplength=340)
        self.err_lbl.pack(pady=(10, 0))

        btn_frame = tk.Frame(frame, bg=C["bg"])
        btn_frame.pack(pady=20)

        self.action_btn = tk.Button(btn_frame, text="ВОЙТИ", font=("Segoe UI", 11, "bold"),
                                    bg=C["accent"], fg="white", activebackground="#748ffc",
                                    bd=0, padx=40, pady=8, cursor="hand2", command=self._action)
        self.action_btn.pack()

        self.switch_btn = tk.Button(frame, text="Создать аккаунт", font=("Segoe UI", 9, "underline"),
                                    bg=C["bg"], fg=C["accent"], activebackground=C["bg"],
                                    bd=0, cursor="hand2", command=self._switch_mode)
        self.switch_btn.pack()

        tk.Label(frame, text="Все данные шифруются локально", font=("Segoe UI", 8), bg=C["bg"], fg="#495057").pack(side=tk.BOTTOM, pady=(15, 0))

    def _switch_mode(self):
        if self.auth_manager.is_first_run():
            return
        self._register_mode = not self._register_mode
        if self._register_mode:
            self.mode_lbl.config(text="РЕГИСТРАЦИЯ")
            self.action_btn.config(text="СОЗДАТЬ")
            self.switch_btn.config(text="Уже есть аккаунт? Войти")
            self.conf_lbl.pack(fill=tk.X, pady=(10, 0), after=self.pass_entry)
            self.conf_entry.pack(fill=tk.X, ipady=8, pady=4, after=self.conf_lbl)
        else:
            self.mode_lbl.config(text="ВХОД")
            self.action_btn.config(text="ВОЙТИ")
            self.switch_btn.config(text="Создать аккаунт")
            self.conf_lbl.pack_forget()
            self.conf_entry.pack_forget()
        self.err_lbl.config(text="")

    def _switch_register(self):
        self._register_mode = True
        self.mode_lbl.config(text="РЕГИСТРАЦИЯ")
        self.action_btn.config(text="СОЗДАТЬ")
        self.switch_btn.config(text="")
        self.switch_btn.pack_forget()
        self.conf_lbl.pack(fill=tk.X, pady=(10, 0), after=self.pass_entry)
        self.conf_entry.pack(fill=tk.X, ipady=8, pady=4, after=self.conf_lbl)

    def _action(self):
        if time.time() < self._lockout:
            rem = int(self._lockout - time.time())
            self.err_lbl.config(text=f"Блокировка: подождите {rem} сек", fg=self.C["error"])
            return
        # === ИСПРАВЛЕНИЕ: сбрасываем цвет на стандартный ошибки перед каждой попыткой ===
        self.err_lbl.config(fg=self.C["error"])
        u = self.user_entry.get().strip()
        p = self.pass_entry.get()
        if not u or not p:
            self.err_lbl.config(text="Введите логин и пароль", fg=self.C["error"])
            return
        if self._register_mode:
            self._register(u, p)
        else:
            self._login(u, p)

    def _register(self, u, p):
        if len(p) < 8:
            self.err_lbl.config(text="Пароль минимум 8 символов", fg=self.C["error"])
            return
        if p != self.conf_entry.get():
            self.err_lbl.config(text="Пароли не совпадают", fg=self.C["error"])
            return
        if self.auth_manager.register(u, p):
            self.err_lbl.config(text="Аккаунт создан!", fg="#51cf66")
            self.window.after(800, self._proceed)
        else:
            self.err_lbl.config(text="Ошибка регистрации", fg=self.C["error"])

    def _login(self, u, p):
        if self.auth_manager.authenticate(u, p):
            self._failed = 0
            self._proceed()
        else:
            self._failed += 1
            rem = 5 - self._failed
            if self._failed >= 5:
                self._lockout = time.time() + 300
                self.err_lbl.config(text="Блокировка на 5 минут", fg=self.C["error"])
            else:
                self.err_lbl.config(text=f"Неверные данные. Осталось: {rem}", fg=self.C["error"])

    def _proceed(self):
        self.window.destroy()
        self.on_success()

    def _on_close(self):
        self.window.destroy()
        import sys
        sys.exit(0)

    def run(self):
        self.window.mainloop()