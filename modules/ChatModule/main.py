import tkinter as tk
from tkinter import scrolledtext, messagebox
from core.module_api import ModuleInterface
import socket
import threading
import secrets
import hashlib
import struct
import time
import json
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


C = {
    "bg": "#0d0d12", "card": "#15151f", "hover": "#1c1c2b",
    "accent": "#5c7cfa", "text": "#e9ecef", "muted": "#868e96",
    "success": "#51cf66", "error": "#ff6b6b", "border": "#2c2c3a",
    "my_msg": "#4dabf7", "peer_msg": "#51cf66",
    "warning": "#ffd43b",  # ← добавлено
}

# ─── Post-Quantum LWE KEM (simplified, n=128) ───
LWE_N = 128
LWE_Q = 4096
LWE_BOUND = 2


def _sample_small(n: int, bound: int = LWE_BOUND) -> List[int]:
    return [secrets.randbelow(2 * bound + 1) - bound for _ in range(n)]


def _gen_matrix(seed: bytes, n: int = LWE_N, q: int = LWE_Q) -> List[List[int]]:
    A = []
    for i in range(n):
        row = []
        for j in range(n):
            h = hashlib.sha3_256(seed + i.to_bytes(2, "little") + j.to_bytes(2, "little")).digest()
            row.append(int.from_bytes(h[:2], "little") % q)
        A.append(row)
    return A


def _mat_vec_mul(A, v, q=LWE_Q):
    return [sum(A[i][j] * v[j] for j in range(len(v))) % q for i in range(len(A))]


def _vec_vec_mul(a, b, q=LWE_Q):
    return sum(a[i] * b[i] for i in range(len(a))) % q


def _encode_message(m_bits, q=LWE_Q):
    half = q // 2
    return sum(m_bits[i] * half for i in range(len(m_bits))) % q


def _decode_message(value, n=LWE_N, q=LWE_Q):
    half = q // 2
    val = value % q
    m = []
    for i in range(n):
        bit_val = (val - sum(m[j] * half for j in range(i))) % q
        dist_0 = min(bit_val, q - bit_val)
        dist_1 = min(abs(bit_val - half), q - abs(bit_val - half))
        m.append(0 if dist_0 < dist_1 else 1)
    return m


def _bits_to_bytes(bits):
    bs = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(8):
            if i + j < len(bits):
                byte |= (bits[i + j] << j)
        bs.append(byte)
    return bytes(bs)


def _bytes_to_bits(data, n=LWE_N):
    bits = []
    for byte in data:
        for j in range(8):
            bits.append((byte >> j) & 1)
            if len(bits) >= n:
                return bits[:n]
    while len(bits) < n:
        bits.append(0)
    return bits[:n]


class PQLWEKEM:
    def __init__(self):
        self.n = LWE_N
        self.q = LWE_Q

    def keygen(self) -> Tuple[bytes, bytes]:
        seed = secrets.token_bytes(32)
        A = _gen_matrix(seed)
        s = _sample_small(self.n)
        e = _sample_small(self.n)
        b = [(x + y) % self.q for x, y in zip(_mat_vec_mul(A, s), e)]
        pub = seed + struct.pack(f"<{self.n}H", *b)
        sec = struct.pack(f"<{self.n}h", *s)
        return pub, sec

    def encaps(self, pub: bytes) -> Tuple[bytes, bytes]:
        seed = pub[:32]
        b = list(struct.unpack(f"<{self.n}H", pub[32:]))
        A = _gen_matrix(seed)
        m_bits = [secrets.randbelow(2) for _ in range(self.n)]
        s_prime = _sample_small(self.n)
        e_prime = _sample_small(self.n)
        e_dprime = secrets.randbelow(2 * LWE_BOUND + 1) - LWE_BOUND
        u = [(x + y) % self.q for x, y in zip(_mat_vec_mul(A, s_prime), e_prime)]
        v = (_vec_vec_mul(b, s_prime) + e_dprime + _encode_message(m_bits)) % self.q
        ct = struct.pack(f"<{self.n}H", *u) + struct.pack("<H", v)
        ss = hashlib.sha3_256(_bits_to_bytes(m_bits)).digest()
        return ct, ss

    def decaps(self, sec: bytes, ct: bytes) -> bytes:
        s = list(struct.unpack(f"<{self.n}h", sec))
        u = list(struct.unpack(f"<{self.n}H", ct[:2 * self.n]))
        v = struct.unpack("<H", ct[2 * self.n:])[0]
        val = (v - _vec_vec_mul(u, s)) % self.q
        m_bits = _decode_message(val)
        return hashlib.sha3_256(_bits_to_bytes(m_bits)).digest()


