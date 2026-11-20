"""家具、洁具平面图块（GB/T 50104 平面图常用图例画法，1:100）。

    from wish_cad.cad import blocks
    blocks.draw(sh, m, level_id)     # 把本层 m.boxes 画成图块

约定
- 块在“家具局部坐标”里按真实尺寸参数化生成：x 沿 size[0]（宽），y 沿 size[1]（深），
  局部 (0,0) 是未旋转时的左下角，正面朝南（局部 -y），背面/靠墙一侧是局部 +y。
- 每种“类型 + 尺寸(+变体)”一个块名，如 TABLE_1800x900、WC_450x700；同型家具复用同一个块。
- 插入点 = box.origin()（未旋转局部原点落到的世界坐标），旋转角 = box.rotation，
  于是旋转后的外包正好落在 box.footprint() 上（at 是旋转后足迹的左下角）。
- 块内实体在 0 层、颜色 BYBLOCK、线宽 BYBLOCK，跟随插入图层（家具 A-FURN、洁具 A-FIXT，0.18）；
  书脊、坐垫缝、琴键这类细部用 0.13 极细线，免得 1:100 下糊成一片。
- 展板风格（sh.style == 'presentation'）在块内先铺极淡的实心填充，再画线；施工图纯线条。
"""
from __future__ import annotations

import math
from contextlib import contextmanager

from . import standard as S

FURN, FIXT = 'A-FURN', 'A-FIXT'
LT_DASH = 'FURN_DASH'          # 吊灯等“在剖切面以上”的构件用虚线
FINE = 13                      # 细部线宽 0.13
FILL = {'furn': '#F1ECE3', 'soft': '#E8E1D4', 'fixt': '#FBFCFC', 'plant': '#D9E6CE', 'leaf': '#C9DBBB'}

# ====================================================================== 类型匹配
# (类型, 关键字, 排除关键字)；按顺序匹配，先具体后笼统
RULES = [
    ('FLOORLAMP', ('台灯', '落地灯'), ()),
    ('LAMP', ('吊灯', '灯具', '吸顶灯', '筒灯', '灯'), ()),
    ('ROHAN', ('罗汉床', '榻'), ()),
    ('SQUAT', ('蹲便', '蹲坑', '蹲式'), ()),
    ('URINAL', ('小便',), ()),
    ('WC', ('坐便', '座便', '马桶', '便器'), ()),
    ('MOP', ('拖把', '污水池', '墩布'), ()),
    ('LAV', ('洗手', '台盆', '洗面', '面盆', '洗脸'), ()),
    ('BED', ('床',), ()),
    ('PLANT', ('绿植', '盆栽', '植物', '盆景', '蕨', '花卉', '树'), ()),
    ('PIANO', ('钢琴',), ()),
    ('SCREEN', ('屏风',), ()),
    ('SHELF', ('书架', '书柜', '展架', '货架', '杂志架', '报刊架'), ()),
    ('COUNTER', ('服务台', '吧台', '前台', '收银', '接待', '借还', '咨询台', '柜台', '总台', '水吧'), ()),
    ('CABINET', ('柜',), ()),
    ('QUANYI', ('圈椅', '太师椅', '官帽椅'), ()),
    ('SOFA', ('沙发',), ()),
    ('ARMCHAIR', ('扶手椅', '休闲椅', '单人椅', '躺椅', '贵妃椅'), ()),
    ('STOOL', ('凳', '吧椅'), ()),
    ('COFFEE', ('茶几', '边几', '角几', '茶台', '咖啡桌'), ()),
    ('SCHOOL', ('课桌',), ()),
    ('CHAIR', ('椅',), ()),
    ('DESK', ('办公桌', '书桌', '电脑桌', '写字', '工作台', '办公台'), ()),
    ('ROUND', ('圆桌', '圆木桌', '圆台', '圆几'), ()),
    ('TABLE', ('阅览桌', '长桌', '会议桌', '餐桌', '桌', '台'), ()),
]
FIXTURES = {'WC', 'SQUAT', 'URINAL', 'MOP', 'LAV'}
LABELLED = {'COUNTER', 'PIANO'}        # 展板风格下写小字的“重要家具”


def classify(box):
    text = box.name
    for kind, keys, excl in RULES:
        if any(k in text for k in keys) and not any(e in text for e in excl):
            return kind
    return 'GENERIC'


def _variant(kind, box):
    name = str(getattr(box, 'name', '') or '')
    w, d = box.size[0], box.size[1]
    if kind == 'SHELF' and ('双面' in name or d >= 560):
        return 'D'
    if kind == 'LAV' and ('盆' in name and '台' not in name):
        return 'W'                     # 挂墙洗手盆
    if kind == 'SOFA' and w < 1150:
        return 'S'                     # 单人沙发
    if kind == 'COFFEE' and ('圆' in name):
        return 'R'
    if kind == 'STOOL' and '吧椅' in name:
        return 'B'
    if kind == 'CHAIR' and ('办公' in name or '转椅' in name):
        return 'O'
    return ''


