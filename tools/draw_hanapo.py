# ハナポ(Hanapo) 48x48 ドット絵ジェネレータ
# 参考仕様: 植木鉢が体のやさしいシルエット / 土の上から芽とつぼみが育つ /
#           葉の「耳」と根っこの「足」/ 感情は花と葉の様子で表す
# 鉢4色 / 土3色 / 葉3色 / 花2色 / 目3色 / 輪郭1px / 透過PNG
# コノハ・スズナと同じ作法・同じ抽象度で描く（記号性を揃える）
import os

from PIL import Image, ImageDraw

W = H = 48
OUT = os.path.dirname(os.path.abspath(__file__))

OUTLINE = (48, 30, 22, 255)
POT_D = (110, 60, 36, 255)
POT_M = (158, 90, 54, 255)
POT_L = (196, 122, 78, 255)
POT_LL = (226, 168, 120, 255)
SOIL_D = (44, 30, 20, 255)
SOIL_M = (68, 46, 30, 255)
SOIL_L = (92, 64, 42, 255)
LEAF_D = (62, 92, 48, 255)
LEAF_M = (104, 146, 76, 255)
LEAF_L = (146, 184, 106, 255)
FLOWER_D = (218, 122, 140, 255)
FLOWER_M = (244, 168, 182, 255)
FLOWER_L = (255, 214, 222, 255)
CORE = (248, 216, 120, 255)
EYE_D = (150, 96, 24, 255)
EYE_M = (240, 200, 96, 255)
EYE_L = (255, 246, 216, 255)
CHEEK = (216, 130, 120, 170)
SPARK = (255, 240, 176, 255)
SWEAT = (150, 195, 225, 255)
WATER = (126, 190, 224, 255)

CX = 23


