"""Лёгкие виджеты на чистом tkinter: прокрутка и сетка карточек товаров.

Почему не CTkScrollableFrame: десятки вложенных CTk-виджетов внутри него «мажутся» при прокрутке
и пересборке (остаются следы текста). Обычные tk-виджеты рисуются быстро и без артефактов."""
import tkinter as tk

import customtkinter as ctk
from PIL import ImageTk

from .images import load_box

FONT = "Segoe UI"
CARD_W, PHOTO_H, GAP = 232, 160, 8


def money(value):
    text = f"{value:,.2f}".replace(",", " ")
    if text.endswith(".00"):
        text = text[:-3]
    return text + " ₽"


def short(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def flat_button(parent, pal, scale, text, command, kind="primary", disabled=False, compact=False):
    """Плоская кнопка на tk.Button: primary / ghost / danger / danger_ghost."""
    if kind == "primary":
        bg, fg, active, border = pal["accent"], pal["accent_fg"], pal["accent_hover"], pal["accent"]
    elif kind == "danger":
        bg, fg, active, border = "#e5484d", "#ffffff", "#c93b40", "#e5484d"
    elif kind == "danger_ghost":
        bg, fg, active, border = pal["card"], "#e5484d", pal["soft"], pal["border"]
    else:
        bg, fg, active, border = pal["card"], pal["text"], pal["soft"], pal["border"]
    size = 14 if compact else 12
    btn = tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg, activebackground=active,
        activeforeground=fg, relief="flat", bd=0, highlightthickness=1,
        highlightbackground=border, highlightcolor=border, takefocus=0, cursor="hand2",
        font=(FONT, -max(9, round(size * scale)), "bold"),
        padx=round((6 if compact else 8) * scale), pady=round((1 if compact else 7) * scale))
    if disabled:
        btn.configure(state="disabled", bg=pal["field"], fg=pal["muted"],
                      disabledforeground=pal["muted"], highlightbackground=pal["field"],
                      cursor="arrow")
    return btn


class ScrollFrame(tk.Frame):
    """Прокручиваемая область: self.inner — контейнер для содержимого."""

    def __init__(self, master, pal):
        super().__init__(master, bg=pal["bg"], bd=0, highlightthickness=0)
        self.pal = pal
        self.scale = max(1.0, min(3.0, self.winfo_fpixels("1i") / 96.0))
        self.canvas = tk.Canvas(self, bg=pal["bg"], bd=0, highlightthickness=0, yscrollincrement=24)
        self.bar = ctk.CTkScrollbar(self, orientation="vertical", command=self.canvas.yview,
                                    fg_color=pal["bg"], button_color=pal["border"],
                                    button_hover_color=pal["muted"], width=12)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y", padx=(6, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=pal["bg"], bd=0, highlightthickness=0)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.attach(self.canvas)
        self.attach(self.inner)

    # --- вспомогательное ---
    def px(self, value):
        return round(value * self.scale)

    def font(self, size, bold=False):
        return (FONT, -max(9, round(size * self.scale)), "bold" if bold else "normal")

    def attach(self, widget):
        """Колесо мыши над виджетом (и его потомками) прокручивает область."""
        widget.bind("<MouseWheel>", self._on_wheel, add="+")
        for child in widget.winfo_children():
            self.attach(child)

    def scroll_top(self):
        self.canvas.yview_moveto(0)

    # --- события ---
    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        self.on_width_changed(event.width)

    def on_width_changed(self, width):
        pass

    def _on_wheel(self, event):
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