class HybridCrypto:
    BROADCAST_KEY = b"ApoloxiasChat_v2"

    def __init__(self):
        self.x_priv = X25519PrivateKey.generate()
        self.x_pub = self.x_priv.public_key()
        self.pq_kem = PQLWEKEM()
        self.pq_pub, self.pq_sec = self.pq_kem.keygen()
        self._session_keys: Dict[str, bytes] = {}

    def get_identity(self) -> bytes:
        return (self.x_pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                + self.pq_pub)

    def derive_key(self, peer_x_pub_bytes: bytes, peer_pq_pub: bytes, pq_ct: bytes) -> bytes:
        peer_x_pub = X25519PublicKey.from_public_bytes(peer_x_pub_bytes)
        x_shared = self.x_priv.exchange(peer_x_pub)
        pq_shared = self.pq_kem.decaps(self.pq_sec, pq_ct)
        hkdf = HKDF(algorithm=hashes.SHA3_256(), length=32, salt=None, info=b"ApoloxiasChatHybrid_v2")
        return hkdf.derive(x_shared + pq_shared)

    def encaps_to_peer(self, peer_pq_pub: bytes) -> Tuple[bytes, bytes]:
        return self.pq_kem.encaps(peer_pq_pub)

    def compute_sas(self, peer_x_pub: bytes, peer_pq_pub: bytes) -> str:
        combined = (self.x_pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                    + self.pq_pub + peer_x_pub + peer_pq_pub)
        h = hashlib.sha3_256(combined).digest()
        return f"{int.from_bytes(h[:4], 'big') % 1000000:06d}"

    def encrypt_packet(self, key: bytes, plaintext: bytes) -> bytes:
        aes = AESGCM(key)
        nonce = secrets.token_bytes(12)
        ciphertext = aes.encrypt(nonce, plaintext, None)
        pad_len = secrets.randbelow(64)
        padding = secrets.token_bytes(pad_len)
        return bytes([pad_len]) + padding + nonce + ciphertext

    def decrypt_packet(self, key: bytes, data: bytes) -> Optional[bytes]:
        try:
            pad_len = data[0]
            offset = 1 + pad_len
            nonce = data[offset:offset + 12]
            ciphertext = data[offset + 12:]
            aes = AESGCM(key)
            return aes.decrypt(nonce, ciphertext, None)
        except Exception:
            return None

    def encrypt_broadcast(self, plaintext: bytes) -> bytes:
        aes = AESGCM(hashlib.sha3_256(self.BROADCAST_KEY).digest()[:32])
        nonce = secrets.token_bytes(12)
        return nonce + aes.encrypt(nonce, plaintext, None)

    def decrypt_broadcast(self, data: bytes) -> Optional[bytes]:
        try:
            nonce = data[:12]
            ciphertext = data[12:]
            aes = AESGCM(hashlib.sha3_256(self.BROADCAST_KEY).digest()[:32])
            return aes.decrypt(nonce, ciphertext, None)
        except Exception:
            return None


# ─── Network ───
MCAST_GROUP = "239.192.42.99"
DISC_PORT = 50000
CHAT_PORT = 50001
BROADCAST_INTERVAL = 8.0


def _get_my_ips() -> List[str]:
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips if ips else ["127.0.0.1"]


