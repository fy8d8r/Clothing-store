"""Тесты веб-версии (Flask test client, база SQLite в памяти).  Запуск:  python -m unittest tests.test_web -v"""
import io
import shutil
import unittest

from PIL import Image

from app import create_app
from store import images
from store.db import Database


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGBA", (400, 300), (200, 40, 40, 255)).save(buf, "PNG")
    buf.seek(0)
    return buf


class WebCase(unittest.TestCase):
    def setUp(self):
        self._old_dir = images.IMAGES_DIR
        images.IMAGES_DIR = images.ROOT / "product_images_test"
        shutil.rmtree(images.IMAGES_DIR, ignore_errors=True)
        self.db = Database({"backend": "sqlite", "sqlite_path": ":memory:"})
        self.app = create_app(self.db, secret_key="test-key")
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(images.IMAGES_DIR, ignore_errors=True)
        images.IMAGES_DIR = self._old_dir

    # --- помощники ---
    def csrf(self):
        with self.c.session_transaction() as s:
            return s.setdefault("csrf", "tok123")

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf", self.csrf())
        return self.c.post(url, data=data, **kw)

    def login(self, name="admin", password="admin123"):
        return self.post("/login", {"username": name, "password": password})

    def html(self, url="/"):
        return self.c.get(url).get_data(as_text=True)

    def pid(self, name):
        return next(p["id"] for p in self.db.list_products() if p["name"] == name)


class AuthTests(WebCase):
    def test_anonymous_is_redirected(self):
        for url in ("/", "/cart", "/admin/add", "/product/1"):
            r = self.c.get(url)
            self.assertEqual((r.status_code, r.headers["Location"]), (302, "/login"), url)

    def test_login_ok_and_wrong(self):
        self.assertIn("Неверный логин", self.login(password="bad").get_data(as_text=True))
        r = self.login()
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/"))
        self.assertIn("Футболка базовая", self.html())

    def test_csrf_required(self):
        self.assertEqual(self.c.post("/login", data={"username": "admin", "password": "admin123"}).status_code, 400)
        self.login()
        self.assertEqual(self.c.post("/logout").status_code, 400)
        self.assertEqual(self.c.post("/logout", data={"csrf": "wrong"}).status_code, 400)
        self.assertEqual(self.post("/logout").status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_register_flow(self):
        r = self.post("/register", {"username": "anna", "password": "secret", "password2": "other"})
        self.assertIn("Пароли не совпадают", r.get_data(as_text=True))
        r = self.post("/register", {"username": "anna", "password": "secret", "password2": "secret"})
        self.assertEqual(r.headers["Location"], "/login")
        self.assertEqual(self.login("anna", "secret").headers["Location"], "/")
        self.post("/logout")
        r = self.post("/register", {"username": "anna", "password": "secret", "password2": "secret"})
        self.assertIn("уже существует", r.get_data(as_text=True))

    def test_login_throttle(self):
        for _ in range(5):
            self.login(password="bad")
        text = self.login().get_data(as_text=True)  # даже верный пароль временно блокируется
        self.assertIn("Слишком много", text)

    def test_security_headers(self):
        h = self.c.get("/login").headers
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])


