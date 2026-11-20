"""立面图、剖面图（2026-11-20）：直接从规格算正投影，和平面图同一套国标线宽、标高、轴号、图框。

    from wish_cad.cad.elevation import draw_elevation, draw_section, write_all
    draw_elevation(m, 'S', out_dir)        # 南立面（人站在南边往北看）；N / E / W 同理
    draw_section(m, m.sections[0], out_dir)  # 按平面图上的剖切符号出剖面
    write_all(m, out_dir)                  # 四个立面 + 全部剖面

画法（GB/T 50104）：
  立面  外轮廓 粗 b；墙面转折、门窗洞口、檐口 中 0.5b；门窗分格 细 0.25b；室外地坪线 加粗 1.4b。
  剖面  剖到的墙、楼板、屋面 涂黑 + 粗 b 轮廓；看到的墙面、门窗 中/细线；地坪线 加粗。
  标高标在图右侧，两端轴号标在地坪线下，左侧竖向三道尺寸（地坪—室内—檐口—屋脊）。
遮挡：按离观察者由远到近逐个画白底面（后画的盖住先画的），不做精确消隐；
楼梯、家具、室外台阶在立/剖面里暂不画。
"""
from __future__ import annotations

import math
from pathlib import Path

from . import standard as S

S.LAYERS.update({
    'A-ELEV-OUTL': (7, 0.70, 'Continuous', '立面外轮廓（粗）'),
    'A-ELEV':      (7, 0.35, 'Continuous', '立面墙面转折、门窗洞口、檐口（中）'),
    'A-ELEV-FINE': (7, 0.18, 'Continuous', '门窗分格、玻璃、幕墙竖梃（细）'),
    'A-ELEV-GRND': (7, 1.00, 'Continuous', '室外地坪线（加粗 1.4b）'),
    'A-ELEV-FILL': (7, 0.00, 'Continuous', '遮挡用白底'),
    'A-SECT':      (7, 0.70, 'Continuous', '剖到的轮廓（粗）'),
    'A-SECT-PATT': (7, 0.00, 'Continuous', '剖到的墙、板涂黑'),
})

from .sheet import Sheet  # noqa: E402  (图层先登记，再建 Sheet)

SIDES = {   # 立面名：观察者看的方向 look
    'S': ((0.0, 1.0), '南立面图'), 'N': ((0.0, -1.0), '北立面图'),
    'E': ((-1.0, 0.0), '东立面图'), 'W': ((1.0, 0.0), '西立面图'),
}
LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXY'      # 国标轴号不用 I、O、Z
WHITE = '#FFFFFF'


# =========================================================================== geometry
def _unit(v):
    L = math.hypot(*v) or 1.0
    return (v[0] / L, v[1] / L)


def _pip(p, poly):
    x, y = p
    c = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][:2]
        x2, y2 = poly[(i + 1) % n][:2]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def _area(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly))) / 2.0


class View:
    """正投影：look = 观察方向（水平单位向量）；h = 图面横坐标（观察者的右手方向），depth 越大越远。"""

    def __init__(self, look, origin=(0.0, 0.0)):
        self.look = _unit(look)
        self.right = (self.look[1], -self.look[0])
        self.o = origin

    def h(self, p):
        return (p[0] - self.o[0]) * self.right[0] + (p[1] - self.o[1]) * self.right[1]

    def d(self, p):
        return (p[0] - self.o[0]) * self.look[0] + (p[1] - self.o[1]) * self.look[1]


def _roof_fn(m):
    """屋顶底面高度 z(x, y)（绝对标高）；没有屋顶返回 None。"""
    r = m.roof
    if not r:
        return None
    fp = r['footprint']
    base = r['base_z']
    kind = r['kind']
    from ..curve import SURFACE_KINDS, height_fn
    if kind in SURFACE_KINDS:
        s_ = r.get('surface') or {}
        f, _ = height_fn(kind, fp, r['rise'], s_.get('axis'), s_.get('wavelength'), s_.get('phase', 0.0),
                         s_.get('shape'), s_.get('heights'))
        return lambda x, y: base + f(x, y)
    if kind == 'flat' or not r.get('rise'):
        return lambda x, y: base
    xs, ys = [p[0] for p in fp], [p[1] for p in fp]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lx, ly = x1 - x0, y1 - y0
    rise = r['rise']
    if kind == 'shed':          # 单坡：沿短边从低到高
        if lx <= ly:
            return lambda x, y: base + rise * (x - x0) / max(lx, 1)
        return lambda x, y: base + rise * (y - y0) / max(ly, 1)
    half = min(lx, ly) / 2
    if kind == 'gable':         # 双坡：屋脊沿长边
        if lx >= ly:
            return lambda x, y: base + rise * max(0.0, 1 - abs(y - cy) / max(half, 1))
        return lambda x, y: base + rise * max(0.0, 1 - abs(x - cx) / max(half, 1))
    # hip 四坡：到外包框最近边的距离 / 半短边
    return lambda x, y: base + rise * max(0.0, min(x - x0, x1 - x, y - y0, y1 - y) / max(half, 1))


