# モコラ(Mokora) 48x48 ドット絵ジェネレータ
# 仕様(ユラリ設計シート準拠): 綿雲のような白ふわ体 / 前髪のカール(あほ毛) /
# 羽のような耳 / 紙・しおりの尻尾 / 茶色のまん丸目 / 頬染め
# 体4-5色 / 影 / 目3色(茶) / 紙・しおり / アクセント / 輪郭やわらかめ / 透過PNG
import os

from PIL import Image, ImageDraw

W = H = 48
OUT = os.path.dirname(os.path.abspath(__file__))

OUTLINE = (92, 80, 66, 255)          # やわらかい茶の輪郭
BODY_LL = (252, 249, 242, 255)       # 体・最明
BODY_L = (245, 240, 229, 255)        # 体・明
BODY_M = (236, 228, 211, 255)        # 体・基本
BODY_D = (219, 207, 184, 255)        # 体・影
BODY_DD = (196, 181, 152, 255)       # 体・深い影
EYE_D = (74, 46, 20, 255)            # 目・こげ茶
EYE_M = (122, 78, 36, 255)           # 目・茶
EYE_L = (255, 255, 255, 255)         # 目・ハイライト
PAPER_D = (168, 146, 104, 255)
PAPER_M = (214, 192, 146, 255)
PAPER_L = (238, 222, 182, 255)
CHEEK = (238, 158, 158, 200)
SPARK = (255, 214, 120, 255)
SWEAT = (150, 195, 225, 255)


