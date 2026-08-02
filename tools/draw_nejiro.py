# ネジロ(Nejiro) 48x48 ドット絵ジェネレータ
# 参考仕様: 真鍮と木の小さなゼンマイ獣 / 頭のゼンマイ鍵 / 垂れた耳 /
#           胸の小窓に宿る記憶の灯り / 小さな足でちょこちょこ
# 48pxに載せるため細部は簡易化し、鍵・垂れ耳・胸の灯り・歯車の4点を記号として立てる
# 体5色 / 木部3色 / 光3色 / アクセント1色 / 輪郭1px / 透過PNG
import os

from PIL import Image, ImageDraw

W = H = 48
OUT = os.path.dirname(os.path.abspath(__file__))

OUTLINE = (36, 26, 18, 255)
BRASS_D = (122, 88, 40, 255)
BRASS_M = (176, 134, 62, 255)
BRASS_L = (214, 176, 96, 255)
BRASS_LL = (240, 214, 148, 255)
WOOD_D = (74, 48, 30, 255)
WOOD_M = (110, 74, 44, 255)
WOOD_L = (146, 102, 62, 255)
EYE_D = (28, 22, 18, 255)
EYE_L = (255, 252, 244, 255)
TEAL = (86, 190, 180, 255)
LAMP_D = (196, 112, 26, 255)
LAMP_M = (246, 168, 52, 255)
LAMP_L = (255, 226, 146, 255)
CHEEK = (206, 130, 96, 165)
SPARK = (255, 240, 176, 255)
SWEAT = (150, 195, 225, 255)
HEART = (238, 132, 148, 255)

CX = 23


def new_canvas():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def px(d, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        d.point((x, y), fill=c)


def hrow(d, x0, x1, y, c):
    for x in range(x0, x1 + 1):
        px(d, x, y, c)


# ---- ころんとした胴体（行ごとの half_width） ----
BODY_ROWS = [
    5, 8, 10, 11, 12, 12, 13, 13, 13, 13, 13, 13,
    13, 13, 12, 12, 11, 10, 9, 7,
]
BODY_TOP = 20


def body(d, cx=CX, top=BODY_TOP):
    n = len(BODY_ROWS)
    for i, hw in enumerate(BODY_ROWS):
        y = top + i
        x0, x1 = cx - hw, cx + hw
        # 上半分は真鍮の光沢、下半分は木部
        if i <= 2:
            c = BRASS_L
        elif i <= 11:
            c = BRASS_M
        elif i <= 14:
            c = WOOD_M
        else:
            c = WOOD_D
        hrow(d, x0, x1, y, c)
        px(d, x0, y, OUTLINE if i in (0, n - 1) else BRASS_D)
        px(d, x1, y, OUTLINE if i in (0, n - 1) else BRASS_D)
        if 1 <= i <= 8:
            px(d, x0 + 1, y, BRASS_LL)
        px(d, x0 - 1, y, OUTLINE)
        px(d, x1 + 1, y, OUTLINE)
    hrow(d, cx - BODY_ROWS[0], cx + BODY_ROWS[0], top - 1, OUTLINE)
    hrow(d, cx - BODY_ROWS[-1], cx + BODY_ROWS[-1], top + n, OUTLINE)
    # 木部と真鍮の継ぎ目（帯金）
    hrow(d, cx - 13, cx + 13, top + 12, BRASS_D)
    for dx in (-9, -3, 4, 9):
        px(d, cx + dx, top + 12, BRASS_LL)


def key(d, cx=CX, top=8, turn=0):
    """頭のゼンマイ鍵。左右の輪を中空リングで描く。turnで回転感。"""
    for side, oy in ((-1, -turn), (1, turn)):
        ox, oyc = cx + side * 4, top + 3 + oy
        for dx in range(-4, 5):
            for dy in range(-4, 5):
                dist = dx * dx + dy * dy
                if 4 <= dist <= 9:
                    px(d, ox + dx, oyc + dy, BRASS_L)
                elif 9 < dist <= 16:
                    px(d, ox + dx, oyc + dy, OUTLINE)
        px(d, ox - 2, oyc - 1, BRASS_LL)
    # 軸（短め）
    for y in range(top + 6, BODY_TOP):
        hrow(d, cx - 1, cx + 1, y, BRASS_M)
        px(d, cx - 1, y, BRASS_LL)
        px(d, cx - 2, y, OUTLINE)
        px(d, cx + 2, y, OUTLINE)


def gear(d, x, y, r=3, c=BRASS_L):
    """小さな歯車（円＋歯4つ）。"""
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                px(d, x + dx, y + dy, c)
    for dx, dy in ((0, -r - 1), (0, r + 1), (-r - 1, 0), (r + 1, 0)):
        px(d, x + dx, y + dy, c)
    for dx, dy in ((0, -r), (0, r), (-r, 0), (r, 0)):
        px(d, x + dx, y + dy, BRASS_D)
    px(d, x, y, OUTLINE)
    px(d, x - 1, y - 1, BRASS_LL)


def ear(d, x, y, flip=False, lift=0):
    """垂れ耳（木の板に真鍮の縁）。liftで持ち上がる。"""
    rows = ((0, 4), (0, 5), (0, 5), (0, 5), (0, 4), (1, 3))
    for i, (off, wdt) in enumerate(rows):
        yy = y + i - round(lift * (len(rows) - i) / len(rows))
        if flip:
            x1 = x - off
            x0 = x1 - wdt + 1
            px(d, x0 - 1, yy, OUTLINE)
            px(d, x0, yy, BRASS_M)
        else:
            x0 = x + off
            x1 = x0 + wdt - 1
            px(d, x1 + 1, yy, OUTLINE)
            px(d, x1, yy, BRASS_M)
        c = WOOD_L if i <= 1 else WOOD_M
        hrow(d, x0 + (1 if flip else 0), x1 - (0 if flip else 1), yy, c)
    top_y = y - round(lift)
    hrow(d, x - (4 if flip else 0), x + (0 if flip else 4), top_y - 1, OUTLINE)
    bot = y + len(rows) - 1
    hrow(d, x - (3 if flip else 1), x + (1 if flip else 3), bot + 1, OUTLINE)


def lamp(d, cx=CX, y=37, level="normal"):
    """胸の小窓（記憶の灯り）。levelで明るさが変わる。"""
    r = 3
    # 真鍮のリング
    for dx in range(-r - 1, r + 2):
        for dy in range(-r - 1, r + 2):
            dist = dx * dx + dy * dy
            if r * r < dist <= (r + 1) * (r + 1):
                px(d, cx + dx, y + dy, BRASS_L)
            elif (r + 1) * (r + 1) < dist <= (r + 2) * (r + 2):
                px(d, cx + dx, y + dy, OUTLINE)
    # 灯り
    inner = {"dim": LAMP_D, "normal": LAMP_M, "bright": LAMP_L}[level]
    core = {"dim": LAMP_M, "normal": LAMP_L, "bright": (255, 248, 214, 255)}[level]
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                px(d, cx + dx, y + dy, inner)
    for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1)):
        px(d, cx + dx, y + dy, core)
    # リングの留め金4つ
    for dx, dy in ((0, -r - 1), (0, r + 1), (-r - 1, 0), (r + 1, 0)):
        px(d, cx + dx, y + dy, BRASS_LL)
    if level == "bright":
        for sx, sy in ((-r - 3, -r - 2), (r + 3, -r - 1), (-r - 2, r + 3)):
            px(d, cx + sx, y + sy, SPARK)