def _outline(m, lid):
    """建筑外轮廓（墙外皮），用来判断墙的哪一面朝外。"""
    from .body import _building_outline
    segs = [s for s in m.segments if s.level == lid]
    return _building_outline(m, lid, segs)


def _faces(s, bld):
    """墙段两侧面：[(p0, p1, 外法向, 是否外侧面)]。"""
    ux, uy = s.unit
    nx, ny = -uy, ux                       # 左法向
    h = s.thickness / 2
    a, b = s.built_a, s.built_b
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    probe = (mid[0] + nx * (h + 5), mid[1] + ny * (h + 5))
    left_out = bool(bld) and not _pip(probe, bld)
    out = []
    for sg in (1, -1):
        p0 = (a[0] + nx * h * sg, a[1] + ny * h * sg)
        p1 = (b[0] + nx * h * sg, b[1] + ny * h * sg)
        is_out = left_out if sg == 1 else (bool(bld) and not left_out)
        out.append((p0, p1, (nx * sg, ny * sg), is_out))
    return out


# =========================================================================== drawing core
class Elev:
    def __init__(self, m, view, sh, z_ground, roof_z, cut=None):
        self.m, self.v, self.sh = m, view, sh
        self.zg = z_ground
        self.roof_z = roof_z
        self.cut = cut          # 剖面：只画 depth >= 0 的东西
        self.polys = []         # 画出来的投影多边形（求外轮廓用）
        self.P = sh.p

    # --------------------------------------------------------------- primitives
    def face(self, pts2, layer='A-ELEV', fill=True, keep=True):
        if fill:
            self.sh.solid(pts2, 'A-ELEV-FILL', rgb=WHITE)
        self.sh.poly(pts2, layer, closed=True)
        if keep:
            self.polys.append(pts2)

    def wall_top(self, s, p):
        top = s.base_z + s.height
        fills = (self.m.raw.get('roof') or {}).get('fill_walls', True)
        if self.roof_z and fills and self.m.roof and self.m.roof['kind'] not in ('flat', 'gable', 'hip', 'shed') \
                and _pip(p, self.m.roof['footprint']):
            top = max(top, self.roof_z(*p))
        return top

    # --------------------------------------------------------------- one wall face with its openings
    def wall_face(self, s, p0, p1, exterior_face):
        v = self.v
        h0, h1 = v.h(p0), v.h(p1)
        if self.cut is not None:                     # 剖面：裁掉观察者一侧（depth < 0）的部分
            d0, d1 = v.d(p0), v.d(p1)
            if d0 < 0 and d1 < 0:
                return
            if (d0 < 0) != (d1 < 0):
                t = d0 / (d0 - d1)
                pc = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
                if d0 < 0:
                    p0, h0 = pc, v.h(pc)
                else:
                    p1, h1 = pc, v.h(pc)
        if abs(h1 - h0) < 5:
            return
        zb = self.zg if (exterior_face and s.base_z <= self.zg + 1500) else s.base_z
        n = max(1, int(math.ceil(abs(h1 - h0) / 300)))
        top = []
        for i in range(n + 1):
            t = i / n
            p = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
            top.append((v.h(p), self.wall_top(s, p)))
        pts = [(h0, zb), (h1, zb)] + list(reversed(top))
        pts = [pts[0], pts[1]] + [q for q in pts[2:]]
        curtain = s.kind == 'curtain'
        self.face(pts, 'A-ELEV')
        if curtain:
            self.curtain(s, p0, p1, zb, top)
        for o in self.m.openings:
            if o.wall == s.id:
                self.opening(s, o, p0, p1)

    def curtain(self, s, p0, p1, zb, top):
        v = self.v
        L = math.dist(p0, p1)
        step = S.DETAIL['mullion_step']
        k = max(1, round(L / step))
        zt = min(z for _, z in top)
        for i in range(1, k):
            t = i / k
            p = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
            self.sh.line((v.h(p), zb), (v.h(p), self.wall_top(s, p)), 'A-ELEV-FINE')
        lv = next((l for l in self.m.levels if l.id == s.level), None)
        z0 = lv.elevation if lv else s.base_z
        for z in (z0, z0 + 2700):           # 横档：楼面、门头高
            if zb < z < zt:
                self.sh.line((v.h(p0), z), (v.h(p1), z), 'A-ELEV-FINE')

    def opening(self, s, o, p0, p1):
        v = self.v
        ux, uy = s.unit
        nx, ny = -uy, ux
        # 洞口在墙中线上的两端，平移到这个面上
        c = s.point_at(o.at)
        e = s.point_at(o.at + o.width)
        off = ((p0[0] - s.built_a[0]), (p0[1] - s.built_a[1]))
        qa, qb = (c[0] + off[0], c[1] + off[1]), (e[0] + off[0], e[1] + off[1])
        if self.cut is not None and (v.d(qa) < 0 or v.d(qb) < 0):
            return
        ha, hb = sorted((v.h(qa), v.h(qb)))
        if hb - ha < 50:
            return
        lv = next((l for l in self.m.levels if l.id == s.level), None)
        z0 = (lv.elevation if lv else s.base_z) + o.sill
        z1 = z0 + o.height
        rect = [(ha, z0), (hb, z0), (hb, z1), (ha, z1)]
        self.sh.poly(rect, 'A-ELEV', closed=True)
        fw = min(S.DETAIL['window_frame'], (hb - ha) / 6)
        fine = 'A-ELEV-FINE'
        if o.kind == 'void':
            return
        inner = [(ha + fw, z0 + (fw if o.kind == 'window' else 0)), (hb - fw, z0 + (fw if o.kind == 'window' else 0)),
                 (hb - fw, z1 - fw), (ha + fw, z1 - fw)]
        self.sh.poly(inner, fine, closed=True)
        if o.kind == 'window':
            w = hb - ha
            nmul = max(0, int(w // 1200))
            for i in range(1, nmul + 1):
                x = ha + w * i / (nmul + 1)
                self.sh.line((x, z0 + fw), (x, z1 - fw), fine)
            if o.height > 1800:                   # 亮子横档
                zt = z1 - 600
                self.sh.line((ha + fw, zt), (hb - fw, zt), fine)
            self.sh.line((ha - self.P(1), z0), (hb + self.P(1), z0), 'A-ELEV')       # 窗台线
        else:
            typ = (o.type or '').lower()
            w = hb - ha
            if typ in ('double', 'mother') or (not typ and w >= 1500):
                xm = ha + w * (0.5 if typ != 'mother' else 0.65)
                self.sh.line((xm, z0), (xm, z1 - fw), fine)
            elif typ == 'sliding':
                xm = ha + w / 2
                self.sh.line((xm, z0), (xm, z1 - fw), fine)
                self.sh.line((xm - 30, z0), (xm - 30, z1 - fw), fine)
            if o.height > 2400:                   # 门亮子
                self.sh.line((ha + fw, z0 + 2100), (hb - fw, z0 + 2100), fine)

    # --------------------------------------------------------------- columns
    def column_rect(self, c):
        v = self.v
        x, y = c['at']
        if c.get('dia') or c.get('shape') == 'round':
            r = (c.get('dia') or 400) / 2
            hc = v.h((x, y))
            return hc - r, hc + r
        w, d = c['size']
        hs = [v.h((x + sx * w / 2, y + sy * d / 2)) for sx in (-1, 1) for sy in (-1, 1)]
        return min(hs), max(hs)

    # --------------------------------------------------------------- roof
    def roof(self, beyond_only=False, layer='A-ELEV'):
        """屋顶投影区域：上沿 = 屋面顶的最高包络，下沿 = 离观察者最近的檐口底边。"""
        r = self.m.roof
        if not r or not self.roof_z:
            return None
        v = self.v
        fp = r['footprint']
        t = r['thickness']
        bnd = []
        n = len(fp)
        for i in range(n):
            a, b = fp[i], fp[(i + 1) % n]
            k = max(1, int(math.ceil(math.dist(a, b) / 150)))
            bnd += [(a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k) for j in range(k)]
        xs, ys = [p[0] for p in fp], [p[1] for p in fp]
        inner = []
        st = 200.0
        x = min(xs) + st / 2
        while x < max(xs):
            y = min(ys) + st / 2
            while y < max(ys):
                if _pip((x, y), fp):
                    inner.append((x, y))
                y += st
            x += st
        if beyond_only:
            bnd = [p for p in bnd if v.d(p) >= 0]
            inner = [p for p in inner if v.d(p) >= 0]
        if not bnd:
            return None
        allp = bnd + inner
        hs = [v.h(p) for p in allp]
        h0, h1 = min(hs), max(hs)
        nb = max(2, int((h1 - h0) / 100))
        bw = (h1 - h0) / nb
        up = [None] * (nb + 1)
        low = [None] * (nb + 1)
        for p in allp:
            i = min(nb, max(0, int(round((v.h(p) - h0) / bw))))
            z = self.roof_z(*p) + t
            if up[i] is None or z > up[i]:
                up[i] = z
        # 檐口下沿：每个分箱中心竖线与外轮廓各边求交，取离观察者最近的交点（插值，不会因采样漏点出尖刺）
        ring = bnd if not beyond_only else None
        edges = []
        if ring:
            for k in range(len(ring)):
                p, q = ring[k], ring[(k + 1) % len(ring)]
                edges.append((v.h(p), v.d(p), self.roof_z(*p), v.h(q), v.d(q), self.roof_z(*q)))
        for i in range(nb + 1):
            hc = h0 + i * bw
            if ring:
                for (ha, da, za, hb, db, zb) in edges:
                    if (ha - hc) * (hb - hc) <= 0 and abs(hb - ha) > 1e-6:
                        t = (hc - ha) / (hb - ha)
                        dd, zz = da + (db - da) * t, za + (za * 0 + (zb - za) * t)
                        if low[i] is None or dd < low[i][0]:
                            low[i] = (dd, zz)
        if not ring:
            for p in bnd:
                i = min(nb, max(0, int(round((v.h(p) - h0) / bw))))
                dd = v.d(p)
                if low[i] is None or dd < low[i][0]:
                    low[i] = (dd, self.roof_z(*p))
        # 补空：取相邻值
        def fill(arr, get=lambda q: q):
            last = None
            for i in range(len(arr)):
                if arr[i] is None:
                    arr[i] = last
                else:
                    last = arr[i]
            last = None
            for i in range(len(arr) - 1, -1, -1):
                if arr[i] is None:
                    arr[i] = last
                else:
                    last = arr[i]
        fill(up)
        fill(low)
        win = 2                                   # 内部点间距 200 / 分箱 100：上沿取前后 2 箱的最大值
        up = [max(up[j] for j in range(max(0, i - win), min(nb, i + win) + 1)) for i in range(nb + 1)]
        top = [(h0 + i * bw, up[i]) for i in range(nb + 1)]
        bot = [(h0 + i * bw, min(low[i][1], up[i] - t)) for i in range(nb + 1)]
        pts = _simplify(top + list(reversed(bot)))
        self.face(pts, layer)
        return pts

    # --------------------------------------------------------------- silhouette
    def silhouette(self):
        """所有投影多边形并集的外边界（粗线）：20 mm 栅格化后取 0.5 等值线。"""
        if not self.polys:
            return
        import numpy as np
        from matplotlib.path import Path as MPath
        import contourpy
        hs = [p[0] for poly in self.polys for p in poly]
        zs = [p[1] for poly in self.polys for p in poly]
        cell = 20.0
        x0, x1 = min(hs) - 3 * cell, max(hs) + 3 * cell
        z0, z1 = max(min(zs), self.zg) - 3 * cell, max(zs) + 3 * cell
        X = np.arange(x0, x1, cell)
        Z = np.arange(z0, z1, cell)
        XX, ZZ = np.meshgrid(X, Z)
        pts = np.column_stack([XX.ravel(), ZZ.ravel()])
        inside = np.zeros(len(pts), bool)
        for poly in self.polys:
            if len(poly) >= 3:
                inside |= MPath(np.asarray(poly)).contains_points(pts, radius=1e-6)
        inside &= pts[:, 1] >= self.zg - 1
        grid = inside.reshape(XX.shape).astype(float)
        for line in contourpy.contour_generator(X, Z, grid).lines(0.5):
            if len(line) < 3:
                continue
            pl = [(float(x), float(z)) for x, z in line]
            pl = [(x, max(z, self.zg)) for x, z in pl]
            self.sh.poly(_simplify(pl, closed=False, tol=cell * 1.6), 'A-ELEV-OUTL')

    # --------------------------------------------------------------- annotation
    def ground(self, h0, h1):
        self.sh.line((h0 - self.P(15), self.zg), (h1 + self.P(15), self.zg), 'A-ELEV-GRND')

    def level_marks(self, h_right, zs):
        """右侧标高：zs = [(z, 说明)]，自下而上，太挤时错开。"""
        P = self.P
        hgt = S.text_h('area', self.sh.scale)
        x = h_right + P(6)
        lastz = None
        col = 0
        for z, note in sorted(zs):
            if lastz is not None and z - lastz < P(5):
                col = 1 - col
            else:
                col = 0
            lastz = z
            ax = x + col * P(36)
            self.sh.line((h_right + P(1), z), (ax + P(12), z), 'A-FLOR-LEVL')
            self.sh.poly([(ax - P(1.5), z + P(3)), (ax, z), (ax + P(1.5), z + P(3)), (ax - P(1.5), z + P(3))], 'A-FLOR-LEVL')
            txt = '±0.000' if abs(z) < 0.5 else f'{z / 1000:.3f}'
            self.sh.text(txt, (ax - P(1.5), z + P(3.6)), 'area', 'A-FLOR-LEVL', align='BOTTOM_LEFT', height=hgt)
            if note:
                self.sh.text(note, (ax + P(12.5), z + P(0.6)), 'small', 'A-ANNO-TEXT', align='BOTTOM_LEFT')

    def vdims(self, h_left, zs):
        P = self.P
        zs = sorted(set(round(z) for z in zs))
        if len(zs) < 2:
            return
        x1 = h_left - P(10)
        x2 = x1 - P(7)
        for a, b in zip(zs, zs[1:]):
            if b - a > P(4):              # 太小的段（如室内外高差 300）只靠标高表达，免得数字挤在一起
                self.sh.dim((h_left, a), (h_left, b), (x1, a), angle=90)
        self.sh.dim((h_left, zs[0]), (h_left, zs[-1]), (x2, zs[0]), angle=90)

    def bubble(self, h, label):
        P = self.P
        r = P(S.GRID_BUBBLE / 2)
        y0 = self.zg - P(3)
        self.sh.line((h, self.zg), (h, y0 - P(4)), 'A-GRID-IDEN')
        c = (h, y0 - P(4) - r)
        self.sh.circle(c, r, 'A-GRID-IDEN')
        self.sh.text(label, c, 'grid', 'A-GRID-IDEN', align='MIDDLE_CENTER')

    def title(self, h_mid, z, text):
        P = self.P
        hh = S.text_h('title', self.sh.scale)
        self.sh.text(text, (h_mid, z), 'title', 'A-TTLB', align='BOTTOM_CENTER', height=hh)
        w = sum(1.25 if ord(ch) > 255 else 0.7 for ch in text) * hh
        self.sh.line((h_mid - w / 2, z - P(1.2)), (h_mid + w / 2, z - P(1.2)), 'A-TTLB', lineweight=70)
        self.sh.line((h_mid - w / 2, z - P(2.2)), (h_mid + w / 2, z - P(2.2)), 'A-TTLB', lineweight=18)
        self.sh.text(f'1:{int(self.sh.scale)}', (h_mid + w / 2 + P(5), z), 'note', 'A-TTLB', align='BOTTOM_LEFT')


def _simplify(pts, closed=True, tol=0.5):
    """去重点 + Douglas–Peucker 抽稀（不会把缓曲线拉成直线）。"""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    if len(out) < 3:
        return out

    def rdp(a, b):
        (ax, ay), (bx, by) = out[a], out[b]
        L = math.hypot(bx - ax, by - ay)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = out[i]
            d = abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / L if L > 1e-9 else math.hypot(px - ax, py - ay)
            if d > best:
                best, bi = d, i
        if best > tol:
            return rdp(a, bi)[:-1] + rdp(bi, b)
        return [out[a], out[b]]
    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    return rdp(0, len(out) - 1)


def _grid(m, lid):
    if m.grid:
        return sorted(m.grid.get('x', [])), sorted(m.grid.get('y', []))
    from .annot import Annot
    sh = Sheet()
    a = Annot(sh, m, lid)
    g = a._auto_grid()
    return sorted(g['x']), sorted(g['y'])


def _labels(n):
    return [LETTERS[i] if i < len(LETTERS) else LETTERS[i // len(LETTERS) - 1] + LETTERS[i % len(LETTERS)] for i in range(n)]


def _zs_common(m, roof_z, e_h0, e_h1, view):
    """标高点：室外地坪、各层楼面、檐口、屋顶最高点。"""
    site = m.site or {}
    zg = site.get('ground_z', -300)
    zs = [(zg, '室外地坪')]
    for i, lv in enumerate(m.levels):
        zs.append((lv.elevation, '' if i else ''))
    top = max(s.base_z + s.height for s in m.segments) if m.segments else 3000
    zs.append((top, '檐口' if m.roof else '女儿墙顶'))
    if m.roof and roof_z:
        fp = m.roof['footprint']
        xs, ys = [p[0] for p in fp], [p[1] for p in fp]
        zmax = top
        st = 400.0
        x = min(xs)
        while x <= max(xs):
            y = min(ys)
            while y <= max(ys):
                if _pip((x, y), fp):
                    zmax = max(zmax, roof_z(x, y) + m.roof['thickness'])
                y += st
            x += st
        if zmax > top + 150:
            zs.append((zmax, '屋面' if m.roof['kind'] == 'flat' else '屋面最高点'))
        else:
            zs[-1] = (top + m.roof['thickness'], '屋面')
    return zs


def _finish(sh, out_dir, stem, title, style):
    from . import render
    res = sh.save(Path(out_dir) / f'{stem}.dxf')
    try:
        res.update(render.render(Path(out_dir) / f'{stem}.dxf', Path(out_dir), stem, scale=sh.scale, style=style, title=title))
    except Exception as exc:     # noqa: BLE001
        res['render'] = f'出图失败：{exc}'
    return res


# =========================================================================== elevation
def draw_elevation(m, side, out_dir, lid=None):
    look, name = SIDES[side]
    dr = getattr(m, 'drawing', None) or {}
    sh = Sheet(scale=dr.get('scale', 100), style=dr.get('style', 'construction'))
    lid = lid or m.levels[0].id
    v = View(look)
    site = m.site or {}
    zg = site.get('ground_z', -300)
    roof_z = _roof_fn(m)
    E = Elev(m, v, sh, zg, roof_z)
    items = []
    for lv in m.levels:
        bld = _outline(m, lv.id)
        for s in (x for x in m.segments if x.level == lv.id and x.exterior):
            for p0, p1, nrm, is_out in _faces(s, bld):
                if not is_out:
                    continue
                if nrm[0] * v.look[0] + nrm[1] * v.look[1] > -0.05:      # 背对观察者
                    continue
                dd = (v.d(p0) + v.d(p1)) / 2
                items.append((dd, 'wall', (s, p0, p1)))
    bld0 = _outline(m, lid)
    for c in m.columns or []:
        if bld0 and _pip(c['at'], bld0):
            continue                                                       # 室内柱看不到
        items.append((v.d(c['at']), 'col', c))
    items.sort(key=lambda t: -t[0])                                        # 远 → 近
    for _, kind, obj in items:
        if kind == 'wall':
            E.wall_face(*obj, exterior_face=True)
        else:
            h0, h1 = E.column_rect(obj)
            z1 = obj['base_z'] + obj['height']
            E.face([(h0, zg), (h1, zg), (h1, z1), (h0, z1)], 'A-ELEV')
    E.roof()
    if not E.polys:
        return {'skipped': f'{name}：没有朝这一面的外墙'}
    E.silhouette()
    hs = [p[0] for poly in E.polys for p in poly]
    h0, h1 = min(hs), max(hs)
    E.ground(h0, h1)
    zs = _zs_common(m, roof_z, h0, h1, v)
    E.level_marks(h1, zs)
    E.vdims(h0, [z for z, _ in zs])
    # 两端轴号
    gx, gy = _grid(m, lid)
    use_x = abs(v.right[0]) > 0.7
    vals = gx if use_x else gy
    labels = [str(i + 1) for i in range(len(vals))] if use_x else _labels(len(vals))
    if vals:
        pts = [((val, 0.0) if use_x else (0.0, val)) for val in vals]
        pairs = sorted(((v.h(p), lab) for p, lab in zip(pts, labels)), key=lambda t: t[0])
        for hh, lab in (pairs[0], pairs[-1]) if len(pairs) > 1 else pairs:
            E.bubble(hh, lab)
        title = f'{pairs[0][1]}—{pairs[-1][1]}轴立面图（{name[0]}）' if len(pairs) > 1 else name
    else:
        title = name
    E.title((h0 + h1) / 2, zg - sh.p(30), title)
    stem = f'立面_{name[:-1]}'
    res = _finish(sh, out_dir, stem, f'{name}', sh.style)
    res['name'] = name
    return res


# =========================================================================== section
def draw_section(m, sec, out_dir):
    dr = getattr(m, 'drawing', None) or {}
    sh = Sheet(scale=dr.get('scale', 100), style=dr.get('style', 'construction'))
    a, b = tuple((sec.get('a') or sec['from'])[:2]), tuple((sec.get('b') or sec['to'])[:2])
    cu = _unit((b[0] - a[0], b[1] - a[1]))
    look = (-cu[1], cu[0]) if sec.get('look', 'left') == 'left' else (cu[1], -cu[0])
    v = View(look, origin=a)
    site = m.site or {}
    zg = site.get('ground_z', -300)
    roof_z = _roof_fn(m)
    E = Elev(m, v, sh, zg, roof_z, cut=True)
    P = sh.p
    L = math.dist(a, b)

    def on_cut(t):
        return (a[0] + cu[0] * t, a[1] + cu[1] * t)

    # ---- 1. 看到的（剖切面以后）：屋顶外轮廓、墙面、柱，远 → 近
    E.roof(beyond_only=True, layer='A-ELEV')
    items = []
    for lv in m.levels:
        bld = _outline(m, lv.id)
        for s in (x for x in m.segments if x.level == lv.id):
            for p0, p1, nrm, is_out in _faces(s, bld):
                if nrm[0] * v.look[0] + nrm[1] * v.look[1] > -0.05:
                    continue
                if v.d(p0) < 0 and v.d(p1) < 0:
                    continue
                items.append(((v.d(p0) + v.d(p1)) / 2, 'wall', (s, p0, p1, is_out)))
    for c in m.columns or []:
        if v.d(c['at']) > 0:
            items.append((v.d(c['at']), 'col', c))
    items.sort(key=lambda t: -t[0])
    for _, kind, obj in items:
        if kind == 'wall':
            s, p0, p1, is_out = obj
            E.wall_face(s, p0, p1, exterior_face=is_out)
        else:
            h0, h1 = E.column_rect(obj)
            z0 = obj['base_z']
            E.face([(h0, z0), (h1, z0), (h1, z0 + obj['height']), (h0, z0 + obj['height'])], 'A-ELEV')

    # ---- 2. 剖到的：墙、柱、楼板、屋面（涂黑 + 粗线）
    cut_polys = []

    def cut(pts):
        sh.solid(pts, 'A-SECT-PATT', color=250 if sh.style == 'construction' else None,
                 rgb=None if sh.style == 'construction' else '#2B2B2B')
        sh.poly(pts, 'A-SECT', closed=True)
        cut_polys.append(pts)

    def seg_cut_interval(s):
        """墙体平面矩形与剖切线的交段（沿剖切线的参数 t0..t1）。"""
        ux, uy = s.unit
        nx, ny = -uy, ux
        h = s.thickness / 2
        A, B = s.built_a, s.built_b
        rect = [(A[0] + nx * h, A[1] + ny * h), (B[0] + nx * h, B[1] + ny * h),
                (B[0] - nx * h, B[1] - ny * h), (A[0] - nx * h, A[1] - ny * h)]
        ts = []
        for i in range(4):
            p, q = rect[i], rect[(i + 1) % 4]
            e = (q[0] - p[0], q[1] - p[1])
            den = cu[0] * e[1] - cu[1] * e[0]
            if abs(den) < 1e-9:
                continue
            w = (p[0] - a[0], p[1] - a[1])
            t = (w[0] * e[1] - w[1] * e[0]) / den
            u = (w[0] * cu[1] - w[1] * cu[0]) / den
            if -1e-9 <= u <= 1 + 1e-9 and -1 <= t <= L + 1:
                ts.append(t)
        if len(ts) < 2:
            return None
        t0, t1 = min(ts), max(ts)
        if t1 - t0 < 1:
            return None
        tm = (t0 + t1) / 2
        pm = on_cut(tm)
        u_along = (pm[0] - s.a[0]) * ux + (pm[1] - s.a[1]) * uy
        return t0, t1, u_along

    for s in m.segments:
        iv = seg_cut_interval(s)
        if not iv:
            continue
        t0, t1, u = iv
        h0, h1 = sorted((v.h(on_cut(t0)), v.h(on_cut(t1))))
        lv = next((l for l in m.levels if l.id == s.level), None)
        z0 = lv.elevation if lv else s.base_z
        zb = zg if (s.exterior and s.base_z <= zg + 1500) else s.base_z
        zt = E.wall_top(s, on_cut((t0 + t1) / 2))
        bands = [(zb, zt)]
        for o in m.openings:
            if o.wall == s.id and o.at <= u <= o.at + o.width:
                lo, hi = z0 + o.sill, z0 + o.sill + o.height
                nb = []
                for p_, q_ in bands:
                    if lo > p_:
                        nb.append((p_, min(q_, lo)))
                    if hi < q_:
                        nb.append((max(p_, hi), q_))
                bands = nb
                if o.kind == 'window':      # 剖到的窗：两条玻璃线
                    sh.line((h0 + (h1 - h0) * 0.4, lo), (h0 + (h1 - h0) * 0.4, hi), 'A-ELEV-FINE')
                    sh.line((h0 + (h1 - h0) * 0.6, lo), (h0 + (h1 - h0) * 0.6, hi), 'A-ELEV-FINE')
        if s.kind == 'curtain':           # 幕墙：画框线，不涂黑
            sh.poly([(h0, zb), (h1, zb), (h1, zt), (h0, zt)], 'A-ELEV', closed=True)
            sh.line(((h0 + h1) / 2, zb), ((h0 + h1) / 2, zt), 'A-ELEV-FINE')
            continue
        for p_, q_ in bands:
            if q_ - p_ > 5:
                cut([(h0, p_), (h1, p_), (h1, q_), (h0, q_)])
    for c in m.columns or []:
        x, y = c['at']
        dd = v.d((x, y))
        r = (c.get('dia') or max(c['size'])) / 2
        if abs(dd) <= r:
            h0, h1 = E.column_rect(c)
            cut([(h0, c['base_z']), (h1, c['base_z']), (h1, c['base_z'] + c['height']), (h0, c['base_z'] + c['height'])])
    # 楼板
    for sl in m.slabs:
        poly = sl['polygon']
        if not poly:
            continue
        ts = []
        n = len(poly)
        for i in range(n):
            p, q = poly[i], poly[(i + 1) % n]
            e = (q[0] - p[0], q[1] - p[1])
            den = cu[0] * e[1] - cu[1] * e[0]
            if abs(den) < 1e-9:
                continue
            w = (p[0] - a[0], p[1] - a[1])
            t = (w[0] * e[1] - w[1] * e[0]) / den
            u = (w[0] * cu[1] - w[1] * cu[0]) / den
            if 0 <= u <= 1:
                ts.append(t)
        ts.sort()
        for t0, t1 in zip(ts[0::2], ts[1::2]):
            h0, h1 = sorted((v.h(on_cut(t0)), v.h(on_cut(t1))))
            cut([(h0, sl['top_z'] - sl['thickness']), (h1, sl['top_z'] - sl['thickness']), (h1, sl['top_z']), (h0, sl['top_z'])])
    # 屋面
    if m.roof and roof_z:
        fp = m.roof['footprint']
        t_ = m.roof['thickness']
        run = []
        k = max(2, int(L / 100))
        def flush():
            if len(run) >= 2:
                top = [(h, z + t_) for h, z in run]
                bot = [(h, z) for h, z in reversed(run)]
                pts = _simplify(top + bot)
                if v.h(on_cut(L)) < v.h(on_cut(0)):
                    pts = list(reversed(pts))
                cut(pts)
        for i in range(k + 1):
            p = on_cut(L * i / k)
            if _pip(p, fp):
                run.append((v.h(p), roof_z(*p)))
            else:
                flush()
                run = []
        flush()

    # ---- 3. 地坪、标高、尺寸、轴号、图名
    allp = E.polys + cut_polys
    if not allp:
        return {'skipped': f'剖面 {sec.get("id")}：剖切线没有穿过建筑'}
    hs = [p[0] for poly in allp for p in poly]
    h0, h1 = min(hs), max(hs)
    E.ground(h0, h1)
    zs = _zs_common(m, roof_z, h0, h1, v)
    E.level_marks(h1, zs)
    E.vdims(h0, [z for z, _ in zs])
    gx, gy = _grid(m, m.levels[0].id)
    marks = []
    for i, x in enumerate(gx):
        if abs(cu[0]) > 1e-6:
            t = (x - a[0]) / cu[0]
            if 0 <= t <= L:
                marks.append((v.h(on_cut(t)), str(i + 1)))
    for lab, y in zip(_labels(len(gy)), gy):
        if abs(cu[1]) > 1e-6:
            t = (y - a[1]) / cu[1]
            if 0 <= t <= L:
                marks.append((v.h(on_cut(t)), lab))
    marks.sort()
    for hh, lab in marks:
        E.bubble(hh, lab)
    if len(marks) >= 2:                       # 轴线间尺寸
        base = zg - P(26)
        for (ha_, _), (hb_, _) in zip(marks, marks[1:]):
            sh.dim((ha_, zg), (hb_, zg), (ha_, base), angle=0)
    sid = sec.get('id', '1')
    name = f'{sid}-{sid} 剖面图'
    E.title((h0 + h1) / 2, zg - P(40), name)
    res = _finish(sh, out_dir, f'剖面_{sid}-{sid}', name, sh.style)
    res['name'] = name
    return res


# =========================================================================== all
def write_all(m, out_dir, sides=('S', 'N', 'E', 'W')):
    out_dir = Path(out_dir)
    res = {}
    for sd in sides:
        try:
            res[SIDES[sd][1]] = draw_elevation(m, sd, out_dir)
        except Exception as exc:       # noqa: BLE001
            res[SIDES[sd][1]] = {'error': f'{type(exc).__name__}: {exc}'}
    for sec in m.sections or []:
        try:
            r = draw_section(m, sec, out_dir)
            res[r.get('name', f'剖面 {sec.get("id")}')] = r
        except Exception as exc:       # noqa: BLE001
            res[f'剖面 {sec.get("id")}'] = {'error': f'{type(exc).__name__}: {exc}'}
    return res
