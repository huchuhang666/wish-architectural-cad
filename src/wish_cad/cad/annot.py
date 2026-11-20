"""标注：定位轴线与轴号、三道尺寸、内部尺寸、弧墙标注、房间名称面积、标高、门窗编号、门窗表、
剖切符号、指北针、图名比例与比例尺（GB/T 50001 / GB/T 50104 平面图惯例）。

    draw(sh, m, level_id) -> dict

原则
- 所有标注只依据 m 的几何数据定位（墙、门窗、柱、房间），不依赖 DXF 里别人画了什么；
  DXF 里已有的实体（家具、台阶、坡道、地坪线等）只拿来做“避让障碍”，所以别的模块改了图形，标注会自动让开。
- 文字、符号先在候选位置里找不压线、不重叠的位置，再画。
"""
from __future__ import annotations

import math
import string

import numpy as np

from . import standard as S

EPS = 1.0


# =========================================================================== small geometry helpers

_FONT = None


def tw(s, h):
    """文字宽度（模型单位）：用 ezdxf 按 SimHei 实测；ezdxf 的字高是大写字母高，汉字约 1.48h，数字约 0.74h。"""
    global _FONT
    if _FONT is None:
        try:
            from ezdxf.fonts import fonts
            _FONT = fonts.make_font(S.FONT, 1.0)
        except Exception:
            _FONT = False
    w = 0.0
    for ch in str(s):
        if ch == ' ':
            w += 0.5
        elif _FONT:
            w += _FONT.text_width(ch) or 0.74
        else:
            w += 1.48 if ord(ch) >= 0x2E80 or ch in '×' else 0.74
    return w * h


def pip(pt, poly):
    """点是否在多边形内（射线法）。"""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y):
            xc = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xc:
                inside = not inside
        j = i
    return inside


def pip_many(P, poly):
    """向量化：P (N,2) 是否在 poly 内。"""
    x, y = P[:, 0], P[:, 1]
    inside = np.zeros(len(P), bool)
    n = len(poly)
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[i - 1][0], poly[i - 1][1]
        cond = (yi > y) != (yj > y)
        with np.errstate(divide='ignore', invalid='ignore'):
            xc = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        inside ^= cond & (x < xc)
    return inside


def _area(poly):
    return sum(poly[i][0] * poly[i - 1][1] - poly[i - 1][0] * poly[i][1] for i in range(len(poly))) / 2.0


def poly_edges(poly, closed=True):
    pts = [tuple(p[:2]) for p in poly]
    e = [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)]
    if closed and len(pts) > 2:
        e.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]))
    return e


