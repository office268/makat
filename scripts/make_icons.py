"""יצירת אייקוני ה-PWA.

האייקון: לוחית רישוי ישראלית - צהוב על רקע כחול כהה. זה הסמל שהכי
מזוהה עם התהליך שהאפליקציה עושה, והוא קריא גם ב-48 פיקסלים.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "icons"

BRAND = (12, 74, 110)        # #0c4a6e - צבע סרגל הניווט
PLATE = (245, 197, 24)       # #f5c518 - צהוב לוחית
INK = (26, 26, 26)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _fit_font(draw, text, max_width, max_size):
    """מוצא את הגודל הגדול ביותר שנכנס לרוחב הנתון."""
    for size in range(max_size, 8, -2):
        font = ImageFont.truetype(FONT, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return ImageFont.truetype(FONT, 10)


def build(size, maskable=False):
    image = Image.new("RGBA", (size, size), BRAND + (255,))
    draw = ImageDraw.Draw(image)

    # אייקון maskable נחתך לעיגול, ולכן התוכן מצטמצם לאזור הבטוח
    inset = size * 0.22 if maskable else size * 0.12
    plate_w = size - inset * 2
    plate_h = plate_w * 0.42
    top = (size - plate_h) / 2
    radius = plate_h * 0.18

    draw.rounded_rectangle(
        [inset, top, inset + plate_w, top + plate_h],
        radius=radius, fill=PLATE, outline=INK, width=max(2, int(size * 0.018)),
    )

    text = "12·345·67"
    font = _fit_font(draw, text, plate_w * 0.82, int(plate_h * 0.62))
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (size / 2 - (bbox[2] - bbox[0]) / 2 - bbox[0],
         top + plate_h / 2 - (bbox[3] - bbox[1]) / 2 - bbox[1]),
        text, font=font, fill=INK,
    )
    return image


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for size in (192, 512):
        path = OUT / f"icon-{size}.png"
        build(size).save(path)
        written.append(path)
    for size in (192, 512):
        path = OUT / f"icon-{size}-maskable.png"
        build(size, maskable=True).save(path)
        written.append(path)

    apple = OUT / "apple-touch-icon.png"
    build(180).save(apple)
    written.append(apple)

    favicon = OUT / "favicon.png"
    build(64).save(favicon)
    written.append(favicon)

    for path in written:
        print(f"  {path.relative_to(ROOT)}  ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
