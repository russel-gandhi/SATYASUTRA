"""Run the Telegram bot (long polling). The API must be running: python scripts/serve.py"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.bot import TelegramBot            # noqa: E402
from satyasutra.config import load_config         # noqa: E402
from satyasutra.envfile import load_env_file      # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)      # httpx logs full URLs, and the token is in the path
    load_env_file(ROOT / ".env")
    TelegramBot(load_config()).run()
