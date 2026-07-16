"""生成PWA用的图标（一次性工具脚本，需要时可重新运行）。"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "icons"
BG = (43, 74, 46)       # 深绿色，呼应生态/土壤主题
LEAF = (168, 209, 141)  # 浅绿色叶片
VEIN = (43, 74, 46)


def make_icon(size: int, path: Path):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = int(size * 0.22)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)

    cx, cy = size / 2, size / 2
    r = size * 0.30
    draw.ellipse([cx - r, cy - r * 1.15, cx + r, cy + r * 0.85], fill=LEAF)
    draw.line([cx - r * 0.7, cy - r * 0.1, cx + r * 0.6, cy - r * 0.55],
               fill=VEIN, width=max(2, size // 60))

    img.save(path)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_icon(192, OUT_DIR / "icon-192.png")
    make_icon(512, OUT_DIR / "icon-512.png")
    make_icon(180, OUT_DIR / "apple-touch-icon.png")
    print("Icons written to", OUT_DIR)


if __name__ == "__main__":
    main()
