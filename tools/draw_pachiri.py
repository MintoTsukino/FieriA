# パチリ(Pachiri) 48x48 ドット絵ジェネレータ
# 参考仕様: ふわふわの雲に小さな雷を宿した丸い体 / 目の光はやさしい稲妻 /
#           しっぽは短い稲妻のかけら / うれしいとパチパチ光る / 困ると雨粒をこぼす
# 48pxに載せるため細部は簡易化し、雲のもこもこ・光る目・稲妻しっぽ・雨粒を記号に立てる
# 雲4色 / 影3色 / 光3色 / 雷3色 / 雨2色 / 輪郭1px / 透過PNG
import os

from PIL import Image, ImageDraw

W = H = 48
OUT = os.path.dirname(os.path.abspath(__file__))

OUTLINE = (58, 54, 56, 255)
CLOUD_LL = (246, 242, 238, 255)
CLOUD_L = (216, 210, 206, 255)
CLOUD_M = (178, 172, 170, 255)
CLOUD_D = (134, 128, 128, 255)
SHADOW = (96, 90, 92, 255)
EYE_D = (198, 146, 40, 255)
EYE_M = (248, 206, 96, 255)
EYE_L = (255, 246, 206, 255)
BOLT_D = (214, 158, 40, 255)
BOLT_M = (250, 202, 70, 255)
BOLT_L = (255, 240, 164, 255)
RAIN_M = (110, 176, 214, 255)
RAIN_L = (172, 216, 240, 255)
CHEEK = (226, 158, 160, 170)
HEART = (238, 140, 156, 255)

CX = 23

# 雲のもこもこ: 円の集合でシルエットを作る (cx, cy, r)
PUFFS = [
    (13, 23, 6), (23, 20, 7), (33, 23, 6),   # 上の三つ山
    (17, 29, 7), (29, 29, 7), (23, 28, 8),   # 下のふくらみ
]