class ChatNetwork:
    def __init__(self, crypto: HybridCrypto, nickname: str, on_peer, on_msg, on_log):
        self.crypto = crypto
        self.nickname = nickname
        self.on_peer = on_peer
        self.on_msg = on_msg
        self.on_log = on_log
        self.peers: Dict[str, dict] = {}
        self.connections: Dict[str, socket.socket] = {}
        self.lock = threading.Lock()
        self.running = False
        self.disc_sock: Optional[socket.socket] = None
        self.listen_sock: Optional[socket.socket] = None
        self.my_ips = _get_my_ips()
        self.on_log(f"IPs: {', '.join(self.my_ips)}", "INFO")

    def start(self):
        self.running = True
        self.disc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.disc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.disc_sock.bind(("", DISC_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(MCAST_GROUP), socket.INADDR_ANY)
        self.disc_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.disc_sock.settimeout(1.0)

        self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen_sock.bind(("", CHAT_PORT))
        self.listen_sock.listen(5)
        self.listen_sock.settimeout(1.0)

        threading.Thread(target=self._disc_loop, daemon=True).start()
        threading.Thread(target=self._disc_listen, daemon=True).start()
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.on_log("Сеть запущена", "INFO")

    def stop(self):
        self.running = False
        for s in [self.disc_sock, self.listen_sock]:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        for conn in self.connections.values():
            try:
                conn.close()
            except Exception:
                pass

    def _is_me(self, ip: str) -> bool:
        return ip in self.my_ips or ip.startswith("127.")

    def _disc_loop(self):
        while self.running:
            try:
                x_pub = self.crypto.x_pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                beacon = json.dumps({"nick": self.nickname, "x_pub": x_pub.hex(),
                                     "pq_pub": self.crypto.pq_pub.hex(), "port": CHAT_PORT}).encode("utf-8")
                encrypted = self.crypto.encrypt_broadcast(beacon)
                self.disc_sock.sendto(encrypted, (MCAST_GROUP, DISC_PORT))
                time.sleep(BROADCAST_INTERVAL)
            except Exception:
                pass

    def _disc_listen(self):
        while self.running:
            try:
                data, addr = self.disc_sock.recvfrom(2048)
                peer_ip = addr[0]
                if self._is_me(peer_ip):
                    continue
                decrypted = self.crypto.decrypt_broadcast(data)
                if not decrypted:
                    continue
                beacon = json.loads(decrypted.decode("utf-8"))
                peer_id = f"{peer_ip}:{beacon['port']}"
                with self.lock:
                    is_new = peer_id not in self.peers
                    self.peers[peer_id] = {
                        "ip": peer_ip, "port": beacon["port"], "nickname": beacon["nick"],
                        "x_pub": bytes.fromhex(beacon["x_pub"]),
                        "pq_pub": bytes.fromhex(beacon["pq_pub"]),
                        "last_seen": time.time(),
                    }
                if is_new:
                    self.on_log(f"Пир: {beacon['nick']} ({peer_ip})", "INFO")
                    self.on_peer(peer_id, beacon["nick"])
            except socket.timeout:
                continue
            except Exception:
                pass

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.listen_sock.accept()
                peer_ip = addr[0]
                if self._is_me(peer_ip):
                    conn.close()
                    continue
                threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                pass

    def _handle_conn(self, conn, addr):
        peer_ip = addr[0]
        peer_id = None
        with self.lock:
            for pid, p in self.peers.items():
                if p["ip"] == peer_ip:
                    peer_id = pid
                    break
        if not peer_id:
            peer_id = f"{peer_ip}:{CHAT_PORT}"
        try:
            conn.settimeout(30.0)
            peer_x_pub = self._recv_all(conn, 32)
            peer_pq_pub = self._recv_all(conn, 32 + LWE_N * 2)
            pq_ct = self._recv_all(conn, LWE_N * 2 + 2)
            if not all([peer_x_pub, peer_pq_pub, pq_ct]):
                return
            aes_key = self.crypto.derive_key(peer_x_pub, peer_pq_pub, pq_ct)
            with self.lock:
                self.crypto._session_keys[peer_id] = aes_key
                self.connections[peer_id] = conn
            while self.running:
                lb = self._recv_all(conn, 4)
                if not lb:
                    break
                length = struct.unpack("<I", lb)[0]
                packet = self._recv_all(conn, length)
                if not packet:
                    break
                plaintext = self.crypto.decrypt_packet(aes_key, packet)
                if plaintext:
                    msg = json.loads(plaintext.decode("utf-8"))
                    self.on_msg(peer_id, msg.get("text", ""))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            with self.lock:
                self.connections.pop(peer_id, None)

    def connect_to_peer(self, peer_id: str) -> bool:
        with self.lock:
            if peer_id not in self.peers:
                return False
            peer = self.peers[peer_id]
            if peer_id in self.connections:
                return True
        try:
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.connect((peer["ip"], peer["port"]))
            x_pub = self.crypto.x_pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            conn.sendall(x_pub)
            conn.sendall(self.crypto.pq_pub)
            pq_ct, aes_key = self.crypto.encaps_to_peer(peer["pq_pub"])
            conn.sendall(pq_ct)
            with self.lock:
                self.crypto._session_keys[peer_id] = aes_key
                self.connections[peer_id] = conn
            sas = self.crypto.compute_sas(peer["x_pub"], peer["pq_pub"])
            self.on_log(f"Подключено. SAS: {sas}", "INFO")
            return True
        except Exception as e:
            self.on_log(f"Connect error: {e}", "ERROR")
            return False

    def send_message(self, peer_id: str, text: str) -> bool:
        with self.lock:
            if peer_id not in self.connections:
                return False
            conn = self.connections[peer_id]
            aes_key = self.crypto._session_keys.get(peer_id)
            if not aes_key:
                return False
        try:
            payload = json.dumps({"text": text}).encode("utf-8")
            packet = self.crypto.encrypt_packet(aes_key, payload)
            length = struct.pack("<I", len(packet))
            conn.sendall(length + packet)
            return True
        except Exception:
            return False

    def _recv_all(self, sock, n: int) -> Optional[bytes]:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data


# ─── UI ───
class ChatModule(ModuleInterface):
    NAME = "ChatModule"
    VERSION = "2.0.0"
    DESCRIPTION = "Защищенный LAN-чат с постквантовым шифрованием"
    ICON = "icon.ico"

    def __init__(self, api):
        super().__init__(api)
        self._frame = None
        self._network = None

    def on_load(self):
        self.api.log("ChatModule загружен", "INFO")

    def on_unload(self):
        if self._network:
            self._network.stop()
        self.api.log("ChatModule выгружен", "INFO")

    def on_activate(self):
        self.api.open_in_main("Secure Chat", ChatFrame)

    def on_deactivate(self):
        pass


class ChatFrame(tk.Frame):
    def __init__(self, parent, api):
        super().__init__(parent, bg=C["bg"])
        self.api = api
        self.crypto = HybridCrypto()
        self.network = None
        self.current_peer = None
        self._peer_map: Dict[int, str] = {}
        self.nickname = ""
        self._build_ui()
        self._load_nickname()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["card"], height=60)
        hdr.pack(fill=tk.X, side=tk.TOP)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="◈ Secure LAN Chat", font=("Segoe UI", 16, "bold"),
                 bg=C["card"], fg=C["accent"]).pack(side=tk.LEFT, padx=20, pady=12)
        self.info_lbl = tk.Label(hdr, text="Инициализация...", font=("Segoe UI", 9),
                                  bg=C["card"], fg=C["muted"])
        self.info_lbl.pack(side=tk.RIGHT, padx=20, pady=12)

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        # Peers
        peers = tk.LabelFrame(body, text="Пиры в сети", bg=C["bg"], fg=C["muted"],
                              font=("Segoe UI", 10), bd=1, relief=tk.FLAT)
        peers.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        self.peers_lb = tk.Listbox(peers, bg=C["card"], fg=C["text"],
                                   selectbackground=C["accent"], selectforeground="white",
                                   font=("Segoe UI", 10), width=26, height=20, bd=0)
        self.peers_lb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.peers_lb.bind("<<ListboxSelect>>", self._on_peer_select)

        # Chat area
        chat = tk.Frame(body, bg=C["bg"])
        chat.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.chat_log = scrolledtext.ScrolledText(chat, bg="#0a0a0f", fg=C["text"],
                                                   font=("Consolas", 10), state=tk.DISABLED,
                                                   wrap=tk.WORD, bd=0, padx=10, pady=10)
        self.chat_log.pack(fill=tk.BOTH, expand=True)
        self.chat_log.tag_config("me", foreground=C["my_msg"])
        self.chat_log.tag_config("peer", foreground=C["peer_msg"])
        self.chat_log.tag_config("sys", foreground=C["muted"])

        inp = tk.Frame(chat, bg=C["bg"])
        inp.pack(fill=tk.X, pady=(10, 0))
        self.msg_entry = tk.Entry(inp, font=("Segoe UI", 11), bg=C["card"], fg=C["text"],
                                  insertbackground=C["accent"], bd=0, relief=tk.FLAT,
                                  highlightthickness=1, highlightcolor=C["accent"],
                                  highlightbackground=C["border"])
        self.msg_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self.msg_entry.bind("<Return>", lambda e: self._send())
        tk.Button(inp, text="➤", font=("Segoe UI", 12), bg=C["accent"], fg="white",
                  activebackground=C["hover"], bd=0, padx=15, cursor="hand2",
                  command=self._send).pack(side=tk.RIGHT, padx=(10, 0))

        # SAS
        self.sas_lbl = tk.Label(self, text="", font=("Consolas", 14, "bold"),
                                bg=C["bg"], fg=C["warning"])
        self.sas_lbl.pack(fill=tk.X, padx=15, pady=5)

        # Log
        self.log_txt = tk.Text(self, font=("Consolas", 8), bg="#0a0a0f",
                                fg=C["success"], height=3, state=tk.DISABLED, bd=0)
        self.log_txt.pack(fill=tk.X, padx=15, pady=5)

    def _load_nickname(self):
        nick = self.api.vault_get("nickname", "")
        if not nick:
            nick = self.api.get_setting("username", "User")
            self.api.vault_set("nickname", nick)
        self.nickname = nick
        self._init_network()

    def _init_network(self):
        self.network = ChatNetwork(self.crypto, self.nickname,
                                   on_peer=self._on_peer_found,
                                   on_msg=self._on_message,
                                   on_log=self._on_log)
        self.network.start()
        self._log(f"ID: {self.crypto.get_identity()[:12].hex()}...")
        self._log("Ожидание пиров...")

    def _on_peer_found(self, peer_id, nickname):
        self.after(0, lambda: self._add_peer(peer_id, nickname))

    def _add_peer(self, peer_id, nickname):
        display = f"{nickname} ({peer_id.split(':')[0]})"
        self.peers_lb.insert(tk.END, display)
        self._peer_map[self.peers_lb.size() - 1] = peer_id
        self._log(f"Новый пир: {nickname}")

    def _on_peer_select(self, event):
        sel = self.peers_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        peer_id = self._peer_map.get(idx)
        if peer_id:
            self.current_peer = peer_id
            peer = self.network.peers.get(peer_id)
            if peer:
                self.info_lbl.config(text=f"Выбран: {peer['nickname']}")
                sas = self.crypto.compute_sas(peer["x_pub"], peer["pq_pub"])
                self.sas_lbl.config(text=f"🔐 SAS: {sas}  (сверьте с собеседником)")
                if peer_id not in self.network.connections:
                    threading.Thread(target=self.network.connect_to_peer, args=(peer_id,), daemon=True).start()

    def _on_message(self, peer_id, text):
        self.after(0, lambda: self._append_chat(peer_id, text, True))

    def _on_log(self, message, level="INFO"):
        self.after(0, lambda: self._log(message))

    def _send(self):
        if not self.current_peer:
            self._log("Выберите пира")
            return
        text = self.msg_entry.get().strip()
        if not text:
            return
        if self.network.send_message(self.current_peer, text):
            self._append_chat(self.current_peer, text, False)
            self.msg_entry.delete(0, tk.END)
        else:
            self._log("Ошибка отправки")

    def _append_chat(self, peer_id, text, incoming):
        peer = self.network.peers.get(peer_id) if self.network else None
        nick = peer["nickname"] if peer else peer_id
        prefix = f"[{nick}]" if incoming else f"[Вы -> {nick}]"
        tag = "peer" if incoming else "me"
        self.chat_log.configure(state=tk.NORMAL)
        self.chat_log.insert(tk.END, f"{prefix} {text}\n", tag)
        self.chat_log.see(tk.END)
        self.chat_log.configure(state=tk.DISABLED)

    def _log(self, message):
        self.log_txt.configure(state=tk.NORMAL)
        self.log_txt.insert(tk.END, f"> {message}\n")
        self.log_txt.see(tk.END)
        self.log_txt.configure(state=tk.DISABLED)