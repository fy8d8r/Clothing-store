"""Фото товаров: копируются в папку product_images рядом с проектом,
в базе хранится ОТНОСИТЕЛЬНЫЙ путь — поэтому проект можно переносить на флешку и в git."""
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .config import ROOT

IMAGES_DIR = ROOT / "product_images"
MAX_SIDE = 1000
FILE_TYPES = [("Изображения", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("Все файлы", "*.*")]


def import_image(src):
    """Копирует выбранное фото в product_images (уменьшая и исправляя поворот),
    возвращает относительный путь вида 'product_images/product_ab12.jpg'."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        flat = Image.new("RGB", im.size, "white")  # прозрачность -> белый фон
        flat.paste(im, mask=im.split()[3])
    name = f"product_{uuid.uuid4().hex[:12]}.jpg"
    flat.save(IMAGES_DIR / name, "JPEG", quality=88)
    return f"{IMAGES_DIR.name}/{name}"


def resolve(path):
    """Находит файл фото. Понимает и относительные пути, и старые абсолютные
    (если проект перенесли, ищет файл по имени в product_images)."""
    if not path:
        return None
    p = Path(path)
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]  # имя файла из пути любой ОС
    for candidate in ((p if p.is_absolute() else ROOT / p), IMAGES_DIR / name):
        if candidate.is_file():
            return candidate
    return None


def delete_image(path):
    """Удаляет файл фото, только если он лежит в product_images."""
    f = resolve(path)
    if f is not None and f.parent == IMAGES_DIR:
        try:
            f.unlink()
        except OSError:
            pass


def load_box(path, box):
    """PIL-картинка, вписанная в рамку box=(w, h); None, если файла нет или он повреждён."""
    f = resolve(path)
    if f is None:
        return None
    try:
        with Image.open(f) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail(box, Image.LANCZOS)
        return im
    except (OSError, ValueError):
        return None
