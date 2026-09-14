import tkinter as tk
from tkinter import ttk, messagebox
import json
import socket
import threading
import time
import struct
import os
import collections
import math

from core.module_api import ModuleInterface

C = {
    "bg": "#0d0d12", "card": "#15151f", "hover": "#1c1c2b",
    "accent": "#5c7cfa", "text": "#e9ecef", "muted": "#868e96",
    "success": "#51cf66", "error": "#ff6b6b", "warning": "#ffd43b",
    "border": "#2c2c3a", "active": "#69db7c", "inactive": "#495057",
}

AUDIO_CONFIG = {
    "rate": 48000, "channels": 1, "chunk_ms": 20,
    "format_bits": 16, "opus_bitrate": 32000,
    "jitter_buffer_ms": 60, "agc_target": 0.3,
}


def chunk_size():
    return int(AUDIO_CONFIG["rate"] * AUDIO_CONFIG["channels"] * AUDIO_CONFIG["chunk_ms"] / 1000)


class OpusCodec:
    def __init__(self):
        self._enc = None
        self._dec = None
        self._avail = False
        self._frame_size = chunk_size()
        self._init()

    def _init(self):
        try:
            import opuslib  # ← ленивый импорт
            self._enc = opuslib.Encoder(fs=AUDIO_CONFIG["rate"], channels=AUDIO_CONFIG["channels"],
                                         application=opuslib.APPLICATION_AUDIO)
            self._enc.bitrate = AUDIO_CONFIG["opus_bitrate"]
            self._dec = opuslib.Decoder(fs=AUDIO_CONFIG["rate"], channels=AUDIO_CONFIG["channels"])
            self._avail = True
        except ImportError:
            self._avail = False
        except Exception:  # ← защита от отсутствия нативной DLL
            self._avail = False

    def encode(self, pcm: bytes) -> bytes:
        if not self._avail or not self._enc:
            return b"\x00" + pcm
        try:
            import opuslib  # ← ленивый импорт
            return b"\x01" + self._enc.encode(pcm, self._frame_size)
        except Exception:
            return b"\x00" + pcm

    def decode(self, data: bytes) -> bytes:
        if len(data) < 1:
            return b""
        marker = data[0:1]
        payload = data[1:]
        if marker == b"\x00" or not self._avail:
            return payload
        try:
            import opuslib  # ← ленивый импорт
            return self._dec.decode(payload, self._frame_size)
        except Exception:
            return b""

    def available(self):
        return self._avail

    def name(self):
        return "Opus" if self._avail else "PCM"


class AudioProcessor:
    def __init__(self):
        self._agc = True
        self._noise_gate = True
        self._noise_floor = 500
        self._agc_gain = 1.0
        self._smooth = 0.9

    def process_capture(self, pcm: bytes) -> bytes:
        import array
        try:
            arr = array.array("h", pcm)
        except Exception:
            return pcm
        if len(arr) == 0:
            return pcm
        rms = math.sqrt(sum(x * x for x in arr) / len(arr))
        if self._noise_gate and rms < self._noise_floor:
            return b"\x00" * len(pcm)
        if self._agc and rms > 0:
            target = 32767 * AUDIO_CONFIG["agc_target"]
            desired = min(target / rms, 10.0)
            self._agc_gain = self._agc_gain * self._smooth + desired * (1 - self._smooth)
            self._agc_gain = max(0.1, min(self._agc_gain, 10.0))
            for i in range(len(arr)):
                val = int(arr[i] * self._agc_gain)
                arr[i] = max(-32768, min(32767, val))
        return arr.tobytes()

    def process_playback(self, pcm: bytes, volume: float = 1.0) -> bytes:
        if volume == 1.0:
            return pcm
        import array
        try:
            arr = array.array("h", pcm)
        except Exception:
            return pcm
        for i in range(len(arr)):
            val = int(arr[i] * volume)
            arr[i] = max(-32768, min(32767, val))
        return arr.tobytes()


