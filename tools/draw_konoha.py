# コノハ(KonohA) 48x48 ドット絵ジェネレータ v2
# 参考仕様: 黒ふわ体(記憶の媒) / 紙の葉の耳・尻尾(記録のページ) / 琥珀の目(想い)
# 体4-5色 / 紙4色 / 目3色 / アクセント1-2色 / 輪郭1px / 透過PNG
import os

from PIL import Image, ImageDraw

W = H = 48
OUT = os.path.dirname(os.path.abspath(__file__))

OUTLINE = (16, 15, 18, 255)
BODY_D = (24, 23, 27, 255)
BODY_M = (36, 34, 40, 255)
BODY_L = (52, 49, 57, 255)
BODY_LL = (74, 70, 80, 255)
PAPER_D = (150, 128, 90, 255)
PAPER_M = (200, 176, 130, 255)
PAPER_L = (228, 210, 168, 255)
PAPER_LL = (244, 234, 204, 255)
EYE_D = (168, 96, 18, 255)
EYE_M = (242, 172, 48, 255)
EYE_L = (255, 234, 160, 255)
CHEEK = (196, 108, 112, 175)
SPARK = (255, 226, 150, 255)
SWEAT = (150, 195, 225, 255)


def new_canvas():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def px(d, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        d.point((x, y), fill=c)


def hrow(d, x0, x1, y, c):
    for x in range(x0, x1 + 1):
        px(d, x, y, c)


# ---- 紙の葉（行ごとの [左端offset, 幅] で葉形を定義。tipが上、baseが下） ----
LEAF_ROWS = [  # 上から下へ 11行。 (offset, width)
    (4, 1),
    (3, 3),
    (2, 4),
    (1, 6),
    (1, 6),
    (0, 7),
    (1, 6),
    (1, 6),
    (2, 4),
    (3, 3),
    (4, 2),
]


def paper_leaf(d, x, y, flip=False, tilt=0):
    """紙の葉。(x,y)=葉の左上基準。flipで左右反転。tiltで行ごとに横ずらし（傾き）。"""
    rows = len(LEAF_ROWS)
    for i, (off, wdt) in enumerate(LEAF_ROWS):
        shift = round(tilt * i / rows)
        if flip:
            x0 = x + (7 - off - wdt) + shift
        else:
            x0 = x + off + shift
        x1 = x0 + wdt - 1
        # 本体色: 上1/3は明、中は基本、下は影寄り
        c = PAPER_L if i <= 2 else (PAPER_M if i <= 6 else PAPER_D)
        hrow(d, x0, x1, y + i, c)
        # 輪郭（左右端）
        px(d, x0 - 1, y + i, OUTLINE)
        px(d, x1 + 1, y + i, OUTLINE)
    # 上下端の輪郭
    for i in (0,):
        off, wdt = LEAF_ROWS[0]
        x0 = x + ((7 - off - wdt) if flip else off)
        hrow(d, x0 - 1, x0 + wdt, y - 1, OUTLINE)
    off, wdt = LEAF_ROWS[-1]
    shift = round(tilt)
    x0 = (x + (7 - off - wdt) + shift) if flip else (x + off + shift)
    hrow(d, x0 - 1, x0 + wdt, y + rows, OUTLINE)
    # 中央の葉脈（縦）＋横の小脈
    vx = x + 3 + (0 if not flip else 1)
    for i in range(1, rows - 1):
        shift = round(tilt * i / rows)
        px(d, vx + shift, y + i, PAPER_D)
    for i in (3, 6):
        shift = round(tilt * i / rows)
        px(d, vx + shift - 1, y + i, PAPER_D if not flip else PAPER_LL)
        px(d, vx + shift + 1, y + i, PAPER_LL if not flip else PAPER_D)
    # 先端ハイライト
    off, wdt = LEAF_ROWS[1]
    x0 = x + ((7 - off - wdt) if flip else off)
    px(d, x0 + 1, y + 1, PAPER_LL)


# ---- 体（行幅指定のふわシルエット） ----
BODY_ROWS = [  # y=19..42 (offset_from_cx, half_width) 中心cx=23
    (0, 6), (0, 8), (0, 10), (0, 11), (0, 12), (0, 12), (0, 13), (0, 13),
    (0, 13), (0, 13), (0, 13), (0, 13), (0, 13), (0, 13), (0, 12), (0, 12),
    (0, 11), (0, 10), (0, 8),
]
FUR_L = [1, 3, 6, 9, 12, 15, 17]   # 起毛を出す行(index)


def body(d, cx=23, top=20):
    n = len(BODY_ROWS)
    for i, (_, hw) in enumerate(BODY_ROWS):
        y = top + i
        x0, x1 = cx - hw, cx + hw
        # 塗り: 上部ハイライト帯 / 中間 / 下部影
        if i <= 3:
            c = BODY_L
        elif i >= n - 5:
            c = BODY_D
        else:
            c = BODY_M
        hrow(d, x0, x1, y, c)
        # 側面の立体: 左右端1pxを影/明で
        px(d, x0, y, BODY_D)
        px(d, x1, y, BODY_D)
        if 2 <= i <= 6:
            px(d, x0 + 1, y, BODY_LL)
            px(d, x1 - 1, y, BODY_LL)
        # 輪郭
        px(d, x0 - 1, y, OUTLINE)
        px(d, x1 + 1, y, OUTLINE)
    # 上端・下端の輪郭
    hw0 = BODY_ROWS[0][1]
    hrow(d, cx - hw0, cx + hw0, top - 1, OUTLINE)
    hwn = BODY_ROWS[-1][1]
    hrow(d, cx - hwn, cx + hwn, top + n, OUTLINE)
    # 起毛: 輪郭に密着した1pxのギザ（浮かせない）
    for i in FUR_L:
        y = top + i
        hw = BODY_ROWS[i][1]
        px(d, cx - hw - 2, y, OUTLINE)
        px(d, cx + hw + 2, y, OUTLINE)
    for dx in (-9, -4, 1, 6, 10):
        px(d, cx + dx, top - 2, OUTLINE)
    for dx in (-7, -2, 3, 8):
        px(d, cx + dx, top + n + 1, OUTLINE)
    # 頭頂の毛ハイライト
    for dx in (-6, 0, 5):
        px(d, cx + dx, top, BODY_LL)


def eyes(d, mode="open", cx=23, y=29):
    lx, rx_ = cx - 6, cx + 6
    if mode == "open":
        for ex in (lx, rx_):
            # 3x5の縦長楕円風
            d.rectangle([ex - 1, y - 2, ex + 1, y + 2], fill=EYE_M)
            px(d, ex - 1, y - 2, EYE_D); px(d, ex + 1, y - 2, EYE_D)
            px(d, ex - 1, y + 2, EYE_D); px(d, ex + 1, y + 2, EYE_D)
            px(d, ex, y - 1, EYE_L); px(d, ex - 1, y - 1, EYE_L)
            px(d, ex, y + 2, EYE_D)
    elif mode == "half":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y, ex + 1, y + 2], fill=EYE_M)
            px(d, ex - 1, y, EYE_D); px(d, ex + 1, y, EYE_D)
            px(d, ex, y, EYE_L)
            hrow(d, ex - 1, ex + 1, y - 1, OUTLINE)
    elif mode == "happy":
        for ex in (lx, rx_):
            px(d, ex - 2, y, EYE_M); px(d, ex + 2, y, EYE_M)
            px(d, ex - 1, y - 1, EYE_L); px(d, ex + 1, y - 1, EYE_L)
            px(d, ex, y - 1, EYE_M)
    elif mode == "x":
        for ex in (lx, rx_):
            for dd in (-1, 0, 1):
                px(d, ex + dd, y + dd, EYE_M)
                px(d, ex + dd, y - dd, EYE_M)
            px(d, ex, y, EYE_L)


