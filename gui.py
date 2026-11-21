"""Style Store: интерфейс на customtkinter — вход, каталог карточками с фото, корзина,
поиск и фильтры, добавление/удаление товаров, выбор цвета и тёмная тема.

Важные правила (из-за них раньше вылезала ошибка «invalid command name»):
  * никаких tk-переменных (StringVar и т.п.), привязанных к виджетам CTk — состояние фильтров
    хранится в обычном словаре self.filters;
  * экран пересобирается не сразу, а через DEFER мс — кнопка CTk успевает завершить анимацию клика;
  * каталог рисуется лёгкими tk-виджетами (store/widgets.py), а не CTkScrollableFrame."""
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk

from .db import SIZE_ORDER
from .images import FILE_TYPES, delete_image, import_image, load_box
from .theme import DEFAULT_COLOR, PRESET_COLORS, darken, palette
from .widgets import FONT, ProductGrid, ScrollFrame, flat_button, money, short

ALL = "Все"
DEFER = 170  # мс: пересобираем экран чуть позже клика, когда кнопка CTk закончила анимацию


def default_filters():
    return dict(search="", category=ALL, size=ALL, pmin="", pmax="", stock=False)


class App(ctk.CTk):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.title("Style Store — магазин одежды")
        self.geometry("1180x740")
        self.minsize(1020, 640)

        self.user = None
        self.accent, self.mode = DEFAULT_COLOR, "light"
        self.pal = palette(self.mode, self.accent)
        self.screen = "login"
        self.container = self.grid_view = self.count_label = self.cart_btn = None
        self.rows = {}
        self.carts = {}          # корзина хранится отдельно для каждого пользователя
        self.filters = default_filters()
        self._job = None

        self.apply_theme(DEFAULT_COLOR, "light")
        self.show_login()

    # ---------- общие помощники ----------
    @property
    def cart(self):
        return self.carts.setdefault(self.user["id"], {})

    def apply_theme(self, accent, mode):
        self.accent, self.mode = accent, mode
        self.pal = palette(mode, accent)
        ctk.set_appearance_mode(mode)
        self.configure(fg_color=self.pal["bg"])

    def _font(self, size, bold=False):
        return ctk.CTkFont(family=FONT, size=size, weight="bold" if bold else "normal")

    def _btn(self, parent, text, command, kind="primary", height=38, **kw):
        p = self.pal
        if kind == "primary":
            colors = dict(fg_color=p["accent"], hover_color=p["accent_hover"], text_color=p["accent_fg"])
        elif kind == "danger":
            colors = dict(fg_color="#e5484d", hover_color="#c93b40", text_color="#ffffff")
        else:
            colors = dict(fg_color="transparent", hover_color=p["soft"], text_color=p["text"],
                          border_width=1, border_color=p["border"])
        return ctk.CTkButton(parent, text=text, command=command, height=height, corner_radius=10,
                             font=self._font(13, True), **colors, **kw)

    def _style_toggle(self, button, on, text):
        p = self.pal
        if on:
            button.configure(text="✓  " + text, fg_color=p["accent"], hover_color=p["accent_hover"],
                             text_color=p["accent_fg"], border_color=p["accent"])
        else:
            button.configure(text=text, fg_color="transparent", hover_color=p["soft"],
                             text_color=p["text"], border_color=p["border"])

    def _entry(self, parent, placeholder="", initial="", **kw):
        p = self.pal
        entry = ctk.CTkEntry(parent, height=38, corner_radius=8, fg_color=p["field"],
                             border_color=p["border"], text_color=p["text"],
                             placeholder_text=placeholder, placeholder_text_color=p["muted"],
                             font=self._font(13), **kw)
        if initial:
            entry.insert(0, initial)
        return entry

    def _menu(self, parent, values, current, command=None):
        p = self.pal
        menu = ctk.CTkOptionMenu(parent, values=values, command=command, height=38, corner_radius=8,
                                 fg_color=p["field"], button_color=p["field"],
                                 button_hover_color=p["soft"], text_color=p["text"],
                                 dropdown_fg_color=p["card"], dropdown_text_color=p["text"],
                                 dropdown_hover_color=p["soft"], font=self._font(13),
                                 dropdown_font=self._font(13), anchor="w", dynamic_resizing=False)
        menu.set(current)
        return menu

    def _new_screen(self, name):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.screen = name
        self.grid_view = self.count_label = self.cart_btn = None
        if self.container is not None:
            self.container.destroy()
        self.container = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.container.pack(fill="both", expand=True)
        return self.container

    def _restyle(self, accent, mode):
        """Применить тему и перестроить каталог (вызывается через after)."""
        self.apply_theme(accent, mode)
        self.show_shop()

    def report_callback_exception(self, exc, val, tb):
        text = str(val)
        if isinstance(val, tk.TclError) and any(
                s in text for s in ("invalid command name", "bad window path name", "has been destroyed")):
            print("Пропущено безвредное обращение к уже закрытому виджету:", text)
            return
        traceback.print_exception(exc, val, tb)
        messagebox.showerror("Ошибка", text)

    # ---------- вход ----------
    def show_login(self):
        self.user = None
        self.filters = default_filters()
        self.apply_theme(DEFAULT_COLOR, "light")
        box = self._new_screen("login")
        p = self.pal

        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=5, uniform="cols")
        box.grid_columnconfigure(1, weight=6, uniform="cols")

        left = ctk.CTkFrame(box, fg_color=p["accent"], corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(left, text="STYLE\nSTORE", font=self._font(48, True), justify="left",
                     text_color=p["accent_fg"]).place(relx=0.1, rely=0.34)
        ctk.CTkLabel(left, text="Одежда, в которой удобно жить", font=self._font(16),
                     text_color=p["accent_fg"]).place(relx=0.1, rely=0.60)

        right = ctk.CTkFrame(box, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        form = ctk.CTkFrame(right, fg_color=p["card"], corner_radius=18)
        form.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(form, text="Добро пожаловать", font=self._font(24, True),
                     text_color=p["text"]).pack(anchor="w", padx=32, pady=(32, 2))
        ctk.CTkLabel(form, text="Войдите, чтобы открыть каталог", font=self._font(13),
                     text_color=p["muted"]).pack(anchor="w", padx=32)
        self.user_entry = self._entry(form, placeholder="Логин", width=320)
        self.user_entry.pack(padx=32, pady=(22, 10))
        self.pass_entry = self._entry(form, placeholder="Пароль", width=320, show="•")
        self.pass_entry.pack(padx=32)
        self._btn(form, "Войти", self.do_login, width=320).pack(padx=32, pady=(20, 8))
        self._btn(form, "Создать аккаунт", self.do_register, "ghost", width=320).pack(padx=32)
        ctk.CTkLabel(form, text="Демо:  admin / admin123   ·   user / user123", font=self._font(12),
                     text_color=p["muted"]).pack(pady=(18, 28))
        self.user_entry.bind("<Return>", lambda e: self.pass_entry.focus())
        self.pass_entry.bind("<Return>", lambda e: self.do_login())

    def do_login(self):
        if self.screen != "login":
            return
        user = self.db.authenticate(self.user_entry.get(), self.pass_entry.get())
        if not user:
            messagebox.showerror("Ошибка входа", "Неверный логин или пароль")
            return
        self.user = user
        mode = "dark" if user["dark_mode"] else "light"
        self.screen = "entering"  # защита от двойного нажатия Enter/«Войти»
        self.after(DEFER, lambda: self._restyle(user["theme_color"], mode))

    def do_register(self):
        try:
            self.db.add_user(self.user_entry.get(), self.pass_entry.get(), "user")
        except ValueError as e:
            messagebox.showerror("Регистрация", str(e))
            return
        messagebox.showinfo("Регистрация", "Аккаунт создан. Теперь можно войти.")

    # ---------- каталог ----------
    def show_shop(self):
        p = self.pal
        f = self.filters
        is_admin = self.user["role"] == "admin"
        categories = [ALL] + self.db.categories()
        sizes = [ALL] + self.db.sizes()
        if f["category"] not in categories:
            f["category"] = ALL
        if f["size"] not in sizes:
            f["size"] = ALL

        box = self._new_screen("shop")
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(1, weight=1)

        # --- боковая панель ---
        side = ctk.CTkFrame(box, fg_color=p["card"], corner_radius=0, width=290)
        side.grid(row=0, column=0, sticky="ns")
        side.pack_propagate(False)

        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=22, pady=20)
        dark_btn = self._btn(bottom, "", self.toggle_dark, "ghost")
        self._style_toggle(dark_btn, self.mode == "dark", "Тёмная тема")
        dark_btn.pack(fill="x", pady=(0, 8))
        self._btn(bottom, "Цвет оформления", self.choose_color, "ghost").pack(fill="x", pady=(0, 8))
        self._btn(bottom, "Выйти", lambda: self.after(DEFER, self.show_login), "ghost").pack(fill="x")

        ctk.CTkLabel(side, text="Style Store", font=self._font(22, True),
                     text_color=p["accent"]).pack(anchor="w", padx=22, pady=(24, 0))
        role = "администратор" if is_admin else "покупатель"
        ctk.CTkLabel(side, text=f"{self.user['username']} · {role}", font=self._font(12),
                     text_color=p["muted"]).pack(anchor="w", padx=22, pady=(2, 14))

        def caption(text):
            ctk.CTkLabel(side, text=text, font=self._font(12),
                         text_color=p["muted"]).pack(anchor="w", padx=22, pady=(10, 4))

        caption("Поиск")
        search = self._entry(side, initial=f["search"])
        search.pack(fill="x", padx=22)
        search.bind("<KeyRelease>", lambda e: self._set_filter("search", search.get()))
        caption("Категория")
        self._menu(side, categories, f["category"],
                   lambda v: self._set_filter("category", v)).pack(fill="x", padx=22)
        caption("Размер")
        self._menu(side, sizes, f["size"],
                   lambda v: self._set_filter("size", v)).pack(fill="x", padx=22)
        caption("Цена, ₽  (от — до)")
        prices = ctk.CTkFrame(side, fg_color="transparent")
        prices.pack(fill="x", padx=22)
        prices.grid_columnconfigure((0, 1), weight=1, uniform="price")
        pmin = self._entry(prices, initial=f["pmin"])
        pmin.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        pmin.bind("<KeyRelease>", lambda e: self._set_filter("pmin", pmin.get()))
        pmax = self._entry(prices, initial=f["pmax"])
        pmax.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        pmax.bind("<KeyRelease>", lambda e: self._set_filter("pmax", pmax.get()))

        self.stock_btn = self._btn(side, "", self.toggle_stock, "ghost")
        self._style_toggle(self.stock_btn, f["stock"], "Только в наличии")
        self.stock_btn.pack(fill="x", padx=22, pady=(16, 8))
        self._btn(side, "Сбросить фильтры", self.reset_filters, "ghost").pack(fill="x", padx=22)

        # --- основная область ---
        main = ctk.CTkFrame(box, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        head.grid_columnconfigure(0, weight=1)
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(titles, text="Каталог", font=self._font(28, True),
                     text_color=p["text"]).pack(anchor="w")
        self.count_label = ctk.CTkLabel(titles, text="", font=self._font(13), text_color=p["muted"])
        self.count_label.pack(anchor="w")
        actions = ctk.CTkFrame(head, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        if is_admin:
            self._btn(actions, "+ Добавить товар", self.add_dialog, "ghost",
                      width=160).pack(side="left", padx=(0, 8))
        self.cart_btn = self._btn(actions, "", self.show_cart, width=150)
        self.cart_btn.pack(side="left")
        self._update_cart_button()

        self.grid_view = ProductGrid(main, p, is_admin, on_open=self.show_product_details,
                                     on_add=self.add_to_cart, on_delete=self.delete_product)
        self.grid_view.grid(row=1, column=0, sticky="nsew")
        self.refresh()

    # ---------- фильтры ----------
    def _set_filter(self, key, value):
        self.filters[key] = value
        self._schedule_refresh()

    def toggle_stock(self):
        self.filters["stock"] = not self.filters["stock"]
        self._style_toggle(self.stock_btn, self.filters["stock"], "Только в наличии")
        self._schedule_refresh()

    def reset_filters(self):
        self.filters = default_filters()
        self.after(DEFER, self.show_shop)

    def _schedule_refresh(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self.refresh)

    def refresh(self):
        self._job = None
        if self.screen != "shop" or self.grid_view is None:
            return
        f = self.filters

        def num(text):
            text = text.strip().replace(",", ".")
            return float(text) if text else None

        try:
            pmin, pmax = num(f["pmin"]), num(f["pmax"])
        except ValueError:
            self.count_label.configure(text="Цена в фильтре должна быть числом")
            return
        rows = self.db.list_products(
            search=f["search"],
            category=None if f["category"] == ALL else f["category"],
            size=None if f["size"] == ALL else f["size"],
            price_min=pmin, price_max=pmax, in_stock=f["stock"])
        self.rows = {r["id"]: r for r in rows}
        self.grid_view.render(rows)
        self.count_label.configure(text=f"Найдено товаров: {len(rows)}")

    # ---------- корзина ----------
    def _update_cart_button(self):
        if self.cart_btn is not None:
            self.cart_btn.configure(text=f"Корзина ({sum(self.cart.values())})")

    def add_to_cart(self, product_id):
        item = self.rows.get(product_id) or self.db.get_product(product_id)
        if not item:
            return
        current = self.cart.get(product_id, 0)
        if current >= item["stock"]:
            messagebox.showinfo("Корзина", "Больше товара нет на складе")
            return
        self.cart[product_id] = current + 1
        self._update_cart_button()

    def show_cart(self):
        p = self.pal
        dlg = self._dialog("Корзина", 720, 660)
        ctk.CTkLabel(dlg, text="Ваша корзина", font=self._font(24, True),
                     text_color=p["text"]).pack(anchor="w", padx=24, pady=(20, 8))

        footer = ctk.CTkFrame(dlg, fg_color=p["card"], corner_radius=14)
        footer.pack(side="bottom", fill="x", padx=20, pady=18)
        total_label = ctk.CTkLabel(footer, text="", font=self._font(22, True), text_color=p["accent"])
        total_label.pack(side="left", padx=20, pady=16)
        checkout_btn = self._btn(footer, "Оформить заказ", lambda: self._checkout(dlg, render), width=170)
        checkout_btn.pack(side="right", padx=(8, 16))
        clear_btn = self._btn(footer, "Очистить", lambda: clear(), "ghost", width=110)
        clear_btn.pack(side="right")

        scroll = ScrollFrame(dlg, p)
        scroll.pack(fill="both", expand=True, padx=20)
        photos = []

        def render():
            if not dlg.winfo_exists():
                return
            for child in scroll.inner.winfo_children():
                child.destroy()
            photos.clear()
            lines, total = [], 0.0
            for pid, qty in list(self.cart.items()):
                item = self.db.get_product(pid)
                if not item or item["stock"] <= 0:
                    self.cart.pop(pid, None)
                    continue
                qty = min(qty, item["stock"])
                self.cart[pid] = qty
                lines.append((item, qty))
                total += item["price"] * qty
            total_label.configure(text=f"Итого: {money(total)}")
            state = "normal" if lines else "disabled"
            checkout_btn.configure(state=state)
            clear_btn.configure(state=state)
            self._update_cart_button()
            if not lines:
                tk.Label(scroll.inner, text="Корзина пока пуста", bg=p["bg"], fg=p["muted"],
                         font=scroll.font(15)).pack(pady=70, padx=200)
                return
            for item, qty in lines:
                self._cart_line(scroll, item, qty, photos, change, remove)
            scroll.scroll_top()

        def change(pid, delta):
            item = self.db.get_product(pid)
            qty = self.cart.get(pid, 0) + delta
            if qty <= 0:
                self.cart.pop(pid, None)
            elif item and qty <= item["stock"]:
                self.cart[pid] = qty
            else:
                messagebox.showinfo("Корзина", "Недостаточно товара на складе", parent=dlg)
            self.after(10, render)

        def remove(pid):
            self.cart.pop(pid, None)
            self.after(10, render)

        def clear():
            self.cart.clear()
            self.after(10, render)

        render()

    def _cart_line(self, scroll, item, qty, photos, change, remove):
        from PIL import ImageTk
        p = self.pal
        pid = item["id"]
        line = tk.Frame(scroll.inner, bg=p["card"], bd=0, highlightthickness=1,
                        highlightbackground=p["border"], highlightcolor=p["border"])
        line.pack(fill="x", padx=4, pady=5)
        line.grid_columnconfigure(1, weight=1)

        side = scroll.px(64)
        box = tk.Frame(line, bg=p["field"], width=side, height=side, bd=0, highlightthickness=0)
        box.grid(row=0, column=0, rowspan=2, padx=scroll.px(12), pady=scroll.px(12))
        box.pack_propagate(False)
        pil = load_box(item.get("image_path"), (side, side))
        if pil is not None:
            photo = ImageTk.PhotoImage(pil)
            photos.append(photo)
            tk.Label(box, image=photo, bg=p["field"], bd=0).place(relx=0.5, rely=0.5, anchor="center")
        else:
            tk.Label(box, text=(item["name"][:1] or "?").upper(), bg=p["field"], fg=p["accent"],
                     font=scroll.font(22, True), bd=0).place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(line, text=short(item["name"], 34), bg=p["card"], fg=p["text"],
                 font=scroll.font(14, True), anchor="w").grid(row=0, column=1, sticky="sw")
        tk.Label(line, text=f'{money(item["price"])} · размер {item["size"]}', bg=p["card"],
                 fg=p["muted"], font=scroll.font(11), anchor="w").grid(row=1, column=1, sticky="nw")

        stepper = tk.Frame(line, bg=p["card"], bd=0, highlightthickness=0)
        stepper.grid(row=0, column=2, rowspan=2, padx=scroll.px(8))
        flat_button(stepper, p, scroll.scale, "−", lambda: change(pid, -1), "ghost",
                    compact=True).pack(side="left")
        tk.Label(stepper, text=str(qty), bg=p["card"], fg=p["text"], font=scroll.font(14, True),
                 width=3).pack(side="left")
        flat_button(stepper, p, scroll.scale, "+", lambda: change(pid, 1), "ghost",
                    compact=True).pack(side="left")

        tk.Label(line, text=money(item["price"] * qty), bg=p["card"], fg=p["accent"],
                 font=scroll.font(15, True), width=10, anchor="e").grid(row=0, column=3, rowspan=2)
        flat_button(line, p, scroll.scale, "✕", lambda: remove(pid), "danger_ghost",
                    compact=True).grid(row=0, column=4, rowspan=2, padx=scroll.px(12))
        scroll.attach(line)

    def _checkout(self, dlg, render):
        cart = dict(self.cart)
        if not cart:
            return
        if not messagebox.askyesno("Подтверждение",
                                   "Оформить заказ и списать товары со склада?", parent=dlg):
            return
        try:
            self.db.purchase(cart)
        except ValueError as e:
            messagebox.showerror("Заказ", str(e), parent=dlg)
            self.after(10, render)
            return
        self.cart.clear()
        messagebox.showinfo("Заказ", "Заказ оформлен! Спасибо за покупку.", parent=dlg)
        self._close(dlg)
        self.after(DEFER, self.show_shop)

    # ---------- диалоги ----------
    def _dialog(self, title, width, height):
        dlg = ctk.CTkToplevel(self)
        dlg.title(title)
        x = self.winfo_x() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_y() + max(0, (self.winfo_height() - height) // 2)
        dlg.geometry(f"{width}x{height}+{x}+{y}")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self.pal["bg"])
        dlg.transient(self)
        self.after(250, lambda: self._grab(dlg))
        return dlg

    @staticmethod
    def _grab(dlg):
        try:
            if dlg.winfo_exists():
                dlg.lift()
                dlg.focus_force()
                dlg.grab_set()
        except tk.TclError:
            pass

    def _close(self, dlg):
        """Закрыть диалог чуть позже (кнопка CTk, нажатая в нём, успевает закончить анимацию)."""
        def do():
            try:
                if dlg.winfo_exists():
                    dlg.destroy()
            except tk.TclError:
                pass
        self.after(DEFER, do)

    def show_product_details(self, product_id):
        item = self.rows.get(product_id) or self.db.get_product(product_id)
        if not item:
            return
        p = self.pal
        is_admin = self.user["role"] == "admin"
        dlg = self._dialog("Информация о товаре", 480, 740 if is_admin else 690)

        bar = ctk.CTkFrame(dlg, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=24, pady=(0, 20))
        self._btn(bar, "В корзину", lambda: (self.add_to_cart(item["id"]), self._close(dlg)),
                  width=10).pack(fill="x")
        if item["stock"] <= 0:
            bar.winfo_children()[0].configure(state="disabled")
        if is_admin:
            self._btn(bar, "Изменить фото…", lambda: self._change_photo(dlg, item),
                      "ghost").pack(fill="x", pady=(8, 0))
        self._btn(bar, "Закрыть", lambda: self._close(dlg), "ghost").pack(fill="x", pady=(8, 0))

        pil = load_box(item.get("image_path"), (840, 480))
        if pil is not None:
            image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(pil.width // 2, pil.height // 2))
            ctk.CTkLabel(dlg, text="", image=image).pack(pady=(20, 6))
        else:
            holder = ctk.CTkFrame(dlg, fg_color=p["field"], corner_radius=12, height=200)
            holder.pack(fill="x", padx=24, pady=(20, 6))
            holder.pack_propagate(False)
            ctk.CTkLabel(holder, text="Нет фотографии", text_color=p["muted"],
                         font=self._font(14)).pack(expand=True)
        ctk.CTkLabel(dlg, text=item["name"], font=self._font(22, True), text_color=p["text"],
                     wraplength=420, justify="left").pack(anchor="w", padx=24, pady=(8, 0))
        ctk.CTkLabel(dlg, text=f'{item["brand"] or "Без бренда"} · {item["category"]}',
                     font=self._font(13), text_color=p["muted"]).pack(anchor="w", padx=24)
        ctk.CTkLabel(dlg, text=money(item["price"]), font=self._font(26, True),
                     text_color=p["accent"]).pack(anchor="w", padx=24, pady=(8, 4))
        info = f'Размер: {item["size"]}    Цвет: {item["color"] or "—"}    На складе: {item["stock"]}'
        ctk.CTkLabel(dlg, text=info, font=self._font(13), text_color=p["text"]).pack(anchor="w", padx=24)
        ctk.CTkLabel(dlg, text=item.get("description") or "Описание не указано.",
                     font=self._font(13), text_color=p["muted"], wraplength=420,
                     justify="left").pack(anchor="w", padx=24, pady=10)

    def _change_photo(self, dlg, item):
        path = filedialog.askopenfilename(parent=dlg, title="Выберите фото товара", filetypes=FILE_TYPES)
        if not path:
            return
        try:
            new_path = import_image(path)
        except (OSError, ValueError):
            messagebox.showerror("Фото", "Не удалось открыть изображение. Выберите PNG или JPG.", parent=dlg)
            return
        old = self.db.set_product_image(item["id"], new_path)
        delete_image(old)
        self._close(dlg)
        self.after(DEFER, self.show_shop)

    def delete_product(self, product_id):
        item = self.rows.get(product_id)
        if not item or not messagebox.askyesno("Удаление", f"Удалить товар «{item['name']}»?"):
            return
        self.db.delete_product(product_id)
        delete_image(item.get("image_path"))
        for cart in self.carts.values():
            cart.pop(product_id, None)
        self.after(DEFER, self.show_shop)

    def add_dialog(self):
        p = self.pal
        dlg = self._dialog("Новый товар", 500, 720)
        ctk.CTkLabel(dlg, text="Новый товар", font=self._font(22, True),
                     text_color=p["text"]).pack(anchor="w", padx=24, pady=(22, 4))
        form = ctk.CTkFrame(dlg, fg_color="transparent")
        form.pack(fill="x", padx=20)
        form.grid_columnconfigure((0, 1), weight=1, uniform="f")

        def put(row, col, label, widget, span=1):
            ctk.CTkLabel(form, text=label, font=self._font(12), text_color=p["muted"]).grid(
                row=row * 2, column=col, columnspan=span, sticky="w", padx=4, pady=(10, 2))
            widget.grid(row=row * 2 + 1, column=col, columnspan=span, sticky="ew", padx=4)

        name_e = self._entry(form)
        put(0, 0, "Название", name_e, 2)
        category_box = ctk.CTkComboBox(
            form, values=self.db.categories(), height=38, corner_radius=8,
            fg_color=p["field"], border_color=p["border"], button_color=p["field"],
            button_hover_color=p["soft"], text_color=p["text"], dropdown_fg_color=p["card"],
            dropdown_text_color=p["text"], dropdown_hover_color=p["soft"], font=self._font(13))
        category_box.set("")
        put(1, 0, "Категория (можно новую)", category_box)
        brand_e = self._entry(form)
        put(1, 1, "Бренд", brand_e)
        size_menu = self._menu(form, SIZE_ORDER, "M")
        put(2, 0, "Размер", size_menu)
        color_e = self._entry(form)
        put(2, 1, "Цвет", color_e)
        price_e = self._entry(form)
        put(3, 0, "Цена, ₽", price_e)
        stock_e = self._entry(form, initial="1")
        put(3, 1, "Количество", stock_e)

        photo = {"path": ""}
        photo_label = ctk.CTkLabel(form, text="Фото не выбрано", text_color=p["muted"], font=self._font(12))

        def choose_photo():
            path = filedialog.askopenfilename(parent=dlg, title="Выберите фото товара", filetypes=FILE_TYPES)
            if path:
                photo["path"] = path
                photo_label.configure(text=short(Path(path).name, 48))

        put(4, 0, "Фотография товара", self._btn(form, "Выбрать фото…", choose_photo, "ghost", height=36), 2)
        photo_label.grid(row=10, column=0, columnspan=2, sticky="w", padx=8, pady=(3, 0))
        description = ctk.CTkTextbox(form, height=80, corner_radius=8, fg_color=p["field"],
                                     text_color=p["text"], border_width=1, border_color=p["border"],
                                     font=self._font(13))
        put(6, 0, "Описание", description, 2)

        def save():
            try:
                price = float(price_e.get().replace(",", "."))
                stock = int(stock_e.get())
            except ValueError:
                messagebox.showerror("Ошибка", "Цена и количество должны быть числами", parent=dlg)
                return
            image_path = ""
            if photo["path"]:
                try:
                    image_path = import_image(photo["path"])
                except (OSError, ValueError):
                    messagebox.showerror("Фото", "Не удалось открыть изображение. Выберите PNG или JPG.",
                                         parent=dlg)
                    return
            try:
                self.db.add_product(name_e.get(), category_box.get(), brand_e.get(), size_menu.get(),
                                    color_e.get(), price, stock, description.get("1.0", "end"), image_path)
            except ValueError as e:
                delete_image(image_path)
                messagebox.showerror("Ошибка", str(e), parent=dlg)
                return
            self._close(dlg)
            self.after(DEFER, self.show_shop)

        buttons = ctk.CTkFrame(dlg, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=24, pady=20)
        buttons.grid_columnconfigure((0, 1), weight=1, uniform="b")
        self._btn(buttons, "Отмена", lambda: self._close(dlg), "ghost").grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        self._btn(buttons, "Сохранить", save).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    # ---------- цвет и тёмная тема ----------
    def choose_color(self):
        p = self.pal
        dlg = self._dialog("Цвет оформления", 400, 290)
        ctk.CTkLabel(dlg, text="Акцентный цвет", font=self._font(20, True),
                     text_color=p["text"]).pack(pady=(22, 10))
        grid = ctk.CTkFrame(dlg, fg_color="transparent")
        grid.pack()

        def pick(color):
            self._close(dlg)
            self._set_accent(color)

        for i, color in enumerate(PRESET_COLORS):
            ctk.CTkButton(grid, text="", width=40, height=40, corner_radius=20, fg_color=color,
                          hover_color=darken(color), command=lambda c=color: pick(c)
                          ).grid(row=i // 5, column=i % 5, padx=6, pady=6)

        def custom():
            _, chosen = colorchooser.askcolor(color=self.accent, parent=dlg, title="Свой цвет")
            if chosen:
                pick(chosen)

        self._btn(dlg, "Свой цвет…", custom, "ghost").pack(pady=14, padx=24, fill="x")

    def _set_accent(self, color):
        color = color.lower()
        self.db.save_theme(self.user["id"], color, self.mode == "dark")
        self.user["theme_color"] = color
        self.after(DEFER, lambda: self._restyle(color, self.mode))

    def toggle_dark(self):
        mode = "light" if self.mode == "dark" else "dark"
        self.db.save_theme(self.user["id"], self.accent, mode == "dark")
        self.user["dark_mode"] = mode == "dark"
        self.after(DEFER, lambda: self._restyle(self.accent, mode))