def new_canvas():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def px(d, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        d.point((x, y), fill=c)


def hrow(d, x0, x1, y, c):
    for x in range(x0, x1 + 1):
        px(d, x, y, c)


# ---- 鉢（縁の帯＋下すぼまりの胴。行ごとの half_width） ----
RIM_ROWS = [14, 14, 14, 14]                       # y=26..29
POT_ROWS = [13, 13, 12, 12, 12, 11, 11, 11,       # y=30..37
            10, 10, 9, 9, 9]                       # y=38..42
POT_TOP = 26


def pot(d, cx=CX, top=POT_TOP):
    # 縁（明るい帯）
    for i, hw in enumerate(RIM_ROWS):
        y = top + i
        x0, x1 = cx - hw, cx + hw
        c = POT_LL if i <= 1 else POT_L
        hrow(d, x0, x1, y, c)
        px(d, x0, y, POT_M)
        px(d, x1, y, POT_M)
        px(d, x0 - 1, y, OUTLINE)
        px(d, x1 + 1, y, OUTLINE)
    hrow(d, cx - RIM_ROWS[0], cx + RIM_ROWS[0], top - 1, OUTLINE)
    # 縁と胴の境目
    hrow(d, cx - 13, cx + 13, top + len(RIM_ROWS), POT_D)
    # 胴
    for i, hw in enumerate(POT_ROWS):
        y = top + len(RIM_ROWS) + i
        x0, x1 = cx - hw, cx + hw
        c = POT_L if i <= 2 else (POT_M if i <= 8 else POT_D)
        hrow(d, x0, x1, y, c)
        px(d, x0, y, POT_D)
        px(d, x1, y, POT_D)
        # 左側の光、右側の影（素焼きの丸み）
        if 1 <= i <= 9:
            px(d, x0 + 1, y, POT_LL)
            px(d, x1 - 1, y, POT_D)
        px(d, x0 - 1, y, OUTLINE)
        px(d, x1 + 1, y, OUTLINE)
    bottom = top + len(RIM_ROWS) + len(POT_ROWS)
    hrow(d, cx - POT_ROWS[-1], cx + POT_ROWS[-1], bottom, OUTLINE)


def soil(d, cx=CX, y=25):
    """鉢の上に盛られた土（縁の内側に収める）。"""
    hrow(d, cx - 12, cx + 12, y + 1, SOIL_M)
    hrow(d, cx - 11, cx + 11, y, SOIL_M)
    hrow(d, cx - 9, cx + 9, y - 1, SOIL_L)
    hrow(d, cx - 12, cx + 12, y + 2, SOIL_D)
    hrow(d, cx - 10, cx + 10, y - 2, SOIL_D)
    for dx in (-8, -3, 4, 9):
        px(d, cx + dx, y - 1, SOIL_D)
    for dx in (-6, 1, 7):
        px(d, cx + dx, y, SOIL_L)


def roots(d, cx=CX, y=43):
    """根っこの足。左右2本ずつ、地面へ伸びる。"""
    for side in (-1, 1):
        bx = cx + side * 6
        hrow(d, bx - 1, bx + 1, y, SOIL_L)
        hrow(d, bx - 1, bx + 1, y + 1, SOIL_M)
        px(d, bx + side * 3, y + 1, SOIL_M)
        px(d, bx + side * 3, y, SOIL_L)
        hrow(d, bx - 2, bx + 2, y + 2, OUTLINE)
        px(d, bx + side * 3, y + 2, OUTLINE)


def stem(d, cx=CX, base_y=23, top_y=14, tilt=0):
    """土から伸びる茎。tiltで先端を左右へ。"""
    n = base_y - top_y
    for i in range(n + 1):
        y = base_y - i
        px(d, cx + round(tilt * i / max(n, 1)), y, LEAF_D)
        if i % 3 == 1:
            px(d, cx + round(tilt * i / max(n, 1)) - 1, y, LEAF_M)


def stem_leaf(d, x, y, flip=False):
    """茎に付く小さな葉。"""
    rows = ((0, 2), (0, 4), (0, 4), (1, 2))
    for i, (off, wdt) in enumerate(rows):
        yy = y + i
        if flip:
            x1 = x - off
            x0 = x1 - wdt + 1
            px(d, x0 - 1, yy, OUTLINE)
        else:
            x0 = x + off
            x1 = x0 + wdt - 1
            px(d, x1 + 1, yy, OUTLINE)
        hrow(d, x0, x1, yy, LEAF_L if i <= 1 else LEAF_M)


def ear_leaf(d, x, y, flip=False, droop=0):
    """鉢の左右に付く葉の耳。droopで下向きにしおれる。"""
    rows = ((0, 2), (0, 4), (1, 4), (0, 2))
    for i, (off, wdt) in enumerate(rows):
        yy = y + i + round(droop * i / len(rows))
        if flip:
            x1 = x - off
            x0 = x1 - wdt + 1
            px(d, x0 - 1, yy, OUTLINE)
        else:
            x0 = x + off
            x1 = x0 + wdt - 1
            px(d, x1 + 1, yy, OUTLINE)
        hrow(d, x0, x1, yy, LEAF_L if i <= 1 else LEAF_M)
    px(d, x + (-2 if flip else 2), y + 1, LEAF_D)  # 葉脈


def bud(d, cx=CX, y=13, tilt=0):
    """閉じたつぼみ（通常状態）。"""
    tx = cx + tilt
    hrow(d, tx - 1, tx + 1, y + 3, FLOWER_D)
    hrow(d, tx - 2, tx + 2, y + 1, FLOWER_M)
    hrow(d, tx - 2, tx + 2, y + 2, FLOWER_M)
    hrow(d, tx - 1, tx + 1, y, FLOWER_L)
    px(d, tx - 1, y + 1, FLOWER_L)
    hrow(d, tx - 1, tx + 1, y - 1, OUTLINE)
    for yy in range(y, y + 4):
        px(d, tx - 3, yy, OUTLINE)
        px(d, tx + 3, yy, OUTLINE)
    hrow(d, tx - 2, tx + 2, y + 4, OUTLINE)
    # がく
    px(d, tx - 2, y + 4, LEAF_M)
    px(d, tx + 2, y + 4, LEAF_M)


def bloom(d, cx=CX, y=12, tilt=0):
    """開いた花（嬉しい・思い出し状態）。"""
    tx = cx + tilt
    # 花びら5枚を放射状に
    petals = ((0, -3), (-3, -1), (3, -1), (-2, 2), (2, 2))
    for ox, oy in petals:
        fx, fy = tx + ox, y + oy
        for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
            px(d, fx + dx, fy + dy, FLOWER_M)
        px(d, fx, fy - 1, FLOWER_L)
        px(d, fx - 1, fy, FLOWER_L)
    for ox, oy in petals:
        fx, fy = tx + ox, y + oy
        px(d, fx + 2 * (1 if ox >= 0 else -1), fy, FLOWER_D)
    # 花芯
    for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
        px(d, tx + dx, y + dy, CORE)
    px(d, tx, y, (255, 244, 200, 255))


def eyes(d, mode="open", cx=CX, y=35):
    lx, rx_ = cx - 5, cx + 5
    if mode == "open":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y - 2, ex + 2, y + 3], fill=EYE_M)
            hrow(d, ex - 1, ex + 2, y + 3, EYE_D)
            px(d, ex, y - 1, EYE_L); px(d, ex + 1, y - 1, EYE_L)
            px(d, ex, y, EYE_L)
            hrow(d, ex, ex + 1, y - 3, OUTLINE)
            hrow(d, ex, ex + 1, y + 4, OUTLINE)
            for yy in range(y - 1, y + 3):
                px(d, ex - 2, yy, OUTLINE)
                px(d, ex + 3, yy, OUTLINE)
            px(d, ex - 1, y - 2, OUTLINE); px(d, ex + 2, y - 2, OUTLINE)
            px(d, ex - 1, y + 3, OUTLINE); px(d, ex + 2, y + 3, OUTLINE)
    elif mode == "half":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y, ex + 2, y + 2], fill=EYE_M)
            px(d, ex, y, EYE_L)
            hrow(d, ex - 1, ex + 2, y - 1, OUTLINE)
            hrow(d, ex - 1, ex + 2, y + 3, OUTLINE)
    elif mode == "happy":
        for ex in (lx, rx_):
            px(d, ex - 2, y + 1, OUTLINE); px(d, ex + 2, y + 1, OUTLINE)
            px(d, ex - 1, y, OUTLINE); px(d, ex + 1, y, OUTLINE)
            px(d, ex, y - 1, OUTLINE)
            px(d, ex - 1, y + 1, EYE_L); px(d, ex + 1, y + 1, EYE_L)
    elif mode == "x":
        for ex in (lx, rx_):
            px(d, ex - 1, y - 1, OUTLINE); px(d, ex + 1, y - 1, OUTLINE)
            px(d, ex, y, OUTLINE)
            px(d, ex - 1, y + 1, OUTLINE); px(d, ex + 1, y + 1, OUTLINE)