def face_extras(d, cx=23, mouth="dot"):
    for ex, side in ((cx - 10, 1), (cx + 10, -1)):
        px(d, ex, 32, CHEEK)
        px(d, ex + side, 32, CHEEK)
    MOUTH = (120, 110, 122, 255)
    if mouth == "dot":
        px(d, cx - 1, 33, MOUTH); px(d, cx, 34, MOUTH); px(d, cx + 1, 33, MOUTH)
    elif mouth == "open":
        px(d, cx - 1, 33, MOUTH); px(d, cx + 1, 33, MOUTH)
        hrow(d, cx - 1, cx + 1, 34, MOUTH)
        px(d, cx, 34, (150, 84, 90, 255))


def feet(d, cx=23, y=40):
    for fx in (cx - 7, cx + 4):
        hrow(d, fx, fx + 2, y, BODY_D)
        px(d, fx - 1, y, OUTLINE); px(d, fx + 3, y, OUTLINE)
        hrow(d, fx, fx + 2, y + 1, OUTLINE)


def sparkle(d, x, y):
    px(d, x, y - 1, SPARK); px(d, x, y + 1, SPARK)
    px(d, x - 1, y, SPARK); px(d, x + 1, y, SPARK)
    px(d, x, y, PAPER_LL)


def base(eye_mode="open", mouth="dot", ear_tilt=(-2, 2)):
    img = new_canvas()
    d = ImageDraw.Draw(img)
    body(d)
    # 尻尾: 体の右横（腰のあたり）から外へ向けて生やす。根本の列が体の
    # 右端に重なることで「生えてる」接続感を出す（第3の耳に見えないよう耳より低く）
    paper_leaf(d, 34, 22, flip=False, tilt=4)
    # 耳: 根本を頭のドームに重ねる（葉の下2〜3行が体の上端に乗る）
    paper_leaf(d, 12, 11, flip=True, tilt=ear_tilt[0])
    paper_leaf(d, 27, 11, flip=False, tilt=ear_tilt[1])
    eyes(d, eye_mode)
    face_extras(d, mouth=mouth)
    feet(d)
    return img, d