class CatalogTests(WebCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_search_cyrillic_and_filters(self):
        t = self.html("/?q=ДЖИНС")
        self.assertIn("Джинсы классические", t)
        self.assertNotIn("Футболка базовая", t)
        self.assertIn("Найдено товаров: 2", t)
        self.assertIn("Джинсы скинни", self.html("/?category=Джинсы&size=S"))
        self.assertNotIn("Кроссовки городские", self.html("/?stock=1"))
        self.assertIn("Кроссовки городские", self.html("/"))
        self.assertIn("Цена в фильтре должна быть числом", self.html("/?pmin=abc"))
        self.assertIn("Найдено товаров: 0", self.html("/?q=zzzz"))

    def test_price_range(self):
        t = self.html("/?pmin=1000&pmax=1600")
        self.assertIn("Футболка с принтом", t)
        self.assertNotIn("Джинсы классические", t)

    def test_html_is_escaped(self):
        self.post("/admin/add", {"name": "<script>alert(1)</script>", "category": "Тест", "brand": "",
                                 "size": "M", "color": "", "price": "10", "stock": "1", "description": ""})
        t = self.html("/")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", t)
        self.assertNotIn("<script>alert(1)</script>", t)

    def test_missing_product_and_photo(self):
        self.assertEqual(self.c.get("/product/9999").status_code, 404)
        self.assertEqual(self.c.get(f"/product/{self.pid('Шарф шерстяной')}/photo").status_code, 404)
        self.assertEqual(self.c.get(f"/product/{self.pid('Шарф шерстяной')}").status_code, 200)


class CartTests(WebCase):
    def setUp(self):
        super().setUp()
        self.login("user", "user123")
        self.pid_ = self.pid("Шарф шерстяной")
        self.stock0 = self.db.get_product(self.pid_)["stock"]

    def test_add_update_remove(self):
        self.post(f"/cart/add/{self.pid_}", {"next": "/?q=Шарф"})
        self.post(f"/cart/add/{self.pid_}")
        t = self.html("/cart")
        self.assertIn("Шарф шерстяной", t)
        self.assertIn("2 580 ₽", t)
        self.post(f"/cart/update/{self.pid_}", {"action": "dec"})
        self.assertIn("1 290 ₽", self.html("/cart"))
        self.post(f"/cart/update/{self.pid_}", {"action": "remove"})
        self.assertIn("Корзина пока пуста", self.html("/cart"))

    def test_cannot_exceed_stock(self):
        for _ in range(self.stock0 + 3):
            self.post(f"/cart/add/{self.pid_}")
        t = self.html("/cart")
        self.assertIn(f'<span class="qty">{self.stock0}</span>', t)
        self.assertIn("Больше товара нет на складе", t)

    def test_out_of_stock_cannot_be_added(self):
        shoes = self.pid("Кроссовки городские")
        self.post(f"/cart/add/{shoes}")
        self.assertIn("Корзина пока пуста", self.html("/cart"))

    def test_checkout_reduces_stock(self):
        self.post(f"/cart/add/{self.pid_}")
        self.post(f"/cart/add/{self.pid_}")
        r = self.post("/cart/checkout")
        self.assertEqual(r.headers["Location"], "/")
        self.assertEqual(self.db.get_product(self.pid_)["stock"], self.stock0 - 2)
        self.assertIn("Заказ оформлен", self.html("/"))
        self.assertIn("Корзина пока пуста", self.html("/cart"))

    def test_checkout_fails_if_stock_vanished(self):
        self.post(f"/cart/add/{self.pid_}")
        self.db._execute("UPDATE products SET stock = 0 WHERE id = %s", (self.pid_,))
        self.post("/cart/checkout")
        self.assertEqual(self.db.get_product(self.pid_)["stock"], 0)
        self.assertIn("Корзина пока пуста", self.html("/cart"))  # исчезнувший остаток убран из корзины

    def test_redirect_is_local_only(self):
        r = self.post(f"/cart/add/{self.pid_}", {"next": "//evil.example/x"})
        self.assertEqual(r.headers["Location"], "/")
        r = self.post(f"/cart/add/{self.pid_}", {"next": "/?q=a"})
        self.assertEqual(r.headers["Location"], "/?q=a")


class AdminTests(WebCase):
    FORM = {"name": "Тестовая куртка", "category": "Верхняя одежда", "brand": "Brand", "size": "L",
            "color": "Красный", "price": "5 990", "stock": "4", "description": "Тёплая"}

    def test_user_has_no_admin_rights(self):
        self.login("user", "user123")
        self.assertEqual(self.c.get("/admin/add").status_code, 403)
        self.assertEqual(self.post(f"/product/{self.pid('Шарф шерстяной')}/delete").status_code, 403)
        self.assertNotIn("Удалить товар", self.html("/"))

    def test_add_with_photo_serve_and_delete(self):
        self.login()
        data = dict(self.FORM, price="5990", photo=(png_bytes(), "куртка.png"))
        r = self.post("/admin/add", data, content_type="multipart/form-data")
        self.assertEqual(r.headers["Location"], "/")
        product = next(p for p in self.db.list_products() if p["name"] == "Тестовая куртка")
        self.assertTrue(product["image_path"].startswith("product_images_test/"))
        photo = self.c.get(f"/product/{product['id']}/photo")
        self.assertEqual((photo.status_code, photo.mimetype), (200, "image/jpeg"))
        photo.close()
        self.assertIn(f"/product/{product['id']}/photo?v=", self.html("/"))
        self.assertTrue(list(images.IMAGES_DIR.glob("*.jpg")))
        self.post(f"/product/{product['id']}/delete", {"next": "/"})
        self.assertIsNone(self.db.get_product(product["id"]))
        self.assertFalse(list(images.IMAGES_DIR.glob("*.jpg")))

    def test_bad_inputs_are_rejected(self):
        self.login()
        r = self.post("/admin/add", dict(self.FORM, price="дорого"))
        self.assertIn("должны быть числами", r.get_data(as_text=True))
        r = self.post("/admin/add", dict(self.FORM, price="10", photo=(io.BytesIO(b"not an image"), "x.png")),
                      content_type="multipart/form-data")
        self.assertIn("Не удалось открыть изображение", r.get_data(as_text=True))
        r = self.post("/admin/add", dict(self.FORM, price="-5"))
        self.assertIn("не может быть отрицательной", r.get_data(as_text=True))
        self.assertFalse([p for p in self.db.list_products() if p["name"] == "Тестовая куртка"])

    def test_change_photo(self):
        self.login()
        pid = self.pid("Шарф шерстяной")
        self.post(f"/product/{pid}/photo/change", {"photo": (png_bytes(), "a.png")}, content_type="multipart/form-data")
        first = self.db.get_product(pid)["image_path"]
        self.post(f"/product/{pid}/photo/change", {"photo": (png_bytes(), "b.png")}, content_type="multipart/form-data")
        second = self.db.get_product(pid)["image_path"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(list(images.IMAGES_DIR.glob("*.jpg"))), 1)  # старое фото удалено


class ThemeTests(WebCase):
    def test_color_and_dark_mode_are_saved(self):
        self.login("user", "user123")
        self.post("/theme", {"color": "#16A34A", "next": "/"})
        self.assertIn("--accent:#16a34a", self.html("/"))
        self.assertEqual(self.db.authenticate("user", "user123")["theme_color"], "#16a34a")
        self.post("/theme", {"dark": "1", "next": "/"})
        t = self.html("/")
        self.assertIn('data-mode="dark"', t)
        self.assertIn("--accent:#16a34a", t)          # цвет сохранился при смене режима
        self.post("/theme", {"color": "javascript:1", "next": "/"})
        self.assertEqual(self.db.authenticate("user", "user123")["theme_color"], "#16a34a")

    def test_theme_is_per_user(self):
        self.login("user", "user123")
        self.post("/theme", {"color": "#dc2626", "dark": "1"})
        self.post("/logout")
        self.login()
        self.assertNotIn("--accent:#dc2626", self.html("/"))
        self.assertIn('data-mode="light"', self.html("/"))


if __name__ == "__main__":
    unittest.main()


class PriceRecalcWebTests(WebCase):
    def setUp(self):
        super().setUp()
        self.login()

    def prices(self):
        return {p["name"]: p["price"] for p in self.db.list_products()}

    def test_only_admin(self):
        self.post("/logout")
        self.login("user", "user123")
        self.assertEqual(self.c.get("/admin/prices").status_code, 403)
        self.assertEqual(self.post("/admin/prices", {"min": "1000", "total": "1500"}).status_code, 403)
        self.assertNotIn("Пересчёт цен", self.html("/"))

    def test_admin_sees_link_and_empty_form(self):
        self.assertIn("Пересчёт цен", self.html("/"))
        t = self.html("/admin/prices")
        self.assertIn('name="min"', t)
        self.assertNotIn("Будет изменено", t)

    def test_preview_shows_affected_products_and_changes_nothing(self):
        before = self.prices()
        t = self.html("/admin/prices?min=1000&total=1500")
        self.assertIn("Будет изменено товаров: 2", t)
        self.assertIn("Футболка базовая", t)
        self.assertNotIn("Шарф шерстяной", t)
        self.assertIn("1 500 ₽", t)
        self.assertEqual(self.prices(), before)

    def test_apply(self):
        self.post("/admin/prices", {"min": "1000", "total": "1500"})
        shirts = [p["price"] for p in self.db.list_products() if p["name"] == "Футболка базовая"]
        self.assertEqual(shirts, [1500, 1500])                  # обе футболки по 990 -> 1500
        self.assertEqual(self.prices()["Шарф шерстяной"], 1290)  # выше минимума — не тронут
        t = self.html("/admin/prices")
        self.assertIn("Пересчёт выполнен: изменено цен — 2", t)
        self.assertIn("1 500 ₽", self.html("/?q=Футболка базовая"))

    def test_example_from_the_task(self):
        self.db._execute("DELETE FROM products")
        for price in (100, 200, 1100):
            self.db.add_product(f"Товар {price}", "Тест", "", "M", "", price, 1)
        self.post("/admin/prices", {"min": "250", "total": "500"})
        self.assertEqual(self.prices(), {"Товар 100": 500, "Товар 200": 500, "Товар 1100": 1100})

    def test_comma_and_spaces(self):
        self.post("/admin/prices", {"min": "1 000,5", "total": "1500"})
        self.assertEqual(self.prices()["Футболка базовая"], 1500)

    def test_bad_input_changes_nothing(self):
        before = self.prices()
        for data in ({"min": "abc", "total": "500"}, {"min": "500", "total": "100"},
                     {"min": "-5", "total": "100"}, {"min": "", "total": ""}, {"min": "nan", "total": "500"}):
            t = self.post("/admin/prices", data).get_data(as_text=True)
            self.assertIn("flash error", t, data)
            self.assertEqual(self.prices(), before, data)
        self.assertIn("не может быть меньше минимальной", self.html("/admin/prices?min=500&total=100"))

    def test_csrf_required(self):
        self.assertEqual(self.c.post("/admin/prices", data={"min": "1000", "total": "1500"}).status_code, 400)
        self.assertEqual(self.prices()["Футболка базовая"], 990)