def rect_edges(x0, y0, x1, y1):
    return poly_edges([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def seg_rect_hits(segs, rects):
    """segs (M,4)，rects (N,4: x0,y0,x1,y1) → (N,M) bool，线段与矩形相交（Liang–Barsky）。"""
    if len(segs) == 0 or len(rects) == 0:
        return np.zeros((len(rects), len(segs)), bool)
    x0, y0 = segs[:, 0][None, :], segs[:, 1][None, :]
    dx, dy = (segs[:, 2] - segs[:, 0])[None, :], (segs[:, 3] - segs[:, 1])[None, :]
    rx0, ry0, rx1, ry1 = (rects[:, i][:, None] for i in range(4))
    t0 = np.zeros((len(rects), len(segs)))
    t1 = np.ones((len(rects), len(segs)))
    ok = np.ones((len(rects), len(segs)), bool)
    for p, q in ((-dx, x0 - rx0), (dx, rx1 - x0), (-dy, y0 - ry0), (dy, ry1 - y0)):
        p = np.broadcast_to(p, t0.shape)
        q = np.broadcast_to(q, t0.shape)
        zero = np.abs(p) < 1e-12
        ok &= ~(zero & (q < 0))
        with np.errstate(divide='ignore', invalid='ignore'):
            r = np.where(zero, 0.0, q / np.where(zero, 1.0, p))
        t0 = np.where(~zero & (p < 0), np.maximum(t0, r), t0)
        t1 = np.where(~zero & (p > 0), np.minimum(t1, r), t1)
    return ok & (t0 <= t1)


def pt_seg_dist(P, segs):
    """P (N,2)，segs (M,4) → 每个点到最近线段的距离 (N,)。"""
    if len(segs) == 0:
        return np.full(len(P), 1e9)
    ax, ay = segs[:, 0][None, :], segs[:, 1][None, :]
    bx, by = segs[:, 2][None, :], segs[:, 3][None, :]
    px, py = P[:, 0][:, None], P[:, 1][:, None]
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy + 1e-9
    t = np.clip(((px - ax) * vx + (py - ay) * vy) / L2, 0, 1)
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return np.sqrt(dx * dx + dy * dy).min(axis=1)


class Obs:
    """避让障碍：按类别存线段和实心矩形。"""

    def __init__(self):
        self.segs = {}
        self.rects = {}

    def seg(self, cat, a, b):
        self.segs.setdefault(cat, []).append((a[0], a[1], b[0], b[1]))

    def poly(self, cat, pts, closed=True):
        self.segs.setdefault(cat, []).extend(poly_edges(pts, closed))

    def rect(self, cat, x0, y0, x1, y1, solid=True):
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        self.segs.setdefault(cat, []).extend(rect_edges(x0, y0, x1, y1))
        if solid:
            self.rects.setdefault(cat, []).append((x0, y0, x1, y1))

    def arrays(self, cats):
        cats = self.segs.keys() if cats is None else cats
        s = [e for c in cats for e in self.segs.get(c, [])]
        r = [e for c in cats for e in self.rects.get(c, [])]
        return np.array(s, float).reshape(-1, 4), np.array(r, float).reshape(-1, 4)

    def hits(self, rects, cats=None, segs_extra=None):
        """每个候选矩形碰到几个障碍。"""
        rects = np.asarray(rects, float).reshape(-1, 4)
        S_, R_ = self.arrays(cats)
        if segs_extra is not None and len(segs_extra):
            S_ = np.vstack([S_, np.asarray(segs_extra, float).reshape(-1, 4)])
        n = seg_rect_hits(S_, rects).sum(axis=1)
        if len(R_):
            ov = ((rects[:, 0][:, None] < R_[:, 2][None, :]) & (rects[:, 2][:, None] > R_[:, 0][None, :]) &
                  (rects[:, 1][:, None] < R_[:, 3][None, :]) & (rects[:, 3][:, None] > R_[:, 1][None, :]))
            n = n + ov.sum(axis=1)
        return n

    def clearance(self, P, cats=None):
        S_, R_ = self.arrays(cats)
        return pt_seg_dist(np.asarray(P, float).reshape(-1, 2), S_)


# =========================================================================== main

def draw(sh, m, level_id):
    return Annot(sh, m, level_id).run()


SOFT = ('furn', 'swing', 'grid', 'floor', 'site', 'roof')
LETTERS = [c for c in string.ascii_uppercase if c not in 'IOZ']

DOOR_TYPES = {'single': '平开门（单扇）', 'double': '平开门（双扇）', 'sliding': '推拉门', 'mother': '子母门', '': '门'}
WIN_TYPES = {'casement': '平开窗', 'fixed': '固定窗', 'sliding': '推拉窗', 'strip': '带形窗', '': '窗'}

# 已有 DXF 实体的避让类别
_LAYER_CAT = [
    ('A-WALL', 'wall'), ('A-GLAZ', 'wall'), ('A-COLS', 'wall'),
    ('A-DOOR', 'door'), ('A-FURN', 'furn'), ('A-FIXT', 'furn'),
    ('A-STRS', 'stair'), ('A-FLOR-OUTL', 'floor'), ('A-SITE', 'site'), ('A-ROOF', 'roof'),
]
_SKIP_LAYERS = ('PATT', 'FILL', 'A-GRID', 'A-DIMS', 'A-TTLB')


def _lv_text(z):
    v = z / 1000.0
    if abs(v) < 5e-4:
        return '±0.000'
    return f'{v:.3f}'


class Annot:
    def __init__(self, sh, m, level_id):
        self.sh, self.m, self.lv = sh, m, level_id
        self.k = sh.scale
        self.P = sh.p
        lvl = next((l for l in m.levels if l.id == level_id), None)
        self.z0 = lvl.elevation if lvl else 0.0
        self.zh = lvl.height if lvl else 3000.0
        self.idx = next((i for i, l in enumerate(m.levels) if l.id == level_id), 0)
        self.segs = [s for s in m.segments if s.level == level_id]
        self.sids = {s.id: s for s in self.segs}
        self.ext = [s for s in self.segs if s.exterior]
        self.openings = [o for o in m.openings if o.wall in self.sids]
        self.cols = [c for c in m.columns if c.get('level') == level_id]
        self.rooms = [r for r in m.rooms if r.get('level', level_id) == level_id]
        self.indoor = [r for r in self.rooms if not r.get('outdoor')]
        self.stairs = [s for s in m.stairs if s.get('level') == level_id]
        self.arcs = [a for a in m.arcs if getattr(a, 'level', level_id) == level_id]
        self.boxes = [b for b in m.boxes if self.z0 - 1000 <= (b.at[2] if len(b.at) > 2 else 0) + self.z0 * 0 <= self.z0 + self.zh]
        self.obs = Obs()
        self.closed_floor = []
        self.stats = {}
        self.items = []          # 画在图外的东西的外包框（纸面布置用）

    # ------------------------------------------------------------------ util
    def cnt(self, key, n=1):
        self.stats[key] = self.stats.get(key, 0) + n
        self.sh.count('annot.' + key, n)

    def text(self, s, at, kind, layer, align='MIDDLE_CENTER', rot=0.0, height=None, avoid=True, **kw):
        h = height or S.text_h(kind, self.k)
        t = self.sh.text(s, at, kind, layer, align=align, rot=rot, height=height, **kw)
        if avoid:
            w = tw(s, h)
            x, y = at
            if align.endswith('LEFT'):
                xc = x + w / 2
            elif align.endswith('RIGHT'):
                xc = x - w / 2
            else:
                xc = x
            if align.startswith('BOTTOM'):
                yc = y + h / 2
            elif align.startswith('TOP'):
                yc = y - h / 2
            else:
                yc = y
            if abs(rot % 180 - 90) < 1:
                self.obs.rect('anno', xc - h / 2, yc - w / 2, xc + h / 2, yc + w / 2)
            else:
                self.obs.rect('anno', xc - w / 2, yc - h / 2, xc + w / 2, yc + h / 2)
        return t

    # ------------------------------------------------------------------ setup
    def _collect_obstacles(self):
        o = self.obs
        for s in self.segs:
            o.poly('wall', self._seg_rect(s))
        for c in self.cols:
            x, y = c['at']
            if c.get('shape') == 'round':
                r = (c.get('dia') or 400) / 2
            else:
                r = max(c.get('size') or (400, 400)) / 2
            o.rect('wall', x - r, y - r, x + r, y + r)
        for b in self.boxes:
            fp = b.footprint()
            xs, ys = [p[0] for p in fp], [p[1] for p in fp]
            o.rect('furn', min(xs), min(ys), max(xs), max(ys))
        for st in self.stairs:
            o.poly('stair', self._stair_rect(st))
        # 门扇开启范围（粗略：门宽 × 门宽 的两侧）
        for op in self.openings:
            if op.kind != 'door':
                continue
            s = self.sids[op.wall]
            ux, uy = s.unit
            nx, ny = -uy, ux
            a = s.point_at(op.at)
            b = s.point_at(op.at + op.width)
            reach = op.width if op.type != 'double' else op.width / 2
            if op.type == 'sliding':
                reach = s.thickness
            for sg in (1, -1):
                pts = [a, b, (b[0] + nx * reach * sg, b[1] + ny * reach * sg), (a[0] + nx * reach * sg, a[1] + ny * reach * sg)]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                o.rect('swing', min(xs), min(ys), max(xs), max(ys), solid=False)
        # 已经画在 DXF 里的实体（body / blocks 的成果）
        try:
            from ezdxf import bbox as _bb
        except Exception:  # pragma: no cover
            _bb = None
        for e in self.sh.msp:
            lay = e.dxf.layer.upper()
            if any(k in lay for k in _SKIP_LAYERS):
                continue
            cat = next((c for p, c in _LAYER_CAT if lay.startswith(p)), 'text')
            t = e.dxftype()
            try:
                if t == 'LINE':
                    o.seg(cat, e.dxf.start, e.dxf.end)
                elif t == 'LWPOLYLINE':
                    pts = [(p[0], p[1]) for p in e.get_points('xy')]
                    if len(pts) > 1:
                        o.poly(cat, pts, closed=e.closed)
                    if e.closed and cat in ('floor', 'stair') and len(pts) > 2:
                        self.closed_floor.append(pts)
                elif t in ('ARC', 'CIRCLE'):
                    c, r = e.dxf.center, e.dxf.radius
                    if t == 'ARC':
                        a0, a1 = e.dxf.start_angle, e.dxf.end_angle
                        if a1 < a0:
                            a1 += 360
                    else:
                        a0, a1 = 0, 360
                    n = max(4, int((a1 - a0) / 15))
                    pts = [(c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                            c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
                    o.poly(cat, pts, closed=False)
                elif t in ('INSERT', 'TEXT', 'MTEXT') and _bb is not None:
                    b = _bb.extents([e], fast=True)
                    if b.has_data:
                        o.rect('furn' if t == 'INSERT' else 'text', b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
            except Exception:
                continue

    def _seg_rect(self, s):
        ux, uy = s.unit
        nx, ny = -uy, ux
        a, b = s.built_a, s.built_b
        h = s.thickness / 2
        return [(a[0] + nx * h, a[1] + ny * h), (b[0] + nx * h, b[1] + ny * h),
                (b[0] - nx * h, b[1] - ny * h), (a[0] - nx * h, a[1] - ny * h)]

    def _stair_rect(self, st):
        x, y = st['at']
        r = math.radians(st.get('dir', 0))
        ux, uy = math.cos(r), math.sin(r)
        nx, ny = -uy, ux
        w, L = st['width'] / 2, st.get('length', 1000)
        return [(x + nx * w, y + ny * w), (x + nx * w + ux * L, y + ny * w + uy * L),
                (x - nx * w + ux * L, y - ny * w + uy * L), (x - nx * w, y - ny * w)]

    def _in_building(self, pt):
        return any(pip(pt, r['polygon']) for r in self.indoor)

    # ------------------------------------------------------------------ grid
    def _auto_grid(self):
        xs, ys = [], []      # (value, weight)
        for s in self.ext:
            if s.arc:
                continue
            ux, uy = s.unit
            if abs(ux) < 0.02:
                xs.append((s.a[0], s.length))
                ys += [(s.a[1], 300), (s.b[1], 300)]
            elif abs(uy) < 0.02:
                ys.append((s.a[1], s.length))
                xs += [(s.a[0], 300), (s.b[0], 300)]
        for c in self.cols:
            xs.append((c['at'][0], 2000))
            ys.append((c['at'][1], 2000))
        return {'x': self._cluster(xs), 'y': self._cluster(ys)}

    @staticmethod
    def _cluster(vals, merge=600, dense=1500):
        vals = sorted(vals)
        groups = []
        for v, w in vals:
            if groups and v - groups[-1][-1][0] < merge:
                groups[-1].append((v, w))
            else:
                groups.append([(v, w)])
        cand = []
        for g in groups:
            wsum = {}
            for v, w in g:
                key = round(v)
                wsum[key] = wsum.get(key, 0) + w
            best = max(wsum.items(), key=lambda kv: kv[1])
            cand.append([float(best[0]), sum(w for _, w in g)])
        # 去掉过密的：相邻小于 dense 时去掉权重小的
        changed = True
        while changed and len(cand) > 2:
            changed = False
            for i in range(len(cand) - 1):
                if cand[i + 1][0] - cand[i][0] < dense:
                    j = i if cand[i][1] < cand[i + 1][1] else i + 1
                    if j in (0, len(cand) - 1) and len(cand) > 2:
                        j = i + 1 if j == 0 else i      # 尽量保住两端
                    cand.pop(j)
                    changed = True
                    break
        return [c[0] for c in cand]

    @staticmethod
    def _letters(n):
        out = []
        for i in range(n):
            if i < len(LETTERS):
                out.append(LETTERS[i])
            else:
                out.append(LETTERS[i // len(LETTERS) - 1] + LETTERS[i % len(LETTERS)])
        return out

    # ------------------------------------------------------------------ dims
    def _dimstyles(self):
        d = self.sh.doc
        if 'WISH-R' not in d.dimstyles:
            ds = d.dimstyles.duplicate_entry('WISH', 'WISH-R')
            ds.dxf.dimblk = ''
            ds.dxf.dimasz = self.P(2.2)
            ds.dxf.dimtad = 1
            ds.dxf.dimtix = 0
            ds.dxf.dimtofl = 1
            ds.dxf.dimtmove = 0

    def _dim(self, p1, p2, base, angle, loc=None):
        try:
            d = self.sh.msp.add_linear_dim(base=tuple(base), p1=tuple(p1), p2=tuple(p2), angle=angle,
                                           location=tuple(loc) if loc else None, dimstyle='WISH',
                                           dxfattribs={'layer': 'A-DIMS'})
            d.render()
        except Exception:
            return None
        self.cnt('dims')
        return d

    def chain(self, vals, orient, line, porig, side_txt=None):
        """一串连续尺寸。orient='h'：量 x，尺寸线 y=line；'v'：量 y，尺寸线 x=line。
        porig：每个点尺寸界线的起点（垂直方向坐标），可为数值或列表。"""
        vals = sorted(vals)
        v2 = []
        for v in vals:
            if not v2 or v - v2[-1] > EPS:
                v2.append(v)
        vals = v2
        if len(vals) < 2:
            return
        if not isinstance(porig, (list, tuple)):
            porig = [porig] * len(vals)
        else:
            porig = list(porig)
        P = self.P
        h = S.text_h('dim', self.k)
        gap = P(0.8)
        up = (0, 1) if orient == 'h' else (-1, 0)
        n = len(vals) - 1
        segs = [(vals[i], vals[i + 1]) for i in range(n)]
        txts = [str(int(round(b - a))) for a, b in segs]
        widths = [tw(t, h) for t in txts]
        occ = []
        centers = [None] * n
        raised = [False] * n
        for i, ((a, b), w) in enumerate(zip(segs, widths)):
            if b - a >= w + P(1.0):
                centers[i] = (a + b) / 2
                occ.append(((a + b) / 2 - w / 2 - P(0.4), (a + b) / 2 + w / 2 + P(0.4)))

        def free(c, w):
            return all(c + w / 2 + P(0.4) <= o0 or c - w / 2 - P(0.4) >= o1 for o0, o1 in occ)

        for i, ((a, b), w) in enumerate(zip(segs, widths)):
            if centers[i] is not None:
                continue
            right = b + P(0.6) + w / 2
            left = a - P(0.6) - w / 2
            order = [left, right] if i == 0 else [right, left] if i == n - 1 else [right, left]
            pick = next((c for c in order if free(c, w)), None)
            if pick is None:
                pick = (a + b) / 2
                raised[i] = True
            centers[i] = pick
            occ.append((pick - w / 2 - P(0.4), pick + w / 2 + P(0.4)))
        for i, (a, b) in enumerate(segs):
            fit = (b - a >= widths[i] + P(1.0))
            off = gap + h / 2 + (h + gap if raised[i] else 0)
            c = centers[i]
            if orient == 'h':
                p1, p2 = (a, porig[i]), (b, porig[i + 1])
                base = (a, line)
                loc = None if fit else (c, line + up[1] * off)
                self._dim(p1, p2, base, 0, loc)
                tr = (c - widths[i] / 2, line + up[1] * off - h / 2, c + widths[i] / 2, line + up[1] * off + h / 2)
            else:
                p1, p2 = (porig[i], a), (porig[i + 1], b)
                base = (line, a)
                loc = None if fit else (line + up[0] * off, c)
                self._dim(p1, p2, base, 90, loc)
                tr = (line + up[0] * off - h / 2, c - widths[i] / 2, line + up[0] * off + h / 2, c + widths[i] / 2)
            self.obs.rect('anno', *tr)
        # 尺寸线和界线作为障碍
        if orient == 'h':
            self.obs.seg('dimline', (vals[0], line), (vals[-1], line))
            for v, po in zip(vals, porig):
                self.obs.seg('dimline', (v, po), (v, line))
        else:
            self.obs.seg('dimline', (line, vals[0]), (line, vals[-1]))
            for v, po in zip(vals, porig):
                self.obs.seg('dimline', (po, v), (line, v))

    # ------------------------------------------------------------------ building box & sides
    def _faces(self):
        xs, ys = [], []
        for s in self.ext:
            for p in self._seg_rect(s):
                xs.append(p[0]); ys.append(p[1])
        for a in self.arcs:
            cx, cy, R, a0, sw = a.arc
            ro = R + a.thickness / 2
            for i in range(65):
                t = a0 + sw * i / 64
                xs.append(cx + ro * math.cos(t)); ys.append(cy + ro * math.sin(t))
        if not xs:
            for r in self.rooms:
                for p in r['polygon']:
                    xs.append(p[0]); ys.append(p[1])
        return min(xs), min(ys), max(xs), max(ys)

    def _side_d1(self, bx0, by0, bx1, by1):
        """第一道尺寸线离外墙外皮的距离（模型 mm），按每个方向上挑檐、散水、台阶等伸出的最大距离决定。"""
        P = self.P
        out = {'S': 0.0, 'N': 0.0, 'W': 0.0, 'E': 0.0}
        site = self.m.site or {}
        if site.get('enabled', True):
            ap = site.get('apron') or 0
            for k in out:
                out[k] = max(out[k], ap)
        roof = self.m.roof
        if roof and roof.get('footprint'):
            fp = roof['footprint']
            out['W'] = max(out['W'], bx0 - min(p[0] for p in fp))
            out['E'] = max(out['E'], max(p[0] for p in fp) - bx1)
            out['S'] = max(out['S'], by0 - min(p[1] for p in fp))
            out['N'] = max(out['N'], max(p[1] for p in fp) - by1)
        # 已画实体（台阶、平台、坡道、挑檐）在建筑投影范围内伸出的距离
        for cat in ('floor', 'stair', 'roof'):
            for x0, y0, x1, y1 in self.obs.segs.get(cat, []):
                for x, y in ((x0, y0), (x1, y1)):
                    if bx0 <= x <= bx1:
                        if y < by0:
                            out['S'] = max(out['S'], by0 - y)
                        if y > by1:
                            out['N'] = max(out['N'], y - by1)
                    if by0 <= y <= by1:
                        if x < bx0:
                            out['W'] = max(out['W'], bx0 - x)
                        if x > bx1:
                            out['E'] = max(out['E'], x - bx1)
        lo, hi = P(S.DIM_FIRST), P(S.DIM_FIRST + 22)
        return {k: min(hi, max(lo, v + P(4))) for k, v in out.items()}

    def _side_segments(self, side):
        out = []
        for s in self.ext:
            if s.arc:
                continue
            ux, uy = s.unit
            if side in 'SN' and abs(uy) > 0.02:
                continue
            if side in 'WE' and abs(ux) > 0.02:
                continue
            # 外侧法线
            for sg in (1, -1):
                nx, ny = -uy * sg, ux * sg
                mid = s.point_at(s.length / 2)
                probe = (mid[0] + nx * (s.thickness / 2 + 300), mid[1] + ny * (s.thickness / 2 + 300))
                if not self._in_building(probe):
                    break
            want = {'S': (0, -1), 'N': (0, 1), 'W': (-1, 0), 'E': (1, 0)}[side]
            if round(nx) == want[0] and round(ny) == want[1]:
                out.append(s)
        return out

    def _neighbor_straight_perp(self, s, pt):
        for o in self.ext:
            if o is s:
                continue
            if math.dist(o.a, pt) < EPS or math.dist(o.b, pt) < EPS:
                if o.arc:
                    return False
                return abs(o.unit[0] * s.unit[0] + o.unit[1] * s.unit[1]) < 0.05
        return False

    def exterior_dims(self, gx, gy):
        bx0, by0, bx1, by1 = self.box
        P = self.P
        ROW = P(S.DIM_ROW)
        d1 = self.d1
        rows = {}
        for side in 'SNWE':
            segs = self._side_segments(side)
            horiz = side in 'SN'
            face = {'S': by0, 'N': by1, 'W': bx0, 'E': bx1}[side]
            sg = -1 if side in 'SW' else 1
            r1 = face + sg * d1[side]
            r2 = r1 + sg * ROW
            r3 = r2 + sg * ROW
            axes = gx if horiz else gy
            lo_ext, hi_ext = (bx0, bx1) if horiz else (by0, by1)
            arcs_here = [a for a in self.arcs if self._arc_side(a) == side]
            if not segs and not axes:
                continue
            # ---- 第一道：门窗洞口、墙垛
            if segs:
                pts = {}       # value -> perpendicular origin
                ops_span = []
                for s in segs:
                    t = s.thickness / 2
                    fc = (s.a[1] if horiz else s.a[0]) + sg * t
                    ends = []
                    for end, built in ((s.a, s.built_a), (s.b, s.built_b)):
                        q = built if self._neighbor_straight_perp(s, end) else end
                        ends.append(q[0] if horiz else q[1])
                    for v in ends:
                        pts[round(v, 1)] = fc
                    for op in self.openings:
                        if op.wall != s.id:
                            continue
                        e0 = s.point_at(op.at)
                        e1 = s.point_at(op.at + op.width)
                        v0, v1 = (e0[0], e1[0]) if horiz else (e0[1], e1[1])
                        pts[round(v0, 1)] = fc
                        pts[round(v1, 1)] = fc
                        ops_span.append((min(v0, v1), max(v0, v1)))
                    lo, hi = min(ends), max(ends)
                    for a in axes:
                        if lo - EPS <= a <= hi + EPS and not any(o0 + EPS < a < o1 - EPS for o0, o1 in ops_span):
                            pts.setdefault(round(a, 1), fc)
                # 建筑两端（如弧墙外凸）
                for v in (lo_ext, hi_ext):
                    if all(abs(v - k) > EPS for k in pts):
                        pts[round(v, 1)] = face
                vals = sorted(pts)
                self.chain(vals, 'h' if horiz else 'v', r1, [pts[v] for v in vals])
                p_next = r1
            else:
                p_next = r1 if arcs_here else face
            # ---- 第二道：轴线
            if len(axes) >= 2:
                self.chain(axes, 'h' if horiz else 'v', r2, p_next)
            # ---- 第三道：总尺寸
            self.chain([lo_ext, hi_ext], 'h' if horiz else 'v', r3, r2 if len(axes) >= 2 else p_next)
            rows[side] = (r1, r2, r3)
        self.rows = rows

    # ------------------------------------------------------------------ arcs
    def _arc_side(self, a):
        cx, cy, R, a0, sw = a.arc
        mid = a0 + sw / 2
        dx, dy = math.cos(mid), math.sin(mid)
        if abs(dx) >= abs(dy):
            return 'E' if dx > 0 else 'W'
        return 'N' if dy > 0 else 'S'

    def arc_dims(self):
        P = self.P
        for a in self.arcs:
            cx, cy, R, a0, sw = a.arc
            t = a.thickness
            side = self._arc_side(a)
            ro = R + t / 2
            rd = ro + self.d1[side]
            a1 = a0 + sw
            # 弧长尺寸：同心圆弧 + 径向尺寸界线 + 箭头
            self.sh.arc((cx, cy), rd, math.degrees(min(a0, a1)), math.degrees(max(a0, a1)), 'A-DIMS')
            for ang in (a0, a1):
                c, s_ = math.cos(ang), math.sin(ang)
                self.sh.line((cx + c * (ro + P(2)), cy + s_ * (ro + P(2))), (cx + c * (rd + P(2)), cy + s_ * (rd + P(2))), 'A-DIMS')
                self.obs.seg('dimline', (cx + c * ro, cy + s_ * ro), (cx + c * rd, cy + s_ * rd))
                # 建筑斜短线
                tx, ty = -s_, c
                k45 = P(1.2) / 2
                px, py = cx + c * rd, cy + s_ * rd
                d = ((tx + c) / math.sqrt(2), (ty + s_) / math.sqrt(2))
                self.sh.line((px - d[0] * k45 * 2, py - d[1] * k45 * 2), (px + d[0] * k45 * 2, py + d[1] * k45 * 2), 'A-DIMS', lineweight=35)
            n = 48
            pts = [(cx + rd * math.cos(a0 + sw * i / n), cy + rd * math.sin(a0 + sw * i / n)) for i in range(n + 1)]
            self.obs.poly('dimline', pts, closed=False)
            L = R * abs(sw)
            txt = f'弧长 {L:.0f}'
            h = S.text_h('dim', self.k)
            mid = a0 + sw / 2
            # 文字放在弧线外侧（与直线尺寸“文字在上”一致），沿切线方向
            rt = rd + P(0.8) + h / 2
            rot = math.degrees(mid) - 90
            if 90 < (rot % 360) < 270:
                rot += 180
            self.text(txt, (cx + rt * math.cos(mid), cy + rt * math.sin(mid)), 'dim', 'A-DIMS', rot=rot)
            self.cnt('arc_dims')
            # 半径标注（到墙中心线）
            self._radius(a)

    def _radius(self, a):
        P = self.P
        cx, cy, R, a0, sw = a.arc
        # 找一个不穿家具的角度
        best, best_n = None, None
        h = S.text_h('dim', self.k)
        for i in range(1, 12):
            ang = a0 + sw * i / 12
            c, s_ = math.cos(ang), math.sin(ang)
            seg = np.array([[cx + c * P(3), cy + s_ * P(3), cx + c * (R - P(2)), cy + s_ * (R - P(2))]])
            # 用线段两侧的细矩形近似
            n_hit = 0
            for f in np.linspace(0.05, 0.95, 15):
                x, y = cx + c * R * f, cy + s_ * R * f
                n_hit += int(self.obs.hits([(x - P(1.2), y - P(1.2), x + P(1.2), y + P(1.2))], cats=('furn', 'stair', 'anno', 'wall', 'door'))[0] > 0)
            score = n_hit * 10 + abs(i - 4)
            if best_n is None or score < best_n:
                best, best_n = ang, score
        ang = best
        c, s_ = math.cos(ang), math.sin(ang)
        mp = (cx + c * R, cy + s_ * R)
        # 中心线 → 弧墙：直线 + 实心箭头 + 文字
        self.sh.line((cx, cy), mp, 'A-DIMS')
        al, aw = P(2.5), P(0.6)
        bx, by = mp[0] - c * al, mp[1] - s_ * al
        self.sh.solid([mp, (bx - s_ * aw, by + c * aw), (bx + s_ * aw, by - c * aw)], 'A-DIMS', color=256)
        txt = f'R={R:.0f}'
        rot = math.degrees(ang)
        if 90 < (rot % 360) < 270:
            rot += 180
        tm = (cx + c * R * 0.62 - s_ * (P(0.8) + h / 2), cy + s_ * R * 0.62 + c * (P(0.8) + h / 2))
        self.text(txt, tm, 'dim', 'A-DIMS', rot=rot)
        self.obs.seg('dimline', (cx, cy), mp)
        # 圆心十字 + “弧墙圆心” + 圆心定位尺寸
        k = P(1.5)
        self.sh.line((cx - k, cy), (cx + k, cy), 'A-DIMS')
        self.sh.line((cx, cy - k), (cx, cy + k), 'A-DIMS')
        self.sh.circle((cx, cy), P(0.6), 'A-DIMS')
        self.obs.rect('anno', cx - k, cy - k, cx + k, cy + k)
        self.cnt('radius_dims')
        self.center_marks.append((cx, cy))

    def arc_center_dims(self, gx, gy):
        """弧心到最近轴线的定位尺寸 + 文字“弧墙圆心”。"""
        P = self.P
        for cx, cy in self.center_marks:
            if gx:
                ax = min(gx, key=lambda v: abs(v - cx))
                if abs(ax - cx) > EPS:
                    self.chain([ax, cx], 'h', cy, cy)
            if gy:
                ay = min(gy, key=lambda v: abs(v - cy))
                if abs(ay - cy) > EPS:
                    self.chain([ay, cy], 'v', cx, cx)
            h = S.text_h('tag', self.k)
            s = '弧墙圆心'
            w = tw(s, h)
            cands = []
            for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + dx * (P(1.5) + w / 2)
                y = cy + dy * (P(1.5) + h / 2)
                cands.append((x, y))
            rects = [(x - w / 2, y - h / 2, x + w / 2, y + h / 2) for x, y in cands]
            n = self.obs.hits(rects, cats=('furn', 'stair', 'anno', 'wall', 'door', 'dimline', 'text', 'floor'))
            i = int(np.argmin(n))
            self.text(s, cands[i], 'tag', 'A-ANNO-TEXT')

    # ------------------------------------------------------------------ grid draw
    def grid_draw(self, gx, gy):
        P = self.P
        bx0, by0, bx1, by1 = self.box
        rows = self.rows
        rb = P(S.GRID_BUBBLE) / 2
        ext = P(4)

        def outer(side):
            if side in rows:
                return rows[side][2]
            face = {'S': by0, 'N': by1, 'W': bx0, 'E': bx1}[side]
            return face + (-1 if side in 'SW' else 1) * self.d1[side]

        hdr = self.sh.doc.header.get('$LTSCALE', 1.0) or 1.0
        lts = (P(14) / 5.08) / hdr          # 点划线一个周期约 14 mm（纸面）
        gl = []
        names_x = [str(i + 1) for i in range(len(gx))]
        names_y = self._letters(len(gy))
        self.grid_names = (dict(zip(gx, names_x)), dict(zip(gy, names_y)))
        for v, nm in zip(gx, names_x):
            yb = outer('S') - ext
            yt = outer('N') + ext
            self.sh.line((v, yb), (v, yt), 'A-GRID', ltscale=lts)
            self.obs.seg('grid', (v, yb), (v, yt))
            for y, sg in ((yb, -1), (yt, 1)):
                c = (v, y + sg * rb)
                self.sh.circle(c, rb, 'A-GRID-IDEN')
                self.text(nm, c, 'grid', 'A-GRID-IDEN')
                self.items.append((c[0] - rb, c[1] - rb, c[0] + rb, c[1] + rb))
            self.cnt('grid_x')
        for v, nm in zip(gy, names_y):
            xl = outer('W') - ext
            xr = outer('E') + ext
            self.sh.line((xl, v), (xr, v), 'A-GRID', ltscale=lts)
            self.obs.seg('grid', (xl, v), (xr, v))
            for x, sg in ((xl, -1), (xr, 1)):
                c = (x + sg * rb, v)
                self.sh.circle(c, rb, 'A-GRID-IDEN')
                self.text(nm, c, 'grid', 'A-GRID-IDEN')
                self.items.append((c[0] - rb, c[1] - rb, c[0] + rb, c[1] + rb))
            self.cnt('grid_y')

    # ------------------------------------------------------------------ interior dims
    def interior_dims(self, gx, gy):
        P = self.P
        on_axis_x = lambda x: any(abs(x - a) < 30 for a in gx)
        on_axis_y = lambda y: any(abs(y - a) < 30 for a in gy)
        done_x, done_y = set(), set()
        host = max(self.indoor, key=lambda r: r['area_m2'], default=None)
        # 1) 与主地面标高不同的房间（下沉区等）：外侧边到最近轴线
        for r in self.indoor:
            if r is host or abs((r.get('elevation') or 0)) < 100:      # 只给下沉/抬高区（卫生间 -0.020 之类不算）
                continue
            poly = r['polygon']
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            if len(poly) > 6:
                continue
            # 横向：放在房间下边外侧（若下边不是外墙）
            for (yy, sg) in ((y0, -1), (y1, 1)):
                if abs(yy - self.box[1]) < 400 or abs(yy - self.box[3]) < 400:
                    continue
                vals = [x0, x1] + [a for a in gx if x0 < a < x1]
                left = [a for a in gx if a <= x0 + EPS]
                if left and abs(left[-1] - x0) > EPS:
                    vals.append(left[-1])
                line = yy + sg * P(5)
                self.chain(sorted(set(vals)), 'h', line, line)
                done_x.update([x0, x1])
                break
            for (xx, sg) in ((x0, -1), (x1, 1)):
                if abs(xx - self.box[0]) < 400 or abs(xx - self.box[2]) < 400:
                    continue
                vals = [y0, y1] + [a for a in gy if y0 < a < y1]
                below = [a for a in gy if a <= y0 + EPS]
                if below and abs(below[-1] - y0) > EPS:
                    vals.append(below[-1])
                line = xx + sg * P(5)
                self.chain(sorted(set(vals)), 'v', line, line)
                done_y.update([y0, y1])
                break
        # 2) 不在轴线上的内墙：一串尺寸，找不压家具的位置
        inner = [s for s in self.segs if not s.exterior and not s.arc and s.kind == 'wall' and s.length > 1500]
        vx = [s for s in inner if abs(s.unit[0]) < 0.02 and not on_axis_x(s.a[0]) and all(abs(s.a[0] - d) > 30 for d in done_x)]
        hy = [s for s in inner if abs(s.unit[1]) < 0.02 and not on_axis_y(s.a[1]) and all(abs(s.a[1] - d) > 30 for d in done_y)]
        for group, orient in ((vx, 'h'), (hy, 'v')):
            for cl in self._clusters(group, orient):
                self._inner_chain(cl, orient, gx, gy)

    def _clusters(self, walls, orient):
        """把相邻（另一方向范围重叠）的内墙归成一串。"""
        out = []
        for s in sorted(walls, key=lambda s: s.a[0] if orient == 'h' else s.a[1]):
            lo, hi = (min(s.a[1], s.b[1]), max(s.a[1], s.b[1])) if orient == 'h' else (min(s.a[0], s.b[0]), max(s.a[0], s.b[0]))
            for cl in out:
                if lo < cl['hi'] and hi > cl['lo']:
                    cl['walls'].append(s)
                    cl['lo'], cl['hi'] = min(cl['lo'], lo), max(cl['hi'], hi)
                    break
            else:
                out.append({'walls': [s], 'lo': lo, 'hi': hi})
        return out

    def _inner_chain(self, cl, orient, gx, gy):
        P = self.P
        walls = cl['walls']
        if orient == 'h':
            vals = sorted({round(s.a[0], 1) for s in walls})
            axes = gx
        else:
            vals = sorted({round(s.a[1], 1) for s in walls})
            axes = gy
        # 两侧参照：最近的轴线
        left = [a for a in axes if a < vals[0] - EPS]
        right = [a for a in axes if a > vals[-1] + EPS]
        if left:
            vals = [left[-1]] + vals
        if right and not left:     # 只取一侧参照轴线，另一侧可由轴线尺寸推出，免得尺寸穿过小房间
            vals = vals + [right[0]]
        if len(vals) < 2:
            return
        h = S.text_h('dim', self.k)
        lo, hi = cl['lo'], cl['hi']
        cands = []
        step = P(1)
        c = lo - P(6)
        while c <= hi + P(6):
            cands.append(c)
            c += step
        best, bscore = None, None
        for c in cands:
            # 每道墙要么被尺寸线穿过，要么离尺寸线不远（墙端外 6 mm 内）
            ok = True
            for s in walls:
                wlo, whi = (min(s.a[1], s.b[1]), max(s.a[1], s.b[1])) if orient == 'h' else (min(s.a[0], s.b[0]), max(s.a[0], s.b[0]))
                if not (wlo - P(6) <= c <= whi + P(6)):
                    ok = False
            if not ok:
                continue
            # 尺寸线不能贴着平行的墙走
            for s in self.segs:
                if orient == 'h' and abs(s.unit[1]) < 0.02:
                    pos, lo2, hi2 = s.a[1], min(s.a[0], s.b[0]), max(s.a[0], s.b[0])
                elif orient == 'v' and abs(s.unit[0]) < 0.02:
                    pos, lo2, hi2 = s.a[0], min(s.a[1], s.b[1]), max(s.a[1], s.b[1])
                else:
                    continue
                if abs(pos - c) < s.thickness / 2 + P(2.5) + h and lo2 < vals[-1] and hi2 > vals[0]:
                    ok = False
                    break
            if not ok:
                continue
            if orient == 'h':
                rects = [(vals[0], c - P(0.3), vals[-1], c + h + P(1.2))]
            else:
                rects = [(c - h - P(1.2), vals[0], c + P(0.3), vals[-1])]
            n = int(self.obs.hits(rects, cats=('furn', 'stair', 'anno', 'dimline', 'text', 'door'))[0])
            # 尽量放在墙端外侧的开敞处，其次房间内
            inside = any(self._in_room_small((vals[0] + vals[-1]) / 2 if orient == 'h' else c,
                                             c if orient == 'h' else (vals[0] + vals[-1]) / 2))
            score = n * 100 + (5 if inside else 0) + abs(c - lo) / P(10) * 0.1
            if bscore is None or score < bscore:
                best, bscore = c, score
        if best is None:
            return
        self.chain(vals, orient, best, best)

    def _in_room_small(self, x, y):
        host = max(self.indoor, key=lambda r: r['area_m2'], default=None)
        return [r for r in self.indoor if r is not host and pip((x, y), r['polygon'])]

    # ------------------------------------------------------------------ door / window tags
    def opening_tags(self):
        P = self.P
        h = S.text_h('tag', self.k)
        for op in self.openings:
            code = op.code or op.id
            s = self.sids[op.wall]
            ux, uy = s.unit
            nx, ny = -uy, ux
            c = s.point_at(op.at + op.width / 2)
            w = tw(code, h)
            # 外墙：先外侧；内墙：两侧都试
            sides = [1, -1]
            probe = (c[0] + nx * (s.thickness / 2 + 300), c[1] + ny * (s.thickness / 2 + 300))
            if s.exterior and self._in_building(probe):
                sides = [-1, 1]
            cands = []
            for pri, sg in enumerate(sides):
                for shift in (0, -0.3, 0.3, -0.6, 0.6):
                    for extra in (0, P(1.5), P(3)):
                        # 文字矩形在法线方向的半宽
                        half_n = abs(nx) * w / 2 + abs(ny) * h / 2
                        d = s.thickness / 2 + P(1.0) + half_n + extra
                        x = c[0] + nx * sg * d + ux * shift * op.width
                        y = c[1] + ny * sg * d + uy * shift * op.width
                        cands.append(((x, y), pri * 3 + abs(shift) * 4 + extra / P(1.5)))
            rects = [(x - w / 2 - P(0.3), y - h / 2 - P(0.3), x + w / 2 + P(0.3), y + h / 2 + P(0.3)) for (x, y), _ in cands]
            n = self.obs.hits(rects, cats=('wall', 'door', 'furn', 'stair', 'anno', 'dimline', 'text', 'floor', 'site', 'roof'))
            score = n * 100 + np.array([p for _, p in cands])
            i = int(np.argmin(score))
            self.text(code, cands[i][0], 'tag', 'A-DOOR-IDEN')
            self.cnt('opening_tags')

    # ------------------------------------------------------------------ placement search
    def _place(self, w, h, polys_in, holes, pref, cats, step=None, maxdist=None, region_box=None, outside=None):
        """在 polys_in（任一多边形内）且不在 holes 内的位置找 w×h 的空地；返回中心点。"""
        P = self.P
        if region_box is None:
            xs = [p[0] for poly in polys_in for p in poly]
            ys = [p[1] for poly in polys_in for p in poly]
            region_box = (min(xs), min(ys), max(xs), max(ys))
        x0, y0, x1, y1 = region_box
        step = step or max(P(1.0), min(x1 - x0, y1 - y0) / 40)
        gxs = np.arange(x0 + w / 2, x1 - w / 2 + 1, step)
        gys = np.arange(y0 + h / 2, y1 - h / 2 + 1, step)
        if len(gxs) == 0 or len(gys) == 0:
            return None
        C = np.array([(x, y) for y in gys for x in gxs], float)
        if maxdist is not None:
            C = C[np.hypot(C[:, 0] - pref[0], C[:, 1] - pref[1]) <= maxdist]
        if len(C) == 0:
            return None
        hw, hh = w / 2, h / 2
        corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (0, 0)]
        ok = np.ones(len(C), bool)
        if polys_in:
            for dx, dy in corners:
                Q = C + np.array([dx, dy])
                inn = np.zeros(len(C), bool)
                for poly in polys_in:
                    inn |= pip_many(Q, poly)
                ok &= inn
        for poly in holes:
            for dx, dy in corners:
                ok &= ~pip_many(C + np.array([dx, dy]), poly)
        if outside is not None:
            for dx, dy in corners:
                ok &= ~outside(C + np.array([dx, dy]))
        C = C[ok]
        if len(C) == 0:
            return None
        R = np.column_stack([C[:, 0] - hw, C[:, 1] - hh, C[:, 0] + hw, C[:, 1] + hh])
        edges = [e for poly in polys_in + list(holes) for e in poly_edges(poly)]
        soft = [c for c in cats if c in SOFT]
        hard = [c for c in cats if c not in SOFT]
        n = self.obs.hits(R, cats=hard, segs_extra=edges).astype(float)
        ns = self.obs.hits(R, cats=soft).astype(float) if soft else np.zeros(len(R))
        clr = np.minimum(self.obs.clearance(C, cats=cats), P(12))
        if edges:
            clr = np.minimum(clr, pt_seg_dist(C, np.array(edges, float)))
        dist = np.hypot(C[:, 0] - pref[0], C[:, 1] - pref[1])
        # 硬障碍（墙、文字、尺寸）绝不压；软障碍（家具、轴线、门扇范围）尽量不压
        score = n * 1e6 + ns * P(40) - clr + 0.25 * dist
        n = n + ns * 1e-3
        i = int(np.argmin(score))
        return (float(C[i, 0]), float(C[i, 1])), float(n[i])

    # ------------------------------------------------------------------ rooms & levels
    def room_labels(self):
        P = self.P
        hn, ha = S.text_h('room', self.k), S.text_h('area', self.k)
        gap = P(1.2) + 0.35 * hn          # 汉字字身比字高（大写字母高）深，留出下伸
        host = max(self.indoor, key=lambda r: r['area_m2'], default=None)
        cats = ('wall', 'furn', 'stair', 'anno', 'dimline', 'text', 'door', 'swing', 'floor', 'grid')
        order = sorted(self.rooms, key=lambda r: r['area_m2'])
        self.label_at = {}
        hn0, ha0, gap0 = hn, ha, gap
        for r in order:
            hn, ha, gap = hn0, ha0, gap0
            poly = [tuple(p[:2]) for p in r['polygon']]
            holes = [[tuple(p[:2]) for p in o['polygon']] for o in self.rooms
                     if o is not r and o['area_m2'] < r['area_m2'] and pip(self._centroid(o['polygon']), poly)]
            name = r['name']
            area = f"{r['area_m2']:.2f}m"         # 右上角另画上标 2
            w = max(tw(name, hn), tw(area, ha) + tw('2', ha * 0.6)) + P(2.5)
            h = hn + gap + ha + P(1)
            pref = self._pole(poly, holes)
            res = self._place(w, h, [poly], holes, pref, cats)
            if res is None:
                continue
            (x, y), n = res
            if n > 0:      # 压到家具/轴线：小房间改用小一号字再试
                f = 0.8
                res2 = self._place(w * f, h * f, [poly], holes, pref, cats)
                if res2 is not None and res2[1] < n:
                    (x, y), n = res2
                    hn, ha, gap, w, h = hn * f, ha * f, gap * f, w * f, h * f
            top = y + h / 2 - P(0.5)
            self.text(name, (x, top - hn / 2), 'room', 'A-AREA-IDEN', height=hn)
            wa = tw(area, ha)
            ws = tw('2', ha * 0.6)
            xa = x - (wa + ws) / 2
            ya = top - hn - gap - ha
            self.text(area, (xa, ya), 'area', 'A-AREA-IDEN', align='BOTTOM_LEFT', height=ha)
            self.text('2', (xa + wa + P(0.1), ya + ha * 0.55), 'area', 'A-AREA-IDEN', align='BOTTOM_LEFT',
                      height=ha * 0.6, avoid=False)
            self.label_at[id(r)] = ((x, y), w, h, poly, holes)
            self.cnt('room_labels')
        # 标高：主地面 ±0.000、标高不同的房间
        for r in order:
            z = r.get('elevation') or 0.0
            if r is not host and abs(z) < 1:
                continue
            if id(r) not in self.label_at:
                continue
            (x, y), w, h, poly, holes = self.label_at[id(r)]
            self.level_mark_near(_lv_text(z + self.z0), (x, y - h / 2 - P(4)), [poly], holes, cats)

    def mask_patterns(self):
        """地砖/铺装分格线在文字、标高符号处断开（相当于给字垫白底）。"""
        pad = self.P(0.6)
        rects = [(x0 - pad, y0 - pad, x1 + pad, y1 + pad) for (x0, y0, x1, y1) in self.obs.rects.get('anno', [])]
        if not rects:
            return
        msp = self.sh.msp
        n = 0
        for e in list(msp.query('LINE[layer=="A-FLOR-PATT" | layer=="A-SITE-PATT"]')):
            a, b = e.dxf.start, e.dxf.end
            ax_, ay_, bx_, by_ = a.x, a.y, b.x, b.y
            dx, dy = bx_ - ax_, by_ - ay_
            cuts = []
            for (x0, y0, x1, y1) in rects:
                t0, t1 = 0.0, 1.0
                ok = True
                for p_, q_ in ((-dx, ax_ - x0), (dx, x1 - ax_), (-dy, ay_ - y0), (dy, y1 - ay_)):
                    if abs(p_) < 1e-12:
                        if q_ < 0:
                            ok = False
                            break
                    else:
                        r = q_ / p_
                        if p_ < 0:
                            t0 = max(t0, r)
                        else:
                            t1 = min(t1, r)
                if ok and t1 - t0 > 1e-6:
                    cuts.append((t0, t1))
            if not cuts:
                continue
            cuts.sort()
            keep, t = [], 0.0
            for c0, c1 in cuts:
                if c0 > t:
                    keep.append((t, c0))
                t = max(t, c1)
            if t < 1:
                keep.append((t, 1.0))
            attrs = e.dxfattribs()
            for k0, k1 in keep:
                if (k1 - k0) * (dx * dx + dy * dy) ** 0.5 < 1:
                    continue
                d = dict(attrs)
                d['start'] = (ax_ + dx * k0, ay_ + dy * k0)
                d['end'] = (ax_ + dx * k1, ay_ + dy * k1)
                msp.add_line(d['start'], d['end'], dxfattribs={k: v for k, v in d.items() if k not in ('start', 'end', 'handle', 'owner')})
            msp.delete_entity(e)
            n += 1
        self.cnt('pattern_masked', n)

    @staticmethod
    def _centroid(poly):
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def _pole(self, poly, holes):
        """视觉中心：网格上离边界（含洞）最远的点。"""
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        st = max(200.0, min(max(xs) - min(xs), max(ys) - min(ys)) / 25)
        C = np.array([(x, y) for x in np.arange(min(xs), max(xs), st) for y in np.arange(min(ys), max(ys), st)])
        if len(C) == 0:
            return self._centroid(poly)
        ok = pip_many(C, poly)
        for hpoly in holes:
            ok &= ~pip_many(C, hpoly)
        C = C[ok]
        if len(C) == 0:
            return self._centroid(poly)
        edges = np.array([e for p in [poly] + holes for e in poly_edges(p)], float)
        d = pt_seg_dist(C, edges)
        i = int(np.argmax(d))
        return float(C[i, 0]), float(C[i, 1])

    def _level_geom(self, txt):
        P = self.P
        h = S.text_h('area', self.k)
        w = max(P(6), tw(txt, h) + P(1.5))
        return w, P(3) + P(0.6) + h

    def level_mark(self, apex, txt):
        """国标标高符号：等腰直角三角形（高 3 mm）+ 引线 + 数字（字在线上）。"""
        P = self.P
        x, y = apex
        h = S.text_h('area', self.k)
        w, H = self._level_geom(txt)
        L = 'A-FLOR-LEVL'
        self.sh.poly([(x - P(3), y + P(3)), (x, y), (x + P(3), y + P(3))], L)
        self.sh.line((x - P(3), y + P(3)), (x - P(3) + w, y + P(3)), L)
        self.text(txt, (x - P(3) + P(0.5), y + P(3) + P(0.6)), 'area', L, align='BOTTOM_LEFT', avoid=False)
        self.obs.rect('anno', x - P(3), y, x - P(3) + w, y + H)
        self.cnt('levels')

    def level_mark_near(self, txt, pref, polys, holes, cats, maxdist=None, outside=None, region_box=None):
        P = self.P
        w, H = self._level_geom(txt)
        res = self._place(w + P(1), H + P(1), polys, holes, pref, cats, step=P(1.0),
                          maxdist=maxdist or P(40), outside=outside, region_box=region_box)
        if res is None or res[1] > 0:
            res2 = self._place(w + P(1), H + P(1), polys, holes, pref, cats, step=P(1.0), outside=outside, region_box=region_box)
            if res is None or (res2 is not None and res2[1] < res[1]):
                res = res2
        if res is None:
            return
        (cx, cy), n = res
        # 矩形中心 → 三角形顶点
        apex = (cx - (w + P(1)) / 2 + P(0.5) + P(3), cy - (H + P(1)) / 2 + P(0.5))
        self.level_mark(apex, txt)

    def ground_level(self):
        site = self.m.site or {}
        if not site or not site.get('enabled', True):
            return
        gz = site.get('ground_z')
        if gz is None:
            return
        P = self.P
        bx0, by0, bx1, by1 = self.box
        # 范围：外墙外皮到第一道尺寸线之间
        d1 = self.d1
        region = (bx0 - d1['W'] + P(1), by0 - d1['S'] + P(1), bx1 + d1['E'] - P(1), by1 + d1['N'] - P(1))
        walls = [self._seg_rect(s) for s in self.ext]
        # 室外平台、台阶、坡道等闭合小区域（不是室外地坪）
        pads = [pl for pl in self.closed_floor if 0 < abs(_area(pl)) < 60e6]

        def inside_bldg(Q):
            r = np.zeros(len(Q), bool)
            for pl in pads:
                r |= pip_many(Q, pl)
            for rm in self.indoor:
                r |= pip_many(Q, rm['polygon'])
            for wr in walls:
                r |= pip_many(Q, wr)
            return r
        # 优先：主入口外侧
        doors = [o for o in self.openings if o.kind == 'door' and self.sids[o.wall].exterior]
        if doors:
            d = max(doors, key=lambda o: o.width)
            s = self.sids[d.wall]
            c = s.point_at(d.at + d.width / 2)
            ux, uy = s.unit
            nx, ny = -uy, ux
            probe = (c[0] + nx * (s.thickness / 2 + 300), c[1] + ny * (s.thickness / 2 + 300))
            sg = -1 if self._in_building(probe) else 1
            pref = (c[0] + nx * sg * P(6) + ux * d.width, c[1] + ny * sg * P(6) + uy * d.width)
        else:
            pref = (bx0 - P(6), by0 - P(6))
        cats = ('wall', 'furn', 'stair', 'anno', 'dimline', 'text', 'door', 'swing', 'floor', 'site', 'roof')
        self.level_mark_near(_lv_text(gz + self.z0 * 0), pref, [], [], cats, maxdist=P(400), outside=inside_bldg, region_box=region)

    # ------------------------------------------------------------------ envelope-based items
    def envelope(self):
        S_, R_ = self.obs.arrays(None)
        xs = list(S_[:, 0]) + list(S_[:, 2])
        ys = list(S_[:, 1]) + list(S_[:, 3])
        for x0, y0, x1, y1 in self.items:
            xs += [x0, x1]; ys += [y0, y1]
        return min(xs), min(ys), max(xs), max(ys)

    def sections(self):
        P = self.P
        ex0, ey0, ex1, ey1 = self.env
        pad = P(3)
        for sec in self.m.sections or []:
            a, b = tuple(sec['a'][:2]), tuple(sec['b'][:2])
            L = math.dist(a, b)
            if L < 1:
                continue
            ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            look = sec.get('look', 'left')
            lx, ly = (-uy, ux) if look == 'left' else (uy, -ux)
            for end, sg in ((a, -1), (b, 1)):
                dx, dy = ux * sg, uy * sg
                # 起点：剖切线与图形外包框的交点外再留 pad
                p = end
                tmax = 0
                for tt in np.arange(0, 1e6, P(1)):
                    q = (end[0] - dx * tt, end[1] - dy * tt)
                    if ex0 - pad <= q[0] <= ex1 + pad and ey0 - pad <= q[1] <= ey1 + pad:
                        tmax = tt
                        break
                    if tt > L:
                        break
                # 从端点往回走到包络边：若端点在包络内，往外推
                inner = end
                while ex0 - pad <= inner[0] <= ex1 + pad and ey0 - pad <= inner[1] <= ey1 + pad:
                    inner = (inner[0] + dx * P(1), inner[1] + dy * P(1))
                outer = (inner[0] + dx * P(8), inner[1] + dy * P(8))
                self.sh.line(inner, outer, 'A-ANNO-SYMB', lineweight=70)
                pe = (outer[0] + lx * P(5), outer[1] + ly * P(5))
                pb = outer
                self.sh.line(pb, pe, 'A-ANNO-SYMB', lineweight=70)
                hh = S.text_h('grid', self.k) * 0.8
                tc = (outer[0] + lx * P(2.5) + dx * (P(1.5) + hh / 2), outer[1] + ly * P(2.5) + dy * (P(1.5) + hh / 2))
                self.text(str(sec['id']), tc, 'grid', 'A-ANNO-SYMB', height=hh)
                xs = [inner[0], outer[0], pe[0], tc[0] - hh, tc[0] + hh]
                ys = [inner[1], outer[1], pe[1], tc[1] - hh, tc[1] + hh]
                self.items.append((min(xs), min(ys), max(xs), max(ys)))
            self.cnt('sections')

    def north_arrow(self, center):
        P = self.P
        ang = math.radians((self.m.site or {}).get('north', 90.0))
        ux, uy = math.cos(ang), math.sin(ang)
        nx, ny = -uy, ux
        r = P(12)
        cx, cy = center
        L = 'A-ANNO-SYMB'
        self.sh.circle(center, r, L, lineweight=25)
        tip = (cx + ux * r, cy + uy * r)
        tail = (cx - ux * r, cy - uy * r)
        tl = (tail[0] + nx * P(1.5), tail[1] + ny * P(1.5))
        tr = (tail[0] - nx * P(1.5), tail[1] - ny * P(1.5))
        self.sh.solid([tip, tl, tr], L, color=256)
        self.sh.poly([tip, tl, tr], L, closed=True, lineweight=18)
        hh = S.text_h('grid', self.k)
        self.text('北', (cx + ux * (r + P(1.5) + hh / 2), cy + uy * (r + P(1.5) + hh / 2)), 'grid', L)
        self.items.append((cx - r - hh * 1.5, cy - r - hh * 1.5, cx + r + hh * 1.5, cy + r + hh * 1.5))
        self.cnt('north')

    def opening_table(self, x0, ytop):
        """门窗表：左上角 (x0, ytop)。返回外包框。"""
        P = self.P
        rows = {}
        for op in self.openings:
            code = op.code or op.id
            r = rows.setdefault(code, {'op': op, 'n': 0})
            r['n'] += 1
        if not rows:
            return None
        def key(c):
            op = rows[c]['op']
            return (0 if op.kind == 'door' else 1 if op.kind == 'window' else 2, c)
        codes = sorted(rows, key=key)
        heads = ['类型', '设计编号', '洞口尺寸（宽×高）', '数量', '备注']
        data = []
        for c in codes:
            op = rows[c]['op']
            if op.kind == 'door':
                typ = DOOR_TYPES.get(op.type, '门')
                note = ''
            elif op.kind == 'window':
                typ = WIN_TYPES.get(op.type, '窗')
                note = f'窗台高 {op.sill:.0f}'
            else:
                typ, note = '洞口', ''
            data.append([typ, c, f'{op.width:.0f}×{op.height:.0f}', str(rows[c]['n']), note])
        th = S.text_h('tag', self.k)
        pad = P(2)
        colw = []
        for j in range(len(heads)):
            colw.append(max(tw(heads[j], th), max(tw(r[j], th) for r in data)) + 2 * pad)
        rh = P(7)
        W = sum(colw)
        H = rh * (len(data) + 1)
        title_h = S.text_h('room', self.k)
        self.text('门窗表', (x0 + W / 2, ytop - title_h / 2), 'room', 'A-ANNO-TEXT')
        top = ytop - title_h - P(3)
        L, Li = 'A-ANNO-SYMB', 'A-ANNO-TABL'
        self.sh.poly([(x0, top), (x0 + W, top), (x0 + W, top - H), (x0, top - H)], L, closed=True)
        for i in range(1, len(data) + 1):
            y = top - rh * i
            self.sh.line((x0, y), (x0 + W, y), L if i == 1 else Li)
        x = x0
        for j, w in enumerate(colw[:-1]):
            x += w
            self.sh.line((x, top), (x, top - H), Li)
        for i, row in enumerate([heads] + data):
            y = top - rh * i - rh / 2
            x = x0
            for j, w in enumerate(colw):
                self.text(row[j], (x + w / 2, y), 'tag', 'A-ANNO-TEXT', avoid=False)
                x += w
        self.cnt('table_rows', len(data))
        bb = (x0, top - H, x0 + W, ytop)
        self.items.append(bb)
        return bb

    def title_block(self, cx, ytop):
        """图名 + 粗细双线 + 比例，比例尺放右侧。"""
        P = self.P
        dr = self.m.drawing or {}
        title = dr.get('title') if self.idx == 0 and dr.get('title') else None
        if not title:
            title = '首层平面图' if self.idx == 0 else f'{self.lv} 平面图'
        scale_txt = f'1:{int(round(self.k))}'
        ht = S.text_h('title', self.k)
        hs = S.text_h('room', self.k)
        wt, ws = tw(title, ht), tw(scale_txt, hs)
        gap = P(4)
        tot = wt + gap + ws
        x0 = cx - tot / 2
        yb = ytop - ht            # 图名基线
        self.text(title, (x0, yb), 'title', 'A-TTLB', align='BOTTOM_LEFT')
        self.text(scale_txt, (x0 + wt + gap, yb), 'room', 'A-TTLB', align='BOTTOM_LEFT')
        y1 = yb - P(1.5)
        self.sh.line((x0, y1), (x0 + wt, y1), 'A-TTLB', lineweight=70)
        self.sh.line((x0, y1 - P(1.0)), (x0 + wt, y1 - P(1.0)), 'A-TTLB', lineweight=18)
        # 比例尺 0 1 2 5 m
        sx = x0 + tot + P(12)
        sy = yb + P(0.5)
        bh = P(1.5)
        L = 'A-TTLB'
        marks = [0, 1000, 2000, 3000, 4000, 5000]
        for i in range(len(marks) - 1):
            a, b = sx + marks[i], sx + marks[i + 1]
            pts = [(a, sy), (b, sy), (b, sy + bh), (a, sy + bh)]
            if i % 2 == 0:
                self.sh.solid(pts, L, color=256)
            self.sh.poly(pts, L, closed=True, lineweight=18)
        for v in (0, 1000, 2000, 5000):
            self.text(f'{v // 1000}', (sx + v, sy + bh + P(1.0)), 'small', L, align='BOTTOM_CENTER')
        self.text('m', (sx + 5000 + P(2.5), sy + bh / 2), 'small', L, align='MIDDLE_LEFT')
        self.cnt('title')
        bb = (x0, y1 - P(2), sx + 5000 + P(5), ytop + P(1))
        self.items.append(bb)
        return bb

    # ------------------------------------------------------------------ run
    def run(self):
        P = self.P
        if 'A-ANNO-TABL' not in self.sh.doc.layers:
            lay = self.sh.doc.layers.add('A-ANNO-TABL', color=7)
            lay.dxf.lineweight = 18
        self._dimstyles()
        self._collect_obstacles()
        self.center_marks = []
        grid = self.m.grid or self._auto_grid()
        gx = sorted(float(v) for v in grid.get('x', []))
        gy = sorted(float(v) for v in grid.get('y', []))
        self.box = self._faces()
        self.d1 = self._side_d1(*self.box)
        self.exterior_dims(gx, gy)
        self.arc_dims()
        self.grid_draw(gx, gy)
        self.arc_center_dims(gx, gy)
        self.interior_dims(gx, gy)
        self.opening_tags()
        self.room_labels()
        self.ground_level()
        self.mask_patterns()
        self.env = self.envelope()
        self.sections()
        self.env = self.envelope()
        ex0, ey0, ex1, ey1 = self.env
        # 指北针：图的右上方；门窗表：指北针下方
        hn = P(12)
        nc = (ex1 + P(10) + hn, ey1 - hn - P(4))
        self.north_arrow(nc)
        self.opening_table(ex1 + P(10), nc[1] - hn - P(14))
        # 图名：图形下方居中（以建筑为中心）
        bx0, by0, bx1, by1 = self.box
        self.title_block((bx0 + bx1) / 2, ey0 - P(8))
        self.stats['grid'] = {'x': gx, 'y': gy}
        return self.stats
