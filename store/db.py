"""Слой данных: MySQL (основной режим) или SQLite (запасной, без сервера)."""
import math
import re
import sqlite3
import threading
from contextlib import contextmanager

from .config import load_config
from .security import hash_password, verify_password
from .theme import valid_color

SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]
MYSQL_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"


class DatabaseError(Exception):
    """Не удалось подключиться к базе или подготовить её."""


MYSQL_SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) NOT NULL UNIQUE,
        salt CHAR(32) NOT NULL,
        password_hash CHAR(64) NOT NULL,
        role ENUM('admin', 'user') NOT NULL DEFAULT 'user',
        theme_color CHAR(7) NOT NULL DEFAULT '#4f46e5',
        dark_mode TINYINT(1) NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) {MYSQL_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS categories (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(80) NOT NULL UNIQUE
    ) {MYSQL_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS products (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(150) NOT NULL,
        category_id INT NOT NULL,
        brand VARCHAR(80) NOT NULL DEFAULT '',
        size VARCHAR(10) NOT NULL,
        color VARCHAR(40) NOT NULL DEFAULT '',
        price DECIMAL(10, 2) NOT NULL,
        stock INT NOT NULL DEFAULT 0,
        description TEXT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_products_category (category_id),
        CONSTRAINT fk_products_category FOREIGN KEY (category_id) REFERENCES categories (id)
    ) {MYSQL_OPTS}""",
]

SQLITE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        salt TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
        theme_color TEXT NOT NULL DEFAULT '#4f46e5',
        dark_mode INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE
    )""",
    """CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category_id INTEGER NOT NULL REFERENCES categories (id),
        brand TEXT NOT NULL DEFAULT '',
        size TEXT NOT NULL,
        color TEXT NOT NULL DEFAULT '',
        price REAL NOT NULL,
        stock INTEGER NOT NULL DEFAULT 0,
        description TEXT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
]

# (название, категория, бренд, размер, цвет, цена, остаток, описание)
SAMPLE_PRODUCTS = [
    ("Футболка базовая", "Футболки", "Nordic", "M", "Белый", 990, 25, "Плотный хлопок, свободный крой."),
    ("Футболка базовая", "Футболки", "Nordic", "L", "Чёрный", 990, 18, "Плотный хлопок, свободный крой."),
    ("Футболка с принтом", "Футболки", "Urban Wave", "S", "Серый", 1490, 10, "Принт на груди, мягкий джерси."),
    ("Джинсы классические", "Джинсы", "DenimCo", "M", "Синий", 3990, 12, "Прямой крой, средняя посадка."),
    ("Джинсы скинни", "Джинсы", "DenimCo", "S", "Чёрный", 4290, 7, "Эластичный деним, облегающий силуэт."),
    ("Толстовка с капюшоном", "Верхняя одежда", "Urban Wave", "L", "Графит", 3490, 9, "Футер с начёсом, карман-кенгуру."),
    ("Куртка демисезонная", "Верхняя одежда", "Nordic", "XL", "Хаки", 7990, 4, "Ветрозащитная, с капюшоном."),
    ("Пуховик зимний", "Верхняя одежда", "Nordic", "M", "Тёмно-синий", 12990, 3, "Натуральный пух, до −25 °C."),
    ("Платье летнее", "Платья", "Bloom", "S", "Бежевый", 3290, 8, "Лёгкая вискоза, длина миди."),
    ("Платье вечернее", "Платья", "Bloom", "M", "Бордовый", 6990, 5, "Атлас, открытая спина."),
    ("Рубашка офисная", "Рубашки", "Classic", "L", "Голубой", 2490, 14, "Хлопок с добавлением эластана."),
    ("Шорты спортивные", "Шорты", "Active", "M", "Чёрный", 1590, 20, "Быстросохнущая ткань, карманы на молнии."),
    ("Кроссовки городские", "Обувь", "Active", "M", "Белый", 5490, 0, "Лёгкая подошва, текстильный верх."),
    ("Шарф шерстяной", "Аксессуары", "Nordic", "M", "Серый", 1290, 15, "Мериносовая шерсть, 180 см."),
]


def _sqlite_like(pattern, value):
    """LIKE без учёта регистра, в том числе для кириллицы (встроенный LIKE SQLite умеет только ASCII)."""
    if pattern is None or value is None:
        return None
    regex, i = "", 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            regex += re.escape(pattern[i + 1])
            i += 2
            continue
        regex += ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
        i += 1
    return 1 if re.fullmatch(regex, str(value), re.IGNORECASE | re.DOTALL) else 0


class Database:
    def __init__(self, config=None):
        self._lock = threading.RLock()  # веб-сервер обслуживает запросы в разных потоках
        self.cfg = config or load_config()
        self.backend = self.cfg["backend"]
        if self.backend not in ("mysql", "sqlite"):
            raise DatabaseError("В config.ini параметр backend должен быть mysql или sqlite.")
        self._load_driver()
        try:
            if self.backend == "mysql":
                self._connect_mysql()
            else:
                self._connect_sqlite()
            for statement in (MYSQL_SCHEMA if self.backend == "mysql" else SQLITE_SCHEMA):
                self._execute(statement)
            # Мягкая миграция старой базы: добавляем путь к фото без потери товаров.
            try:
                self._execute("ALTER TABLE products ADD COLUMN image_path VARCHAR(500) DEFAULT ''")
            except self._Error:
                pass
            self._seed()
        except self._Error as err:
            hint = ("Проверьте, что MySQL запущен, а host, port, user, password и database "
                    "в config.ini указаны верно. Чтобы временно работать без сервера, "
                    "поставьте backend = sqlite."
                    if self.backend == "mysql" else "Не удалось открыть файл базы SQLite.")
            raise DatabaseError(f"{err}\n\n{hint}") from err

    # ---------- подключение ----------
    def _load_driver(self):
        if self.backend == "mysql":
            try:
                import mysql.connector
                from mysql.connector import errors
            except ImportError:
                raise DatabaseError("Не установлен драйвер MySQL.\nВыполните: pip install mysql-connector-python")
            self._mysql = mysql.connector
            self._Error, self._Integrity = errors.Error, errors.IntegrityError
        else:
            self._Error, self._Integrity = sqlite3.Error, sqlite3.IntegrityError

    def _mysql_params(self):
        c = self.cfg
        return dict(host=c["host"], port=c["port"], user=c["user"], password=c["password"],
                    charset="utf8mb4", autocommit=True, connection_timeout=5)

    def _connect_mysql(self):
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.cfg["database"]):
            raise DatabaseError("Имя базы данных: только латиница, цифры и «_».")
        try:  # создаём базу, если есть право; иначе она должна уже существовать
            conn = self._mysql.connect(**self._mysql_params())
            try:
                conn.cursor().execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.cfg['database']}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            finally:
                conn.close()
        except self._Error:
            pass
        self.conn = self._mysql.connect(database=self.cfg["database"], **self._mysql_params())

    def _connect_sqlite(self):
        self.conn = sqlite3.connect(self.cfg["sqlite_path"], check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.isolation_level = None  # автокоммит
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.create_function("like", 2, _sqlite_like)

    def _query(self, sql, params=(), one=False):
        with self._lock:
            if self.backend == "sqlite":
                rows = [dict(r) for r in self.conn.execute(sql.replace("%s", "?"), params).fetchall()]
                return (rows[0] if rows else None) if one else rows
            self.conn.ping(reconnect=True, attempts=3, delay=1)
            cur = self.conn.cursor(dictionary=True)
            try:
                cur.execute(sql, params)
                return cur.fetchone() if one else cur.fetchall()
            finally:
                cur.close()

    def _execute(self, sql, params=()):
        with self._lock:
            if self.backend == "sqlite":
                cur = self.conn.execute(sql.replace("%s", "?"), params)
                return cur.lastrowid, cur.rowcount
            self.conn.ping(reconnect=True, attempts=3, delay=1)
            cur = self.conn.cursor()
            try:
                cur.execute(sql, params)
                return cur.lastrowid, cur.rowcount
            finally:
                cur.close()

    def _seed(self):
        if not self._query("SELECT 1 AS x FROM users LIMIT 1", one=True):
            self.add_user("admin", "admin123", "admin")
            self.add_user("user", "user123", "user")
        if not self._query("SELECT 1 AS x FROM products LIMIT 1", one=True):
            for item in SAMPLE_PRODUCTS:
                self.add_product(*item)

    # ---------- пользователи ----------
    def add_user(self, username, password, role="user"):
        username = username.strip()
        if len(username) < 3:
            raise ValueError("Логин должен быть не короче 3 символов")
        if len(password) < 4:
            raise ValueError("Пароль должен быть не короче 4 символов")
        if role not in ("admin", "user"):
            raise ValueError("Неизвестная роль")
        salt, pwd_hash = hash_password(password)
        try:
            self._execute(
                "INSERT INTO users (username, salt, password_hash, role) VALUES (%s, %s, %s, %s)",
                (username, salt, pwd_hash, role))
        except self._Integrity:
            raise ValueError("Такой пользователь уже существует")

    def get_user(self, user_id):
        row = self._query("SELECT id, username, role, theme_color, dark_mode FROM users WHERE id = %s",
                          (user_id,), one=True)
        if row:
            row["dark_mode"] = bool(row["dark_mode"])
        return row

    def authenticate(self, username, password):
        row = self._query("SELECT * FROM users WHERE username = %s", (username.strip(),), one=True)
        if row and verify_password(password, row["salt"], row["password_hash"]):
            row["dark_mode"] = bool(row["dark_mode"])
            return row
        return None

    def save_theme(self, user_id, color, dark_mode):
        if not valid_color(color):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        self._execute("UPDATE users SET theme_color = %s, dark_mode = %s WHERE id = %s",
                      (color.lower(), int(bool(dark_mode)), user_id))

    # ---------- товары ----------
    def _category_id(self, name):
        row = self._query("SELECT id FROM categories WHERE name = %s", (name,), one=True)
        if row:
            return row["id"]
        new_id, _ = self._execute("INSERT INTO categories (name) VALUES (%s)", (name,))
        return new_id

    def list_products(self, search="", category=None, size=None,
                      price_min=None, price_max=None, in_stock=False):
        sql = ("SELECT p.id, p.name, c.name AS category, p.brand, p.size, p.color, "
               "p.price, p.stock, p.description, p.image_path "
               "FROM products p JOIN categories c ON c.id = p.category_id WHERE 1=1")
        params = []
        term = search.strip()
        if term:
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            sql += " AND (p.name LIKE %s OR c.name LIKE %s OR p.brand LIKE %s OR p.color LIKE %s)"
            params += [like] * 4
        if category:
            sql += " AND c.name = %s"
            params.append(category)
        if size:
            sql += " AND p.size = %s"
            params.append(size)
        if price_min is not None:
            sql += " AND p.price >= %s"
            params.append(price_min)
        if price_max is not None:
            sql += " AND p.price <= %s"
            params.append(price_max)
        if in_stock:
            sql += " AND p.stock > 0"
        sql += " ORDER BY p.id"
        rows = self._query(sql, params)
        for r in rows:
            r["price"] = float(r["price"])
        return rows

    def add_product(self, name, category, brand, size, color, price, stock, description="", image_path=""):
        name, category, size = name.strip(), category.strip(), size.strip()
        if not name or not category or not size:
            raise ValueError("Заполните название, категорию и размер")
        if price < 0:
            raise ValueError("Цена не может быть отрицательной")
        if stock < 0:
            raise ValueError("Количество не может быть отрицательным")
        new_id, _ = self._execute(
            "INSERT INTO products (name, category_id, brand, size, color, price, stock, description, image_path) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (name, self._category_id(category), brand.strip(), size, color.strip(),
             price, stock, description.strip() or None, image_path or ""))
        return new_id

    def delete_product(self, product_id):
        _, count = self._execute("DELETE FROM products WHERE id = %s", (product_id,))
        # убираем категории, в которых не осталось товаров
        self._execute("DELETE FROM categories WHERE id NOT IN (SELECT DISTINCT category_id FROM products)")
        return count > 0

    def categories(self):
        return [r["name"] for r in self._query("SELECT name FROM categories ORDER BY name")]

    def sizes(self):
        found = [r["size"] for r in self._query("SELECT DISTINCT size FROM products")]
        return sorted(found, key=lambda s: (SIZE_ORDER.index(s) if s in SIZE_ORDER else 99, s))

    @contextmanager
    def _tx(self):
        """Транзакция: всё применяется целиком или не применяется вообще."""
        with self._lock:
            self._execute("BEGIN" if self.backend == "sqlite" else "START TRANSACTION")
            try:
                yield
            except BaseException:
                self._execute("ROLLBACK")
                raise
            else:
                self._execute("COMMIT")

    def get_product(self, product_id):
        row = self._query(
            "SELECT p.id, p.name, c.name AS category, p.brand, p.size, p.color, "
            "p.price, p.stock, p.description, p.image_path "
            "FROM products p JOIN categories c ON c.id = p.category_id WHERE p.id = %s",
            (product_id,), one=True)
        if row:
            row["price"] = float(row["price"])
        return row

    def set_product_image(self, product_id, image_path):
        """Меняет фото товара, возвращает путь к прежнему файлу (чтобы его удалить)."""
        row = self._query("SELECT image_path FROM products WHERE id = %s", (product_id,), one=True)
        self._execute("UPDATE products SET image_path = %s WHERE id = %s", (image_path or "", product_id))
        return row["image_path"] if row else None

    def purchase(self, items):
        """Списывает {id товара: количество} со склада одной транзакцией (всё или ничего)."""
        with self._tx():
            for pid, qty in items.items():
                _, changed = self._execute(
                    "UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s",
                    (qty, pid, qty))
                if changed == 0:
                    row = self._query("SELECT name FROM products WHERE id = %s", (pid,), one=True)
                    raise ValueError("Недостаточно на складе: " + (row["name"] if row else "товар удалён"))

    MAX_PRICE = 99_999_999.99

    @classmethod
    def _check_price(cls, value, label):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label}: введите число")
        if value < 0:
            raise ValueError(f"{label} не может быть отрицательной")
        if value > cls.MAX_PRICE:
            raise ValueError(f"{label} слишком большая")

    def products_below(self, min_price):
        """Товары, у которых цена строго меньше min_price (кандидаты на пересчёт)."""
        self._check_price(min_price, "Минимальная цена")
        rows = self._query("SELECT id, name, price FROM products WHERE price < %s ORDER BY id", (min_price,))
        for row in rows:
            row["price"] = float(row["price"])
        return rows

    def raise_low_prices(self, min_price, new_price):
        """Пересчёт цен: у всех товаров с ценой МЕНЬШЕ min_price цена становится равной new_price.
        Остальные товары не меняются. Возвращает число изменённых товаров."""
        self._check_price(min_price, "Минимальная цена")
        self._check_price(new_price, "Итоговая цена")
        if new_price < min_price:
            raise ValueError("Итоговая цена не может быть меньше минимальной")
        _, changed = self._execute("UPDATE products SET price = %s WHERE price < %s", (new_price, min_price))
        return changed

    def change_stock(self, product_id, delta):
        """Безопасно меняет остаток товара; отрицательный остаток запрещён."""
        row = self._query("SELECT stock FROM products WHERE id = %s", (product_id,), one=True)
        if not row or int(row["stock"]) + int(delta) < 0:
            return False
        self._execute("UPDATE products SET stock = stock + %s WHERE id = %s", (int(delta), product_id))
        return True