class ProductGrid(ScrollFrame):
    """Сетка карточек. Карточки создаются один раз и при фильтрации только показываются/скрываются."""

    def __init__(self, master, pal, is_admin, on_open, on_add, on_delete):
        super().__init__(master, pal)
        self.is_admin = is_admin
        self.on_open, self.on_add, self.on_delete = on_open, on_add, on_delete
        self._cards = {}      # id -> (подпись данных, карточка)
        self._photos = []     # ссылки на PhotoImage, иначе их удалит сборщик мусора
        self._order = []
        self._layout_key = None
        self._job = None
        self._empty = tk.Label(self.inner, text="Ничего не найдено.\nИзмените поиск или фильтры.",
                               bg=pal["bg"], fg=pal["muted"], font=self.font(15), justify="center")

    # --- жизненный цикл ---
    def destroy(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        super().destroy()

    def on_width_changed(self, width):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(120, self._layout)

    # --- отрисовка ---
    def render(self, rows):
        shown = set()
        for row in rows:
            signature = (row["name"], row["brand"], row["category"], row["size"],
                         row["price"], row["stock"], row.get("image_path") or "")
            cached = self._cards.get(row["id"])
            if cached is None or cached[0] != signature:
                if cached is not None:
                    cached[1].destroy()
                card = self._build_card(row)
                self.attach(card)
                self._cards[row["id"]] = (signature, card)
            shown.add(row["id"])
        for pid, (_, card) in self._cards.items():
            if pid not in shown:
                card.grid_remove()
        self._order = [row["id"] for row in rows]
        self._layout_key = None
        self._layout()
        self.scroll_top()

    def _layout(self):
        self._job = None
        if not self.winfo_exists():
            return
        if not self._order:
            self._empty.grid(row=0, column=0, padx=20, pady=70)
            return
        self._empty.grid_remove()
        gap = self.px(GAP)
        cols = max(1, self.canvas.winfo_width() // (self.px(CARD_W) + 2 * gap))
        key = (cols, tuple(self._order))
        if key == self._layout_key:
            return
        self._layout_key = key
        for index, pid in enumerate(self._order):
            self._cards[pid][1].grid(row=index // cols, column=index % cols,
                                     padx=gap, pady=gap, sticky="n")

    def _build_card(self, row):
        p = self.pal
        pad = self.px(12)
        width = self.px(CARD_W)
        inner_w = width - 2 * pad
        card = tk.Frame(self.inner, bg=p["card"], width=width, bd=0, highlightthickness=1,
                        highlightbackground=p["border"], highlightcolor=p["border"])

        box = tk.Frame(card, bg=p["field"], width=inner_w, height=self.px(PHOTO_H), bd=0,
                       highlightthickness=0)
        box.pack(padx=pad, pady=(pad, self.px(8)))
        box.pack_propagate(False)
        pil = load_box(row.get("image_path"), (inner_w, self.px(PHOTO_H)))
        if pil is not None:
            photo = ImageTk.PhotoImage(pil)
            self._photos.append(photo)
            picture = tk.Label(box, image=photo, bg=p["field"], bd=0)
        else:
            picture = tk.Label(box, text="Нет фотографии", bg=p["field"], fg=p["muted"],
                               font=self.font(13), bd=0)
        picture.place(relx=0.5, rely=0.5, anchor="center")

        name = tk.Label(card, text=short(row["name"], 44), bg=p["card"], fg=p["text"],
                        font=self.font(15, True), justify="left", anchor="nw",
                        wraplength=inner_w, height=2)
        name.pack(fill="x", padx=pad)
        meta = f'{row["brand"] or "Без бренда"} · {row["category"]} · {row["size"]}'
        tk.Label(card, text=short(meta, 36), bg=p["card"], fg=p["muted"], font=self.font(11),
                 anchor="w").pack(fill="x", padx=pad, pady=(2, 0))
        tk.Label(card, text=money(row["price"]), bg=p["card"], fg=p["accent"],
                 font=self.font(19, True), anchor="w").pack(fill="x", padx=pad, pady=(6, 0))
        stock = row["stock"]
        if stock <= 0:
            stock_text, stock_color = "Нет в наличии", "#e5484d"
        elif stock <= 3:
            stock_text, stock_color = f"Осталось {stock} шт.", "#d97706"
        else:
            stock_text, stock_color = f"В наличии: {stock}", p["muted"]
        tk.Label(card, text=stock_text, bg=p["card"], fg=stock_color, font=self.font(11),
                 anchor="w").pack(fill="x", padx=pad)

        bar = tk.Frame(card, bg=p["card"], bd=0, highlightthickness=0)
        bar.pack(fill="x", padx=pad, pady=(self.px(10), pad if not self.is_admin else self.px(6)))
        bar.grid_columnconfigure((0, 1), weight=1, uniform="buttons")
        pid = row["id"]
        flat_button(bar, p, self.scale, "Подробнее", lambda: self.on_open(pid), "ghost").grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        flat_button(bar, p, self.scale, "В корзину", lambda: self.on_add(pid), "primary",
                    disabled=stock <= 0).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        if self.is_admin:
            extra = tk.Frame(card, bg=p["card"], bd=0, highlightthickness=0)
            extra.pack(fill="x", padx=pad, pady=(0, pad))
            flat_button(extra, p, self.scale, "Удалить товар", lambda: self.on_delete(pid),
                        "danger_ghost").pack(fill="x")

        for widget in (box, picture, name):
            widget.bind("<Button-1>", lambda _e, i=pid: self.on_open(i))
            widget.configure(cursor="hand2")
        return card
