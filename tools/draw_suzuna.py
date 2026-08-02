# スズナ(Suzuna) 48x48 ドット絵ジェネレータ
# 参考仕様: 苔玉のふわ体(記憶のぬくもり) / 頭頂の双葉と葉の耳 / 琥珀の目 /
#           小花とツタの装飾 / 土と根
# 体4-5色 / 葉4色 / 目3色 / 花3色 / アクセント2色 / 輪郭1px / 透過PNG
# コノハ(draw_konoha.py)と同じ作法・同じ抽象度で描く（記号性を揃える）
import os

from PIL import Image, ImageDraw

W = H = 48
OUT = os.path.dirname(os.path.abspath(__file__))

OUTLINE = (28, 40, 26, 255)
BODY_D = (58, 84, 52, 255)
BODY_M = (86, 118, 70, 255)
BODY_L = (114, 148, 88, 255)
BODY_LL = (146, 178, 112, 255)
LEAF_D = (74, 116, 58, 255)
LEAF_M = (122, 172, 78, 255)
LEAF_L = (162, 204, 108, 255)
LEAF_LL = (198, 228, 148, 255)
EYE_D = (168, 112, 24, 255)
EYE_M = (240, 182, 62, 255)
EYE_L = (255, 238, 176, 255)
FLOWER_D = (214, 188, 156, 255)
FLOWER_M = (246, 238, 214, 255)
FLOWER_L = (255, 252, 240, 255)
PINK = (238, 176, 186, 255)
SOIL_D = (72, 54, 40, 255)
SOIL_M = (104, 78, 56, 255)
CHEEK = (206, 142, 132, 170)
SPARK = (255, 240, 176, 255)
SWEAT = (150, 195, 225, 255)
WATER = (126, 190, 224, 255)