class JitterBuffer:
    def __init__(self, max_ms=200):
        self._buf = collections.deque()
        self._lock = threading.Lock()
        self._max = int(max_ms / AUDIO_CONFIG["chunk_ms"])
        self._target = int(AUDIO_CONFIG["jitter_buffer_ms"] / AUDIO_CONFIG["chunk_ms"])

    def add(self, seq: int, data: bytes, timestamp: int):
        with self._lock:
            for i, (s, d, t) in enumerate(self._buf):
                if seq == s:
                    return
                if seq < s:
                    self._buf.insert(i, (seq, data, timestamp))
                    return
            self._buf.append((seq, data, timestamp))
            while len(self._buf) > self._max:
                self._buf.popleft()

    def get(self):
        with self._lock:
            if len(self._buf) < self._target:
                return None
            if not self._buf:
                return None
            seq, data, ts = self._buf.popleft()
            return data

    def clear(self):
        with self._lock:
            self._buf.clear()


class AudioEngine:
    def __init__(self):
        self._recording = False
        self._playing = False
        self._rec_cb = None
        self._play_queue = collections.deque(maxlen=50)
        self._play_lock = threading.Lock()
        self._backend = None
        self._pa = None
        self._stream_in = None
        self._stream_out = None
        self._chunk = chunk_size()
        self._init_backend()

    def _init_backend(self):
        try:
            import pyaudio
            self._pa = pyaudio.PyAudio()
            self._backend = "pyaudio"
        except ImportError:
            self._backend = "sox" if os.name == "posix" else "stub"

    def start_recording(self, callback):
        self._recording = True
        self._rec_cb = callback
        if self._backend == "pyaudio":
            import pyaudio
            self._stream_in = self._pa.open(format=pyaudio.paInt16, channels=AUDIO_CONFIG["channels"],
                                            rate=AUDIO_CONFIG["rate"], input=True, frames_per_buffer=self._chunk)
            threading.Thread(target=self._rec_loop, daemon=True).start()
        elif self._backend == "sox":
            threading.Thread(target=self._rec_sox, daemon=True).start()

    def _rec_loop(self):
        import pyaudio
        while self._recording and self._stream_in:
            try:
                data = self._stream_in.read(self._chunk, exception_on_overflow=False)
                if self._rec_cb:
                    self._rec_cb(data)
            except Exception:
                break

    def _rec_sox(self):
        import subprocess
        try:
            cmd = ["arecord", "-f", "S16_LE", "-r", str(AUDIO_CONFIG["rate"]),
                   "-c", str(AUDIO_CONFIG["channels"]), "-t", "raw", "-"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            bpc = self._chunk * 2
            while self._recording:
                data = proc.stdout.read(bpc)
                if not data or len(data) < bpc:
                    break
                if self._rec_cb:
                    self._rec_cb(data)
            proc.terminate()
        except Exception:
            pass

    def stop_recording(self):
        self._recording = False
        if self._stream_in:
            try:
                self._stream_in.stop_stream()
                self._stream_in.close()
            except Exception:
                pass
            self._stream_in = None

    def start_playback(self):
        self._playing = True
        if self._backend == "pyaudio":
            import pyaudio
            self._stream_out = self._pa.open(format=pyaudio.paInt16, channels=AUDIO_CONFIG["channels"],
                                             rate=AUDIO_CONFIG["rate"], output=True, frames_per_buffer=self._chunk)
            threading.Thread(target=self._play_loop, daemon=True).start()
        elif self._backend == "sox":
            threading.Thread(target=self._play_sox, daemon=True).start()

    def _play_loop(self):
        silence = b"\x00" * (self._chunk * 2)
        while self._playing:
            with self._play_lock:
                data = self._play_queue.popleft() if self._play_queue else silence
            if data and self._stream_out:
                try:
                    self._stream_out.write(data)
                except Exception:
                    break

    def _play_sox(self):
        import subprocess
        try:
            cmd = ["aplay", "-f", "S16_LE", "-r", str(AUDIO_CONFIG["rate"]),
                   "-c", str(AUDIO_CONFIG["channels"]), "-t", "raw", "-"]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            bpc = self._chunk * 2
            silence = b"\x00" * bpc
            while self._playing:
                with self._play_lock:
                    data = self._play_queue.popleft() if self._play_queue else silence
                if data:
                    try:
                        proc.stdin.write(data)
                    except Exception:
                        break
            proc.stdin.close()
            proc.terminate()
        except Exception:
            pass

    def stop_playback(self):
        self._playing = False
        if self._stream_out:
            try:
                self._stream_out.stop_stream()
                self._stream_out.close()
            except Exception:
                pass
            self._stream_out = None

    def play_chunk(self, data: bytes):
        with self._play_lock:
            self._play_queue.append(data)

    def available(self):
        return self._backend != "stub"

    def backend(self):
        return self._backend

    def config_str(self):
        return f"{AUDIO_CONFIG['rate']}Hz/{AUDIO_CONFIG['channels']}ch/{AUDIO_CONFIG['chunk_ms']}ms"

    def close(self):
        self.stop_recording()
        self.stop_playback()
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass


# ─── Stealth Discovery (multicast) ───
class VoiceDiscovery:
    MCAST = "239.192.42.99"
    PORT = 54000
    INTERVAL = 5
    TIMEOUT = 18

    def __init__(self, username, on_change):
        self.username = username
        self.on_change = on_change
        self.running = False
        self.peers = {}
        self._sock = None
        self._thread = None
        self._own_ips = self._get_ips()
        self._voice_port = 54001

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

    def start(self, voice_port):
        self._voice_port = voice_port
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
            self._cleanup()
            if now - last > self.INTERVAL:
                msg = json.dumps({"type": "voice_announce", "app": "Apoloxias",
                                  "username": self.username, "voice_port": self._voice_port,
                                  "timestamp": now}).encode("utf-8")
                try:
                    self._sock.sendto(msg, (self.MCAST, self.PORT))
                except Exception:
                    pass
                last = now
            try:
                data, addr = self._sock.recvfrom(1024)
                peer_ip = addr[0]
                if peer_ip in self._own_ips or peer_ip.startswith("127."):
                    continue
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "voice_announce" and msg.get("app") == "Apoloxias":
                    old_ips = set(self.peers.keys())
                    self.peers[peer_ip] = {"name": msg.get("username", "Unknown"),
                                           "voice_port": msg.get("voice_port", 54001),
                                           "last_seen": now}
                    if set(self.peers.keys()) != old_ips:
                        self.on_change()
            except socket.timeout:
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


class VoiceNetwork:
    HEADER = "!IH"
    HEADER_SIZE = 6
    FLAG_OPUS = 0x01
    FLAG_SILENCE = 0x02

    def __init__(self, port, on_audio):
        self.port = port
        self.on_audio = on_audio
        self.running = False
        self._sock = None
        self._thread = None
        self._seq = 0
        self._seq_lock = threading.Lock()

    def start(self):
        self.running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self.port))
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    def _recv_loop(self):
        while self.running:
            try:
                data, addr = self._sock.recvfrom(2048)
                if len(data) < self.HEADER_SIZE:
                    continue
                seq, flags = struct.unpack(self.HEADER, data[:self.HEADER_SIZE])
                self.on_audio(addr[0], data[self.HEADER_SIZE:], seq, flags)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception:
                pass

    def send_to(self, ip: str, port: int, audio: bytes, is_opus: bool = False):
        if not self._sock:
            return
        with self._seq_lock:
            self._seq = (self._seq + 1) % 0xFFFFFFFF
            seq = self._seq
        flags = self.FLAG_OPUS if is_opus else 0
        packet = struct.pack(self.HEADER, seq, flags) + audio
        try:
            self._sock.sendto(packet, (ip, port))
        except Exception:
            pass