def eyes(d, mode="open", cx=CX, y=27):
    lx, rx_ = cx - 5, cx + 5
    if mode == "open":
        for ex in (lx, rx_):
            d.rectangle([ex - 2, y - 2, ex + 1, y + 2], fill=EYE_D)
            px(d, ex - 2, y - 2, BRASS_D); px(d, ex + 1, y - 2, BRASS_D)
            px(d, ex - 2, y + 2, BRASS_D); px(d, ex + 1, y + 2, BRASS_D)
            px(d, ex - 1, y - 1, EYE_L); px(d, ex, y - 1, EYE_L)
            px(d, ex, y + 1, TEAL)
    elif mode == "half":
        for ex in (lx, rx_):
            d.rectangle([ex - 2, y, ex + 1, y + 2], fill=EYE_D)
            px(d, ex - 1, y, EYE_L)
            hrow(d, ex - 2, ex + 1, y - 1, BRASS_D)
    elif mode == "happy":
        for ex in (lx, rx_):
            px(d, ex - 2, y + 1, EYE_D); px(d, ex + 2, y + 1, EYE_D)
            px(d, ex - 1, y, EYE_D); px(d, ex + 1, y, EYE_D)
            px(d, ex, y - 1, EYE_D)
            px(d, ex, y, EYE_L)
    elif mode == "x":
        for ex in (lx, rx_):
            px(d, ex - 1, y - 1, EYE_D); px(d, ex + 1, y - 1, EYE_D)
            px(d, ex, y, EYE_D)
            px(d, ex - 1, y + 1, EYE_D); px(d, ex + 1, y + 1, EYE_D)