# ====================================================================== 块内绘图小工具
class G:
    """往块里画东西：局部坐标 + 可嵌套的平移旋转（给椅子之类的子构件用）。"""

    def __init__(self, blk, present):
        self.b = blk
        self.present = present
        self.tf = (0.0, 0.0, 0.0)

    # -- 变换
    def T(self, x, y):
        ox, oy, a = self.tf
        c, s = math.cos(a), math.sin(a)
        return (ox + x * c - y * s, oy + x * s + y * c)

    @contextmanager
    def at(self, x, y, deg=0.0):
        old = self.tf
        ox, oy = self.T(x, y)
        self.tf = (ox, oy, old[2] + math.radians(deg))
        try:
            yield self
        finally:
            self.tf = old

    def _a(self, fine=False, dashed=False):
        a = {'layer': '0', 'color': 0, 'lineweight': FINE if fine else -2}
        if dashed:
            a['linetype'] = LT_DASH
        return a

    def _pts(self, pts):
        out = []
        for p in pts:
            x, y = self.T(p[0], p[1])
            out.append((x, y, p[2] if len(p) > 2 else 0.0))
        return out

    # -- 图元
    def line(self, p, q, fine=False, dashed=False):
        self.b.add_line(self.T(*p[:2]), self.T(*q[:2]), dxfattribs=self._a(fine, dashed))

    def pl(self, pts, closed=True, fine=False, dashed=False):
        self.b.add_lwpolyline(self._pts(pts), format='xyb', close=closed, dxfattribs=self._a(fine, dashed))

    def circle(self, c, r, fine=False, dashed=False):
        self.b.add_circle(self.T(*c), r, dxfattribs=self._a(fine, dashed))

    def arc(self, c, r, a0, a1, fine=False, dashed=False):
        da = math.degrees(self.tf[2])
        self.b.add_arc(self.T(*c), r, a0 + da, a1 + da, dxfattribs=self._a(fine, dashed))

    def ell(self, c, rx, ry, fine=False, t0=0.0, t1=math.tau):
        a = self.tf[2]
        if rx >= ry:
            major, ratio = (rx * math.cos(a), rx * math.sin(a), 0), ry / rx
        else:   # 长轴沿局部 y：参数角整体偏 90°
            major, ratio = (-ry * math.sin(a), ry * math.cos(a), 0), rx / ry
            t0, t1 = t0 - math.pi / 2, t1 - math.pi / 2
        self.b.add_ellipse(self.T(*c), major_axis=major, ratio=ratio, start_param=t0, end_param=t1,
                           dxfattribs=self._a(fine))

    def fill(self, pts, rgb='furn'):
        if not self.present:
            return
        h = self.b.add_hatch(color=7, dxfattribs={'layer': '0'})
        col = FILL.get(rgb, rgb)
        h.rgb = tuple(int(col.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
        h.paths.add_polyline_path(self._pts(pts), is_closed=True)

    # -- 组合形状（返回顶点，便于先填充）
    @staticmethod
    def rr_pts(x0, y0, x1, y1, r=0.0):
        r = max(0.0, min(r, (x1 - x0) / 2 - 0.01, (y1 - y0) / 2 - 0.01))
        if r <= 0.5:
            return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        k = math.tan(math.radians(22.5))
        return [(x0 + r, y0, 0), (x1 - r, y0, k), (x1, y0 + r, 0), (x1, y1 - r, k),
                (x1 - r, y1, 0), (x0 + r, y1, k), (x0, y1 - r, 0), (x0, y0 + r, k)]

    def rr(self, x0, y0, x1, y1, r=0.0, fine=False, fill=None, dashed=False):
        pts = self.rr_pts(x0, y0, x1, y1, r)
        if fill:
            self.fill(pts, fill)
        self.pl(pts, fine=fine, dashed=dashed)
        return pts

    def disc(self, c, r, fill=None, fine=False, dashed=False):
        if fill:
            self.fill([(c[0] - r, c[1], 1.0), (c[0] + r, c[1], 1.0)], fill)
        self.circle(c, r, fine=fine, dashed=dashed)

    def oval(self, c, rx, ry, fill=None, fine=False):
        if fill:
            self.fill([(c[0] + rx * math.cos(t), c[1] + ry * math.sin(t))
                       for t in (i * math.tau / 48 for i in range(48))], fill)
        self.ell(c, rx, ry, fine=fine)


def _rng(seed):
    """确定性的伪随机序列（书脊宽度、叶片角度抖动），保证同一个块每次长得一样。"""
    x = (seed * 2654435761 + 12345) & 0xFFFFFFFF
    while True:
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        yield x / 0x7FFFFFFF


# ====================================================================== 子构件
def chair(g, cx, cy, face, sw=440, sd=440, kind='plain'):
    """一把椅子：中心 (cx,cy)，face 为坐的人面朝的方向（度，0=+x，90=+y）。
    椅子局部系里人面朝 -y，靠背在 +y。"""
    with g.at(cx, cy, face - 270):
        hw, hd = sw / 2, sd / 2
        if kind == 'office':
            bt = max(70, sd * 0.17)
            g.rr(-hw + 45, -hd, hw - 45, hd - bt - 20, r=90, fill='soft')
            y1, y2 = hd - bt, hd - 0.12 * hw
            g.fill([(-hw + 15, y1, 0), (hw - 15, y1, 0), (hw - 15, y2, 0.14), (-hw + 15, y2, 0)], 'soft')
            g.pl([(-hw + 15, y1, -0.08), (hw - 15, y1, 0), (hw - 15, y2, 0.14), (-hw + 15, y2, 0)])
            for sx in (-1, 1):        # 扶手
                xa, xb = sorted((sx * hw, sx * (hw - 40)))
                g.rr(xa, -hd + 90, xb, hd - bt - 40, r=18)
            g.circle((0, -hd * 0.15), min(sw, sd) * 0.06, fine=True)
        elif kind == 'school':
            bt = max(40, sd * 0.12)
            g.rr(-hw, -hd, hw, hd - bt - 10, r=30, fill='furn')
            g.rr(-hw + 10, hd - bt, hw - 10, hd, r=bt / 2, fill='furn')
            g.line((-hw + 30, hd - bt - 10), (-hw + 30, hd - bt), fine=True)
            g.line((hw - 30, hd - bt - 10), (hw - 30, hd - bt), fine=True)
        else:
            bt = max(45, sd * 0.13)
            y1 = hd - bt
            g.rr(-hw, -hd, hw, y1 - 12, r=45, fill='furn')
            g.rr(-hw + 30, -hd + 30, hw - 30, y1 - 42, r=30, fine=True)        # 坐垫
            y2 = hd - 0.1 * hw
            g.fill([(-hw, y1, 0), (hw, y1, 0), (hw, y2, 0.1), (-hw, y2, 0)], 'furn')
            g.pl([(-hw, y1, 0), (hw, y1, 0), (hw, y2, 0.1), (-hw, y2, 0)])


def books(g, x0, x1, yf, depth, seed, inward=1):
    """一格书：沿 x 排开，书脊在 yf（正面），向 inward 方向伸 depth 左右；书按 70~160 宽成组。"""
    r = _rng(seed)
    x = x0 + 25
    pts = []
    while x < x1 - 60:
        bw = 60 + 100 * next(r)
        bw = min(bw, x1 - 25 - x)
        dd = depth * (0.72 + 0.28 * next(r))
        pts.append((x, x + bw, dd))
        x += bw
        if next(r) < 0.18:            # 偶尔留空、或一本斜放
            x += 40
    for xa, xb, dd in pts:
        ya, yb = yf, yf + inward * dd
        g.pl([(xa, ya), (xb, ya), (xb, yb), (xa, yb)], fine=True)
        if xb - xa > 110:            # 宽书组再分一条书脊
            xm = (xa + xb) / 2
            g.line((xm, ya), (xm, ya + inward * dd * 0.9), fine=True)


def leaf(g, ang, r0, r1, bulge=0.35, fill='leaf', rib=True):
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    p, q = (r0 * c, r0 * s), (r1 * c, r1 * s)
    pts = [(p[0], p[1], bulge), (q[0], q[1], bulge)]
    g.fill(pts, fill)
    g.pl(pts)
    if rib:
        m = r0 + (r1 - r0) * 0.8
        g.line(p, (m * c, m * s), fine=True)


# ====================================================================== 各类图块
def b_table(g, w, d, v):
    g.rr(0, 0, w, d, r=15, fill='furn')
    g.rr(30, 30, w - 30, d - 30, r=8, fine=True)
    n = max(1, int((w + 200) // 620))                                     # 1800 长桌每侧 3 座
    cw = min(460, w / n - 100)
    for i in range(n):
        x = (i + 0.5) * w / n
        chair(g, x, -60 - cw / 2, 90, cw, cw)
        if d >= 650:
            chair(g, x, d + 60 + cw / 2, 270, cw, cw)
    if d >= 1100 and w >= 2000:        # 会议桌两端
        chair(g, -60 - cw / 2, d / 2, 0, cw, cw)
        chair(g, w + 60 + cw / 2, d / 2, 180, cw, cw)
    elif d < 650 and w < 1000:
        pass


def b_round(g, w, d, v):
    D = min(w, d)
    R = D / 2
    c = (w / 2, d / 2)
    g.disc(c, R, fill='furn')
    g.circle(c, R - 30, fine=True)
    n = max(3, min(10, int(math.pi * (D + 600) / 750)))
    cw = 440 if D >= 900 else 400
    for i in range(n):
        a = 90 + 360 * i / n + 180 / n * (n % 2 == 0 and n > 4)
        rr = R + 60 + cw / 2
        chair(g, c[0] + rr * math.cos(math.radians(a)), c[1] + rr * math.sin(math.radians(a)), a + 180, cw, cw)


def b_desk(g, w, d, v):
    g.rr(0, 0, w, d, r=10, fill='furn')
    g.line((0, d - 25), (w, d - 25), fine=True)                           # 后挡板
    # 侧柜（台面下，虚线）
    pw = min(420, w * 0.3)
    g.pl([(w - pw - 20, 25), (w - 20, 25), (w - 20, d - 45), (w - pw - 20, d - 45)], dashed=True, fine=True)
    # 显示器 + 键盘 + 鼠标
    mx = (w - pw) / 2 if w > 1200 else w / 2
    mw = min(560, w * 0.4)
    g.rr(mx - mw / 2, d - 170, mx + mw / 2, d - 120, r=10)
    g.rr(mx - 70, d - 120, mx + 70, d - 70, r=8, fine=True)
    kw = min(440, w * 0.32)
    g.rr(mx - kw / 2, 110, mx + kw / 2, 260, r=12, fine=True)
    g.oval((mx + kw / 2 + 110, 185), 32, 50, fine=True)
    chair(g, mx, -20 - 260, 90, 560, 540, 'office')


def b_school(g, w, d, v):
    g.rr(0, 0, w, d, r=12, fill='furn')
    g.line((25, d * 0.3), (w - 25, d * 0.3), fine=True)                   # 桌斗
    g.pl([(25, d * 0.3), (25, d - 25), (w - 25, d - 25), (w - 25, d * 0.3)], closed=False, fine=True, dashed=True)
    cw = min(400, w * 0.62)
    chair(g, w / 2, -40 - cw * 0.5, 90, cw, cw * 0.95, 'school')


def b_chair(g, w, d, v):
    chair(g, w / 2, d / 2, 270, w, d, 'office' if v == 'O' else 'plain')


def b_stool(g, w, d, v):
    R = min(w, d) / 2
    c = (w / 2, d / 2)
    g.disc(c, R, fill='furn')
    g.circle(c, R * 0.72, fine=True)
    if v == 'B':                     # 吧椅：后侧弧形靠背
        g.arc(c, R * 1.0 - 10, 30, 150)
        g.arc(c, R * 0.85, 30, 150, fine=True)


def b_shelf(g, w, d, v):
    double = v == 'D'
    g.rr(0, 0, w, d, fill='furn')
    nb = max(1, round(w / 900))
    bay = w / nb
    t = 20
    for i in range(1, nb):
        x = i * bay
        g.line((x - t / 2, 0), (x - t / 2, d), fine=True)
        g.line((x + t / 2, 0), (x + t / 2, d), fine=True)
    if double:
        g.line((0, d / 2 - 10), (w, d / 2 - 10))
        g.line((0, d / 2 + 10), (w, d / 2 + 10))
        depth = min(240, d / 2 - 50)
        for i in range(nb):
            books(g, i * bay, (i + 1) * bay, 25, depth, 2 * i + int(w))
            books(g, i * bay, (i + 1) * bay, d - 25, depth, 2 * i + 1 + int(w), inward=-1)
    else:
        g.line((0, d - 25), (w, d - 25))                                  # 背板
        depth = min(250, d - 90)
        for i in range(nb):
            books(g, i * bay, (i + 1) * bay, 25, depth, i + int(w))


def b_cabinet(g, w, d, v):
    g.rr(0, 0, w, d, fill='furn')
    g.line((0, 25), (w, 25), fine=True)                                   # 柜门
    g.line((0, d), (w, 0), fine=True)                                     # 高柜对角线
    n = max(1, round(w / 500))
    for i in range(1, n):
        g.line((i * w / n, 0), (i * w / n, 25), fine=True)


def b_sofa(g, w, d, v):
    single = v == 'S'
    a = max(110, min(220, w * (0.15 if single else 0.085)))
    bf = max(120, min(170, d * 0.17))                                     # 靠背框
    bc = max(100, min(160, d * 0.17))                                     # 靠垫
    g.fill(G.rr_pts(0, 0, w, d, 70), 'soft')
    g.rr(0, d - bf, w, d, r=55)
    g.rr(0, 0, a, d - bf, r=a * 0.45)
    g.rr(w - a, 0, w, d - bf, r=a * 0.45)
    inner = w - 2 * a
    n = 1 if single else max(1, round(inner / 680))
    cw = inner / n
    for i in range(n):
        x0 = a + i * cw
        g.rr(x0 + 8, d - bf - bc, x0 + cw - 8, d - bf - 6, r=45, fine=True)        # 靠垫
        g.rr(x0 + 8, 25, x0 + cw - 8, d - bf - bc - 10, r=45)                      # 坐垫
        if not single:
            g.line((x0 + cw / 2 - 40, 60), (x0 + cw / 2 + 40, 60), fine=True)      # 坐垫车缝
    # 扶手上的软包线
    g.line((a * 0.5, 60), (a * 0.5, d - bf - 30), fine=True)
    g.line((w - a * 0.5, 60), (w - a * 0.5, d - bf - 30), fine=True)


def b_armchair(g, w, d, v):
    b_sofa(g, w, d, 'S')


def b_quanyi(g, w, d, v):
    t = max(40, w * 0.06)
    R = w / 2 - 10
    yc = d - R - 10
    xl, xr = w / 2 - R, w / 2 + R
    yf = max(60, d * 0.12)
    hw = R - t - 40
    ri = R - t
    ytop = yc + math.sqrt(max(ri * ri - hw * hw, 0)) - 15
    g.rr(w / 2 - hw, 20, w / 2 + hw, ytop, r=12, fill='furn')            # 座面
    g.rr(w / 2 - hw + 40, 60, w / 2 + hw - 40, ytop - 40, r=8, fine=True)
    band = [(xl, yf, 0), (xl, yc, -1.0), (xr, yc, 0), (xr, yf, 0),
            (xr - t, yf, 0), (xr - t, yc, 1.0), (xl + t, yc, 0), (xl + t, yf, 0)]
    g.fill(band, 'soft')
    g.pl(band)
    g.disc((xl + t / 2, yf), t * 0.75, fill='soft')                       # 扶手出头
    g.disc((xr - t / 2, yf), t * 0.75, fill='soft')
    g.line((w / 2 - hw + 20, -30), (w / 2 + hw - 20, -30), fine=True)     # 踏脚枨
    g.line((w / 2 - hw + 20, -30), (w / 2 - hw + 20, 20), fine=True)
    g.line((w / 2 + hw - 20, -30), (w / 2 + hw - 20, 20), fine=True)


def b_coffee(g, w, d, v):
    if v == 'R':
        R = min(w, d) / 2
        g.disc((w / 2, d / 2), R, fill='furn')
        g.circle((w / 2, d / 2), R - 40, fine=True)
        return
    g.rr(0, 0, w, d, r=min(60, d * 0.08), fill='furn')
    g.rr(45, 45, w - 45, d - 45, r=25, fine=True)
    for x, y in ((80, 80), (w - 80, 80), (w - 80, d - 80), (80, d - 80)):   # 台面下桌腿
        g.rr(x - 18, y - 18, x + 18, y + 18, fine=True, dashed=True)


def b_counter(g, w, d, v):
    g.rr(0, 0, w, d, fill='furn')
    ledge = min(300, d * 0.4)
    g.line((0, ledge), (w, ledge))                                        # 高台面（顾客侧）
    g.line((0, ledge - 20), (w, ledge - 20), fine=True)
    g.line((40, ledge), (40, d), fine=True)                               # 端板
    g.line((w - 40, ledge), (w - 40, d), fine=True)
    n = max(1, int(w // 1300))
    # 内侧工作区：低台面下的柜体虚线
    g.pl([(60, ledge + 30), (w - 60, ledge + 30), (w - 60, d - 30), (60, d - 30)], fine=True, dashed=True)
    for i in range(n):
        x = (i + 0.5) * w / n
        g.rr(x - 260, ledge + 25, x + 260, ledge + 70, r=8)               # 显示器
        g.rr(x - 200, d - 210, x + 200, d - 90, r=10, fine=True)          # 键盘
        chair(g, x, d + 40 + 270, 270, 540, 520, 'office')
    if w >= 2400:                                                        # 扫描器/打印机
        g.rr(w - 520, d - 280, w - 160, d - 60, r=10, fine=True)


def b_bed(g, w, d, v):
    g.rr(0, 0, w, d, r=20, fill='furn')
    g.rr(0, d - 70, w, d, r=10)                                           # 床头板
    g.rr(25, 25, w - 25, d - 70, r=30, fine=True)                         # 床垫
    n = 2 if w >= 1350 else 1
    pw = (w - 100) / n
    for i in range(n):
        x0 = 50 + i * pw
        g.rr(x0 + 15, d - 70 - 420, x0 + pw - 15, d - 70 - 60, r=70, fill='soft')
    yb = d - 70 - 520
    g.line((25, yb), (w - 25, yb))                                        # 被子翻边
    g.pl([(w - 25, yb - 380), (w - 380, yb), (w - 25, yb)], closed=True, fine=True)


def b_rohan(g, w, d, v):
    t = max(60, min(90, d * 0.08))
    g.rr(0, 0, w, d, r=15, fill='furn')
    g.rr(0, d - t, w, d, r=8)                                             # 后围子
    g.rr(0, 0.22 * d, t, d - t, r=8)                                      # 侧围子
    g.rr(w - t, 0.22 * d, w, d - t, r=8)
    g.rr(t + 25, 25, w - t - 25, d - t - 25, r=20, fine=True)             # 床面
    s = min(620, d * 0.6)
    cx, cy = w / 2, (d - t) / 2
    g.rr(cx - s / 2, cy - s * 0.4, cx + s / 2, cy + s * 0.4, r=10, fill='soft')     # 炕几
    g.rr(cx - s / 2 + 35, cy - s * 0.4 + 35, cx + s / 2 - 35, cy + s * 0.4 - 35, r=6, fine=True)
    for x0 in (t + 60, w - t - 60 - 460):                                 # 靠枕
        g.rr(x0, d - t - 170, x0 + 460, d - t - 25, r=60, fine=True)


def b_wc(g, w, d, v):
    cx = w / 2
    tt = min(200, d * 0.28)                                               # 水箱深
    g.rr(w * 0.06, d - tt, w * 0.94, d, r=30, fill='fixt')
    g.rr(w * 0.06 + 25, d - tt + 25, w * 0.94 - 25, d - 25, r=15, fine=True)
    g.circle((cx, d - tt / 2), 22, fine=True)                             # 冲水按钮
    y0, y1 = 25, d - tt - 45
    ry = (y1 - y0) / 2
    rx = w * 0.44
    cy = y0 + ry
    # 颈部：从座圈后方连到水箱
    xn = rx * 0.55
    yn = cy + ry * math.sqrt(1 - (xn / rx) ** 2)
    neck = [(cx - xn, yn), (cx - xn, d - tt), (cx + xn, d - tt), (cx + xn, yn)]
    g.fill(neck + [(cx, cy)], 'fixt')
    g.pl(neck, closed=False)
    g.oval((cx, cy), rx, ry, fill='fixt')                                 # 座圈外沿
    g.ell((cx, cy - ry * 0.08), rx - 55, ry * 0.72)                       # 座圈内沿
    g.ell((cx, cy - ry * 0.02), rx - 95, ry * 0.5, fine=True)             # 水面
    g.line((cx - xn + 25, yn + 10), (cx + xn - 25, yn + 10), fine=True)   # 铰链


def b_squat(g, w, d, v):
    x0, x1 = w * 0.08, w * 0.92
    r = (x1 - x0) / 2
    g.rr(x0, d * 0.03, x1, d * 0.97, r=r * 0.9, fill='fixt')
    g.rr(x0 + 45, d * 0.08, x1 - 45, d * 0.97 - 45, r=r * 0.75, fine=True)
    # 便槽开口 + 前端防溅罩
    ox0, ox1 = w * 0.3, w * 0.7
    g.rr(ox0, d * 0.16, ox1, d * 0.74, r=(ox1 - ox0) / 2)
    g.pl([(x0 + 45, d * 0.74, 0), (x1 - 45, d * 0.74, 0)], closed=False, fine=True)
    g.arc((w / 2, d * 0.74), (ox1 - ox0) / 2, 0, 180, fine=True)
    g.circle((w / 2, d * 0.28), min(30, w * 0.07), fine=True)             # 排水口
    for sx in (-1, 1):                                                    # 防滑踏脚
        xa = w / 2 + sx * (ox1 - ox0) / 2 + sx * 25
        xb = w / 2 + sx * (r - 60)
        for k in range(3):
            y = d * (0.30 + 0.12 * k)
            g.line((xa, y), (xb, y), fine=True)


def b_urinal(g, w, d, v):
    x0, x1 = w * 0.08, w * 0.92
    r = (x1 - x0) / 2
    out = [(x0, d, 0), (x0, r, 1.0), (x1, r, 0), (x1, d, 0)]
    g.fill(out, 'fixt')
    g.pl(out)
    i0, i1 = x0 + 45, x1 - 45
    ri = (i1 - i0) / 2
    g.pl([(i0, d - 60, 0), (i0, r, 1.0), (i1, r, 0), (i1, d - 60, 0)])
    g.circle((w / 2, d * 0.45), min(28, w * 0.07), fine=True)             # 排水口
    g.circle((w / 2, d - 30), 22, fine=True)                              # 感应冲水
    _ = ri


def b_lav(g, w, d, v):
    if v == 'W':                                                         # 挂墙洗手盆
        x0, x1 = w * 0.05, w * 0.95
        r = min((x1 - x0) / 2, d * 0.6)
        out = [(x0, d, 0), (x0, r, 0.4142), (x0 + r, 0, 0), (x1 - r, 0, 0.4142), (x1, r, 0), (x1, d, 0)]
        g.fill(out, 'fixt')
        g.pl(out)
        n = 1
    else:
        g.rr(0, 0, w, d, fill='fixt')
        g.line((0, 25), (w, 25), fine=True)                               # 台面前沿挡水
        g.line((0, d - 40), (w, d - 40), fine=True)                       # 后挡水
        n = max(1, int(w // 750))
    for i in range(n):
        x = (i + 0.5) * w / n
        rx = min(250, w / n * 0.36)
        ry = min(rx * 0.75, d * 0.3)
        cy = d * 0.45
        g.oval((x, cy), rx, ry, fill='#FFFFFF')
        g.ell((x, cy - 8), rx - 35, ry - 30, fine=True)
        g.circle((x, cy - 15), 18, fine=True)                             # 排水
        yf = d - 70                                                      # 龙头
        g.circle((x, yf), 28)
        g.rr(x - 12, cy + ry - 55, x + 12, yf - 28, r=6)
        g.line((x + 28, yf), (x + 95, yf + 20), fine=True)                # 单把手


def b_mop(g, w, d, v):
    g.rr(0, 0, w, d, r=20, fill='fixt')
    g.rr(50, 50, w - 50, d - 90, r=35)
    g.line((50, 50), (w / 2 - 25, (d - 40) / 2 - 25), fine=True)
    g.line((w - 50, 50), (w / 2 + 25, (d - 40) / 2 - 25), fine=True)
    g.circle((w / 2, (d - 40) / 2), 30, fine=True)
    g.circle((w / 2, d - 45), 25)                                         # 龙头
    g.line((w / 2, d - 70), (w / 2, d - 150), fine=True)


def b_plant(g, w, d, v):
    """盆栽平面：锯齿叶冠外轮廓 + 放射叶脉 + 内层叶片 + 中心。"""
    R = min(w, d) / 2
    small = R < 220
    mid = R < 420                       # 1:100 下直径不到 8 mm：少画几笔，免得糊成一团
    with g.at(w / 2, d / 2):
        r = _rng(int(R) + 7)
        n = 7 if small else (9 if mid else (11 if R < 650 else 15))
        a0 = 90 + 360 / n / 2
        pts, tips = [], []
        for i in range(n):
            at = math.radians(a0 + 360 * i / n + 8 * (next(r) - 0.5))
            rt = R * (0.9 + 0.1 * next(r))
            av = math.radians(a0 + 360 * (i + 0.5) / n)
            rv = R * (0.66 + 0.08 * next(r))
            tips.append((at, rt))
            pts.append((rt * math.cos(at), rt * math.sin(at), 0.22))
            pts.append((rv * math.cos(av), rv * math.sin(av), 0.22))
        g.fill(pts, 'plant')
        g.pl(pts)
        if not mid:
            for at, rt in tips:                                          # 叶脉
                g.line((R * 0.16 * math.cos(at), R * 0.16 * math.sin(at)),
                       (rt * 0.82 * math.cos(at), rt * 0.82 * math.sin(at)), fine=True)
        m = 4 if small else (5 if mid else 7)
        for i in range(m):                                               # 内层叶片
            a = a0 + 360 * (i + 0.3) / m + 10 * (next(r) - 0.5)
            leaf(g, a, R * 0.12, R * (0.5 + 0.08 * next(r)), 0.3, rib=not mid)
        g.disc((0, 0), R * (0.12 if small else 0.08), fill='leaf', fine=True)


def b_lamp(g, w, d, v):
    R = min(w, d) / 2
    c = (w / 2, d / 2)
    g.circle(c, R, dashed=True)
    g.circle(c, R * 0.45, dashed=True, fine=True)
    e = R * 1.3
    g.line((c[0] - e, c[1]), (c[0] + e, c[1]), dashed=True, fine=True)
    g.line((c[0], c[1] - e), (c[0], c[1] + e), dashed=True, fine=True)


def b_floorlamp(g, w, d, v):
    R = min(w, d) / 2
    c = (w / 2, d / 2)
    g.disc(c, R, fill='furn')
    g.circle(c, R * 0.35, fine=True)
    g.line((c[0] - R * 0.7, c[1] - R * 0.7), (c[0] + R * 0.7, c[1] + R * 0.7), fine=True)
    g.line((c[0] - R * 0.7, c[1] + R * 0.7), (c[0] + R * 0.7, c[1] - R * 0.7), fine=True)


def b_piano(g, w, d, v):
    kb = min(260, d * 0.38)
    g.rr(0, kb, w, d, r=15, fill='furn')                                  # 琴身
    g.rr(30, kb + 30, w - 30, d - 30, r=8, fine=True)
    k0, k1 = w * 0.06, w * 0.94
    g.rr(k0, 0, k1, kb, fill='#FFFFFF')                                   # 键盘
    nk = 26
    for i in range(1, nk):
        x = k0 + (k1 - k0) * i / nk
        g.line((x, 0), (x, kb * 0.45 if i % 7 in (0, 3) else kb), fine=True)
    g.line((k0, kb * 0.45), (k1, kb * 0.45), fine=True)
    bw = min(760, w * 0.55)
    g.rr(w / 2 - bw / 2, -420, w / 2 + bw / 2, -100, r=20, fill='furn')    # 琴凳
    g.rr(w / 2 - bw / 2 + 30, -390, w / 2 + bw / 2 - 30, -130, r=12, fine=True)


def b_screen(g, w, d, v):
    n = max(2, round(w / 420))
    t = 30
    pts = [(w * i / n, d * (0.2 if i % 2 == 0 else 0.8)) for i in range(n + 1)]
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        L = math.hypot(xb - xa, yb - ya)
        nx, ny = -(yb - ya) / L * t / 2, (xb - xa) / L * t / 2
        quad = [(xa + nx, ya + ny), (xb + nx, yb + ny), (xb - nx, yb - ny), (xa - nx, ya - ny)]
        g.fill(quad, 'soft')
        g.pl(quad)
        for k in (1, 2, 3):                                              # 扇面格心
            f = k / 4
            g.line((xa + (xb - xa) * f + nx, ya + (yb - ya) * f + ny),
                   (xa + (xb - xa) * f - nx, ya + (yb - ya) * f - ny), fine=True)
    for p in pts[1:-1]:
        g.circle(p, t * 0.6, fine=True)


def b_generic(g, w, d, v):
    g.rr(0, 0, w, d, fill='furn')
    g.line((0, 0), (w, d), fine=True)
    g.line((0, d), (w, 0), fine=True)


BUILDERS = {
    'TABLE': b_table, 'ROUND': b_round, 'DESK': b_desk, 'SCHOOL': b_school, 'CHAIR': b_chair,
    'STOOL': b_stool, 'SHELF': b_shelf, 'CABINET': b_cabinet, 'SOFA': b_sofa, 'ARMCHAIR': b_armchair,
    'QUANYI': b_quanyi, 'COFFEE': b_coffee, 'COUNTER': b_counter, 'BED': b_bed, 'ROHAN': b_rohan,
    'WC': b_wc, 'SQUAT': b_squat, 'URINAL': b_urinal, 'LAV': b_lav, 'MOP': b_mop, 'PLANT': b_plant,
    'LAMP': b_lamp, 'FLOORLAMP': b_floorlamp, 'PIANO': b_piano, 'SCREEN': b_screen, 'GENERIC': b_generic,
}


# ====================================================================== 块定义与插入
def _ensure_linetype(sh):
    lts = sh.doc.linetypes
    if LT_DASH not in lts:
        # 文档 $LTSCALE = 比例×0.5；这里的图案单位乘上它正好是纸面 2 mm 划 + 1 mm 空
        lts.add(LT_DASH, pattern=[6.0, 4.0, -2.0], description='家具虚线 __ __ __')


def make_block(sh, kind, w, d, variant=''):
    """按类型和真实尺寸取（或生成）块，返回块名。"""
    _ensure_linetype(sh)
    w, d = int(round(w)), int(round(d))
    name = f'{kind}_{w}x{d}' + (f'_{variant}' if variant else '')
    blk, created = sh.block(name)
    if created:
        BUILDERS.get(kind, b_generic)(G(blk, sh.style == 'presentation'), float(w), float(d), variant)
    return name


def _local_to_world(box, x, y):
    ox, oy = box.origin()[:2]
    r = math.radians(box.rotation)
    return (ox + x * math.cos(r) - y * math.sin(r), oy + x * math.sin(r) + y * math.cos(r))


def place(sh, box, kind=None):
    """画一个 box；返回 (类型, 块名)。"""
    kind = kind or classify(box)
    var = _variant(kind, box)
    w, d = box.size[0], box.size[1]
    name = make_block(sh, kind, w, d, var)
    layer = FIXT if kind in FIXTURES else FURN
    sh.insert(name, box.origin()[:2], rot=box.rotation, layer=layer)
    label = None
    if kind == 'GENERIC':
        label = box.name
    elif sh.style == 'presentation' and kind in LABELLED:
        label = box.name
    if label:
        sh.count('furniture_label')
        if kind == 'COUNTER':
            ledge = min(300, d * 0.4)
            at = _local_to_world(box, w / 2, (ledge - 20) / 2)          # 写在前沿高台面那条空带里
            h = min(S.text_h('small', sh.scale), 0.62 * (ledge - 20))
        else:
            at = _local_to_world(box, w / 2, d / 2)
            h = min(S.text_h('small', sh.scale), 0.45 * min(w, d))
        sh.text(str(label), at, 'small', layer, height=h)
    return kind, name


def _level_of(m, z):
    levels = sorted(m.levels, key=lambda l: l.elevation)
    lid = levels[0].id if levels else None
    for l in levels:
        if l.elevation <= z + 600:      # 容许下沉区（-450 等）仍算本层
            lid = l.id
    return lid


def draw(sh, m, level_id):
    stats = {'inserts': 0, 'fixtures': 0, 'labels': 0, 'types': {}, 'fallback': []}
    for box in getattr(m, 'boxes', []) or []:
        if _level_of(m, box.at[2] if len(box.at) > 2 else 0.0) != level_id:
            continue
        try:
            kind, _ = place(sh, box)
        except Exception as e:                                         # 单个家具出错不拖垮整张图
            stats.setdefault('errors', []).append(f'{box.id}: {e}')
            continue
        stats['inserts'] += 1
        stats['types'][kind] = stats['types'].get(kind, 0) + 1
        if kind in FIXTURES:
            stats['fixtures'] += 1
        if kind == 'GENERIC':
            stats['fallback'].append(box.name)
    stats['labels'] = sh.stats.get('furniture_label', 0)
    stats['blocks'] = sum(1 for b in sh.doc.blocks if b.name.split('_')[0] in BUILDERS)
    sh.count('furniture', stats['inserts'])
    return stats
