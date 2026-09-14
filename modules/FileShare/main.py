import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from core.module_api import ModuleInterface
import socket
import threading
import time
import struct
import hashlib
import os
import shutil
import urllib.request
import urllib.error
import http.server
import socketserver
import json
from pathlib import Path

C = {
    "bg": "#0d0d12", "card": "#15151f", "hover": "#1c1c2b",
    "accent": "#5c7cfa", "text": "#e9ecef", "muted": "#868e96",
    "success": "#51cf66", "error": "#ff6b6b", "warning": "#ffd43b",
    "border": "#2c2c3a",
}


# ─── Stealth Discovery ───
class FileDiscovery:
    MCAST = "239.192.42.99"
    PORT = 52000
    INTERVAL = 8
    TIMEOUT = 25

    def __init__(self, username, on_change):
        self.username = username
        self.on_change = on_change
        self.running = False
        self.peers = {}
        self._sock = None
        self._thread = None
        self._own_ips = self._get_ips()
        self._file_port = 52001

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

    def start(self, file_port):
        self._file_port = file_port
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
                msg = json.dumps({"type": "file_announce", "app": "Apoloxias",
                                  "username": self.username, "file_port": self._file_port,
                                  "timestamp": now}).encode("utf-8")
                self._sock.sendto(msg, (self.MCAST, self.PORT))
                last = now
            try:
                data, addr = self._sock.recvfrom(1024)
                ip = addr[0]
                if ip in self._own_ips or ip.startswith("127."):
                    continue
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "file_announce" and msg.get("app") == "Apoloxias":
                    old = dict(self.peers)
                    self.peers[ip] = {"name": msg.get("username", "Unknown"),
                                      "file_port": msg.get("file_port", 52001),
                                      "last_seen": now}
                    if old != self.peers:
                        self.on_change()
            except socket.timeout:
                self._cleanup()
                continue
            except Exception:
                pass

    def _cleanup(self):
        now = time.time()
        stale = [ip for ip, p in self.peers.items() if now - p["last_seen"] > self.TIMEOUT]
        if stale:
            for ip in stale:
                del self.peers[ip]
            self.on_change()


