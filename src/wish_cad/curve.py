"""弧线与曲面（2026-11-19）：弧墙、带圆弧的轮廓、曲面屋顶网格。

规格写法
- 任何点列（outline、walls[].path、rooms/slabs 的 polygon、roof.footprint）里，在两个点之间插入
  {"through": [x, y]}，这条边就变成经过该点的圆弧。
- 墙也可以写 "arc": {"center": [cx, cy], "radius": R, "from": 0, "to": 90}（度，逆时针从 from 到 to；
  to - from = 360 是整圆）。
- 弧墙在 SU 里由短直墙拼成（每段 ≤ 6°），门窗所在的位置正好单独成一段，所以开洞照常用 at（沿弧长量）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MAX_STEP_DEG = 6.0


# --------------------------------------------------------------------------- arcs

def arc3(p0, pm, p1):
    """Circle through three points -> (cx, cy, R, a0, sweep) with sweep signed, or None if collinear."""
    ax, ay = p0; bx, by = pm; cx_, cy_ = p1
    d = 2 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
    if abs(d) < 1e-6:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy_) + (bx * bx + by * by) * (cy_ - ay) + (cx_ * cx_ + cy_ * cy_) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx_ - bx) + (bx * bx + by * by) * (ax - cx_) + (cx_ * cx_ + cy_ * cy_) * (bx - ax)) / d
    R = math.dist((ux, uy), p0)
    a0 = math.atan2(ay - uy, ax - ux)
    am = math.atan2(by - uy, bx - ux)
    a1 = math.atan2(cy_ - uy, cx_ - ux)
    ccw = lambda a, b: (b - a) % (2 * math.pi)
    sweep = ccw(a0, a1)
    if ccw(a0, am) > sweep:          # the through point is not on the CCW way -> go clockwise
        sweep = sweep - 2 * math.pi
    return ux, uy, R, a0, sweep


def arc_points(arc, step_deg=MAX_STEP_DEG, include_start=True):
    cx, cy, R, a0, sw = arc
    n = max(1, math.ceil(abs(math.degrees(sw)) / step_deg))
    out = [(cx + R * math.cos(a0 + sw * k / n), cy + R * math.sin(a0 + sw * k / n)) for k in range(n + 1)]
    return out if include_start else out[1:]


def parse_path(v, what, problems, closed=False):
    """Points + per-edge 'through' point (or None). Edge i runs point i -> point i+1 (wrapping when closed)."""
    if not isinstance(v, list):
        problems.append(f'{what} 应该是点列 [[x,y],...]')
        return [], []
    pts, thr = [], []
    pending = None
    for i, item in enumerate(v):
        if isinstance(item, dict):
            t = item.get('through')
            if not (isinstance(t, (list, tuple)) and len(t) >= 2):
                problems.append(f'{what} 第 {i} 项：圆弧要写成 {{"through": [x, y]}}')
                continue
            if not pts:
                problems.append(f'{what}：{{"through"}} 前面要先有一个起点')
                continue
            if pending is not None:
                problems.append(f'{what}：两个 through 连在一起了，中间要有一个点')
            pending = (float(t[0]), float(t[1]))
            continue
        if not (isinstance(item, (list, tuple)) and len(item) >= 2):
            problems.append(f'{what} 第 {i} 个点格式不对：{item!r}')
            continue
        p = (float(item[0]), float(item[1]))
        if pts:
            thr.append(pending)
        pending = None
        pts.append(p)
    if closed:
        if len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6 and pending is None:
            pts.pop(); thr.pop() if thr else None
        thr.append(pending)          # closing edge (a trailing through bends it)
    elif pending is not None:
        problems.append(f'{what}：最后的 through 后面缺终点')
    need = 3 if closed else 2
    if len(pts) < need and not (closed and len(pts) == 2 and all(thr)):
        problems.append(f'{what} 至少要 {need} 个点')
    return pts, thr


def edge_arcs(pts, thr, closed, what, problems):
    """List of arc tuples (or None for straight) per edge."""
    n = len(pts)
    out = []
    for i in range(len(thr)):
        a, b = pts[i], pts[(i + 1) % n]
        t = thr[i]
        if t is None:
            out.append(None)
            continue
        arc = arc3(a, t, b)
        if arc is None:
            problems.append(f'{what} 第 {i} 条边：through 点和两端在一条直线上，画不出圆弧')
            out.append(None)
        else:
            out.append(arc)
    return out


def dense(pts, arcs, closed):
    """Polyline with arcs tessellated (for slabs, roofs, rooms)."""
    out = []
    n = len(pts)
    for i in range(len(arcs) if closed else n - 1):
        a = pts[i]
        out.append(a)
        if arcs[i]:
            out += arc_points(arcs[i], include_start=False)[:-1]
    if not closed:
        out.append(pts[-1])
    return out


def from_arc_spec(a, what, problems):
    """'arc': {center, radius, from, to} -> path list with through items."""
    try:
        cx, cy = float(a['center'][0]), float(a['center'][1])
        R = float(a['radius'])
        f, t = float(a.get('from', 0)), float(a.get('to', 90))
    except (KeyError, TypeError, ValueError, IndexError):
        problems.append(f'{what} 的 arc 要写 {{"center":[x,y],"radius":R,"from":度,"to":度}}')
        return None, False
    if R <= 0 or abs(t - f) < 1:
        problems.append(f'{what} 的 arc：半径要 > 0，角度差要 ≥ 1°')
        return None, False
    P = lambda deg: [cx + R * math.cos(math.radians(deg)), cy + R * math.sin(math.radians(deg))]
    if abs(t - f) >= 359.999:        # full circle = two half arcs, closed
        return [P(f), {'through': P(f + 90)}, P(f + 180), {'through': P(f + 270)}], True
    return [P(f), {'through': P((f + t) / 2)}, P(t)], False


# --------------------------------------------------------------------------- logical arc wall

@dataclass
class ArcWall:
    """One curved wall as the user wrote it; later split into straight chords (spec.Segment)."""
    id: str
    level: str
    arc: tuple            # (cx, cy, R, a0, sweep)
    thickness: float
    height: float
    base_z: float
    ext_start: float = 0.0
    ext_end: float = 0.0
    exterior: bool = False
    material: str = 'wall'
    chords: list = field(default_factory=list)

    @property
    def length(self):
        return self.arc[2] * abs(self.arc[4])

    def point_at(self, s):
        cx, cy, R, a0, sw = self.arc
        a = a0 + math.copysign(s / R, sw)
        return (cx + R * math.cos(a), cy + R * math.sin(a))

    def breaks(self, openings):
        """Arc-length break points: openings become exactly one chord each, the rest ≤ MAX_STEP_DEG."""
        L, R = self.length, self.arc[2]
        spans = sorted((o.at, o.at + o.width) for o in openings)
        cuts = [0.0]
        cur = 0.0
        step = math.radians(MAX_STEP_DEG) * R
        for a, b in spans + [(L, L)]:
            if a > cur + 1:
                n = max(1, math.ceil((a - cur) / step - 1e-9))
                cuts += [cur + (a - cur) * k / n for k in range(1, n + 1)]
            if b > a:
                cuts.append(b)
            cur = max(cur, b)
        out = []
        for c in cuts:
            if not out or c - out[-1] > 0.5:
                out.append(min(c, L))
        if L - out[-1] > 0.5:
            out.append(L)
        return out


def sag(R, chord_arc_len):
    return R * (1 - math.cos(chord_arc_len / (2 * R)))


# --------------------------------------------------------------------------- curved roof surfaces

SURFACE_KINDS = ('vault', 'wave', 'dome', 'surface')


def height_fn(kind, fp, rise, axis=None, wavelength=None, phase=0.0, shape=None, heights=None):
    """z offset above the roof base for plan point (x, y). fp = footprint polygon."""
    xs, ys = [p[0] for p in fp], [p[1] for p in fp]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    lx, ly = x1 - x0, y1 - y0
    if axis not in ('x', 'y'):
        axis = 'x' if lx >= ly else 'y'          # vault / wave run along the long side by default
    if kind == 'vault':
        # circular-arc section across the short span, constant along `axis`
        w = ly if axis == 'x' else lx
        c = (y0 + y1) / 2 if axis == 'x' else (x0 + x1) / 2
        Rr = (w * w / 4 + rise * rise) / (2 * rise)

        def f(x, y):
            t = (y if axis == 'x' else x) - c
            return max(0.0, math.sqrt(max(0.0, Rr * Rr - t * t)) - (Rr - rise))
        return f, axis
    if kind == 'wave':
        L = lx if axis == 'x' else ly
        lam = wavelength or L / 1.5
        s0 = x0 if axis == 'x' else y0

        def f(x, y):
            s = (x if axis == 'x' else y) - s0
            return rise * (0.5 - 0.5 * math.cos(2 * math.pi * s / lam + math.radians(phase)))
        return f, axis
    if kind == 'surface':
        # 自由曲面：heights 是控制点高度表（行 = 从南 y0 到北 y1，列 = 从西 x0 到东 x1），
        # 均匀铺满外包框，按 Catmull-Rom 双三次样条平滑插值（曲面过每个控制点）
        H = [[float(v) for v in row] for row in heights]
        ny_, nx_ = len(H), len(H[0])

        def cr(vals, t):
            n = len(vals)
            if n == 1:
                return vals[0]
            t = min(max(t, 0.0), n - 1.0)
            i = min(int(t), n - 2)
            u = t - i
            p0 = vals[max(i - 1, 0)]; p1 = vals[i]; p2 = vals[i + 1]; p3 = vals[min(i + 2, n - 1)]
            return 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u +
                          (-p0 + 3 * p1 - 3 * p2 + p3) * u ** 3)

        def f(x, y):
            tx = (x - x0) / max(lx, 1e-9) * (nx_ - 1)
            ty = (y - y0) / max(ly, 1e-9) * (ny_ - 1)
            col = [cr(row, tx) for row in H]
            return max(0.0, cr(col, ty))
        return f, axis
    if kind == 'dome':
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if shape is None:      # 平面接近矩形（占外包框 ≥ 85%）用枕形，否则圆/椭圆穹顶
            shape = 'pillow' if abs(_area(fp)) >= 0.85 * lx * ly else 'round'
        if shape == 'pillow':
            # 枕形穹面：四边落到 0、四角不再压平；z = rise·(1-u²)(1-v²)，边缘留 2% 让墙顶补齐有厚度
            hx, hy = lx / 2 * 1.02, ly / 2 * 1.02

            def f(x, y):
                u, v = (x - cx) / hx, (y - cy) / hy
                return rise * max(0.0, 1 - u * u) * max(0.0, 1 - v * v)
            return f, axis
        rx, ry = lx / 2 * 1.02, ly / 2 * 1.02

        def f(x, y):
            d2 = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            return rise * max(0.0, 1 - d2)
        return f, axis
    raise ValueError(kind)


def _clip(poly, x0, y0, x1, y1):
    """Sutherland–Hodgman: part of `poly` inside the axis-aligned cell."""
    def cut(pts, inside, inter):
        out = []
        for i in range(len(pts)):
            p, q = pts[i - 1], pts[i]
            if inside(q):
                if not inside(p):
                    out.append(inter(p, q))
                out.append(q)
            elif inside(p):
                out.append(inter(p, q))
        return out

    def ix(p, q, x):
        t = (x - p[0]) / (q[0] - p[0]); return (x, p[1] + (q[1] - p[1]) * t)

    def iy(p, q, y):
        t = (y - p[1]) / (q[1] - p[1]); return (p[0] + (q[0] - p[0]) * t, y)
    pts = poly
    for inside, inter in ((lambda p: p[0] >= x0, lambda p, q: ix(p, q, x0)), (lambda p: p[0] <= x1, lambda p, q: ix(p, q, x1)),
                          (lambda p: p[1] >= y0, lambda p, q: iy(p, q, y0)), (lambda p: p[1] <= y1, lambda p, q: iy(p, q, y1))):
        if not pts:
            break
        pts = cut(pts, inside, inter)
    return pts


def _area(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) / 2


def _earclip(poly):
    """Triangulate a simple polygon (list of points); returns index triples into poly."""
    n = len(poly)
    if n < 3:
        return []
    idx = list(range(n))
    if _area(poly) < 0:
        idx.reverse()
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        found = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            cr = cross(a, b, c)
            if cr <= 1e-9:
                if abs(cr) <= 1e-9:          # collinear: drop the middle point
                    idx.pop(k); found = True; break
                continue
            ok = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = poly[j]
                if cross(a, b, p) >= -1e-9 and cross(b, c, p) >= -1e-9 and cross(c, a, p) >= -1e-9:
                    ok = False; break
            if ok:
                tris.append((i0, i1, i2)); idx.pop(k); found = True; break
        if not found:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def _insert_grid_crossings(poly, xs, ys):
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        out.append(p)
        ts = []
        for gx in xs:
            if (p[0] - gx) * (q[0] - gx) < 0:
                ts.append((gx - p[0]) / (q[0] - p[0]))
        for gy in ys:
            if (p[1] - gy) * (q[1] - gy) < 0:
                ts.append((gy - p[1]) / (q[1] - p[1]))
        for t in sorted(ts):
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


def surface_mesh(fp, f, base_z, thickness, cell=None):
    """Thick curved roof: vertices (x,y,z) and triangles; bottom = base_z + f, top = bottom + thickness.
    Returns dict(points, tris, area_ratio)."""
    xs_, ys_ = [p[0] for p in fp], [p[1] for p in fp]
    X0, X1, Y0, Y1 = min(xs_), max(xs_), min(ys_), max(ys_)
    cell = cell or max(300.0, max(X1 - X0, Y1 - Y0) / 40)
    nx, ny = max(1, math.ceil((X1 - X0) / cell)), max(1, math.ceil((Y1 - Y0) / cell))
    gxs = [X0 + (X1 - X0) * i / nx for i in range(nx + 1)]
    gys = [Y0 + (Y1 - Y0) * j / ny for j in range(ny + 1)]
    poly = fp if _area(fp) > 0 else list(reversed(fp))
    poly = _insert_grid_crossings(poly, gxs[1:-1], gys[1:-1])

    key = lambda p: (round(p[0], 1), round(p[1], 1))
    vid, plan = {}, []

    def V(p):
        k = key(p)
        if k not in vid:
            vid[k] = len(plan); plan.append(k)
        return vid[k]

    tris2d, tri_area = [], 0.0
    for i in range(nx):
        for j in range(ny):
            piece = _clip(poly, gxs[i], gys[j], gxs[i + 1], gys[j + 1])
            clean = []
            for p in piece:
                if not clean or math.dist(p, clean[-1]) > 0.5:
                    clean.append(p)
            if len(clean) > 2 and math.dist(clean[0], clean[-1]) < 0.5:
                clean.pop()
            if len(clean) < 3 or abs(_area(clean)) < 1:
                continue
            for a, b, c in _earclip(clean):
                tris2d.append((V(clean[a]), V(clean[b]), V(clean[c])))
                tri_area += abs(_area([clean[a], clean[b], clean[c]]))
    ring = [V(p) for p in poly]
    n = len(plan)
    pts = [(x, y, base_z + f(x, y)) for x, y in plan] + [(x, y, base_z + f(x, y) + thickness) for x, y in plan]
    tris = []
    for a, b, c in tris2d:
        tris.append((a, c, b))                 # bottom, facing down
        tris.append((a + n, b + n, c + n))     # top, facing up
    for k in range(len(ring)):                 # side band along the boundary
        a, b = ring[k], ring[(k + 1) % len(ring)]
        if a == b:
            continue
        tris.append((a, b, b + n))
        tris.append((a, b + n, a + n))
    return {'points': pts, 'tris': tris, 'area_ratio': tri_area / max(1.0, abs(_area(poly))), 'cell': cell}