def new_canvas():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def px(d, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        d.point((x, y), fill=c)


def hrow(d, x0, x1, y, c):
    for x in range(x0, x1 + 1):
        px(d, x, y, c)


def cloud_mask(dy=0):
    """雲の内側を示す真偽グリッド。dyで全体を上下に動かす。"""
    m = [[False] * W for _ in range(H)]
    for cx, cy, r in PUFFS:
        for x in range(cx - r - 1, cx + r + 2):
            for y in range(cy - r - 1 + dy, cy + r + 2 + dy):
                if 0 <= x < W and 0 <= y < H:
                    if (x - cx) ** 2 + (y - cy - dy) ** 2 <= r * r:
                        m[y][x] = True
    return m


def cloud(d, dy=0, gloom=False):
    """雲の体。上はふんわり明るく、下は影。gloomで暗く沈む（困る用）。"""
    m = cloud_mask(dy)
    ys = [y for y in range(H) if any(m[y])]
    top, bottom = ys[0], ys[-1]
    span = max(bottom - top, 1)
    for y in range(H):
        for x in range(W):
            if not m[y][x]:
                continue
            t = (y - top) / span
            if t < 0.22:
                c = CLOUD_LL
            elif t < 0.5:
                c = CLOUD_L
            elif t < 0.78:
                c = CLOUD_M
            else:
                c = CLOUD_D
            if gloom:
                c = {CLOUD_LL: CLOUD_L, CLOUD_L: CLOUD_M,
                     CLOUD_M: CLOUD_D, CLOUD_D: SHADOW}[c]
            px(d, x, y, c)
    # 輪郭（内側の縁を1px）
    for y in range(H):
        for x in range(W):
            if not m[y][x]:
                continue
            if (not m[y - 1][x] if y > 0 else True) or \
               (not m[y + 1][x] if y < H - 1 else True) or \
               (not m[y][x - 1] if x > 0 else True) or \
               (not m[y][x + 1] if x < W - 1 else True):
                px(d, x, y, OUTLINE)
    # もこもこの陰影（塊の境目を軽く示す）
    for cx, cy, r in PUFFS[:3]:
        px(d, cx - r + 2, cy + dy + r - 3, CLOUD_M if not gloom else CLOUD_D)


def bolt_tail(d, x=38, y=30, big=False):
    """しっぽの稲妻のかけら。bigで大きく光る。"""
    pts = ((0, 0), (1, 1), (0, 2), (2, 2), (1, 3), (2, 4))
    for ox, oy in pts:
        px(d, x + ox, y + oy, BOLT_M)
    px(d, x, y, BOLT_L)
    px(d, x + 1, y + 1, BOLT_L)
    for ox, oy in ((-1, 0), (0, -1), (3, 4), (2, 5)):
        px(d, x + ox, y + oy, BOLT_D)
    if big:
        for ox, oy in ((3, 0), (4, 1), (3, 2), (5, 3)):
            px(d, x + ox, y + oy, BOLT_M)
        px(d, x + 3, y + 0, BOLT_L)


def eyes(d, mode="open", cx=CX, y=26):
    """光る目（やさしい稲妻のぬくもり）。"""
    lx, rx_ = cx - 5, cx + 5
    if mode == "open":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y - 3, ex + 1, y + 3], fill=EYE_M)
            px(d, ex, y - 2, EYE_L); px(d, ex, y - 1, EYE_L)
            px(d, ex - 1, y - 2, EYE_L)
            px(d, ex, y + 2, EYE_D)
            hrow(d, ex - 1, ex + 1, y - 4, OUTLINE)
            hrow(d, ex - 1, ex + 1, y + 4, OUTLINE)
            for yy in range(y - 3, y + 4):
                px(d, ex - 2, yy, OUTLINE)
                px(d, ex + 2, yy, OUTLINE)
    elif mode == "half":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y, ex + 1, y + 2], fill=EYE_M)
            px(d, ex, y, EYE_L)
            hrow(d, ex - 1, ex + 1, y - 1, OUTLINE)
            hrow(d, ex - 1, ex + 1, y + 3, OUTLINE)
            for yy in range(y, y + 3):
                px(d, ex - 2, yy, OUTLINE)
                px(d, ex + 2, yy, OUTLINE)
    elif mode == "happy":
        for ex in (lx, rx_):
            px(d, ex - 2, y + 1, EYE_M); px(d, ex + 2, y + 1, EYE_M)
            px(d, ex - 1, y - 1, EYE_L); px(d, ex + 1, y - 1, EYE_L)
            px(d, ex, y - 2, EYE_M)
            px(d, ex - 2, y + 2, OUTLINE); px(d, ex + 2, y + 2, OUTLINE)
    elif mode == "sad":
        # 困り目（下がった半目）
        for ex, side in ((lx, 1), (rx_, -1)):
            px(d, ex - side, y - 1, OUTLINE)
            d.rectangle([ex - 1, y, ex + 1, y + 2], fill=EYE_D)
            px(d, ex, y, EYE_M)
            hrow(d, ex - 1, ex + 1, y + 3, OUTLINE)


def mouth(d, kind="dot", cx=CX, y=32):
    if kind == "dot":
        for ox in (-2, 2):
            px(d, cx + ox, y - 1, OUTLINE)
        for ox in (-1, 0, 1):
            px(d, cx + ox, y, OUTLINE)
    elif kind == "open":
        d.rectangle([cx - 1, y - 1, cx + 1, y + 1], fill=OUTLINE)
        px(d, cx, y, HEART)
        for ox in (-2, 2):
            px(d, cx + ox, y - 1, OUTLINE)
    elif kind == "wave":
        # 困ったときの波線口
        px(d, cx - 2, y, OUTLINE); px(d, cx - 1, y - 1, OUTLINE)
        px(d, cx, y, OUTLINE); px(d, cx + 1, y - 1, OUTLINE)
        px(d, cx + 2, y, OUTLINE)


