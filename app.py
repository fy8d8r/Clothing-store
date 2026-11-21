"""Style Store — веб-версия на Flask.

Запуск:  python app.py   →  http://127.0.0.1:5000
Использует тот же слой данных (store/db.py), что и настольная версия: SQLite или MySQL (config.ini)."""
import secrets
import sys
import time
from datetime import timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request, send_file,
                   session, url_for)
from PIL import Image

from store.config import ROOT
from store.db import SIZE_ORDER, Database, DatabaseError
from store.images import delete_image, import_image, resolve
from store.theme import DEFAULT_COLOR, PRESET_COLORS, palette, valid_color

LOGIN_LIMIT, LOGIN_WINDOW = 5, 300  # 5 неудачных попыток за 5 минут


def _load_secret_key():
    """Ключ подписи сессий: переменная SHOP_SECRET_KEY или файл instance/secret_key.txt (создаётся сам)."""
    import os
    env = os.environ.get("SHOP_SECRET_KEY")
    if env:
        return env
    path = ROOT / "instance" / "secret_key.txt"
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def safe_next(default="catalog"):
    """Адрес возврата только внутри сайта (защита от open redirect)."""
    target = request.form.get("next") or request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for(default)


def create_app(db=None, secret_key=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key or _load_secret_key(),
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,           # фото до 12 МБ
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    app.db = db if db is not None else Database()
    app.failed_logins = {}
    store = app.db
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.template_filter("money")
    def money(value):
        text = f"{value:,.2f}".replace(",", " ")
        return (text[:-3] if text.endswith(".00") else text) + " ₽"

    @app.template_filter("filename")
    def filename(path):
        return (path or "").replace("\\", "/").rsplit("/", 1)[-1]

    # ---------- общие хуки ----------
    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = store.get_user(uid)
            if g.user is None:
                session.clear()
        if request.method == "POST":
            token, sent = session.get("csrf"), request.form.get("csrf", "")
            if not token or not secrets.compare_digest(token.encode(), sent.encode()):
                abort(400)

    @app.context_processor
    def inject_globals():
        user = g.get("user")
        accent = user["theme_color"] if user else DEFAULT_COLOR
        mode = "dark" if user and user["dark_mode"] else "light"
        cart_count = sum(int(v) for v in session.get("cart", {}).values())
        return dict(user=user, pal=palette(mode, accent), mode=mode, presets=PRESET_COLORS,
                    cart_count=cart_count)

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; form-action 'self'; frame-ancestors 'none'")
        return resp

    for code, text in ((400, "Сессия устарела или запрос неверный. Обновите страницу и повторите."),
                       (403, "Недостаточно прав для этого действия."),
                       (404, "Страница не найдена."),
                       (413, "Файл слишком большой (максимум 12 МБ).")):
        app.register_error_handler(
            code, lambda e, code=code, text=text: (render_template("error.html", code=code, text=text), code))

    # ---------- вход / регистрация ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("catalog"))
        if request.method == "POST":
            ip = request.remote_addr or "?"
            now = time.time()
            recent = [t for t in app.failed_logins.get(ip, []) if now - t < LOGIN_WINDOW]
            app.failed_logins[ip] = recent
            if len(recent) >= LOGIN_LIMIT:
                flash("Слишком много неудачных попыток. Подождите несколько минут.", "error")
            else:
                user = store.authenticate(request.form.get("username", ""), request.form.get("password", ""))
                if user:
                    app.failed_logins.pop(ip, None)
                    session.clear()
                    session.permanent = True
                    session["uid"] = user["id"]
                    return redirect(url_for("catalog"))
                recent.append(now)
                flash("Неверный логин или пароль", "error")
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("catalog"))
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if password != request.form.get("password2", ""):
                flash("Пароли не совпадают", "error")
            else:
                try:
                    store.add_user(username, password, "user")
                except ValueError as e:
                    flash(str(e), "error")
                else:
                    flash("Аккаунт создан. Теперь можно войти.", "success")
                    return redirect(url_for("login"))
        return render_template("register.html", username=request.form.get("username", ""))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- каталог ----------
    @app.get("/")
    @login_required
    def catalog():
        f = {k: request.args.get(k, "").strip() for k in ("q", "category", "size", "pmin", "pmax")}
        f["stock"] = request.args.get("stock") == "1"

        def num(text):
            text = text.replace(",", ".")
            return float(text) if text else None

        price_error = False
        try:
            pmin, pmax = num(f["pmin"]), num(f["pmax"])
        except ValueError:
            pmin = pmax = None
            price_error = True
        rows = store.list_products(search=f["q"], category=f["category"] or None, size=f["size"] or None,
                                   price_min=pmin, price_max=pmax, in_stock=f["stock"])
        return render_template("catalog.html", rows=rows, f=f, price_error=price_error,
                               categories=store.categories(), sizes=store.sizes())

    @app.get("/product/<int:pid>")
    @login_required
    def product(pid):
        item = store.get_product(pid)
        if not item:
            abort(404)
        return render_template("product.html", p=item, in_cart=int(session.get("cart", {}).get(str(pid), 0)))

    @app.get("/product/<int:pid>/photo")
    @login_required
    def product_photo(pid):
        item = store.get_product(pid)
        path = resolve(item["image_path"]) if item else None
        if path is None:
            abort(404)
        return send_file(path, max_age=86400)

    # ---------- администратор: товары ----------
    def read_photo(field="photo"):
        """Возвращает (относительный путь | '', ошибка | None)."""
        file = request.files.get(field)
        if file is None or not file.filename:
            return "", None
        try:
            return import_image(file.stream), None
        except (OSError, ValueError, Image.DecompressionBombError):
            return "", "Не удалось открыть изображение. Выберите файл PNG, JPG или WEBP."

    @app.route("/admin/add", methods=["GET", "POST"])
    @admin_required
    def admin_add():
        form = {"name": "", "category": "", "brand": "", "size": "M", "color": "",
                "price": "", "stock": "1", "description": ""}
        if request.method == "POST":
            form = {k: request.form.get(k, "").strip() for k in form}
            try:
                price = float(form["price"].replace(",", "."))
                stock = int(form["stock"])
            except ValueError:
                flash("Цена и количество должны быть числами", "error")
                return render_template("admin_add.html", form=form, categories=store.categories(), sizes=SIZE_ORDER)
            image_path, error = read_photo()
            if error:
                flash(error, "error")
            else:
                try:
                    store.add_product(form["name"], form["category"], form["brand"], form["size"],
                                      form["color"], price, stock, form["description"], image_path)
                except ValueError as e:
                    delete_image(image_path)
                    flash(str(e), "error")
                else:
                    flash(f"Товар «{form['name']}» добавлен", "success")
                    return redirect(url_for("catalog"))
        return render_template("admin_add.html", form=form, categories=store.categories(), sizes=SIZE_ORDER)

    @app.post("/product/<int:pid>/delete")
    @admin_required
    def product_delete(pid):
        item = store.get_product(pid)
        if item:
            store.delete_product(pid)
            delete_image(item["image_path"])
            flash(f"Товар «{item['name']}» удалён", "success")
        return redirect(safe_next())

    @app.post("/product/<int:pid>/photo/change")
    @admin_required
    def product_photo_change(pid):
        if not store.get_product(pid):
            abort(404)
        image_path, error = read_photo()
        if error or not image_path:
            flash(error or "Выберите файл с фотографией", "error")
        else:
            delete_image(store.set_product_image(pid, image_path))
            flash("Фото обновлено", "success")
        return redirect(url_for("product", pid=pid))

    # ---------- корзина ----------
    def read_cart():
        return {int(k): int(v) for k, v in session.get("cart", {}).items() if str(k).isdigit()}

    def write_cart(cart):
        session["cart"] = {str(k): v for k, v in cart.items() if v > 0}

    def cart_lines():
        """Актуальные строки корзины: исчезнувшие товары убираются, количество не больше остатка."""
        cart, lines, total = read_cart(), [], 0.0
        for pid, qty in list(cart.items()):
            item = store.get_product(pid)
            if not item or item["stock"] <= 0:
                cart.pop(pid)
                continue
            qty = cart[pid] = min(qty, item["stock"])
            lines.append((item, qty))
            total += item["price"] * qty
        write_cart(cart)
        return lines, total

    @app.get("/cart")
    @login_required
    def cart():
        lines, total = cart_lines()
        return render_template("cart.html", lines=lines, total=total)

    @app.post("/cart/add/<int:pid>")
    @login_required
    def cart_add(pid):
        item = store.get_product(pid)
        cart = read_cart()
        if not item:
            flash("Такого товара больше нет", "error")
        elif cart.get(pid, 0) >= item["stock"]:
            flash("Больше товара нет на складе", "error")
        else:
            cart[pid] = cart.get(pid, 0) + 1
            write_cart(cart)
            flash(f"«{item['name']}» добавлен в корзину", "success")
        return redirect(safe_next())

    @app.post("/cart/update/<int:pid>")
    @login_required
    def cart_update(pid):
        cart, action = read_cart(), request.form.get("action")
        item = store.get_product(pid)
        qty = cart.get(pid, 0)
        if action == "inc":
            qty += 1
        elif action == "dec":
            qty -= 1
        elif action == "remove":
            qty = 0
        if item and qty > item["stock"]:
            flash("Недостаточно товара на складе", "error")
            qty = item["stock"]
        cart[pid] = max(qty, 0)
        write_cart(cart)
        return redirect(url_for("cart"))

    @app.post("/cart/clear")
    @login_required
    def cart_clear():
        session["cart"] = {}
        return redirect(url_for("cart"))

    @app.post("/cart/checkout")
    @login_required
    def cart_checkout():
        lines, _ = cart_lines()
        if not lines:
            flash("Добавьте товары перед оформлением", "error")
            return redirect(url_for("cart"))
        try:
            store.purchase({item["id"]: qty for item, qty in lines})
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("cart"))
        session["cart"] = {}
        flash("Заказ оформлен! Спасибо за покупку.", "success")
        return redirect(url_for("catalog"))

    # ---------- оформление ----------
    @app.post("/theme")
    @login_required
    def theme():
        color = request.form.get("color", g.user["theme_color"]).lower()
        dark = request.form.get("dark")
        dark_mode = g.user["dark_mode"] if dark is None else dark == "1"
        if valid_color(color):
            store.save_theme(g.user["id"], color, dark_mode)
        return redirect(safe_next())

    return app


if __name__ == "__main__":
    try:
        application = create_app()
    except DatabaseError as e:
        print("Не удалось подключиться к базе данных:\n", e)
        sys.exit(1)
    application.run(host="127.0.0.1", port=5000, debug=False)
