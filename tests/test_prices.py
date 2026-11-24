"""Тесты пересчёта цен.  Запуск:  python -m unittest tests.test_prices -v"""
import unittest

from store.db import Database


class PriceRecalcTests(unittest.TestCase):
    def setUp(self):
        self.db = Database({"backend": "sqlite", "sqlite_path": ":memory:"})
        self.db._execute("DELETE FROM products")
        self.ids = {price: self.db.add_product(f"Товар {price}", "Тест", "", "M", "", price, 1)
                    for price in (100, 200, 250, 1100)}

    def prices(self):
        return {p["name"]: p["price"] for p in self.db.list_products()}

    def test_example_from_the_task(self):
        """min = 250, итог = 500: цены 100 и 200 становятся 500, 1100 остаётся."""
        changed = self.db.raise_low_prices(250, 500)
        self.assertEqual(changed, 2)
        self.assertEqual(self.prices(), {"Товар 100": 500, "Товар 200": 500,
                                         "Товар 250": 250, "Товар 1100": 1100})

    def test_price_equal_to_minimum_is_not_changed(self):
        self.db.raise_low_prices(250, 500)
        self.assertEqual(self.prices()["Товар 250"], 250)  # «меньше», а не «меньше или равно»

    def test_second_run_changes_nothing(self):
        self.db.raise_low_prices(250, 500)
        self.assertEqual(self.db.raise_low_prices(250, 500), 0)

    def test_nothing_below_minimum(self):
        self.assertEqual(self.db.raise_low_prices(50, 500), 0)
        self.assertEqual(self.prices()["Товар 100"], 100)

    def test_preview_lists_only_affected_products(self):
        below = self.db.products_below(250)
        self.assertEqual([p["name"] for p in below], ["Товар 100", "Товар 200"])
        self.assertEqual(self.prices()["Товар 100"], 100)  # предпросмотр ничего не меняет

    def test_validation(self):
        for args in ((250, 100), (-1, 500), (250, -5), (float("nan"), 500), (250, float("inf")),
                     (250, 10 ** 12), ("250", 500)):
            with self.assertRaises(ValueError, msg=str(args)):
                self.db.raise_low_prices(*args)
        self.assertEqual(self.prices()["Товар 100"], 100)  # при ошибке цены не тронуты

    def test_decimal_prices(self):
        self.db.add_product("Копейки", "Тест", "", "M", "", 249.99, 1)
        self.db.raise_low_prices(250, 500.50)
        self.assertEqual(self.prices()["Копейки"], 500.50)


if __name__ == "__main__":
    unittest.main()