def mouth(d, kind="dot", cx=CX, y=40):
    if kind == "dot":
        # 下に凸の小さな笑み。鉢の茶色に埋もれないよう明色で縁取る
        for ox in (-2, 2):
            px(d, cx + ox, y - 1, OUTLINE)
            px(d, cx + ox, y - 2, POT_LL)
        for ox in (-1, 0, 1):
            px(d, cx + ox, y, OUTLINE)
            px(d, cx + ox, y + 1, POT_LL)
        px(d, cx - 3, y - 1, POT_LL); px(d, cx + 3, y - 1, POT_LL)
    elif kind == "open":
        d.rectangle([cx - 1, y - 1, cx + 1, y + 1], fill=FLOWER_D)
        px(d, cx, y - 1, FLOWER_L)
        hrow(d, cx - 1, cx + 1, y - 2, OUTLINE)
        hrow(d, cx - 1, cx + 1, y + 2, OUTLINE)
        for ox in (-2, 2):
            px(d, cx + ox, y, OUTLINE)


def cheeks(d, cx=CX, y=38, strong=False):
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


def base(eye_mode="open", mouth_kind="dot", flower="bud",
         stem_tilt=0, ear_droop=0):
    img = new_canvas()
    d = ImageDraw.Draw(img)
    roots(d)
    pot(d)
    soil(d)
    stem(d, tilt=stem_tilt)
    stem_leaf(d, CX + 1, 18, flip=False)
    stem_leaf(d, CX - 1, 20, flip=True)
    ear_leaf(d, 10, 29, flip=True, droop=ear_droop)
    ear_leaf(d, 36, 29, flip=False, droop=ear_droop)
    if flower == "bud":
        bud(d, tilt=stem_tilt)
    elif flower == "bloom":
        bloom(d, tilt=stem_tilt)
    eyes(d, eye_mode)
    mouth(d, mouth_kind)
    return img, d


def main():
    os.makedirs(OUT, exist_ok=True)

    # 通常: つぼみ
    img, d = base("open", "dot", "bud")
    img.save(os.path.join(OUT, "hanapo_idle.png"))

    # 考え中: 半目・茎が傾く・思考の点
    img, d = base("half", "dot", "bud", stem_tilt=2)
    for i, x in enumerate((38, 41, 44)):
        px(d, x, 20 - (i % 2), LEAF_L)
    img.save(os.path.join(OUT, "hanapo_thinking.png"))

    # お手伝い中(writing): じょうろで水やり
    img, d = base("happy", "open", "bud")
    d.rectangle([37, 20, 43, 25], fill=POT_LL, outline=OUTLINE)
    hrow(d, 34, 36, 22, POT_LL)
    px(d, 33, 23, OUTLINE)
    for wx, wy in ((34, 25), (33, 27), (35, 28)):
        px(d, wx, wy, WATER)
    img.save(os.path.join(OUT, "hanapo_writing.png"))

    # 思い出す(recall): 花がひらいて光が満ちる
    img, d = base("open", "open", "bloom")
    sparkle(d, 6, 16); sparkle(d, 41, 9); sparkle(d, 8, 36)
    img.save(os.path.join(OUT, "hanapo_recall.png"))

    # 甘える(love): 花がほころび、照れ頬とハート
    img, d = base("happy", "open", "bloom")
    cheeks(d, strong=True)
    hx, hy = 38, 18
    for ox, oy in ((-1, 0), (0, 0), (1, 0), (2, 0), (-1, -1), (2, -1),
                   (0, 1), (1, 1)):
        px(d, hx + ox, hy + oy, FLOWER_M)
    px(d, hx, hy + 2, FLOWER_M)
    img.save(os.path.join(OUT, "hanapo_love.png"))

    # 困る(error): ×目・汗・葉がしおれ、つぼみもうつむく
    img, d = base("x", "dot", "bud", stem_tilt=-3, ear_droop=3)
    px(d, 10, 22, SWEAT); px(d, 10, 23, SWEAT)
    px(d, 9, 23, (200, 226, 245, 255))
    img.save(os.path.join(OUT, "hanapo_error.png"))

    for name in ("idle", "thinking", "writing", "recall", "error", "love"):
        p = os.path.join(OUT, f"hanapo_{name}.png")
        Image.open(p).resize((W * 8, H * 8), Image.NEAREST).save(
            os.path.join(OUT, f"preview_hanapo_{name}.png"))
    print("done")


if __name__ == "__main__":
    main()