def cheeks(d, cx=CX, y=30, strong=False):
    for ex, side in ((cx - 9, 1), (cx + 9, -1)):
        px(d, ex, y, CHEEK)
        px(d, ex + side, y, CHEEK)
        if strong:
            px(d, ex, y + 1, CHEEK)
            px(d, ex + side, y + 1, CHEEK)


def raindrop(d, x, y):
    px(d, x, y, RAIN_L)
    px(d, x, y + 1, RAIN_M)
    px(d, x - 1, y + 1, RAIN_M)
    px(d, x + 1, y + 1, RAIN_M)
    px(d, x, y + 2, RAIN_M)


def spark(d, x, y, c=BOLT_L):
    px(d, x, y, c)
    px(d, x - 1, y, c); px(d, x + 1, y, c)
    px(d, x, y - 1, c); px(d, x, y + 1, c)


def base(eye_mode="open", mouth_kind="dot", dy=0, gloom=False,
         tail_big=False):
    img = new_canvas()
    d = ImageDraw.Draw(img)
    cloud(d, dy=dy, gloom=gloom)
    bolt_tail(d, y=30 + dy, big=tail_big)
    eyes(d, eye_mode, y=26 + dy)
    mouth(d, mouth_kind, y=32 + dy)
    return img, d


def main():
    os.makedirs(OUT, exist_ok=True)

    # 通常
    img, d = base("open", "dot")
    img.save(os.path.join(OUT, "pachiri_idle.png"))

    # 考え中: 半目・思考の点
    img, d = base("half", "dot", dy=1)
    for i, x in enumerate((41, 44, 47)):
        px(d, x, 17 - (i % 2), CLOUD_L)
    img.save(os.path.join(OUT, "pachiri_thinking.png"))

    # お手伝い中(writing): 手紙をそっと差し出す
    img, d = base("happy", "dot")
    d.rectangle([39, 16, 47, 22], fill=CLOUD_LL, outline=OUTLINE)
    for i in range(4):
        px(d, 40 + i, 17 + i, OUTLINE)
        px(d, 46 - i, 17 + i, OUTLINE)
    img.save(os.path.join(OUT, "pachiri_writing.png"))

    # 思い出す(recall): パチッと光る
    img, d = base("open", "open", tail_big=True)
    spark(d, 6, 20); spark(d, 42, 14); spark(d, 9, 38)
    for bx, by in ((10, 16), (37, 34)):
        for ox, oy in ((0, 0), (1, 1), (0, 2)):
            px(d, bx + ox, by + oy, BOLT_M)
    img.save(os.path.join(OUT, "pachiri_recall.png"))

    # 甘える(love): 照れ頬とハート、しっぽも元気
    img, d = base("happy", "open", tail_big=True)
    cheeks(d, strong=True)
    hx, hy = 39, 15
    for ox, oy in ((-1, 0), (0, 0), (1, 0), (2, 0), (-1, -1), (2, -1),
                   (0, 1), (1, 1)):
        px(d, hx + ox, hy + oy, HEART)
    px(d, hx, hy + 2, HEART)
    img.save(os.path.join(OUT, "pachiri_love.png"))

    # 困る(error): 雲が沈み、ぽつりと雨粒
    img, d = base("sad", "wave", dy=2, gloom=True)
    raindrop(d, 15, 41)
    raindrop(d, 24, 43)
    raindrop(d, 31, 40)
    img.save(os.path.join(OUT, "pachiri_error.png"))

    for name in ("idle", "thinking", "writing", "recall", "error", "love"):
        p = os.path.join(OUT, f"pachiri_{name}.png")
        Image.open(p).resize((W * 8, H * 8), Image.NEAREST).save(
            os.path.join(OUT, f"preview_pachiri_{name}.png"))
    print("done")


if __name__ == "__main__":
    main()