def muzzle(d, kind="dot", cx=CX, y=31):
    """鼻と口（小さな金具）。"""
    px(d, cx, y, EYE_D)
    px(d, cx - 1, y, BRASS_D)
    px(d, cx + 1, y, BRASS_D)
    if kind == "dot":
        px(d, cx - 1, y + 2, EYE_D)
        px(d, cx, y + 2, EYE_D)
        px(d, cx + 1, y + 2, EYE_D)
    elif kind == "open":
        d.rectangle([cx - 1, y + 1, cx + 1, y + 3], fill=EYE_D)
        px(d, cx, y + 2, HEART)


def cheeks(d, cx=CX, y=30, strong=False):
    for ex, side in ((cx - 9, 1), (cx + 9, -1)):
        px(d, ex, y, CHEEK)
        px(d, ex + side, y, CHEEK)
        if strong:
            px(d, ex, y + 1, CHEEK)
            px(d, ex + side, y + 1, CHEEK)


def feet(d, cx=CX, y=40):
    for side in (-1, 1):
        bx = cx + side * 6
        hrow(d, bx - 2, bx + 2, y, WOOD_D)
        hrow(d, bx - 2, bx + 2, y + 1, BRASS_D)
        hrow(d, bx - 2, bx + 2, y + 2, OUTLINE)
        px(d, bx - 3, y + 1, OUTLINE)
        px(d, bx + 3, y + 1, OUTLINE)


def sparkle(d, x, y):
    px(d, x, y, SPARK)
    px(d, x - 1, y, SPARK); px(d, x + 1, y, SPARK)
    px(d, x, y - 1, SPARK); px(d, x, y + 1, SPARK)


def base(eye_mode="open", mouth_kind="dot", lamp_level="normal",
         key_turn=0, ear_lift=0):
    img = new_canvas()
    d = ImageDraw.Draw(img)
    feet(d)
    ear(d, 9, 27, flip=True, lift=ear_lift)
    ear(d, 37, 27, flip=False, lift=ear_lift)
    body(d)
    key(d, turn=key_turn)
    gear(d, CX - 12, 22, r=2)
    gear(d, CX + 12, 23, r=2)
    lamp(d, level=lamp_level)
    eyes(d, eye_mode)
    muzzle(d, mouth_kind)
    return img, d


def main():
    os.makedirs(OUT, exist_ok=True)

    # 通常
    img, d = base("open", "dot", "normal")
    img.save(os.path.join(OUT, "nejiro_idle.png"))

    # 考え中: 半目・鍵が回る・思考の点
    img, d = base("half", "dot", "normal", key_turn=1)
    for i, x in enumerate((41, 44, 47)):
        px(d, x, 16 - (i % 2), BRASS_L)
    img.save(os.path.join(OUT, "nejiro_thinking.png"))

    # お手伝い中(writing): スパナを持つ
    img, d = base("happy", "dot", "normal", ear_lift=1)
    d.rectangle([38, 22, 40, 30], fill=BRASS_L, outline=OUTLINE)
    px(d, 38, 21, BRASS_L); px(d, 40, 21, BRASS_L)
    px(d, 39, 21, OUTLINE)
    px(d, 37, 22, OUTLINE); px(d, 41, 22, OUTLINE)
    img.save(os.path.join(OUT, "nejiro_writing.png"))

    # 思い出す(recall): 胸の灯りが満ちる
    img, d = base("open", "open", "bright", ear_lift=1)
    sparkle(d, 5, 20); sparkle(d, 43, 14); sparkle(d, 7, 38)
    img.save(os.path.join(OUT, "nejiro_recall.png"))

    # 甘える(love): 照れ頬とハート
    img, d = base("happy", "open", "bright", ear_lift=2)
    cheeks(d, strong=True)
    hx, hy = 39, 17
    for ox, oy in ((-1, 0), (0, 0), (1, 0), (2, 0), (-1, -1), (2, -1),
                   (0, 1), (1, 1)):
        px(d, hx + ox, hy + oy, HEART)
    px(d, hx, hy + 2, HEART)
    img.save(os.path.join(OUT, "nejiro_love.png"))

    # 困る(error): ×目・汗・灯りが弱る・耳が垂れる
    img, d = base("x", "dot", "dim", key_turn=-1)
    px(d, 9, 20, SWEAT); px(d, 9, 21, SWEAT)
    px(d, 8, 21, (200, 226, 245, 255))
    img.save(os.path.join(OUT, "nejiro_error.png"))

    for name in ("idle", "thinking", "writing", "recall", "error", "love"):
        p = os.path.join(OUT, f"nejiro_{name}.png")
        Image.open(p).resize((W * 8, H * 8), Image.NEAREST).save(
            os.path.join(OUT, f"preview_nejiro_{name}.png"))
    print("done")


if __name__ == "__main__":
    main()
