import socket
import struct
import random
import time
import threading
from typing import Optional, Callable, Dict, List


class StealthSocket:
    """Обёртка над UDP сокетом с минимизацией следа."""

    def __init__(self, port: int = 0, multicast: bool = True):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if multicast:
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
        self._sock.bind(("", port))
        self._sock.settimeout(2.0)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def send(self, data: bytes, addr: tuple):
        try:
            self._sock.sendto(data, addr)
        except Exception:
            pass

    def recv(self, size: int = 2048) -> Optional[tuple]:
        try:
            return self._sock.recvfrom(size)
        except socket.timeout:
            return None
        except Exception:
            return None

    def close(self):
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass

    def join_multicast(self, group: str):
        mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)


class StealthDiscovery:
    """Обнаружение пиров через multicast (меньше шума в сети чем broadcast)."""

    def __init__(self, 
                 multicast_group: str,
                 port: int,
                 identity: bytes,
                 on_peer_found: Callable,
                 on_peer_lost: Callable,
                 interval: float = 10.0,
                 timeout: float = 30.0):
        self._group = multicast_group
        self._port = port
        self._identity = identity
        self._on_peer_found = on_peer_found
        self._on_peer_lost = on_peer_lost
        self._interval = interval
        self._timeout = timeout
        self._peers: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._running = False
        self._sock: Optional[StealthSocket] = None
        self._thread: Optional[threading.Thread] = None
        self._own_ips = self._get_own_ips()

    def _get_own_ips(self) -> List[str]:
        ips = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
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

    def start(self):
        self._running = True
        self._sock = StealthSocket(self._port, multicast=True)
        self._sock.join_multicast(self._group)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None

    def _loop(self):
        last_announce = 0
        while self._running:
            now = time.time()
            if now - last_announce > self._interval:
                self._announce()
                last_announce = now
            self._listen()
            self._cleanup()
            time.sleep(0.5)

    def _announce(self):
        payload = self._identity + struct.pack("!d", time.time())
        self._sock.send(payload, (self._group, self._port))

    def _listen(self):
        now = time.time()
        result = self._sock.recv(2048)
        if not result:
            return
        data, addr = result
        ip = addr[0]
        if ip in self._own_ips or ip.startswith("127."):
            return
        if len(data) < 24:
            return
        peer_id = data[:16]
        timestamp = struct.unpack("!d", data[16:24])[0]
        peer_key = peer_id.hex()
        with self._lock:
            is_new = peer_key not in self._peers
            self._peers[peer_key] = {
                "id": peer_id,
                "ip": ip,
                "port": addr[1],
                "last_seen": now,
                "first_seen": self._peers.get(peer_key, {}).get("first_seen", now),
            }
        if is_new:
            self._on_peer_found(peer_key, self._peers[peer_key])

    def _cleanup(self):
        now = time.time()
        stale = []
        with self._lock:
            for key, peer in list(self._peers.items()):
                if now - peer["last_seen"] > self._timeout:
                    stale.append(key)
            for key in stale:
                peer = self._peers.pop(key)
                self._on_peer_lost(key, peer)

    def get_peers(self) -> List[dict]:
        with self._lock:
            return list(self._peers.values())


class StealthTCP:
    """TCP соединение с ephemeral портом и минимальным TTL."""

    def __init__(self, local_port: int = 0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", local_port))
        self._sock.settimeout(15)

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def listen(self, backlog: int = 5):
        self._sock.listen(backlog)

    def accept(self):
        return self._sock.accept()

    def connect(self, addr: tuple):
        self._sock.connect(addr)

    def send(self, data: bytes):
        self._sock.sendall(data)

    def recv(self, size: int) -> bytes:
        return self._sock.recv(size)

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
