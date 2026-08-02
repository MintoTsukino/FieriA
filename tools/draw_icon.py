# FieriA アプリアイコン生成（fieria.ico）
# モチーフ: 吹き出し（会話）× 双葉（in fieri=生成途上）× 記憶の点（…）
# 「会話が記憶になって育つ家」を1枚に。フラットデザイン・カイト形の葉
# （曲線の葉は小サイズで潰れるため、菱形の記号性を採用。2026-08-02）
import os

from PIL import Image, ImageDraw

OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # リポジトリ直下
S = 256

BG = (29, 30, 34, 255)        # ダークテーマ系の紺墨
BUBBLE = (241, 236, 225, 255)  # 生成り（washi系のクリーム）
STEM = (86, 128, 66, 255)
LEAF = (100, 154, 72, 255)
LEAF_L = (148, 198, 104, 255)
DOT = (200, 172, 98, 255)      # 琥珀（記憶の色）


def draw(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 角丸スクエア背景
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=56, fill=BG)

    # 吹き出し（左下にしっぽ）
    d.rounded_rectangle([44, 46, 212, 170], radius=38, fill=BUBBLE)
    d.polygon([(78, 164), (64, 206), (120, 170)], fill=BUBBLE)

    # 茎
    d.rounded_rectangle([124, 94, 132, 148], radius=4, fill=STEM)

    # 双葉（カイト形。付け根=茎上部、先端=左右斜め上）
    d.polygon([(127, 100), (100, 98), (76, 64), (112, 70)], fill=LEAF)
    d.polygon([(129, 100), (156, 98), (180, 64), (144, 70)], fill=LEAF_L)

    # 記憶の点「…」
    for x, r in ((150, 8), (174, 7), (195, 6)):
        d.ellipse([x - r, 198 - r, x + r, 198 + r], fill=DOT)

    return img


def main():
    img = draw()
    ico_path = os.path.join(OUT, "fieria.ico")
    img.save(ico_path, format="ICO",
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("wrote", ico_path)


if __name__ == "__main__":
    main()