def new_canvas():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def px(d, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        d.point((x, y), fill=c)


def hrow(d, x0, x1, y, c):
    for x in range(x0, x1 + 1):
        px(d, x, y, c)


# ---- 苔玉の体（行ごとの half_width。中心cx=23） ----
# 横に潰れて見えたため、幅を絞って行数を増やし丸みを出す（実機FBでの調整）
BODY_ROWS = [  # top から下へ 20行
    5, 8, 10, 11, 12, 12, 13, 13, 13, 13, 13, 13, 13, 12, 12, 11, 10, 9, 7, 5,
]
MOSS_TUFT = [3, 6, 9, 12, 15, 17]  # 苔の起毛を出す行(index)


def body(d, cx=23, top=21):
    n = len(BODY_ROWS)
    for i, hw in enumerate(BODY_ROWS):
        y = top + i
        x0, x1 = cx - hw, cx + hw
        # 上部は日の当たる明色、下部は影
        if i <= 3:
            c = BODY_L
        elif i >= n - 5:
            c = BODY_D
        else:
            c = BODY_M
        hrow(d, x0, x1, y, c)
        px(d, x0, y, BODY_D)
        px(d, x1, y, BODY_D)
        if 2 <= i <= 6:
            px(d, x0 + 1, y, BODY_LL)
            px(d, x1 - 1, y, BODY_LL)
        px(d, x0 - 1, y, OUTLINE)
        px(d, x1 + 1, y, OUTLINE)
    hrow(d, cx - BODY_ROWS[0], cx + BODY_ROWS[0], top - 1, OUTLINE)
    hrow(d, cx - BODY_ROWS[-1], cx + BODY_ROWS[-1], top + n, OUTLINE)
    # 苔の起毛（輪郭に密着した1pxのギザ）
    for i in MOSS_TUFT:
        y = top + i
        hw = BODY_ROWS[i]
        px(d, cx - hw - 2, y, OUTLINE)
        px(d, cx + hw + 2, y, OUTLINE)
    for dx in (-8, -3, 2, 7):
        px(d, cx + dx, top - 2, OUTLINE)
    # 苔のまだら（明色の粒）
    for mx, my in ((-9, 3), (-4, 6), (6, 4), (10, 8), (-11, 9), (3, 2)):
        px(d, cx + mx, top + my, BODY_LL)
    for mx, my in ((-7, 12), (5, 13), (9, 11), (-2, 15)):
        px(d, cx + mx, top + my, BODY_D)


def sprout(d, cx=23, base_y=20, tilt=0):
    """頭頂の双葉。tiltで左右へ少し傾ける。"""
    # 茎
    for i, y in enumerate(range(base_y, base_y - 6, -1)):
        px(d, cx + round(tilt * i / 6), y, LEAF_D)
    tip = base_y - 6
    tx = cx + round(tilt)
    # 左の葉
    for i, (off, wdt) in enumerate(((0, 3), (-1, 5), (-2, 5), (-1, 4), (0, 2))):
        y = tip + i
        x1 = tx - 1 + off
        x0 = x1 - wdt + 1
        c = LEAF_L if i <= 1 else LEAF_M
        hrow(d, x0, x1, y, c)
        px(d, x0 - 1, y, OUTLINE)
    # 右の葉
    for i, (off, wdt) in enumerate(((0, 3), (-1, 5), (-2, 5), (-1, 4), (0, 2))):
        y = tip + i
        x0 = tx + 1 - off
        x1 = x0 + wdt - 1
        c = LEAF_LL if i <= 1 else LEAF_L
        hrow(d, x0, x1, y, c)
        px(d, x1 + 1, y, OUTLINE)
    hrow(d, tx - 3, tx + 3, tip - 1, OUTLINE)
    px(d, tx - 3, tip + 1, LEAF_LL)
    px(d, tx + 3, tip + 1, LEAF_LL)


def ear_leaf(d, x, y, flip=False, tilt=0):
    """横向きの葉の耳。(x,y)=根本の位置。flipで左向き。"""
    rows = ((0, 2), (0, 3), (1, 4), (0, 3), (0, 2))
    for i, (off, wdt) in enumerate(rows):
        yy = y + i
        shift = round(tilt * i / len(rows))
        if flip:
            x1 = x - off + shift
            x0 = x1 - wdt + 1
            px(d, x0 - 1, yy, OUTLINE)
        else:
            x0 = x + off + shift
            x1 = x0 + wdt - 1
            px(d, x1 + 1, yy, OUTLINE)
        c = LEAF_L if i <= 2 else LEAF_M
        hrow(d, x0, x1, yy, c)
    px(d, x + (-2 if flip else 2), y + 2, LEAF_D)  # 葉脈


def clover_tail(d, x=38, y=34):
    """クローバーのつぼみ尻尾（右下から伸びるツタの先）。"""
    for i, (dx, dy) in enumerate(((0, 0), (1, -1), (2, -1), (3, -2))):
        px(d, x + dx, y + dy, SOIL_M)
    cx, cy = x + 4, y - 3
    for ox, oy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
        px(d, cx + ox, cy + oy, LEAF_M)
    px(d, cx, cy, LEAF_L)


def flowers(d, cx=23, top=21):
    """体に咲く小花（2輪）。"""
    for fx, fy, core in ((cx - 9, top + 4, PINK), (cx + 9, top + 13, FLOWER_L)):
        for ox, oy in ((0, -2), (-2, 0), (2, 0), (0, 2),
                       (-1, -1), (1, -1), (-1, 1), (1, 1)):
            px(d, fx + ox, fy + oy, FLOWER_M)
        for ox, oy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            px(d, fx + ox, fy + oy, FLOWER_L)
        px(d, fx, fy, core)


def soil(d, cx=23, y=41):
    """足元の土と根。"""
    hrow(d, cx - 7, cx + 7, y, SOIL_M)
    hrow(d, cx - 6, cx + 6, y + 1, SOIL_D)
    px(d, cx - 8, y, OUTLINE)
    px(d, cx + 8, y, OUTLINE)
    hrow(d, cx - 6, cx + 6, y + 2, OUTLINE)
    for dx in (-5, -1, 4):
        px(d, cx + dx, y, SOIL_D)


def eyes(d, mode="open", cx=23, y=31):
    lx, rx_ = cx - 5, cx + 5
    if mode == "open":
        # 4x6の大きめ楕円。琥珀の光がしっかり見えるようにする（実機FBでの調整）
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y - 2, ex + 2, y + 3], fill=EYE_M)
            for ox in (-1, 2):
                px(d, ex + ox, y - 2, EYE_D)
                px(d, ex + ox, y + 3, EYE_D)
            hrow(d, ex - 1, ex + 2, y + 3, EYE_D)
            px(d, ex, y - 1, EYE_L); px(d, ex + 1, y - 1, EYE_L)
            px(d, ex, y, EYE_L)
            # 輪郭で体から浮かせる
            hrow(d, ex, ex + 1, y - 3, OUTLINE)
            hrow(d, ex, ex + 1, y + 4, OUTLINE)
            for yy in range(y - 1, y + 3):
                px(d, ex - 2, yy, OUTLINE)
                px(d, ex + 3, yy, OUTLINE)
            px(d, ex - 1, y - 2, OUTLINE); px(d, ex + 2, y - 2, OUTLINE)
            px(d, ex - 1, y + 3, OUTLINE); px(d, ex + 2, y + 3, OUTLINE)
    elif mode == "half":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y, ex + 1, y + 2], fill=EYE_M)
            px(d, ex - 1, y, EYE_D); px(d, ex + 1, y, EYE_D)
            px(d, ex, y, EYE_L)
            hrow(d, ex - 1, ex + 1, y - 1, OUTLINE)
    elif mode == "happy":
        # 弧を描いた笑い目
        for ex in (lx, rx_):
            px(d, ex - 2, y, EYE_M); px(d, ex + 2, y, EYE_M)
            px(d, ex - 1, y - 1, EYE_L); px(d, ex + 1, y - 1, EYE_L)
            px(d, ex, y - 1, EYE_M)
    elif mode == "x":
        for ex in (lx, rx_):
            px(d, ex - 1, y - 1, EYE_D); px(d, ex + 1, y - 1, EYE_D)
            px(d, ex, y, EYE_D)
            px(d, ex - 1, y + 1, EYE_D); px(d, ex + 1, y + 1, EYE_D)


