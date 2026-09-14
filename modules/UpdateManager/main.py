import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import json
import urllib.request
import urllib.error
import zipfile
import io
import os
import shutil
import threading
import hashlib
import time
import socket
import http.server
import socketserver
import struct

from core.module_api import ModuleInterface

C = {
    "bg": "#0d0d12", "card": "#15151f", "hover": "#1c1c2b",
    "accent": "#5c7cfa", "text": "#e9ecef", "muted": "#868e96",
    "success": "#51cf66", "error": "#ff6b6b", "warning": "#ffd43b",
    "border": "#2c2c3a",
}


# ─── LAN Discovery via Multicast ───
class LANUpdateDiscovery:
    MCAST = "239.192.42.99"
    PORT = 53000
    INTERVAL = 10
    TIMEOUT = 35

    def __init__(self, catalog_cb, port=53001):
        self.catalog_cb = catalog_cb
        self.port = port
        self.running = False
        self.peers = {}
        self._sock = None
        self._thread = None
        self._own_ips = self._get_ips()

    def _get_ips(self):
        ips = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
        return ips if ips else ["127.0.0.1"]

    def start(self):
        self.running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self.PORT))
        mreq = struct.pack("4sl", socket.inet_aton(self.MCAST), socket.INADDR_ANY)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def _loop(self):
        last = 0
        while self.running:
            now = time.time()
            if now - last > self.INTERVAL:
                msg = json.dumps({"type": "discover", "app": "Apoloxias", "version": "2.0", "port": self.port}).encode("utf-8")
                self._sock.sendto(msg, (self.MCAST, self.PORT))
                last = now
            try:
                data, addr = self._sock.recvfrom(1024)
                peer_ip = addr[0]
                if peer_ip in self._own_ips or peer_ip.startswith("127."):
                    continue
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "discover" and msg.get("app") == "Apoloxias":
                    reply = json.dumps({"type": "announce", "app": "Apoloxias",
                                        "hostname": socket.gethostname(), "port": self.port,
                                        "modules_count": len(self.catalog_cb().get("modules", []))}).encode("utf-8")
                    self._sock.sendto(reply, addr)
                elif msg.get("type") == "announce" and msg.get("app") == "Apoloxias":
                    old = dict(self.peers)
                    self.peers[peer_ip] = {"name": f"LAN: {msg.get('hostname', peer_ip)}",
                                           "url": f"http://{peer_ip}:{msg.get('port', self.port)}/catalog",
                                           "enabled": True, "trusted": False, "type": "lan",
                                           "last_seen": now}
                    if old != self.peers:
                        pass
            except socket.timeout:
                self._cleanup()
                continue
            except Exception:
                pass

    def _cleanup(self):
        now = time.time()
        stale = [ip for ip, p in self.peers.items() if now - p["last_seen"] > self.TIMEOUT]
        for ip in stale:
            del self.peers[ip]

    def get_peers(self):
        self._cleanup()
        return list(self.peers.values())


# ─── HTTP Server ───
class ThreadedLANServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_handler(catalog_dict, zips_dict):
    cd = catalog_dict
    zd = zips_dict

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path == "/catalog":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(cd).encode("utf-8"))
            elif self.path.startswith("/download/"):
                name = self.path[len("/download/"):]
                if name in zd:
                    path = zd[name]
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(path.stat().st_size))
                        self.end_headers()
                        with open(path, "rb") as f:
                            while chunk := f.read(65536):
                                self.wfile.write(chunk)
                    except Exception:
                        self.send_error(500)
                else:
                    self.send_error(404)
            else:
                self.send_error(404)

    return Handler


# ─── Module ───
class UpdateManagerModule(ModuleInterface):
    NAME = "UpdateManager"
    VERSION = "2.0.0"
    DESCRIPTION = "Менеджер обновлений модулей (Web + LAN)"
    ICON = "icon.ico"

    def __init__(self, api):
        super().__init__(api)
        self._frame = None

    def on_load(self):
        self.api.log("UpdateManager загружен", "INFO")

    def on_unload(self):
        self.api.log("UpdateManager выгружен", "INFO")

    def on_activate(self):
        self.api.open_in_main("Менеджер обновлений", UpdateManagerFrame)

    def on_deactivate(self):
        pass


