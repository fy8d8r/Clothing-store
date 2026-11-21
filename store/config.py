"""Настройки: config.ini или переменные окружения SHOP_DB_*.
Если config.ini нет, приложение работает на SQLite — без установки сервера."""
import configparser
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "backend": "sqlite",
    "sqlite_path": str(ROOT / "shop.db"),
    "host": "localhost",
    "port": "3306",
    "user": "root",
    "password": "",
    "database": "clothing_store",
}


def load_config():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / "config.ini", encoding="utf-8")
    cfg = dict(DEFAULTS)
    for section in ("app", "mysql"):
        if parser.has_section(section):
            cfg.update(parser[section])
    for key in DEFAULTS:
        value = os.environ.get("SHOP_DB_" + key.upper())
        if value is not None:
            cfg[key] = value
    cfg["backend"] = cfg["backend"].strip().lower()
    cfg["port"] = int(cfg["port"])
    return cfg