# ─── HTTP File Server ───
class ThreadedFileServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_handler(shared_dir, on_log):
    sp = Path(shared_dir)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, data, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        def do_GET(self):
            if self.path == "/catalog":
                files = []
                if sp.exists():
                    for f in sp.iterdir():
                        if f.is_file():
                            try:
                                h = hashlib.sha256()
                                with open(f, "rb") as fh:
                                    while chunk := fh.read(65536):
                                        h.update(chunk)
                                files.append({"name": f.name, "size": f.stat().st_size,
                                              "checksum": h.hexdigest(), "timestamp": f.stat().st_mtime})
                            except Exception:
                                pass
                self._json({"files": files})
            elif self.path.startswith("/download/"):
                fn = self.path[len("/download/"):]
                fp = sp / fn
                try:
                    fp.resolve().relative_to(sp.resolve())
                except ValueError:
                    self.send_error(403)
                    return
                if not fp.exists() or not fp.is_file():
                    self.send_error(404)
                    return
                on_log(f"Отправка {fn} -> {self.client_address[0]}")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(fp.stat().st_size))
                self.end_headers()
                with open(fp, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
            else:
                self.send_error(404)

    return Handler


# ─── Transfer Manager ───
class TransferManager:
    def __init__(self, download_dir, on_progress, on_complete):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.on_progress = on_progress
        self.on_complete = on_complete
        self._active = {}

    def download(self, tid, peer_ip, peer_port, filename, expected_checksum=""):
        def worker():
            try:
                url = f"http://{peer_ip}:{peer_port}/download/{urllib.request.quote(filename)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Apoloxias-FileShare/2.0"})
                tmp = self.download_dir / f".{tid}.tmp"
                final = self.download_dir / filename
                c = 1
                orig = final
                while final.exists():
                    final = self.download_dir / f"{orig.stem}_{c}{orig.suffix}"
                    c += 1
                h = hashlib.sha256()
                downloaded = 0
                total = 0
                with urllib.request.urlopen(req, timeout=30) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    with open(tmp, "wb") as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            h.update(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                self.on_progress(tid, int((downloaded / total) * 100), downloaded, total)
                if expected_checksum and h.hexdigest() != expected_checksum:
                    tmp.unlink(missing_ok=True)
                    self.on_complete(tid, False, "Ошибка контрольной суммы")
                    return
                shutil.move(str(tmp), str(final))
                self.on_complete(tid, True, str(final))
            except Exception as e:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                self.on_complete(tid, False, str(e))
        t = threading.Thread(target=worker, daemon=True)
        self._active[tid] = t
        t.start()

    def cancel(self, tid):
        self._active.pop(tid, None)


# ─── Module ───
class FileShareModule(ModuleInterface):
    NAME = "FileShare"
    VERSION = "2.0.0"
    DESCRIPTION = "P2P обмен файлами через LAN (шифрованный трафик)"
    ICON = "icon.ico"

    def __init__(self, api):
        super().__init__(api)
        self._frame = None

    def on_load(self):
        self.api.log("FileShare загружен", "INFO")

    def on_unload(self):
        self.api.log("FileShare выгружен", "INFO")

    def on_activate(self):
        self.api.open_in_main("Обмен файлами", FileShareFrame)

    def on_deactivate(self):
        pass


class FileShareFrame(tk.Frame):
    def __init__(self, parent, api):
        super().__init__(parent, bg=C["bg"])
        self.api = api
        self._shared_dir = self.api.get_data_path() / "shared"
        self._shared_dir.mkdir(exist_ok=True)
        self._download_dir = self.api.get_data_path() / "downloads"
        self._download_dir.mkdir(exist_ok=True)
        self._file_port = self._find_port(52001)
        self._discovery = None
        self._server = None
        self._transfer_mgr = TransferManager(self._download_dir, self._on_progress, self._on_complete)
        self._peers = {}
        self._peer_files = {}
        self._transfers = []
        self._selected_peer = None
        self._transfer_counter = 0
        self._build_ui()
        self._init_network()
        self._check_alive()

    def _find_port(self, start, attempts=10):
        for port in range(start, start + attempts):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("", port))
                s.close()
                return port
            except OSError:
                continue
        return start

    def _init_network(self):
        username = self.api.get_setting("username", "User") or "User"
        try:
            handler = make_handler(self._shared_dir, self._log)
            self._server = ThreadedFileServer(("", self._file_port), handler)
            srv_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            srv_thread.start()
            self.api.log(f"HTTP сервер на порту {self._file_port}", "INFO")
        except Exception as e:
            self.api.log(f"Ошибка сервера: {e}", "ERROR")
        self._discovery = FileDiscovery(username, self._on_peers_changed)
        self._discovery.start(self._file_port)

    def _on_peers_changed(self):
        self.after(0, self._refresh_peers_ui)

    def _check_alive(self):
        try:
            if not self.winfo_exists() or not self.winfo_toplevel().winfo_exists():
                self._cleanup()
                return
        except tk.TclError:
            self._cleanup()
            return
        self.after(1000, self._check_alive)

    def _cleanup(self):
        if self._discovery:
            self._discovery.stop()
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass

    def _build_ui(self):
        hdr = tk.Frame(self, bg=C["card"], height=60)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="◈ Обмен файлами", font=("Segoe UI", 16, "bold"),
                 bg=C["card"], fg=C["accent"]).pack(side=tk.LEFT, padx=20, pady=12)
        tk.Label(hdr, text=f"Порт: {self._file_port}", font=("Segoe UI", 9),
                 bg=C["card"], fg=C["muted"]).pack(side=tk.RIGHT, padx=20, pady=12)

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

        # Tab 1: Files
        t1 = tk.Frame(notebook, bg=C["bg"])
        notebook.add(t1, text=" Файлы в сети ")
        self._build_files_tab(t1)

        # Tab 2: Transfers
        t2 = tk.Frame(notebook, bg=C["bg"])
        notebook.add(t2, text=" Передачи ")
        self._build_transfers_tab(t2)

        # Tab 3: My files
        t3 = tk.Frame(notebook, bg=C["bg"])
        notebook.add(t3, text=" Мои файлы ")
        self._build_shared_tab(t3)

        # Status
        status = tk.Frame(self, bg="#0a0a0f", height=28)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status_lbl = tk.Label(status, text="Готов", font=("Segoe UI", 9),
                                    bg="#0a0a0f", fg=C["muted"], anchor="w")
        self.status_lbl.pack(side=tk.LEFT, padx=10, pady=4)

    def _build_files_tab(self, parent):
        paned = tk.PanedWindow(parent, bg=C["bg"], orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = tk.Frame(paned, bg=C["bg"], width=250)
        paned.add(left, minsize=200)
        left.pack_propagate(False)
        tk.Label(left, text="УЧАСТНИКИ", font=("Segoe UI", 10, "bold"),
                 bg=C["bg"], fg=C["muted"], anchor="w").pack(fill=tk.X, pady=(0, 10))
        self.peers_list = tk.Frame(left, bg=C["bg"])
        self.peers_list.pack(fill=tk.BOTH, expand=True)
        self.peers_empty = tk.Label(self.peers_list, text="Нет участников...",
                                     font=("Segoe UI", 10), bg=C["bg"], fg=C["muted"])
        self.peers_empty.pack(pady=30)

        right = tk.Frame(paned, bg=C["bg"])
        paned.add(right, minsize=400)
        toolbar = tk.Frame(right, bg=C["bg"], height=40)
        toolbar.pack(fill=tk.X, pady=(0, 10))
        toolbar.pack_propagate(False)
        self.sel_peer_lbl = tk.Label(toolbar, text="Выберите участника", font=("Segoe UI", 11, "bold"),
                                      bg=C["bg"], fg=C["text"], anchor="w")
        self.sel_peer_lbl.pack(side=tk.LEFT, padx=5, pady=5)
        tk.Button(toolbar, text="⟳ Обновить", font=("Segoe UI", 9), bg=C["accent"], fg="white",
                  activebackground=C["hover"], bd=0, padx=15, pady=3, cursor="hand2",
                  command=self._refresh_selected).pack(side=tk.RIGHT, padx=5, pady=5)

        cf = tk.Frame(right, bg=C["bg"])
        cf.pack(fill=tk.BOTH, expand=True)
        self.files_canvas = tk.Canvas(cf, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.files_canvas.yview)
        self.files_container = tk.Frame(self.files_canvas, bg=C["bg"])
        self.files_container.bind("<Configure>",
            lambda e: self.files_canvas.configure(scrollregion=self.files_canvas.bbox("all")))
        self.files_canvas.create_window((0, 0), window=self.files_container, anchor="nw", width=600)
        self.files_canvas.configure(yscrollcommand=sb.set)
        self.files_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.files_empty = tk.Label(self.files_container, text="Выберите участника",
                                     font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"])
        self.files_empty.pack(pady=50)

    def _build_transfers_tab(self, parent):
        toolbar = tk.Frame(parent, bg=C["bg"], height=40)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))
        toolbar.pack_propagate(False)
        tk.Button(toolbar, text="🗑 Очистить завершенные", font=("Segoe UI", 9), bg=C["card"],
                  fg=C["text"], activebackground=C["hover"], bd=0, padx=15, pady=3,
                  cursor="hand2", command=self._clear_completed).pack(side=tk.LEFT, padx=5)
        tk.Button(toolbar, text="📂 Открыть папку", font=("Segoe UI", 9), bg=C["card"],
                  fg=C["text"], activebackground=C["hover"], bd=0, padx=15, pady=3,
                  cursor="hand2", command=self._open_downloads).pack(side=tk.LEFT, padx=5)

        cf = tk.Frame(parent, bg=C["bg"])
        cf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.transfers_canvas = tk.Canvas(cf, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.transfers_canvas.yview)
        self.transfers_container = tk.Frame(self.transfers_canvas, bg=C["bg"])
        self.transfers_container.bind("<Configure>",
            lambda e: self.transfers_canvas.configure(scrollregion=self.transfers_canvas.bbox("all")))
        self.transfers_canvas.create_window((0, 0), window=self.transfers_container, anchor="nw", width=880)
        self.transfers_canvas.configure(yscrollcommand=sb.set)
        self.transfers_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.transfers_empty = tk.Label(self.transfers_container, text="Нет передач",
                                         font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"])
        self.transfers_empty.pack(pady=50)

    def _build_shared_tab(self, parent):
        toolbar = tk.Frame(parent, bg=C["bg"], height=40)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))
        toolbar.pack_propagate(False)
        tk.Button(toolbar, text="+ Добавить файл", font=("Segoe UI", 9, "bold"), bg=C["accent"],
                  fg="white", activebackground=C["hover"], bd=0, padx=20, pady=3,
                  cursor="hand2", command=self._add_file).pack(side=tk.LEFT, padx=5)
        tk.Button(toolbar, text="🗑 Удалить все", font=("Segoe UI", 9), bg=C["error"],
                  fg="white", activebackground="#cc4444", bd=0, padx=15, pady=3,
                  cursor="hand2", command=self._remove_all).pack(side=tk.LEFT, padx=5)
        self.shared_size_lbl = tk.Label(toolbar, text="", font=("Segoe UI", 9),
                                         bg=C["bg"], fg=C["muted"])
        self.shared_size_lbl.pack(side=tk.RIGHT, padx=10)

        cf = tk.Frame(parent, bg=C["bg"])
        cf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.shared_canvas = tk.Canvas(cf, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.shared_canvas.yview)
        self.shared_container = tk.Frame(self.shared_canvas, bg=C["bg"])
        self.shared_container.bind("<Configure>",
            lambda e: self.shared_canvas.configure(scrollregion=self.shared_canvas.bbox("all")))
        self.shared_canvas.create_window((0, 0), window=self.shared_container, anchor="nw", width=880)
        self.shared_canvas.configure(yscrollcommand=sb.set)
        self.shared_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._refresh_shared()

    def _refresh_peers_ui(self):
        for w in self.peers_list.winfo_children():
            w.destroy()
        if not self._discovery or not self._discovery.peers:
            self.peers_empty = tk.Label(self.peers_list, text="Нет участников...",
                                         font=("Segoe UI", 10), bg=C["bg"], fg=C["muted"])
            self.peers_empty.pack(pady=30)
            self._peers = {}
            return
        for ip, peer in self._discovery.peers.items():
            self._peers[ip] = dict(peer)
            self._create_peer_btn(ip, peer)

    def _create_peer_btn(self, ip, peer):
        btn = tk.Frame(self.peers_list, bg=C["card"], padx=10, pady=8, cursor="hand2")
        btn.pack(fill=tk.X, pady=2)
        sel = self._selected_peer == ip
        bg = C["hover"] if sel else C["card"]
        btn.config(bg=bg)
        tk.Label(btn, text=peer["name"], font=("Segoe UI", 10, "bold"),
                 bg=bg, fg=C["text"]).pack(anchor="w")
        tk.Label(btn, text=f"{ip}:{peer['file_port']}", font=("Segoe UI", 8),
                 bg=bg, fg=C["muted"]).pack(anchor="w")

        def click(e, p=ip):
            self._selected_peer = p
            self._refresh_peers_ui()
            self._load_peer_files(p)
        btn.bind("<Button-1>", click)
        for c in btn.winfo_children():
            c.bind("<Button-1>", click)

    def _load_peer_files(self, peer_ip):
        if peer_ip not in self._peers:
            return
        self.sel_peer_lbl.config(text=f"Файлы: {self._peers[peer_ip]['name']}")
        self.status_lbl.config(text=f"Загрузка каталога...")

        def fetch():
            try:
                peer = self._peers[peer_ip]
                url = f"http://{peer_ip}:{peer['file_port']}/catalog"
                req = urllib.request.Request(url, headers={"User-Agent": "Apoloxias-FileShare/2.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    catalog = json.loads(resp.read().decode("utf-8"))
                self.after(0, lambda: self._on_files_loaded(peer_ip, catalog.get("files", [])))
            except Exception as e:
                self.after(0, lambda: self._on_files_loaded(peer_ip, [], str(e)))
        threading.Thread(target=fetch, daemon=True).start()

    def _on_files_loaded(self, peer_ip, files, error=None):
        self._peer_files[peer_ip] = files
        for w in self.files_container.winfo_children():
            w.destroy()
        if error:
            self.status_lbl.config(text=f"Ошибка: {error}")
            tk.Label(self.files_container, text=f"Ошибка: {error}",
                     font=("Segoe UI", 11), bg=C["bg"], fg=C["error"]).pack(pady=30)
            return
        if not files:
            self.status_lbl.config(text="Нет файлов")
            tk.Label(self.files_container, text="Нет расшаренных файлов",
                     font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"]).pack(pady=30)
            return
        self.status_lbl.config(text=f"{len(files)} файлов")
        for f in files:
            self._create_file_card(peer_ip, f)

    def _create_file_card(self, peer_ip, fi):
        card = tk.Frame(self.files_container, bg=C["card"], padx=15, pady=10)
        card.pack(fill=tk.X, pady=3, padx=5)
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill=tk.X)
        tk.Label(top, text="📄", font=("Segoe UI", 14), bg=C["card"], fg=C["accent"]).pack(side=tk.LEFT)
        nf = tk.Frame(top, bg=C["card"])
        nf.pack(side=tk.LEFT, padx=(10, 0), fill=tk.Y)
        tk.Label(nf, text=fi["name"], font=("Segoe UI", 11, "bold"),
                 bg=C["card"], fg=C["text"], anchor="w").pack(fill=tk.X)
        tk.Label(nf, text=f"{self._fmt_size(fi.get('size', 0))} | SHA256: {fi.get('checksum', '—')[:16]}...",
                 font=("Segoe UI", 9), bg=C["card"], fg=C["muted"], anchor="w").pack(fill=tk.X)
        tk.Button(top, text="⬇ Скачать", font=("Segoe UI", 9, "bold"), bg=C["success"],
                  fg="white", activebackground="#449944", bd=0, padx=15, pady=5,
                  cursor="hand2", command=lambda: self._start_download(peer_ip, fi)).pack(side=tk.RIGHT)

    def _fmt_size(self, size):
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _refresh_selected(self):
        if self._selected_peer:
            self._load_peer_files(self._selected_peer)

    def _start_download(self, peer_ip, fi):
        if not self.api.request_permission("network"):
            self.api.show_notification("Отказано", "Нет разрешения на сеть", "warning")
            return
        self._transfer_counter += 1
        tid = f"dl_{self._transfer_counter}"
        transfer = {
            "id": tid, "type": "download", "filename": fi["name"],
            "peer": self._peers.get(peer_ip, {}).get("name", peer_ip),
            "peer_ip": peer_ip, "peer_port": self._peers.get(peer_ip, {}).get("file_port", 52001),
            "progress": 0, "status": "active", "path": "",
            "checksum": fi.get("checksum", ""), "size": fi.get("size", 0),
        }
        self._transfers.insert(0, transfer)
        self._refresh_transfers()
        self._transfer_mgr.download(tid, peer_ip, transfer["peer_port"], fi["name"], transfer["checksum"])
        self.status_lbl.config(text=f"Скачивание {fi['name']}...")

    def _on_progress(self, tid, pct, downloaded, total):
        for t in self._transfers:
            if t["id"] == tid:
                t["progress"] = pct
                break
        self.after(0, self._refresh_transfers)

    def _on_complete(self, tid, success, result):
        for t in self._transfers:
            if t["id"] == tid:
                t["status"] = "done" if success else "error"
                t["progress"] = 100 if success else t["progress"]
                t["path"] = result if success else ""
                break
        self.after(0, self._refresh_transfers)
        if success:
            self.api.show_notification("Готово", f"Файл: {result}", "success")
            self.status_lbl.config(text="Загрузка завершена")
        else:
            self.api.show_notification("Ошибка", result, "error")
            self.status_lbl.config(text=f"Ошибка: {result}")

    def _refresh_transfers(self):
        for w in self.transfers_container.winfo_children():
            w.destroy()
        if not self._transfers:
            self.transfers_empty = tk.Label(self.transfers_container, text="Нет передач",
                                             font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"])
            self.transfers_empty.pack(pady=50)
            return
        for t in self._transfers:
            self._create_transfer_card(t)

    def _create_transfer_card(self, t):
        card = tk.Frame(self.transfers_container, bg=C["card"], padx=15, pady=10)
        card.pack(fill=tk.X, pady=3, padx=5)
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill=tk.X)
        icon = "⬇" if t["type"] == "download" else "⬆"
        sc = {"active": C["accent"], "done": C["success"], "error": C["error"]}.get(t["status"], C["muted"])
        st = {"active": "Загрузка...", "done": "Готово", "error": "Ошибка"}.get(t["status"], t["status"])
        tk.Label(top, text=f"{icon} {t['filename']}", font=("Segoe UI", 11, "bold"),
                 bg=C["card"], fg=C["text"]).pack(side=tk.LEFT)
        tk.Label(top, text=f"[{t['peer']}] — {st}", font=("Segoe UI", 9),
                 bg=C["card"], fg=sc).pack(side=tk.LEFT, padx=(10, 0))
        if t["status"] == "done" and t["path"]:
            def open_f(p=t["path"]):
                try:
                    if os.name == "nt":
                        os.startfile(p)
                    else:
                        import subprocess
                        subprocess.Popen(["xdg-open", p])
                except Exception:
                    pass
            tk.Button(top, text="Открыть", font=("Segoe UI", 8), bg=C["hover"],
                      fg=C["text"], activebackground=C["card"], bd=0, padx=10, pady=2,
                      cursor="hand2", command=open_f).pack(side=tk.RIGHT)
        bar_bg = tk.Frame(card, bg="#0f3460", height=6)
        bar_bg.pack(fill=tk.X, pady=(8, 0))
        bar_bg.pack_propagate(False)
        bar_fg = tk.Frame(bar_bg, bg=sc, height=6)
        bar_fg.place(relwidth=t["progress"] / 100, relheight=1)
        tk.Label(card, text=f"{t['progress']}%", font=("Segoe UI", 8),
                 bg=C["card"], fg=C["muted"], anchor="e").pack(fill=tk.X, pady=(2, 0))

    def _clear_completed(self):
        self._transfers = [t for t in self._transfers if t["status"] == "active"]
        self._refresh_transfers()

    def _open_downloads(self):
        try:
            if os.name == "nt":
                os.startfile(str(self._download_dir))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(self._download_dir)])
        except Exception:
            pass

    def _refresh_shared(self):
        for w in self.shared_container.winfo_children():
            w.destroy()
        files = []
        total = 0
        if self._shared_dir.exists():
            for f in self._shared_dir.iterdir():
                if f.is_file():
                    s = f.stat().st_size
                    total += s
                    files.append({"name": f.name, "size": s, "path": str(f)})
        self.shared_size_lbl.config(text=f"Всего: {self._fmt_size(total)} | {len(files)} файлов")
        if not files:
            tk.Label(self.shared_container, text="Нет файлов. Нажмите '+ Добавить'",
                     font=("Segoe UI", 11), bg=C["bg"], fg=C["muted"]).pack(pady=50)
            return
        for f in files:
            self._create_shared_card(f)

    def _create_shared_card(self, f):
        card = tk.Frame(self.shared_container, bg=C["card"], padx=15, pady=10)
        card.pack(fill=tk.X, pady=3, padx=5)
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill=tk.X)
        tk.Label(top, text="📄", font=("Segoe UI", 14), bg=C["card"], fg=C["accent"]).pack(side=tk.LEFT)
        tk.Label(top, text=f["name"], font=("Segoe UI", 11, "bold"),
                 bg=C["card"], fg=C["text"]).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(top, text=self._fmt_size(f["size"]), font=("Segoe UI", 9),
                 bg=C["card"], fg=C["muted"]).pack(side=tk.RIGHT)

    def _add_file(self):
        path = filedialog.askopenfilename(title="Выберите файл")
        if not path:
            return
        try:
            src = Path(path)
            dst = self._shared_dir / src.name
            if dst.exists():
                if not messagebox.askyesno("Файл существует", f"'{src.name}' уже расшарен. Заменить?"):
                    return
            shutil.copy2(str(src), str(dst))
            self._refresh_shared()
            self.api.show_notification("Добавлено", f"'{src.name}' доступен в сети", "success")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def _remove_all(self):
        if not messagebox.askyesno("Подтверждение", "Удалить все расшаренные файлы?"):
            return
        try:
            for f in self._shared_dir.iterdir():
                if f.is_file():
                    f.unlink()
            self._refresh_shared()
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def _log(self, message):
        self.api.log(f"[FileShare] {message}", "INFO")