class UpdateManagerFrame(tk.Frame):
    def __init__(self, parent, api):
        super().__init__(parent, bg=C["bg"])
        self.api = api
        self._update_items = []
        self._sources = []
        self._installed = {}
        self._modules_dir = self.api.get_module_path().parent.parent / "modules"
        self._sources_file = self.api.get_data_path() / "sources.json"
        self._lan_enabled = tk.BooleanVar(value=False)
        self._lan_port = 53001
        self._lan_discovery = None
        self._lan_server = None
        self._lan_zips = {}
        self._lan_catalog = {}
        self._lan_lock = threading.Lock()
        self._build_ui()
        self._load_data()
        self._check_alive()

    def _check_alive(self):
        try:
            if not self.winfo_exists() or not self.winfo_toplevel().winfo_exists():
                self._stop_lan()
                return
        except tk.TclError:
            self._cleanup()
            return
        self.after(1000, self._check_alive)

    def _cleanup(self):
        self._stop_lan()

    def _stop_lan(self):
        with self._lan_lock:
            if self._lan_discovery:
                try:
                    self._lan_discovery.stop()
                except Exception:
                    pass
                self._lan_discovery = None
            if self._lan_server:
                try:
                    self._lan_server.shutdown()
                    self._lan_server.server_close()
                except Exception:
                    pass
                self._lan_server = None
            for path in self._lan_zips.values():
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
            self._lan_zips = {}

    def _find_port(self, start=53001, attempts=10):
        for port in range(start, start + attempts):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("", port))
                s.close()
                return port
            except OSError:
                continue
        return None

    def _build_lan_catalog(self):
        modules = []
        self._scan_installed()
        for name, info in self._installed.items():
            modules.append({"name": name, "version": info["version"],
                            "description": info["manifest"].get("description", ""),
                            "author": info["manifest"].get("author", "Unknown"),
                            "download_url": f"/download/{name}", "checksum": ""})
        return {"modules": modules}

    def _build_zips(self):
        zips = {}
        zip_dir = self.api.get_data_path() / "lan_zips"
        zip_dir.mkdir(exist_ok=True)
        for f in zip_dir.iterdir():
            if f.suffix == ".zip":
                try:
                    f.unlink()
                except Exception:
                    pass
        if not self._modules_dir.exists():
            return zips
        for md in self._modules_dir.iterdir():
            if not md.is_dir():
                continue
            mf = md / "manifest.json"
            if not mf.exists():
                continue
            zp = zip_dir / f"{md.name}.zip"
            try:
                with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fp in md.rglob("*"):
                        if fp.is_file():
                            zf.write(fp, str(fp.relative_to(md)))
                zips[md.name] = zp
            except Exception as e:
                self.api.log(f"Ошибка упаковки {md.name}: {e}", "ERROR")
        return zips

    def _init_lan_async(self):
        def worker():
            try:
                self._lan_port = self._find_port(53001)
                if not self._lan_port:
                    self.after(0, lambda: self._on_lan_error("Нет свободного порта"))
                    return
                catalog = self._build_lan_catalog()
                zips = self._build_zips()
                with self._lan_lock:
                    self._lan_catalog = catalog
                    self._lan_zips = zips
                try:
                    handler = make_handler(self._lan_catalog, self._lan_zips)
                    self._lan_server = ThreadedLANServer(("", self._lan_port), handler)
                    srv_thread = threading.Thread(target=self._lan_server.serve_forever, daemon=True)
                    srv_thread.start()
                except Exception as e:
                    self.after(0, lambda: self._on_lan_error(f"Ошибка сервера: {e}"))
                    return
                self._lan_discovery = LANUpdateDiscovery(lambda: self._lan_catalog, self._lan_port)
                self._lan_discovery.start()
                self.after(0, self._on_lan_started)
            except Exception as e:
                self.after(0, lambda: self._on_lan_error(str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _on_lan_started(self):
        self.api.log(f"LAN на порту {self._lan_port}", "INFO")
        self.status_lbl.config(text=f"LAN активен (порт {self._lan_port})")
        self.api.show_notification("LAN", "LAN-обмен включен", "success")
        self._load_sources_ui()

    def _on_lan_error(self, msg):
        self.api.log(f"LAN ошибка: {msg}", "ERROR")
        self.status_lbl.config(text=f"LAN ошибка: {msg}")
        self._lan_enabled.set(False)
        self.api.show_notification("LAN ошибка", msg, "error")
        self._stop_lan()

    def _load_sources(self):
        if self._sources_file.exists():
            try:
                with open(self._sources_file, "r", encoding="utf-8") as f:
                    self._sources = json.load(f)
            except Exception as e:
                self.api.log(f"Ошибка загрузки источников: {e}", "ERROR")
                self._sources = []
        else:
            self._sources = [{"name": "Apoloxias Official", "url": "https://api.apoloxias.example/modules",
                              "enabled": True, "trusted": True}]
            self._save_sources()

    def _save_sources(self):
        try:
            with open(self._sources_file, "w", encoding="utf-8") as f:
                json.dump(self._sources, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.api.log(f"Ошибка сохранения: {e}", "ERROR")

    def _add_source(self, name, url, trusted=False):
        for s in self._sources:
            if s["url"] == url or s["name"] == name:
                return False
        self._sources.append({"name": name, "url": url, "enabled": True, "trusted": trusted})
        self._save_sources()
        return True

    def _remove_source(self, index):
        if 0 <= index < len(self._sources):
            self._sources.pop(index)
            self._save_sources()
            return True
        return False

    def _scan_installed(self):
        self._installed = {}
        if not self._modules_dir.exists():
            return
        for md in self._modules_dir.iterdir():
            if not md.is_dir():
                continue
            mf = md / "manifest.json"
            if mf.exists():
                try:
                    with open(mf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._installed[data.get("name", md.name)] = {"version": data.get("version", "0.0.0"),
                                                                    "path": str(md), "manifest": data}
                except Exception:
                    pass

    def _build_ui(self):
        hdr = tk.Frame(self, bg=C["card"], height=60)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="◈ Менеджер обновлений", font=("Segoe UI", 16, "bold"),
                 bg=C["card"], fg=C["accent"]).pack(side=tk.LEFT, padx=20, pady=12)
        self.refresh_btn = tk.Button(hdr, text="⟳ Обновить", font=("Segoe UI", 9), bg=C["accent"],
                                       fg="white", activebackground=C["hover"], bd=0, padx=15, pady=5,
                                       cursor="hand2", command=self._refresh_all)
        self.refresh_btn.pack(side=tk.RIGHT, padx=10, pady=10)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=C["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=C["card"], foreground=C["text"],
                        font=("Segoe UI", 9), padding=(15, 5))
        style.map("TNotebook.Tab", background=[("selected", "#0f3460")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TFrame", background=C["bg"])

        t1 = tk.Frame(notebook, bg=C["bg"])
        notebook.add(t1, text=" Доступные обновления ")
        self._build_updates_tab(t1)

        t2 = tk.Frame(notebook, bg=C["bg"])
        notebook.add(t2, text=" Установленные модули ")
        self._build_installed_tab(t2)

        t3 = tk.Frame(notebook, bg=C["bg"])
        notebook.add(t3, text=" Источники ")
        self._build_sources_tab(t3)

        status = tk.Frame(self, bg="#0a0a0f", height=30)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status_lbl = tk.Label(status, text="Готов", font=("Segoe UI", 9),
                                    bg="#0a0a0f", fg=C["muted"], anchor="w")
        self.status_lbl.pack(side=tk.LEFT, padx=10, pady=5)
        self.progress_lbl = tk.Label(status, text="", font=("Segoe UI", 9),
                                      bg="#0a0a0f", fg=C["accent"], anchor="e")
        self.progress_lbl.pack(side=tk.RIGHT, padx=10, pady=5)

    def _build_updates_tab(self, parent):
        toolbar = tk.Frame(parent, bg=C["bg"], height=40)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))
        toolbar.pack_propagate(False)
        tk.Button(toolbar, text="☑ Все", font=("Segoe UI", 9), bg=C["card"], fg=C["text"],
                  activebackground=C["hover"], bd=0, padx=12, pady=3, cursor="hand2",
                  command=self._select_all).pack(side=tk.LEFT, padx=5)
        tk.Button(toolbar, text="☐ Снять", font=("Segoe UI", 9), bg=C["card"], fg=C["text"],
                  activebackground=C["hover"], bd=0, padx=12, pady=3, cursor="hand2",
                  command=self._deselect_all).pack(side=tk.LEFT, padx=5)
        self.install_btn = tk.Button(toolbar, text="⬇ Установить выбранное", font=("Segoe UI", 9, "bold"),
                                      bg=C["success"], fg="white", activebackground="#449944",
                                      bd=0, padx=20, pady=3, cursor="hand2", command=self._install_selected)
        self.install_btn.pack(side=tk.RIGHT, padx=5)

        cf = tk.Frame(parent, bg=C["bg"])
        cf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.updates_canvas = tk.Canvas(cf, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.updates_canvas.yview)
        self.updates_list = tk.Frame(self.updates_canvas, bg=C["bg"])
        self.updates_list.bind("<Configure>",
            lambda e: self.updates_canvas.configure(scrollregion=self.updates_canvas.bbox("all")))
        self.updates_canvas.create_window((0, 0), window=self.updates_list, anchor="nw", width=820)
        self.updates_canvas.configure(yscrollcommand=sb.set)
        self.updates_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.updates_empty = tk.Label(self.updates_list, text="Нет обновлений. Нажмите ⟳",
                                       font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"])
        self.updates_empty.pack(pady=50)

    def _build_installed_tab(self, parent):
        cf = tk.Frame(parent, bg=C["bg"])
        cf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.installed_canvas = tk.Canvas(cf, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.installed_canvas.yview)
        self.installed_list = tk.Frame(self.installed_canvas, bg=C["bg"])
        self.installed_list.bind("<Configure>",
            lambda e: self.installed_canvas.configure(scrollregion=self.installed_canvas.bbox("all")))
        self.installed_canvas.create_window((0, 0), window=self.installed_list, anchor="nw", width=820)
        self.installed_canvas.configure(yscrollcommand=sb.set)
        self.installed_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_sources_tab(self, parent):
        toolbar = tk.Frame(parent, bg=C["bg"], height=40)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))
        toolbar.pack_propagate(False)
        tk.Button(toolbar, text="+ Добавить источник", font=("Segoe UI", 9), bg=C["accent"], fg="white",
                  activebackground=C["hover"], bd=0, padx=15, pady=3, cursor="hand2",
                  command=self._add_source_dialog).pack(side=tk.LEFT, padx=5)
        tk.Checkbutton(toolbar, text="LAN-обмен", variable=self._lan_enabled, font=("Segoe UI", 9),
                       bg=C["bg"], fg=C["text"], selectcolor=C["bg"], activebackground=C["bg"],
                       command=self._toggle_lan).pack(side=tk.RIGHT, padx=10)

        cf = tk.Frame(parent, bg=C["bg"])
        cf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.sources_canvas = tk.Canvas(cf, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.sources_canvas.yview)
        self.sources_list = tk.Frame(self.sources_canvas, bg=C["bg"])
        self.sources_list.bind("<Configure>",
            lambda e: self.sources_canvas.configure(scrollregion=self.sources_canvas.bbox("all")))
        self.sources_canvas.create_window((0, 0), window=self.sources_list, anchor="nw", width=820)
        self.sources_canvas.configure(yscrollcommand=sb.set)
        self.sources_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _toggle_lan(self):
        if self._lan_enabled.get():
            self.status_lbl.config(text="Запуск LAN...")
            self._init_lan_async()
        else:
            self._stop_lan()
            self.status_lbl.config(text="LAN отключен")
            self.api.show_notification("LAN", "LAN-обмен отключен", "info")
            self._load_sources_ui()

    def _load_data(self):
        self._load_sources()
        self._load_installed_ui()
        self._load_sources_ui()

    def _load_installed_ui(self):
        for w in self.installed_list.winfo_children():
            w.destroy()
        self._scan_installed()
        if not self._installed:
            tk.Label(self.installed_list, text="Нет установленных модулей",
                     font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"]).pack(pady=50)
            return
        for name, info in sorted(self._installed.items()):
            self._create_installed_card(name, info)

    def _create_installed_card(self, name, info):
        card = tk.Frame(self.installed_list, bg=C["card"], padx=15, pady=12)
        card.pack(fill=tk.X, pady=3, padx=5)
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill=tk.X)
        tk.Label(top, text=name, font=("Segoe UI", 11, "bold"), bg=C["card"], fg=C["text"]).pack(side=tk.LEFT)
        tk.Label(top, text=f"v{info['version']}", font=("Segoe UI", 9), bg=C["card"], fg=C["muted"]).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(card, text=f"Путь: {info['path']}", font=("Segoe UI", 8), bg=C["card"], fg="#495057", anchor="w").pack(fill=tk.X, pady=(5, 0))

    def _load_sources_ui(self):
        for w in self.sources_list.winfo_children():
            w.destroy()
        if not self._sources:
            tk.Label(self.sources_list, text="Нет источников", font=("Segoe UI", 11),
                     bg=C["bg"], fg=C["muted"]).pack(pady=20)
        else:
            for i, src in enumerate(self._sources):
                self._create_source_card(i, src)
        if self._lan_enabled.get() and self._lan_discovery:
            peers = self._lan_discovery.get_peers()
            if peers:
                tk.Frame(self.sources_list, bg="#0f3460", height=2).pack(fill=tk.X, padx=5, pady=10)
                tk.Label(self.sources_list, text="LAN-СОСЕДИ", font=("Segoe UI", 9, "bold"),
                         bg=C["bg"], fg=C["success"], anchor="w").pack(fill=tk.X, padx=5, pady=(0, 5))
                for peer in peers:
                    self._create_lan_peer_card(peer)

    def _create_source_card(self, index, src):
        card = tk.Frame(self.sources_list, bg=C["card"], padx=15, pady=12)
        card.pack(fill=tk.X, pady=3, padx=5)
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill=tk.X)
        sc = C["success"] if src.get("enabled") else C["error"]
        st = "●" if src.get("enabled") else "○"
        tk.Label(top, text=st, font=("Segoe UI", 10), bg=C["card"], fg=sc).pack(side=tk.LEFT)
        tk.Label(top, text=src["name"], font=("Segoe UI", 11, "bold"), bg=C["card"], fg=C["text"]).pack(side=tk.LEFT, padx=(8, 0))
        if src.get("trusted"):
            tk.Label(top, text="✓ Доверенный", font=("Segoe UI", 8), bg=C["card"], fg=C["success"]).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(card, text=src["url"], font=("Segoe UI", 9), bg=C["card"], fg=C["muted"], anchor="w").pack(fill=tk.X, pady=(5, 0))
        bf = tk.Frame(card, bg=C["card"])
        bf.pack(fill=tk.X, pady=(8, 0))
        tk.Button(bf, text="Вкл/Выкл", font=("Segoe UI", 8), bg=C["hover"], fg=C["text"],
                  activebackground="#0f3460", bd=0, padx=12, pady=2, cursor="hand2",
                  command=lambda idx=index: self._toggle_source(idx)).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(bf, text="Удалить", font=("Segoe UI", 8), bg=C["error"], fg="white",
                  activebackground="#cc4444", bd=0, padx=12, pady=2, cursor="hand2",
                  command=lambda idx=index: self._remove_source_dialog(idx)).pack(side=tk.LEFT)

    def _create_lan_peer_card(self, peer):
        card = tk.Frame(self.sources_list, bg=C["hover"], padx=15, pady=10)
        card.pack(fill=tk.X, pady=2, padx=5)
        top = tk.Frame(card, bg=C["hover"])
        top.pack(fill=tk.X)
        tk.Label(top, text="🖧", font=("Segoe UI", 12), bg=C["hover"], fg=C["success"]).pack(side=tk.LEFT)
        tk.Label(top, text=peer["name"], font=("Segoe UI", 11, "bold"), bg=C["hover"], fg=C["text"]).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(top, text="LAN", font=("Segoe UI", 8, "bold"), bg=C["hover"], fg=C["success"]).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(card, text=peer["url"], font=("Segoe UI", 9), bg=C["hover"], fg=C["muted"], anchor="w").pack(fill=tk.X, pady=(3, 0))
        tk.Label(card, text=f"Модулей: {peer.get('modules_count', '?')}", font=("Segoe UI", 9), bg=C["hover"], fg="#aaaaaa", anchor="w").pack(fill=tk.X, pady=(3, 0))

    def _toggle_source(self, idx):
        if 0 <= idx < len(self._sources):
            self._sources[idx]["enabled"] = not self._sources[idx].get("enabled", True)
            self._save_sources()
            self._load_sources_ui()

    def _remove_source_dialog(self, idx):
        if messagebox.askyesno("Подтверждение", f"Удалить '{self._sources[idx]['name']}'?"):
            self._remove_source(idx)
            self._load_sources_ui()

    def _add_source_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Добавить источник")
        dlg.geometry("400x200")
        dlg.configure(bg=C["card"])
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        dlg.update_idletasks()
        x = (dlg.winfo_screenwidth() // 2) - 200
        y = (dlg.winfo_screenheight() // 2) - 100
        dlg.geometry(f"400x200+{x}+{y}")

        tk.Label(dlg, text="Название:", font=("Segoe UI", 10), bg=C["card"], fg=C["muted"], anchor="w").pack(fill=tk.X, padx=20, pady=(15, 2))
        name_entry = tk.Entry(dlg, font=("Segoe UI", 10), bg=C["hover"], fg=C["text"],
                              insertbackground=C["accent"], bd=0, highlightthickness=1,
                              highlightcolor=C["accent"], highlightbackground=C["border"])
        name_entry.pack(fill=tk.X, padx=20, pady=2, ipady=5)
        tk.Label(dlg, text="URL:", font=("Segoe UI", 10), bg=C["card"], fg=C["muted"], anchor="w").pack(fill=tk.X, padx=20, pady=(10, 2))
        url_entry = tk.Entry(dlg, font=("Segoe UI", 10), bg=C["hover"], fg=C["text"],
                             insertbackground=C["accent"], bd=0, highlightthickness=1,
                             highlightcolor=C["accent"], highlightbackground=C["border"])
        url_entry.pack(fill=tk.X, padx=20, pady=2, ipady=5)
        url_entry.insert(0, "https://")

        def save():
            n = name_entry.get().strip()
            u = url_entry.get().strip()
            if not n or not u:
                messagebox.showwarning("Ошибка", "Заполните все поля")
                return
            if self._add_source(n, u):
                self._load_sources_ui()
                dlg.destroy()
                self.api.show_notification("Источник добавлен", f"'{n}' добавлен", "success")
            else:
                messagebox.showwarning("Ошибка", "Уже существует")

        bf = tk.Frame(dlg, bg=C["card"])
        bf.pack(fill=tk.X, padx=20, pady=15)
        tk.Button(bf, text="Отмена", font=("Segoe UI", 9), bg=C["hover"], fg=C["text"],
                  activebackground="#0f3460", bd=0, padx=20, pady=5, cursor="hand2",
                  command=dlg.destroy).pack(side=tk.LEFT)
        tk.Button(bf, text="Добавить", font=("Segoe UI", 9, "bold"), bg=C["accent"], fg="white",
                  activebackground=C["hover"], bd=0, padx=20, pady=5, cursor="hand2",
                  command=save).pack(side=tk.RIGHT)

    def _refresh_all(self):
        self.status_lbl.config(text="Проверка обновлений...")
        self.refresh_btn.config(state=tk.DISABLED)
        def check():
            updates = self._fetch_updates()
            self.after(0, lambda: self._on_refresh_done(updates))
        threading.Thread(target=check, daemon=True).start()

    def _fetch_updates(self):
        updates = []
        self._scan_installed()
        for src in self._sources:
            if not src.get("enabled", True):
                continue
            try:
                req = urllib.request.Request(src["url"], headers={"User-Agent": "Apoloxias-UpdateManager/2.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    catalog = json.loads(resp.read().decode("utf-8"))
                self._process_catalog(catalog, src["name"], src["url"], updates)
            except Exception as e:
                self.api.log(f"Ошибка {src['name']}: {e}", "ERROR")
        if self._lan_enabled.get() and self._lan_discovery:
            for peer in self._lan_discovery.get_peers():
                try:
                    req = urllib.request.Request(peer["url"], headers={"User-Agent": "Apoloxias-UpdateManager/2.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        catalog = json.loads(resp.read().decode("utf-8"))
                    base_url = peer["url"].replace("/catalog", "")
                    for item in catalog.get("modules", []):
                        item["download_url"] = f"{base_url}/download/{item['name']}"
                    self._process_catalog(catalog, peer["name"], peer["url"], updates)
                except Exception as e:
                    self.api.log(f"LAN {peer['name']}: {e}", "ERROR")
        return updates

    def _process_catalog(self, catalog, source_name, source_url, updates):
        for item in catalog.get("modules", []):
            name = item.get("name")
            version = item.get("version", "0.0.0")
            if not name:
                continue
            if name in self._installed:
                cv = self._installed[name]["version"]
                if self._ver_cmp(version, cv) > 0:
                    updates.append({"name": name, "version": version, "current_version": cv,
                                    "description": item.get("description", ""), "author": item.get("author", "Unknown"),
                                    "download_url": item.get("download_url", ""), "checksum": item.get("checksum", ""),
                                    "source": source_name, "source_url": source_url, "type": "update", "selected": False})
            else:
                updates.append({"name": name, "version": version, "current_version": None,
                                "description": item.get("description", ""), "author": item.get("author", "Unknown"),
                                "download_url": item.get("download_url", ""), "checksum": item.get("checksum", ""),
                                "source": source_name, "source_url": source_url, "type": "new", "selected": False})

    def _ver_cmp(self, v1, v2):
        def p(v):
            return [int(x) for x in v.split(".") if x.isdigit()]
        a, b = p(v1), p(v2)
        for i in range(max(len(a), len(b))):
            x = a[i] if i < len(a) else 0
            y = b[i] if i < len(b) else 0
            if x > y:
                return 1
            if x < y:
                return -1
        return 0

    def _on_refresh_done(self, updates):
        self._update_items = updates
        self.refresh_btn.config(state=tk.NORMAL)
        for w in self.updates_list.winfo_children():
            w.destroy()
        if not updates:
            self.updates_empty = tk.Label(self.updates_list, text="Все модули актуальны",
                                           font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"])
            self.updates_empty.pack(pady=50)
            self.status_lbl.config(text="Все модули актуальны")
            return
        for item in updates:
            self._create_update_card(item)
        nc = sum(1 for u in updates if u["type"] == "new")
        uc = sum(1 for u in updates if u["type"] == "update")
        self.status_lbl.config(text=f"Найдено: {len(updates)} ({nc} новых, {uc} обновлений)")

    def _create_update_card(self, item):
        var = tk.BooleanVar(value=item.get("selected", False))
        item["var"] = var
        card = tk.Frame(self.updates_list, bg=C["card"], padx=15, pady=10)
        card.pack(fill=tk.X, pady=3, padx=5)
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill=tk.X)
        cb = tk.Checkbutton(top, variable=var, bg=C["card"], activebackground=C["card"], selectcolor=C["bg"])
        cb.pack(side=tk.LEFT)
        tc = C["warning"] if item["type"] == "update" else C["success"]
        tt = "Обновление" if item["type"] == "update" else "Новый"
        tk.Label(top, text=tt, font=("Segoe UI", 8, "bold"), bg=C["card"], fg=tc).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(top, text=item["name"], font=("Segoe UI", 11, "bold"), bg=C["card"], fg=C["text"]).pack(side=tk.LEFT)
        if item["type"] == "update":
            tk.Label(top, text=f"v{item['current_version']} → v{item['version']}", font=("Segoe UI", 9),
                     bg=C["card"], fg=C["warning"]).pack(side=tk.LEFT, padx=(10, 0))
        else:
            tk.Label(top, text=f"v{item['version']}", font=("Segoe UI", 9), bg=C["card"], fg=C["success"]).pack(side=tk.LEFT, padx=(10, 0))
        is_lan = item["source"].startswith("LAN:")
        sc = C["success"] if is_lan else C["muted"]
        tk.Label(top, text=f"[{item['source']}]", font=("Segoe UI", 8), bg=C["card"], fg=sc).pack(side=tk.RIGHT)
        if item.get("description"):
            tk.Label(card, text=item["description"], font=("Segoe UI", 9), bg=C["card"],
                     fg="#aaaaaa", anchor="w", wraplength=750).pack(fill=tk.X, pady=(5, 0))
        tk.Label(card, text=f"Автор: {item['author']}", font=("Segoe UI", 8), bg=C["card"], fg="#495057", anchor="w").pack(fill=tk.X, pady=(3, 0))
        item["progress_frame"] = tk.Frame(card, bg=C["card"])
        item["progress_bar"] = tk.Frame(item["progress_frame"], bg=C["accent"], height=3)

    def _select_all(self):
        for item in self._update_items:
            item["selected"] = True
            if "var" in item:
                item["var"].set(True)

    def _deselect_all(self):
        for item in self._update_items:
            item["selected"] = False
            if "var" in item:
                item["var"].set(False)

    def _install_selected(self):
        selected = [i for i in self._update_items if i.get("var") and i["var"].get()]
        if not selected:
            messagebox.showwarning("Внимание", "Выберите хотя бы один модуль")
            return
        if not self.api.request_permission("network"):
            self.api.show_notification("Отказано", "Нет разрешения на сеть", "warning")
            return
        self.install_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text=f"Загрузка {len(selected)} модулей...")
        def install_all():
            for i, item in enumerate(selected):
                self.after(0, lambda it=item: self._show_progress(it, 0, f"Загрузка {it['name']}..."))
                ok = self._download_install(item)
                p = int(((i + 1) / len(selected)) * 100)
                self.after(0, lambda it=item, s=ok, pr=p: self._on_item_done(it, s, pr))
            self.after(0, self._on_install_done)
        threading.Thread(target=install_all, daemon=True).start()

    def _show_progress(self, item, pct, text):
        self.progress_lbl.config(text=text)
        if "progress_frame" in item:
            item["progress_frame"].pack(fill=tk.X, pady=(8, 0))
            item["progress_bar"].place(relwidth=pct / 100, relheight=1)

    def _on_item_done(self, item, success, total_progress):
        if "progress_frame" in item:
            item["progress_frame"].pack_forget()
        if success and "var" in item:
            item["var"].set(False)
        self.progress_lbl.config(text=f"Прогресс: {total_progress}%")

    def _on_install_done(self):
        self.install_btn.config(state=tk.NORMAL)
        self.status_lbl.config(text="Установка завершена")
        self.progress_lbl.config(text="")
        self.api.show_notification("Готово", "Установка завершена", "success")
        self._load_installed_ui()
        self._refresh_all()

    def _download_install(self, item):
        try:
            url = item.get("download_url", "")
            if not url:
                return False
            req = urllib.request.Request(url, headers={"User-Agent": "Apoloxias-UpdateManager/2.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if item.get("checksum"):
                if hashlib.sha256(data).hexdigest() != item["checksum"]:
                    self.api.log(f"Контрольная сумма {item['name']}", "ERROR")
                    return False
            self._modules_dir.mkdir(parents=True, exist_ok=True)
            target = self._modules_dir / item["name"]
            if target.exists():
                backup = self._modules_dir / f"{item['name']}_backup"
                if backup.exists():
                    shutil.rmtree(backup)
                shutil.copytree(target, backup)
                shutil.rmtree(target)
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(target)
            if not (target / "manifest.json").exists():
                backup = self._modules_dir / f"{item['name']}_backup"
                if backup.exists():
                    shutil.copytree(backup, target)
                return False
            backup = self._modules_dir / f"{item['name']}_backup"
            if backup.exists():
                shutil.rmtree(backup)
            self.api.log(f"Установлен {item['name']} v{item['version']}", "INFO")
            self.api.publish("module.installed", {"name": item["name"], "version": item["version"]})
            return True
        except Exception as e:
            self.api.log(f"Ошибка {item['name']}: {e}", "ERROR")
            try:
                backup = self._modules_dir / f"{item['name']}_backup"
                target = self._modules_dir / item["name"]
                if backup.exists() and not target.exists():
                    shutil.copytree(backup, target)
            except Exception:
                pass
            return False