def new_canvas():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def px(d, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        d.point((x, y), fill=c)


def hrow(d, x0, x1, y, c):
    for x in range(x0, x1 + 1):
        px(d, x, y, c)


# 体: 行ごとの half_width（綿雲のもこもこ感を幅の揺れで出す）
BODY_ROWS = [
    6, 9, 11, 12, 13, 13, 14, 13, 14, 13, 14, 13, 14, 13, 13, 12, 12, 11, 9,
]
FUR_ROWS = [2, 5, 8, 11, 14, 16]


def body(d, cx=23, top=19):
    n = len(BODY_ROWS)
    for i, hw in enumerate(BODY_ROWS):
        y = top + i
        x0, x1 = cx - hw, cx + hw
        if i <= 3:
            c = BODY_LL
        elif i >= n - 5:
            c = BODY_D
        else:
            c = BODY_L
        hrow(d, x0, x1, y, c)
        # もこ感: 幅が奇数で凹んでる行の端に明色
        px(d, x0, y, BODY_M)
        px(d, x1, y, BODY_M)
        px(d, x0 - 1, y, OUTLINE)
        px(d, x1 + 1, y, OUTLINE)
    hw0 = BODY_ROWS[0]
    hrow(d, cx - hw0, cx + hw0, top - 1, OUTLINE)
    hwn = BODY_ROWS[-1]
    hrow(d, cx - hwn, cx + hwn, top + n, OUTLINE)
    # 下面の深い影
    hrow(d, cx - hwn + 2, cx + hwn - 2, top + n - 1, BODY_DD)
    # 起毛（輪郭密着）
    for i in FUR_ROWS:
        y = top + i
        hw = BODY_ROWS[i]
        px(d, cx - hw - 2, y, OUTLINE)
        px(d, cx + hw + 2, y, OUTLINE)
    for dx in (-8, -3, 2, 7):
        px(d, cx + dx, top - 2, OUTLINE)
    for dx in (-6, -1, 4):
        px(d, cx + dx, top + n + 1, OUTLINE)


def ahoge(d, cx=23, top=19):
    """前髪のカール（ぷっくり丸い?型あほ毛）。モコラのシグネチャ。
    行ごとの (offset_from_cx, width) で太めのカールを描く。"""
    rows = [  # 上から。カールの頭→巻き→首元
        (-1, 4),   # top-9
        (-2, 6),   # top-8
        (-3, 7),   # top-7
        (-3, 3),   # top-6 左側だけ(巻きの内側の抜け)
        (-3, 3),   # top-5
        (-2, 4),   # top-4
        (0, 2),    # top-3 首
        (0, 2),    # top-2
    ]
    extra = [(2, top - 6, 2), (2, top - 5, 2)]  # 巻きの右外側
    for i, (off, wdt) in enumerate(rows):
        y = top - 9 + i
        x0 = cx + off
        x1 = x0 + wdt - 1
        hrow(d, x0, x1, y, BODY_LL)
        px(d, x0 - 1, y, OUTLINE)
        px(d, x1 + 1, y, OUTLINE)
    for off, y, wdt in extra:
        hrow(d, cx + off, cx + off + wdt - 1, y, BODY_L)
        px(d, cx + off + wdt, y, OUTLINE)
    # 上端・下端の輪郭とカール内側の影
    hrow(d, cx - 2, cx + 3, top - 10, OUTLINE)
    px(d, cx, top - 5, BODY_D)  # 巻きの内側の穴
    px(d, cx + 1, top - 5, OUTLINE)
    px(d, cx + 1, top - 6, OUTLINE)
    px(d, cx - 1, top - 3, OUTLINE)
    px(d, cx + 2, top - 3, OUTLINE)


# 羽耳: 行ごとの (offset, width) — ふわっと外へ開く羽
WING_ROWS = [
    (6, 3),
    (3, 6),
    (1, 8),
    (0, 9),
    (0, 9),
    (1, 8),
    (2, 6),
    (4, 4),
]


def wing_ear(d, x, y, flip=False):
    """羽のような耳。(x,y)=左上基準。flipで左右反転（flip=Trueが左耳）。"""
    for i, (off, wdt) in enumerate(WING_ROWS):
        if flip:
            x0 = x + (8 - off - wdt)
        else:
            x0 = x + off
        x1 = x0 + wdt - 1
        c = BODY_LL if i <= 2 else (BODY_L if i <= 4 else BODY_M)
        hrow(d, x0, x1, y + i, c)
        px(d, x0 - 1, y + i, OUTLINE)
        px(d, x1 + 1, y + i, OUTLINE)
        # 羽の筋（羽根らしい2本線）
        if i in (2, 3, 4, 5) and wdt >= 6:
            px(d, x0 + wdt // 3, y + i, BODY_D)
            px(d, x0 + (wdt * 2) // 3, y + i, BODY_M)
    off, wdt = WING_ROWS[0]
    x0 = x + ((8 - off - wdt) if flip else off)
    hrow(d, x0 - 1, x0 + wdt, y - 1, OUTLINE)
    off, wdt = WING_ROWS[-1]
    x0 = x + ((8 - off - wdt) if flip else off)
    hrow(d, x0 - 1, x0 + wdt, y + len(WING_ROWS), OUTLINE)


def scroll_tail(d, x=37, y=30):
    """紙・しおりの尻尾（小さな巻物）。"""
    d.rectangle([x, y, x + 6, y + 8], fill=PAPER_L, outline=OUTLINE)
    hrow(d, x + 1, x + 5, y + 1, PAPER_M)
    hrow(d, x + 1, x + 5, y + 7, PAPER_D)
    # 巻きの丸
    d.rectangle([x, y - 2, x + 6, y], fill=PAPER_M, outline=OUTLINE)
    hrow(d, x + 1, x + 5, y - 1, PAPER_L)
    # しおりの線
    for yy in (y + 3, y + 5):
        hrow(d, x + 2, x + 4, yy, PAPER_D)


def eyes(d, mode="open", cx=23, y=29):
    """茶色のまん丸目。"""
    lx, rx_ = cx - 6, cx + 6
    if mode == "open":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y - 2, ex + 1, y + 1], fill=EYE_M)
            px(d, ex - 1, y - 2, EYE_D); px(d, ex + 1, y - 2, EYE_D)
            hrow(d, ex - 1, ex + 1, y + 1, EYE_D)
            px(d, ex, y + 2, EYE_D)
            px(d, ex - 1, y - 1, EYE_L)
    elif mode == "half":
        for ex in (lx, rx_):
            d.rectangle([ex - 1, y, ex + 1, y + 1], fill=EYE_M)
            hrow(d, ex - 1, ex + 1, y - 1, OUTLINE)
            px(d, ex, y + 1, EYE_D)
            px(d, ex - 1, y, EYE_L)
    elif mode == "happy":
        for ex in (lx, rx_):
            px(d, ex - 2, y, EYE_D); px(ex_ := d, ex + 2, y, EYE_D)
            px(d, ex - 1, y - 1, EYE_D); px(d, ex + 1, y - 1, EYE_D)
            px(d, ex, y - 1, EYE_M)
    elif mode == "x":
        for ex in (lx, rx_):
            for dd in (-1, 0, 1):
                px(d, ex + dd, y + dd, EYE_D)
                px(d, ex + dd, y - dd, EYE_D)
            px(d, ex, y, EYE_M)


def face_extras(d, cx=23, mouth="dot", strong_cheek=False):
    for ex, side in ((cx - 10, 1), (cx + 10, -1)):
        px(d, ex, 32, CHEEK)
        px(d, ex + side, 32, CHEEK)
        if strong_cheek:
            px(d, ex, 33, CHEEK)
            px(d, ex + side, 33, CHEEK)
    MOUTH = (150, 120, 100, 255)
    if mouth == "dot":
        px(d, cx - 1, 33, MOUTH); px(d, cx, 34, MOUTH); px(d, cx + 1, 33, MOUTH)
    elif mouth == "open":
        px(d, cx - 1, 33, MOUTH); px(d, cx + 1, 33, MOUTH)
        hrow(d, cx - 1, cx + 1, 34, MOUTH)
        px(d, cx, 34, (216, 130, 130, 255))


def sparkle(d, x, y):
    px(d, x, y - 1, SPARK); px(d, x, y + 1, SPARK)
    px(d, x - 1, y, SPARK); px(d, x + 1, y, SPARK)
    px(d, x, y, (255, 245, 210, 255))


def base(eye_mode="open", mouth="dot", strong_cheek=False):
    img = new_canvas()
    d = ImageDraw.Draw(img)
    scroll_tail(d)
    body(d)
    ahoge(d)
    wing_ear(d, 7, 14, flip=True)
    wing_ear(d, 30, 14, flip=False)
    eyes(d, eye_mode)
    face_extras(d, mouth=mouth, strong_cheek=strong_cheek)
    return img, d


def main():
    os.makedirs(OUT, exist_ok=True)
    sub = os.path.join(OUT, "mokora")
    os.makedirs(sub, exist_ok=True)

    img, d = base("open", "dot")
    img.save(os.path.join(sub, "idle.png"))

    img, d = base("half", "dot")
    for i, x in enumerate((40, 43, 46)):
        px(d, x, 14 - (i % 2), BODY_DD)
    img.save(os.path.join(sub, "thinking.png"))

    img, d = base("happy", "open")
    d.rectangle([39, 36, 45, 44], fill=PAPER_L, outline=PAPER_D)
    hrow(d, 41, 43, 39, PAPER_D)
    hrow(d, 41, 43, 41, PAPER_D)
    img.save(os.path.join(sub, "writing.png"))

    img, d = base("open", "open")
    sparkle(d, 5, 14); sparkle(d, 43, 8); sparkle(d, 4, 32)
    img.save(os.path.join(sub, "recall.png"))

    img, d = base("x", "dot")
    px(d, 9, 18, SWEAT); px(d, 9, 19, SWEAT); px(d, 8, 19, (200, 226, 245, 255))
    img.save(os.path.join(sub, "error.png"))

    img, d = base("happy", "open", strong_cheek=True)
    img.save(os.path.join(sub, "love.png"))

    for name in ("idle", "thinking", "writing", "recall", "error", "love"):
        p = os.path.join(sub, f"{name}.png")
        Image.open(p).resize((W * 8, H * 8), Image.NEAREST).save(
            os.path.join(sub, f"preview_{name}.png"))
    print("done")


if __name__ == "__main__":
    main()