# ─── Module ───
class VoiceChatModule(ModuleInterface):
    NAME = "VoiceChat"
    VERSION = "2.0.0"
    DESCRIPTION = "Голосовой чат LAN — HQ Audio (48kHz, Opus, AGC, Jitter)"
    ICON = "icon.ico"

    def __init__(self, api):
        super().__init__(api)
        self._frame = None

    def on_load(self):
        self.api.log("VoiceChat загружен", "INFO")

    def on_unload(self):
        self.api.log("VoiceChat выгружен", "INFO")

    def on_activate(self):
        self.api.open_in_main("Голосовой чат HQ", VoiceChatFrame)

    def on_deactivate(self):
        pass


class VoiceChatFrame(tk.Frame):
    def __init__(self, parent, api):
        super().__init__(parent, bg=C["bg"])
        self.api = api
        self.audio = AudioEngine()
        self.opus = OpusCodec()
        self.processor = AudioProcessor()
        self._muted = True
        self._deafened = False
        self._jitter = {}
        self._jitter_lock = threading.Lock()
        self._voice_port = self._find_port(54001)
        self._discovery = None
        self._voice_net = None
        self._peers = {}
        self._peers_lock = threading.Lock()
        self._stats = {"sent": 0, "recv": 0, "lost": 0, "bytes_sent": 0, "bytes_recv": 0}
        self._stats_lock = threading.Lock()
        self._peer_cards = {}
        self._vol_sliders = {}
        self._build_ui()
        if not self.audio.available():
            self._show_warning()
        else:
            self._init_network()
        self._check_alive()
        self._start_stats()

    def _find_port(self, start, attempts=10):
        for port in range(start, start + attempts):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.bind(("", port))
                s.close()
                return port
            except OSError:
                continue
        return start

    def _show_warning(self):
        w = tk.Toplevel(self)
        w.title("Аудио недоступно")
        w.geometry("450x180")
        w.configure(bg=C["card"])
        w.transient(self.winfo_toplevel())
        w.grab_set()
        w.update_idletasks()
        x = (w.winfo_screenwidth() // 2) - 225
        y = (w.winfo_screenheight() // 2) - 90
        w.geometry(f"450x180+{x}+{y}")
        tk.Label(w, text="⚠ Аудио бэкенд недоступен", font=("Segoe UI", 12, "bold"),
                 bg=C["card"], fg=C["warning"]).pack(pady=15)
        tk.Label(w, text=f"Бэкенд: {self.audio.backend()}\nУстановите: pip install pyaudio\nДля сжатия: pip install opuslib",
                 font=("Segoe UI", 9), bg=C["card"], fg=C["muted"], justify="center").pack(pady=5)
        tk.Button(w, text="OK", font=("Segoe UI", 9, "bold"), bg=C["accent"], fg="white",
                  activebackground=C["hover"], bd=0, padx=30, pady=5, cursor="hand2",
                  command=w.destroy).pack(pady=10)

    def _init_network(self):
        username = self.api.get_setting("username", "User") or "User"
        self._voice_net = VoiceNetwork(self._voice_port, self._on_audio)
        self._voice_net.start()
        self._discovery = VoiceDiscovery(username, self._on_peers_changed)
        self._discovery.start(self._voice_port)
        self.api.log(f"VoiceChat UDP порт {self._voice_port}", "INFO")

    def _on_audio(self, ip, data, seq, flags):
        if self._deafened:
            return
        with self._peers_lock:
            if ip not in self._peers:
                return
            self._peers[ip]["last_packet"] = time.time()

        with self._stats_lock:
            self._stats["recv"] += 1
            self._stats["bytes_recv"] += len(data)

        is_opus = bool(flags & VoiceNetwork.FLAG_OPUS)
        pcm = self.opus.decode(data) if is_opus else data
        if not pcm:
            return

        volume = 1.0
        with self._peers_lock:
            volume = self._peers[ip].get("volume", 1.0)

        if volume > 0:
            pcm = self.processor.process_playback(pcm, volume)
            with self._jitter_lock:
                if ip not in self._jitter:
                    self._jitter[ip] = JitterBuffer()
                self._jitter[ip].add(seq, pcm, int(time.time() * 1000))

        self.after(0, lambda: self._update_activity(ip, True))

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

        with self._jitter_lock:
            for ip, jb in list(self._jitter.items()):
                while True:
                    data = jb.get()
                    if data is None:
                        break
                    self.audio.play_chunk(data)

        now = time.time()
        with self._peers_lock:
            for ip, peer in list(self._peers.items()):
                if now - peer.get("last_packet", 0) > 2:
                    self.after(0, lambda ip=ip: self._update_activity(ip, False))

        self.after(20, self._check_alive)

    def _cleanup(self):
        if self._discovery:
            self._discovery.stop()
            self._discovery = None
        if self._voice_net:
            self._voice_net.stop()
            self._voice_net = None
        self.audio.close()

    def _start_stats(self):
        def update():
            try:
                if not self.winfo_exists():
                    return
                with self._stats_lock:
                    sent = self._stats["sent"]
                    recv = self._stats["recv"]
                    lost = self._stats["lost"]
                codec = self.opus.name()
                total = sent + recv + lost
                loss = (lost / total * 100) if total > 0 else 0
                self.stats_lbl.config(text=f"Кодек: {codec} | ↑{sent} ↓{recv} | Потери: {loss:.1f}%")
            except Exception:
                pass
            self.after(1000, update)
        self.after(1000, update)

    def _build_ui(self):
        hdr = tk.Frame(self, bg=C["card"], height=60)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="◈ ГОЛОСОВОЙ ЧАТ HQ", font=("Segoe UI", 16, "bold"),
                 bg=C["card"], fg=C["accent"]).pack(side=tk.LEFT, padx=20, pady=12)
        self.status_lbl = tk.Label(hdr, text="● Ожидание", font=("Segoe UI", 9),
                                    bg=C["card"], fg=C["inactive"])
        self.status_lbl.pack(side=tk.RIGHT, padx=20, pady=12)

        main = tk.Frame(self, bg=C["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        # Left: peers
        left = tk.Frame(main, bg=C["bg"], width=300)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)
        tk.Label(left, text="УЧАСТНИКИ В СЕТИ", font=("Segoe UI", 10, "bold"),
                 bg=C["bg"], fg=C["muted"], anchor="w").pack(fill=tk.X, pady=(0, 10))
        self.peers_container = tk.Frame(left, bg=C["bg"])
        self.peers_container.pack(fill=tk.BOTH, expand=True)
        self.peers_empty = tk.Label(self.peers_container, text="Нет участников...",
                                     font=("Segoe UI", 10), bg=C["bg"], fg=C["inactive"])
        self.peers_empty.pack(pady=30)

        # Right: controls
        right = tk.Frame(main, bg=C["bg"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        controls = tk.Frame(right, bg=C["card"], padx=20, pady=20)
        controls.pack(fill=tk.X, pady=(0, 10))
        tk.Label(controls, text="УПРАВЛЕНИЕ", font=("Segoe UI", 11, "bold"),
                 bg=C["card"], fg=C["text"], anchor="w").pack(fill=tk.X)

        btn_row = tk.Frame(controls, bg=C["card"])
        btn_row.pack(fill=tk.X, pady=(15, 0))

        self.mute_btn = tk.Button(btn_row, text="🎤 Включить микрофон", font=("Segoe UI", 10, "bold"),
                                   bg=C["error"], fg="white", activebackground="#cc4444", bd=0,
                                   padx=20, pady=10, cursor="hand2", command=self._toggle_mute)
        self.mute_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.deafen_btn = tk.Button(btn_row, text="🔊 Звук вкл", font=("Segoe UI", 10),
                                     bg=C["hover"], fg=C["text"], activebackground="#0f3460",
                                     bd=0, padx=20, pady=10, cursor="hand2", command=self._toggle_deafen)
        self.deafen_btn.pack(side=tk.LEFT, padx=5)

        self.audio_info = tk.Label(controls, text=f"Бэкенд: {self.audio.backend()} | {self.audio.config_str()} | Кодек: {self.opus.name()}",
                                    font=("Segoe UI", 9), bg=C["card"], fg="#495057", anchor="w")
        self.audio_info.pack(fill=tk.X, pady=(10, 0))

        self.stats_lbl = tk.Label(controls, text="Статистика: —", font=("Segoe UI", 9),
                                   bg=C["card"], fg=C["muted"], anchor="w")
        self.stats_lbl.pack(fill=tk.X, pady=(5, 0))

        settings = tk.Frame(controls, bg=C["card"])
        settings.pack(fill=tk.X, pady=(10, 0))
        self.agc_var = tk.BooleanVar(value=True)
        tk.Checkbutton(settings, text="AGC", variable=self.agc_var, font=("Segoe UI", 9),
                       bg=C["card"], fg=C["text"], selectcolor=C["bg"], activebackground=C["card"],
                       command=self._update_settings).pack(side=tk.LEFT, padx=(0, 15))
        self.ng_var = tk.BooleanVar(value=True)
        tk.Checkbutton(settings, text="Шумоподавление", variable=self.ng_var, font=("Segoe UI", 9),
                       bg=C["card"], fg=C["text"], selectcolor=C["bg"], activebackground=C["card"],
                       command=self._update_settings).pack(side=tk.LEFT, padx=(0, 15))

        # Log
        log_frame = tk.Frame(right, bg=C["bg"])
        log_frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(log_frame, text="ЖУРНАЛ", font=("Segoe UI", 10, "bold"),
                 bg=C["bg"], fg=C["muted"], anchor="w").pack(fill=tk.X, pady=(0, 5))
        self.log_txt = tk.Text(log_frame, font=("Consolas", 9), bg="#0a0a0f", fg=C["success"],
                               height=8, state=tk.DISABLED, wrap=tk.WORD, bd=0, padx=10, pady=10)
        self.log_txt.pack(fill=tk.BOTH, expand=True)
        self._log("VoiceChat HQ инициализирован")
        self._log(f"Конфиг: {self.audio.config_str()}, Кодек: {self.opus.name()}")

    def _update_settings(self):
        self.processor._agc = self.agc_var.get()
        self.processor._noise_gate = self.ng_var.get()
        self._log(f"AGC: {'вкл' if self.processor._agc else 'выкл'}, Шумоподавление: {'вкл' if self.processor._noise_gate else 'выкл'}")

    def _refresh_peers_ui(self):
        for w in self.peers_container.winfo_children():
            w.destroy()

        current_ips = set(self._discovery.peers.keys()) if self._discovery else set()

        with self._peers_lock:
            old_ips = set(self._peers.keys())
            gone = old_ips - current_ips
            for ip in gone:
                del self._peers[ip]
                with self._jitter_lock:
                    self._jitter.pop(ip, None)

            if not current_ips:
                self.peers_empty = tk.Label(self.peers_container, text="Нет участников...",
                                             font=("Segoe UI", 10), bg=C["bg"], fg=C["inactive"])
                self.peers_empty.pack(pady=30)
                return

            for ip, peer in self._discovery.peers.items():
                if ip not in self._peers:
                    self._peers[ip] = dict(peer)
                    self._peers[ip]["volume"] = 1.0
                    self._peers[ip]["active"] = False
                    self._peers[ip]["last_packet"] = 0
                self._create_peer_card(ip, peer)

    def _create_peer_card(self, ip, peer):
        card = tk.Frame(self.peers_container, bg=C["hover"], padx=12, pady=10)
        card.pack(fill=tk.X, pady=2)
        top = tk.Frame(card, bg=C["hover"])
        top.pack(fill=tk.X)
        ind_frame = tk.Frame(top, bg=C["hover"], width=20)
        ind_frame.pack(side=tk.LEFT)
        ind_frame.pack_propagate(False)
        ind = tk.Label(ind_frame, text="●", font=("Segoe UI", 10), bg=C["hover"], fg=C["inactive"])
        ind.pack()
        with self._peers_lock:
            self._peers[ip]["indicator"] = ind
        vu_frame = tk.Frame(card, bg="#0a0a0f", height=4)
        vu_frame.pack(fill=tk.X, pady=(5, 0))
        vu_frame.pack_propagate(False)
        vu_bar = tk.Frame(vu_frame, bg=C["active"], height=4)
        vu_bar.place(relwidth=0, relheight=1)
        with self._peers_lock:
            self._peers[ip]["vu_bar"] = vu_bar
        info = tk.Frame(top, bg=C["hover"])
        info.pack(side=tk.LEFT, padx=(8, 0), fill=tk.Y)
        tk.Label(info, text=peer["name"], font=("Segoe UI", 11, "bold"), bg=C["hover"], fg=C["text"]).pack(anchor="w")
        tk.Label(info, text=f"{ip}:{peer['voice_port']}", font=("Segoe UI", 8), bg=C["hover"], fg=C["muted"]).pack(anchor="w")
        vol_frame = tk.Frame(card, bg=C["hover"])
        vol_frame.pack(fill=tk.X, pady=(8, 0))
        tk.Label(vol_frame, text="Громкость:", font=("Segoe UI", 9), bg=C["hover"], fg=C["muted"]).pack(side=tk.LEFT)
        def set_volume(val, peer_ip=ip):
            with self._peers_lock:
                if peer_ip in self._peers:
                    self._peers[peer_ip]["volume"] = float(val) / 100
        slider = tk.Scale(vol_frame, from_=0, to=200, orient=tk.HORIZONTAL, bg=C["hover"], fg=C["text"],
                          highlightthickness=0, troughcolor="#0f3460", activebackground=C["accent"],
                          showvalue=False, length=120, command=set_volume)
        slider.set(100)
        slider.pack(side=tk.LEFT, padx=(10, 0))
        self._vol_sliders[ip] = slider

    def _update_activity(self, ip, active):
        with self._peers_lock:
            if ip not in self._peers:
                return
            self._peers[ip]["active"] = active
            ind = self._peers[ip].get("indicator")
            vu = self._peers[ip].get("vu_bar")
        if ind and ind.winfo_exists():
            ind.config(fg=C["active"] if active else C["inactive"])
        if vu and vu.winfo_exists():
            vu.config(bg=C["active"] if active else "#333333")
            vu.place(relwidth=0.7 if active else 0, relheight=1)

    def _toggle_mute(self):
        if self._muted:
            if not self.audio.available():
                messagebox.showwarning("Аудио", "Бэкенд недоступен")
                return
            if not self.api.request_permission("microphone"):
                self.api.show_notification("Отказано", "Нет разрешения на микрофон", "warning")
                return
            self._muted = False
            self.mute_btn.config(text="🎤 Микрофон вкл", bg=C["success"], activebackground="#449944")
            self.status_lbl.config(text="● В эфире", fg=C["success"])
            self.audio.start_recording(self._on_captured)
            self._log("Микрофон включен")
        else:
            self._muted = True
            self.mute_btn.config(text="🎤 Включить микрофон", bg=C["error"], activebackground="#cc4444")
            self.status_lbl.config(text="● Ожидание", fg=C["inactive"])
            self.audio.stop_recording()
            self._log("Микрофон отключен")

    def _on_captured(self, data: bytes):
        if self._muted or not self._voice_net:
            return
        processed = self.processor.process_capture(data)
        if processed == b"\x00" * len(data):
            return
        encoded = self.opus.encode(processed)
        is_opus = encoded[0:1] == b"\x01"
        with self._stats_lock:
            self._stats["sent"] += 1
            self._stats["bytes_sent"] += len(encoded)

        with self._peers_lock:
            peers_snapshot = list(self._peers.items())
        for ip, peer in peers_snapshot:
            self._voice_net.send_to(ip, peer["voice_port"], encoded, is_opus)

    def _toggle_deafen(self):
        self._deafened = not self._deafened
        if self._deafened:
            self.deafen_btn.config(text="🔇 Звук выкл", bg=C["error"])
            self._log("Звук отключен")
        else:
            self.deafen_btn.config(text="🔊 Звук вкл", bg=C["hover"])
            self._log("Звук включен")

    def _log(self, message: str):
        self.log_txt.configure(state=tk.NORMAL)
        ts = time.strftime("%H:%M:%S")
        self.log_txt.insert(tk.END, f"[{ts}] {message}\n")
        self.log_txt.see(tk.END)
        self.log_txt.configure(state=tk.DISABLED)