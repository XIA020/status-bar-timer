"""Generate the multi-size, transparent status bar timer icon."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
MASTER = 512


def render(size: int) -> Image.Image:
    scale = MASTER / size
    image = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # A dark, round field keeps the cyan ring visible in both light and dark trays.
    draw.ellipse((30, 30, 482, 482), fill=(20, 30, 39, 255),
                 outline=(28, 197, 224, 255), width=24)

    # Three smooth bars and a short baseline preserve the source mark at small sizes.
    baseline = 360
    bar_color = (244, 250, 252, 255)
    for left, top, right in ((132, 254, 198), (223, 185, 289), (314, 116, 380)):
        draw.rounded_rectangle((left, top, right, baseline), radius=21,
                               fill=bar_color)
    draw.rounded_rectangle((112, 376, 400, 392), radius=8,
                           fill=(28, 197, 224, 255))

    if size != MASTER:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def main() -> None:
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    icons = [render(size) for size in sizes]
    icons[-1].save(ROOT / "tray_icon.ico", format="ICO", append_images=icons[:-1],
                   sizes=[(size, size) for size in sizes])
    preview = ROOT / "build" / "tray_icon_preview.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    render(MASTER).save(preview)


if __name__ == "__main__":
    main()