def main():
    os.makedirs(OUT, exist_ok=True)
    img, d = base("open", "dot")
    img.save(os.path.join(OUT, "idle.png"))

    img, d = base("half", "dot")
    for i, x in enumerate((40, 43, 46)):
        px(d, x, 20 - (i % 2), BODY_LL)
    img.save(os.path.join(OUT, "thinking.png"))

    img, d = base("happy", "open")
    # 右側に小さな紙とペン
    d.rectangle([39, 30, 45, 38], fill=PAPER_L, outline=PAPER_D)
    hrow(d, 41, 43, 33, PAPER_D)
    hrow(d, 41, 43, 35, PAPER_D)
    img.save(os.path.join(OUT, "writing.png"))

    img, d = base("open", "open")
    sparkle(d, 6, 16); sparkle(d, 42, 6); sparkle(d, 4, 34)
    img.save(os.path.join(OUT, "recall.png"))

    img, d = base("happy", "open")
    # 頬を強める（なでられて照れてる）
    d2 = ImageDraw.Draw(img)
    for ex, side in ((13, 1), (33, -1)):
        px(d2, ex, 33, CHEEK); px(d2, ex + side, 33, CHEEK)
    img.save(os.path.join(OUT, "love.png"))

    img, d = base("x", "dot")
    px(d, 10, 20, SWEAT); px(d, 10, 21, SWEAT); px(d, 9, 21, (200, 226, 245, 255))
    img.save(os.path.join(OUT, "error.png"))

    for name in ("idle", "thinking", "writing", "recall", "error", "love"):
        p = os.path.join(OUT, f"{name}.png")
        Image.open(p).resize((W * 8, H * 8), Image.NEAREST).save(
            os.path.join(OUT, f"preview_{name}.png"))
    print("done")


if __name__ == "__main__":
    main()
