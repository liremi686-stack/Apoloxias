from pathlib import Path
import sys

def _resolve_base_dir() -> Path:
    """
    Работает для:
      - обычного запуска python apoloxias.py
      - Nuitka standalone / onefile
      - PyInstaller / cx_Freeze
    """
    # 1. Nuitka / PyInstaller: запущен как .exe
    main_path = Path(sys.argv[0]).resolve()
    if main_path.suffix.lower() == ".exe":
        return main_path.parent

    # 2. PyInstaller и аналоги (если exe запущен через загрузчик)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent

    # 3. Обычный запуск .py
    return Path(__file__).parent.parent


BASE_DIR = _resolve_base_dir()

APP_NAME = "Apoloxias"
APP_VERSION = "2.0.0"
APP_AUTHOR = "Apoloxias Team"

DATA_DIR = BASE_DIR / "data"
MODULES_DIR = BASE_DIR / "modules"
LOG_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
MODULES_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Stealth network config
STEALTH_MODE = True
NETWORK_TIMEOUT = 15
MAX_DISCOVERY_INTERVAL = 10
MIN_DISCOVERY_INTERVAL = 30
USE_MULTICAST = True
MULTICAST_GROUP = "239.192.42.99"
DISCOVERY_PORT_RANGE = (51000, 52000)

# Crypto
AES_KEY_SIZE = 32
SALT_SIZE = 32
PBKDF2_ITERATIONS = 800000

# UI
UI_COLORS = {
    "bg": "#0d0d12",
    "bg_card": "#15151f",
    "bg_hover": "#1c1c2b",
    "accent": "#5c7cfa",
    "accent_hover": "#748ffc",
    "success": "#51cf66",
    "warning": "#ffd43b",
    "error": "#ff6b6b",
    "text": "#e9ecef",
    "text_muted": "#868e96",
    "border": "#2c2c3a",
}

MODULE_PERMISSIONS = [
    "network", "filesystem", "clipboard", "notifications", "camera", "microphone"
]