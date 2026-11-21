"""Цвета и палитры оформления."""
import re

DEFAULT_COLOR = "#4f46e5"
PRESET_COLORS = ["#4f46e5", "#2563eb", "#0891b2", "#0d9488", "#16a34a",
                 "#ea580c", "#dc2626", "#db2777", "#9333ea", "#374151"]
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def valid_color(color) -> bool:
    return bool(COLOR_RE.match(color or ""))


def _rgb(color):
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def mix(c1, c2, t):
    a, b = _rgb(c1), _rgb(c2)
    return _hex(x + (y - x) * t for x, y in zip(a, b))


def lighten(color, k=0.15):
    return mix(color, "#ffffff", k)


def darken(color, k=0.15):
    return mix(color, "#000000", k)


def contrast_color(color):
    """Чёрный или белый текст — что читаемее на данном фоне."""
    r, g, b = _rgb(color)
    return "#111111" if 0.299 * r + 0.587 * g + 0.114 * b > 160 else "#ffffff"


def palette(mode, accent):
    dark = mode == "dark"
    if dark:
        p = dict(bg="#14161b", card="#1e2128", field="#272b34",
                 text="#e8eaf0", muted="#9aa0ae", border="#2f3440")
    else:
        p = dict(bg="#f3f4f8", card="#ffffff", field="#f3f4f6",
                 text="#1f2430", muted="#6b7280", border="#e3e5ec")
    p.update(
        accent=accent,
        accent_fg=contrast_color(accent),
        accent_hover=lighten(accent, 0.12) if dark else darken(accent, 0.15),
        soft=mix(p["card"], accent, 0.14),
        header=mix(p["card"], p["text"], 0.05),
    )
    return p