def mouth(d, kind="dot", cx=23, y=37):
    # 体の暗色に埋もれないよう、口はクリーム色で描いて輪郭を添える
    if kind == "dot":
        # 下に凸の小さな笑み（牙に見えないよう弧一本で描く）
        for ox in (-2, 2):
            px(d, cx + ox, y - 1, FLOWER_M)
        for ox in (-1, 0, 1):
            px(d, cx + ox, y, FLOWER_M)
        px(d, cx - 3, y - 2, OUTLINE); px(d, cx + 3, y - 2, OUTLINE)
    elif kind == "open":
        d.rectangle([cx - 1, y - 1, cx + 1, y + 1], fill=PINK)
        px(d, cx, y - 1, FLOWER_L)
        for ox in (-2, 2):
            px(d, cx + ox, y, OUTLINE)
        hrow(d, cx - 1, cx + 1, y - 2, OUTLINE)
        hrow(d, cx - 1, cx + 1, y + 2, OUTLINE)


def cheeks(d, cx=23, y=35, strong=False):
    for ex, side in ((cx - 9, 1), (cx + 9, -1)):
        px(d, ex, y, CHEEK)
        px(d, ex + side, y, CHEEK)
        if strong:
            px(d, ex, y + 1, CHEEK)
            px(d, ex + side, y + 1, CHEEK)


def sparkle(d, x, y):
    px(d, x, y, SPARK)
    px(d, x - 1, y, SPARK); px(d, x + 1, y, SPARK)
    px(d, x, y - 1, SPARK); px(d, x, y + 1, SPARK)


def base(eye_mode="open", mouth_kind="dot", sprout_tilt=0, ear_tilt=0):
    img = new_canvas()
    d = ImageDraw.Draw(img)
    soil(d)
    body(d)
    ear_leaf(d, 11, 28, flip=True, tilt=-ear_tilt)
    ear_leaf(d, 35, 28, flip=False, tilt=ear_tilt)
    sprout(d, tilt=sprout_tilt)
    flowers(d)
    clover_tail(d)
    eyes(d, eye_mode)
    mouth(d, mouth_kind)
    return img, d


def main():
    os.makedirs(OUT, exist_ok=True)

    # 通常
    img, d = base("open", "dot")
    img.save(os.path.join(OUT, "suzuna_idle.png"))

    # 考え中: 半目・双葉を傾ける・思考の点
    img, d = base("half", "dot", sprout_tilt=2, ear_tilt=1)
    for i, x in enumerate((41, 44, 47)):
        px(d, x, 18 - (i % 2), LEAF_L)
    img.save(os.path.join(OUT, "suzuna_thinking.png"))

    # お手伝い中(writing): じょうろで水やり
    img, d = base("happy", "open")
    d.rectangle([38, 22, 44, 27], fill=FLOWER_D, outline=OUTLINE)
    hrow(d, 35, 37, 24, FLOWER_D)
    px(d, 34, 25, OUTLINE)
    for i, (wx, wy) in enumerate(((35, 27), (34, 29), (36, 30), (33, 32))):
        px(d, wx, wy, WATER)
    img.save(os.path.join(OUT, "suzuna_writing.png"))

    # 思い出す(recall): 花が開いて光が満ちる
    img, d = base("open", "open")
    sparkle(d, 6, 18); sparkle(d, 41, 12); sparkle(d, 8, 38)
    for fx, fy in ((15, 24), (31, 27)):
        for ox, oy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            px(d, fx + ox, fy + oy, FLOWER_L)
        px(d, fx, fy, EYE_M)
    img.save(os.path.join(OUT, "suzuna_recall.png"))

    # 甘える(love): 照れ頬とハート
    img, d = base("happy", "open")
    cheeks(d, strong=True)
    hx, hy = 38, 16
    for ox, oy in ((-1, 0), (0, 0), (1, 0), (2, 0), (-1, -1), (2, -1)):
        px(d, hx + ox, hy + oy, PINK)
    for ox, oy in ((0, 1), (1, 1)):
        px(d, hx + ox, hy + oy, PINK)
    px(d, hx, hy + 2, PINK)
    img.save(os.path.join(OUT, "suzuna_love.png"))

    # 困る(error): ×目・汗・双葉がしおれる
    img, d = base("x", "dot", sprout_tilt=-3, ear_tilt=-1)
    px(d, 11, 24, SWEAT); px(d, 11, 25, SWEAT)
    px(d, 10, 25, (200, 226, 245, 255))
    img.save(os.path.join(OUT, "suzuna_error.png"))

    for name in ("idle", "thinking", "writing", "recall", "error", "love"):
        p = os.path.join(OUT, f"suzuna_{name}.png")
        Image.open(p).resize((W * 8, H * 8), Image.NEAREST).save(
            os.path.join(OUT, f"preview_suzuna_{name}.png"))
    print("done")


if __name__ == "__main__":
    main()
