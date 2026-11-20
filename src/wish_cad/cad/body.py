"""建筑本体 + 室内外表达：draw(sh, m, level_id) -> dict

画什么（图层见 standard.LAYERS）：
- 墙：剖到的实墙按“并集”出轮廓（L/T 接头、弧墙、与柱相交处都没有多余线），门窗洞口断开；
  外墙 A-WALL-EXT 粗线 + 深色涂实，内墙 A-WALL（轻质隔墙用 0.7b）+ 施工图斜线填充 / 展板浅灰。
- 玻璃幕墙：两道框线（中粗）+ 玻璃线（细）+ 竖梃（涂实），弧形幕墙沿弧连续，幕墙上的门照样开。
- 窗：国标四线（平开）、三线（固定）、推拉两扇错开、带形窗分格；外窗画窗台线。
- 门：单扇/双扇/子母/推拉，门扇有厚度，开启弧细线；外门向外开，内门开向较小的房间。
- 柱：方柱/圆柱粗轮廓 + 涂实，墙线不穿柱。
- 室内外：散水（含转角斜线）、室外铺装带及分格、外门入口平台 + 台阶（自动连接附近坡道）、
  下沉区高差边线、防滑地砖网格、展板风格室内外底色。
- 楼梯/坡道/台阶（m.stairs）：踏步线、梯段边与栏杆、折断线、箭头、上/下文字、坡度。
- 屋面挑檐投影虚线。
画的顺序：底色 → 场地 → 地面线 → 楼梯 → 门窗幕墙 → 墙 → 柱 → 挑檐虚线（DXF 里后画的压在上面）。
"""
from __future__ import annotations

import math

from . import standard as S

D = S.DETAIL


# =========================================================================== small geometry

def _sub(p, q):
    return (p[0] - q[0], p[1] - q[1])


def _add(p, q, k=1.0):
    return (p[0] + q[0] * k, p[1] + q[1] * k)


def _dot(p, q):
    return p[0] * q[0] + p[1] * q[1]


def _cross(p, q):
    return p[0] * q[1] - p[1] * q[0]


def _unit(v):
    L = math.hypot(v[0], v[1]) or 1.0
    return (v[0] / L, v[1] / L)


def _rot90(v):
    return (-v[1], v[0])


def _neg(v):
    return (-v[0], -v[1])


def _ang(v):
    return math.degrees(math.atan2(v[1], v[0]))


def _left(s):
    return _rot90(s.unit)


def _loc(s, u, v):
    """段局部坐标：沿 a→b 方向 u，左法向 v。"""
    ux, uy = s.unit
    return (s.a[0] + ux * u - uy * v, s.a[1] + uy * u + ux * v)


def _isect(p, d, q, e):
    cr = _cross(d, e)
    if abs(cr) < 1e-12:
        return None
    t = _cross(_sub(q, p), e) / cr
    return (p[0] + d[0] * t, p[1] + d[1] * t)


def _signed_area(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly))) / 2.0


def _offset(poly, d):
    """简单多边形的斜接偏移，d > 0 向外。"""
    n = len(poly)
    ccw = _signed_area(poly) > 0
    out = []
    for i in range(n):
        p0, p1, p2 = poly[i - 1], poly[(i) % n], poly[(i + 1) % n]

        def nrm(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy) or 1.0
            return (dy / L, -dx / L) if ccw else (-dy / L, dx / L)
        n1, n2 = nrm(p0, p1), nrm(p1, p2)
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        bl = math.hypot(bx, by)
        if bl < 1e-9:
            out.append((p1[0] + n1[0] * d, p1[1] + n1[1] * d))
            continue
        bx, by = bx / bl, by / bl
        k = d / max(bx * n1[0] + by * n1[1], 0.2)
        out.append((p1[0] + bx * k, p1[1] + by * k))
    return out


def _dedupe(poly, tol=1.0):
    out = []
    for p in poly:
        if not out or math.dist(out[-1], p) > tol:
            out.append(tuple(p[:2]))
    if len(out) > 2 and math.dist(out[0], out[-1]) <= tol:
        out.pop()
    return out


def _pip(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xc = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < xc:
                inside = not inside
        j = i
    return inside


def _seg_t(a, b, c, d):
    r, s = _sub(b, a), _sub(d, c)
    den = _cross(r, s)
    if abs(den) < 1e-12:
        return None
    ca = _sub(c, a)
    t = _cross(ca, s) / den
    u = _cross(ca, r) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return t
    return None


class Poly:
    def __init__(self, pts, **tag):
        self.pts = [tuple(p[:2]) for p in pts]
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        self.box = (min(xs), min(ys), max(xs), max(ys))
        self.tag = tag

    def contains(self, p):
        b = self.box
        if p[0] < b[0] or p[0] > b[2] or p[1] < b[1] or p[1] > b[3]:
            return False
        return _pip(p, self.pts)

    def edges(self):
        n = len(self.pts)
        return [(self.pts[i], self.pts[(i + 1) % n]) for i in range(n)]

    def hits(self, a, b):
        b_ = self.box
        if max(a[0], b[0]) < b_[0] - 1 or min(a[0], b[0]) > b_[2] + 1 or \
           max(a[1], b[1]) < b_[1] - 1 or min(a[1], b[1]) > b_[3] + 1:
            return []
        out = []
        for c, d in self.edges():
            t = _seg_t(a, b, c, d)
            if t is not None:
                out.append(t)
        return out


class Circle:
    def __init__(self, c, r, **tag):
        self.c, self.r = tuple(c[:2]), r
        self.tag = tag

    def contains(self, p):
        return math.dist(p, self.c) < self.r

    def hits(self, a, b):
        d = _sub(b, a)
        f = _sub(a, self.c)
        A = _dot(d, d)
        if A < 1e-12:
            return []
        B = 2 * _dot(f, d)
        C = _dot(f, f) - self.r ** 2
        disc = B * B - 4 * A * C
        if disc < 0:
            return []
        sq = math.sqrt(disc)
        return [t for t in ((-B - sq) / (2 * A), (-B + sq) / (2 * A)) if 0 <= t <= 1]


def _clip(a, b, regions, keep):
    """把线段 ab 在所有 region 边界处打断，留下 keep(中点) 为真的部分（相邻的合并）。"""
    ts = {0.0, 1.0}
    for r in regions:
        for t in r.hits(a, b):
            if 1e-6 < t < 1 - 1e-6:
                ts.add(t)
    ts = sorted(ts)
    out = []
    d = _sub(b, a)
    for t0, t1 in zip(ts, ts[1:]):
        if (t1 - t0) * math.hypot(*d) < 0.5:
            continue
        mid = _add(a, d, (t0 + t1) / 2)
        if keep(mid):
            p, q = _add(a, d, t0), _add(a, d, t1)
            if out and math.dist(out[-1][1], p) < 0.5:
                out[-1] = (out[-1][0], q)
            else:
                out.append((p, q))
    return out


def _outside(regions):
    return lambda p: not any(r.contains(p) for r in regions)


def _lines_clipped(sh, pts, layer, regions, closed=False, **kw):
    """折线逐段裁掉落在 regions 里的部分后画出。"""
    n = len(pts)
    pairs = [(pts[i], pts[i + 1]) for i in range(n - 1)] + ([(pts[-1], pts[0])] if closed else [])
    keep = _outside(regions)
    k = 0
    for a, b in pairs:
        for p, q in (_clip(a, b, regions, keep) if regions else [(a, b)]):
            sh.line(p, q, layer, **kw)
            k += 1
    return k


def _merge_collinear(pieces):
    """同一直线上重叠/相接的线段合并成一根。pieces: [(p, q)]"""
    groups = {}
    for p, q in pieces:
        d = _unit(_sub(q, p))
        if d[0] < -1e-9 or (abs(d[0]) < 1e-9 and d[1] < 0):
            d = _neg(d)
        ang = round(math.degrees(math.atan2(d[1], d[0])) % 180, 2)
        nrm = _rot90(d)
        off = round(_dot(p, nrm) * 2) / 2
        key = (ang % 180, off)
        groups.setdefault(key, []).append((p, q, d))
    res = []
    for (ang, off), items in groups.items():
        d = items[0][2]
        nrm = _rot90(d)
        iv = sorted(tuple(sorted((_dot(p, d), _dot(q, d)))) for p, q, _ in items)
        base = _add((0.0, 0.0), nrm, _dot(items[0][0], nrm))
        cur = None
        for s0, s1 in iv:
            if cur and s0 <= cur[1] + 0.5:
                cur[1] = max(cur[1], s1)
            else:
                if cur:
                    res.append((_add(base, d, cur[0]), _add(base, d, cur[1])))
                cur = [s0, s1]
        if cur:
            res.append((_add(base, d, cur[0]), _add(base, d, cur[1])))
    return res


def _circle_pts(c, r, n=48):
    return [(c[0] + r * math.cos(2 * math.pi * k / n), c[1] + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _solid(sh, pts, layer, rgb=None, color=None, holes=()):
    if rgb:
        return sh.solid(pts, layer, rgb=rgb, holes=holes)
    return sh.solid(pts, layer, color=color if color is not None else 7, holes=holes)


def _arrow(sh, p0, p1, layer):
    """细线箭头（实心箭头头）。"""
    sh.line(p0, p1, layer)
    d = _unit(_sub(p1, p0))
    n = _rot90(d)
    L, w = sh.p(2.2), sh.p(0.55)
    base = _add(p1, d, -L)
    sh.solid([p1, _add(base, n, w), _add(base, n, -w)], layer, color=7)


def _break_line(sh, a, b, layer):
    """折断线：直线中间一个 Z 形。"""
    d = _unit(_sub(b, a))
    n = _rot90(d)
    m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    z = sh.p(1.2)
    pts = [a, _add(m, d, -z), _add(_add(m, d, -z / 3), n, z * 1.4), _add(_add(m, d, z / 3), n, -z * 1.4),
           _add(m, d, z), b]
    sh.poly(pts, layer)


def _readable(angle):
    a = (angle + 180) % 360 - 180
    if a > 90:
        a -= 180
    elif a <= -90:
        a += 180
    return a


# =========================================================================== model helpers

def _level(m, lid):
    return next((l for l in m.levels if l.id == lid), m.levels[0])


def _building_outline(m, lid, segs):
    """建筑外皮轮廓：优先用本层楼板（outline 外偏 t/2），否则用外墙段外皮点拼。"""
    lv = _level(m, lid)
    cands = [s for s in m.slabs if s.get('id') == f'SLAB_{lid}']
    if not cands:
        cands = [s for s in m.slabs if abs(s.get('top_z', 0) - lv.elevation) < 1]
    if cands:
        best = max(cands, key=lambda s: abs(_signed_area(s['polygon'])))
        return _dedupe(best['polygon'])
    return None


def _rooms(m, lid):
    return [r for r in (m.rooms or []) if r.get('level') == lid]


def _room_at(rooms, p):
    hit = [r for r in rooms if _pip(p, r['polygon'])]
    return min(hit, key=lambda r: abs(_signed_area(r['polygon']))) if hit else None


def _ops_on(m, sid):
    return [o for o in m.openings if o.wall == sid]


# =========================================================================== joints & wall polygons

def _joints(segs):
    """两段端点相接、且至少一方是弧墙弦段或幕墙时做斜接：{(seg.id,'a'|'b'): (点, 斜接线方向)}。
    两道普通直墙的转角靠段延伸重叠 + 并集轮廓处理，不斜接。"""
    ends = [(s, e, s.a if e == 'a' else s.b) for s in segs for e in ('a', 'b')]
    out = {}
    for s, e, p in ends:
        hits = [(n, f) for n, f, q in ends if n is not s and math.dist(p, q) < 1.0]
        if len(hits) != 1:
            continue
        n, f = hits[0]
        if s.kind == 'wall' and n.kind == 'wall' and not (s.arc or n.arc):
            continue
        d_in = s.unit if e == 'b' else _neg(s.unit)
        d_out = n.unit if f == 'a' else _neg(n.unit)
        if _dot(d_in, d_out) < -0.5:
            continue
        mdir = _add(_rot90(d_in), _rot90(d_out))
        if math.hypot(*mdir) < 1e-6:
            continue
        out[(s.id, e)] = (tuple(p), _unit(mdir), n)
    return out


def _cap(s, end, v, joints):
    j = joints.get((s.id, end))
    if not j:
        return _loc(s, 0.0 if end == 'a' else s.length, v)
    p, mdir, _ = j
    q = _isect(_loc(s, 0, v), s.unit, p, mdir)
    return q if q is not None else _loc(s, 0.0 if end == 'a' else s.length, v)


def _spans(s, ops, joints):
    """段上去掉洞口后的实体区间 [(u0,u1)] 以及两端 u。"""
    u_lo = 0.0 if (s.id, 'a') in joints else -s.ext_start
    u_hi = s.length if (s.id, 'b') in joints else s.length + s.ext_end
    cuts = sorted((o.at, o.at + o.width) for o in ops)
    u, out = u_lo, []
    for a, b in cuts:
        if a > u + 0.5:
            out.append((u, min(a, u_hi)))
        u = max(u, b)
    if u_hi > u + 0.5:
        out.append((u, u_hi))
    return [x for x in out if x[1] - x[0] > 0.5], u_lo, u_hi


def _quad(s, u0, u1, v0, v1, joints, u_lo, u_hi):
    """区间 [u0,u1]×[v0,v1] 的四边形，到端点处按斜接线收头。返回 (lo0, lo1, hi1, hi0), at_a, at_b。"""
    at_a = (s.id, 'a') in joints and u0 <= u_lo + 0.01
    at_b = (s.id, 'b') in joints and u1 >= u_hi - 0.01
    lo0 = _cap(s, 'a', v0, joints) if at_a else _loc(s, u0, v0)
    hi0 = _cap(s, 'a', v1, joints) if at_a else _loc(s, u0, v1)
    lo1 = _cap(s, 'b', v0, joints) if at_b else _loc(s, u1, v0)
    hi1 = _cap(s, 'b', v1, joints) if at_b else _loc(s, u1, v1)
    return (lo0, lo1, hi1, hi0), at_a, at_b


def _wall_polys(walls, m, joints):
    """实墙涂实多边形：直墙每个实体区间一个四边形；同一道弧墙相邻弦段连成一整块。"""
    out = []
    run = None   # [lo_pts, hi_pts, seg]

    def flush():
        nonlocal run
        if run:
            lo, hi, s0 = run
            out.append({'pts': lo + hi[::-1], 'ext': s0.exterior, 't': s0.thickness, 'seg': s0})
        run = None

    for s in walls:
        h = s.thickness / 2
        spans, u_lo, u_hi = _spans(s, _ops_on(m, s.id), joints)
        for u0, u1 in spans:
            (lo0, lo1, hi1, hi0), at_a, at_b = _quad(s, u0, u1, -h, h, joints, u_lo, u_hi)
            if not s.arc:
                flush()
                out.append({'pts': [lo0, lo1, hi1, hi0], 'ext': s.exterior, 't': s.thickness, 'seg': s})
                continue
            prev = joints.get((s.id, 'a'))
            if run and at_a and prev and prev[2].arc == s.arc and run[2].arc == s.arc and math.dist(run[0][-1], lo0) < 1.0:
                run[0].append(lo1)
                run[1].append(hi1)
            else:
                flush()
                run = [[lo0, lo1], [hi0, hi1], s]
            if not at_b:
                flush()
        if not s.arc:
            flush()
    flush()
    return out


def _col_regions(cols):
    regs = []
    for c in cols:
        if c['shape'] == 'round':
            regs.append(Circle(c['at'], (c['dia'] or 400) / 2, col=c))
        else:
            regs.append(Poly(_col_rect(c), col=c))
    return regs


def _col_rect(c):
    w, d = c['size']
    r = math.radians(c.get('rotation', 0))
    cs, sn = math.cos(r), math.sin(r)
    x, y = c['at']
    return [(x + px * cs - py * sn, y + px * sn + py * cs)
            for px, py in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))]


def _union_outline(polys, others):
    """所有墙多边形边界中，只留下一侧在（墙∪柱）里、另一侧不在的部分 = 并集外轮廓。
    返回 {(layer, lw): [(p,q)]}。"""
    regs = [Poly(p['pts'], rec=p) for p in polys]
    allr = regs + list(others)

    def inside(pt):
        return any(r.contains(pt) for r in allr)

    out = {}
    for reg in regs:
        rec = reg.tag['rec']
        if rec['ext']:
            key = ('A-WALL-EXT', None)
        else:
            key = ('A-WALL', 50 if rec['t'] < 200 else None)   # 轻质隔墙：次要构件 0.7b
        for a, b in reg.edges():
            if math.dist(a, b) < 0.5:
                continue
            n = _rot90(_unit(_sub(b, a)))
            for p, q in _clip(a, b, allr, lambda mid: inside(_add(mid, n, 0.8)) != inside(_add(mid, n, -0.8))):
                out.setdefault(key, []).append((p, q))
    return {k: _merge_collinear(v) for k, v in out.items()}


# =========================================================================== doors & windows

def _inward(s, bld):
    """+1：段的左法向指向室内。"""
    if not bld:
        return 1
    mid = _loc(s, s.length / 2, s.thickness / 2 + 300)
    return 1 if _pip(mid, bld) else -1


def _door_side(s, o, bld, rooms):
    """门扇开向哪一侧（+1 = 左法向）。外门向外开；内门开向较小的房间（房间一侧，不向走道/大厅）。"""
    h = s.thickness / 2
    if s.exterior:
        return -_inward(s, bld)
    um = o.at + o.width / 2
    rp = _room_at(rooms, _loc(s, um, h + 500))
    rm = _room_at(rooms, _loc(s, um, -h - 500))
    ap = abs(_signed_area(rp['polygon'])) if rp else 1e18
    am = abs(_signed_area(rm['polygon'])) if rm else 1e18
    if abs(ap - am) < 1e3:
        # 一样大：开向“防滑地砖”等湿区以外的一侧（卫生间门习惯向内开，这里两边同大时开向卫生间）
        wet = lambda r: r and any(k in (r.get('floor') or '') + r['name'] for k in ('卫生', '厕', '防滑'))
        return 1 if wet(rp) else -1 if wet(rm) else 1
    return 1 if ap < am else -1


def _swing_leaf(sh, s, u_h, u_far, side, h, t):
    """平开门扇：铰链在 u_h 一侧墙面，门扇 90° 开启，返回开启弧。"""
    H = _loc(s, u_h, side * h)
    n = _rot90(s.unit) if side > 0 else _neg(_rot90(s.unit))
    du = s.unit if u_far > u_h else _neg(s.unit)
    Lf = abs(u_far - u_h)
    tip = _add(H, n, Lf)
    sh.poly([H, tip, _add(tip, du, t), _add(H, du, t)], 'A-DOOR', closed=True)
    a_n, a_d = _ang(n), _ang(du)
    if _cross(du, n) > 0:
        sh.arc(H, Lf, a_d, a_n, 'A-DOOR-SWNG')
    else:
        sh.arc(H, Lf, a_n, a_d, 'A-DOOR-SWNG')


def _draw_door(sh, s, o, bld, rooms, h):
    t = D['door_leaf_t']
    typ = o.type or ('double' if o.width >= 1500 else 'single')
    u0, u1 = o.at, o.at + o.width
    if typ == 'sliding':
        ov = 50
        mid = (u0 + u1) / 2
        for a, b, v0, v1 in ((u0, mid + ov / 2, 0, t), (mid - ov / 2, u1, -t, 0)):
            sh.poly([_loc(s, a, v0), _loc(s, b, v0), _loc(s, b, v1), _loc(s, a, v1)], 'A-DOOR', closed=True)
        return
    side = _door_side(s, o, bld, rooms)
    # 铰链放在离墙端更近的门边，门扇开到底时贴着邻墙
    near_start = o.at <= s.length - (o.at + o.width)
    uh, uf = (u0, u1) if near_start else (u1, u0)
    if typ == 'double':
        mid = (u0 + u1) / 2
        _swing_leaf(sh, s, u0, mid, side, h, t)
        _swing_leaf(sh, s, u1, mid, side, h, t)
    elif typ == 'mother':
        um = uh + (uf - uh) * 2 / 3
        _swing_leaf(sh, s, uh, um, side, h, t)
        _swing_leaf(sh, s, uf, um, side, h, t)
    else:
        _swing_leaf(sh, s, uh, uf, side, h, t)


def _draw_window(sh, s, o, bld, style):
    h = s.thickness / 2
    u0, u1 = o.at, o.at + o.width
    typ = o.type or 'casement'
    L = lambda v, a=u0, b=u1, lay='A-GLAZ': sh.line(_loc(s, a, v), _loc(s, b, v), lay)
    if style == 'presentation':
        sh.solid([_loc(s, u0, -h), _loc(s, u1, -h), _loc(s, u1, h), _loc(s, u0, h)], 'A-GLAZ',
                 rgb=S.PRESENT['water'])
    # 墙两侧外皮线
    L(-h)
    L(h)
    g = s.thickness / 6
    if typ == 'fixed':
        L(0.0)                                   # 固定窗：三线，玻璃居中
    elif typ == 'sliding':
        ov = 60                                  # 推拉窗：两扇在两条轨道上错开
        mid = (u0 + u1) / 2
        f = min(40, s.thickness / 8)
        for a, b, v in ((u0, mid + ov / 2, g * 0.5), (mid - ov / 2, u1, -g * 0.5)):
            sh.poly([_loc(s, a, v - f / 2), _loc(s, b, v - f / 2), _loc(s, b, v + f / 2), _loc(s, a, v + f / 2)],
                    'A-GLAZ', closed=True)
    else:                                        # 平开窗 / 带形窗：四线 + 分扇短线
        L(-g)
        L(g)
        step = 900 if typ == 'strip' else 750
        n = max(1, round(o.width / step))
        if typ == 'strip':
            n = max(2, n)
        for k in range(1, n):
            u = u0 + o.width * k / n
            sh.line(_loc(s, u, -g), _loc(s, u, g), 'A-GLAZ')
    # 窗台线（外窗，外侧挑出 60）
    if s.exterior and bld:
        out = -_inward(s, bld)
        e = 60
        v1 = out * (h + e)
        sh.poly([_loc(s, u0 - e, out * h), _loc(s, u0 - e, v1), _loc(s, u1 + e, v1), _loc(s, u1 + e, out * h)],
                'A-GLAZ')


# =========================================================================== curtain walls

def _curtain_chains(curtains):
    chains, by_arc = [], {}
    for s in curtains:
        if s.arc:
            if s.arc not in by_arc:
                by_arc[s.arc] = []
                chains.append(by_arc[s.arc])
            by_arc[s.arc].append(s)
        else:
            chains.append([s])
    return chains


def _curtain_hc(s):
    """幕墙平面厚度：用段自身厚度（与相邻实墙外皮齐平）；没给时按 DETAIL['curtain_t']。"""
    return (s.thickness or D['curtain_t']) / 2


def _trim_ends(s, spans, joints, regs):
    """幕墙端头插进实墙/柱时，在墙面处截断（端头竖梃贴墙面，不伸进 T 接头）。"""
    if not spans or not regs:
        return spans
    a, b = s.a, s.b
    ts = sorted({t for r in regs for t in r.hits(a, b)})
    out = list(spans)
    if (s.id, 'a') not in joints and any(r.contains(s.a) for r in regs):
        cut = next((t * s.length for t in ts if t * s.length > 0.5), None)
        if cut is not None and cut < out[0][1]:
            out[0] = (max(out[0][0], cut), out[0][1])
    if (s.id, 'b') not in joints and any(r.contains(s.b) for r in regs):
        cut = next((t * s.length for t in reversed(ts) if t * s.length < s.length - 0.5), None)
        if cut is not None and cut > out[-1][0]:
            out[-1] = (out[-1][0], min(out[-1][1], cut))
    return [x for x in out if x[1] - x[0] > 0.5]


def _curtain_regions(curtains, joints):
    regs = []
    for s in curtains:
        h = max(_curtain_hc(s), s.thickness / 2)
        (lo0, lo1, hi1, hi0), _, _ = _quad(s, 0, s.length, -h, h, joints, 0, s.length)
        regs.append(Poly([lo0, lo1, hi1, hi0], seg=s))
    return regs


def _draw_curtains(sh, m, curtains, joints, block_regs, bld, rooms, style):
    n_mull = 0
    mw = D['mullion_w']
    keep = _outside(block_regs)
    for chain in _curtain_chains(curtains):
        for s in chain:
            hc = _curtain_hc(s)
            ops = [o for o in _ops_on(m, s.id) if o.kind in ('door', 'void')]
            spans, u_lo, u_hi = _spans(s, ops, joints)
            spans = _trim_ends(s, spans, joints, block_regs)
            for u0, u1 in spans:
                (lo0, lo1, hi1, hi0), at_a, at_b = _quad(s, u0, u1, -hc, hc, joints, u_lo, u_hi)
                if style == 'presentation':
                    sh.solid([lo0, lo1, hi1, hi0], 'A-GLAZ', rgb=S.PRESENT['water'])
                for a, b, lay in ((lo0, lo1, 'A-GLAZ-FRAM'), (hi0, hi1, 'A-GLAZ-FRAM'),
                                  (_cap(s, 'a', 0, joints) if at_a else _loc(s, u0, 0),
                                   _cap(s, 'b', 0, joints) if at_b else _loc(s, u1, 0), 'A-GLAZ')):
                    for p, q in _clip(a, b, block_regs, keep):
                        sh.line(p, q, lay)
                # 竖梃：区间两端（与相邻弦段相接处各画半根）+ 中间按 mullion_step 均分
                ends = [(u0, at_a, +1), (u1, at_b, -1)]
                for u, joined, dirn in ends:
                    w = mw / 2 if joined else mw
                    ua, ub = sorted((u, u + dirn * w))
                    (q0, q1, q2, q3), _, _ = _quad(s, ua, ub, -hc, hc, joints, u_lo, u_hi)
                    c = _loc(s, (ua + ub) / 2, 0)
                    if keep(c):
                        _solid(sh, [q0, q1, q2, q3], 'A-GLAZ-FRAM', rgb='#3C3C3C' if style == 'presentation' else None)
                        n_mull += 1
                span = u1 - u0
                k = max(1, math.ceil(span / D['mullion_step'] - 1e-6))
                for i in range(1, k):
                    u = u0 + span * i / k
                    c = _loc(s, u, 0)
                    if not keep(c):
                        continue
                    pts = [_loc(s, u - mw / 2, -hc), _loc(s, u + mw / 2, -hc), _loc(s, u + mw / 2, hc), _loc(s, u - mw / 2, hc)]
                    _solid(sh, pts, 'A-GLAZ-FRAM', rgb='#3C3C3C' if style == 'presentation' else None)
                    n_mull += 1
            for o in _ops_on(m, s.id):
                if o.kind == 'door':
                    _draw_door(sh, s, o, bld, rooms, hc)
    return n_mull


# =========================================================================== stairs / ramps / steps

def _sframe(st):
    a = math.radians(st['dir'])
    du = (math.cos(a), math.sin(a))
    dw = _rot90(du)
    o = st['at']
    P = lambda u, w: (o[0] + du[0] * u + dw[0] * w, o[1] + du[1] * u + dw[1] * w)
    return P, du, dw


def _stair_rect(st):
    P, _, _ = _sframe(st)
    W, L = st['width'], st['length']
    return [P(0, -W / 2), P(L, -W / 2), P(L, W / 2), P(0, W / 2)]


def _draw_stair(sh, st, regs, lv_h):
    P, du, dw = _sframe(st)
    W, L, n = st['width'], st['length'], max(1, st['steps'])
    kind = st['kind']
    label = st.get('label') or ('上' if st.get('up', True) else '下')
    keep = _outside(regs)

    def ln(a, b, lay):
        for p, q in _clip(a, b, regs, keep):
            sh.line(p, q, lay)

    txt_h = S.text_h('note', sh.scale)
    if kind == 'ramp':
        for w in (-W / 2, W / 2):
            ln(P(0, w), P(L, w), 'A-STRS')
        ln(P(0, -W / 2), P(0, W / 2), 'A-STRS')
        ln(P(L, -W / 2), P(L, W / 2), 'A-STRS')
        a0, a1 = (P(min(600, L * 0.15), 0), P(L - min(400, L * 0.1), 0))
        if not st.get('up', True):
            a0, a1 = a1, a0
        _arrow(sh, a0, a1, 'A-STRS-ARRW')
        rot = _readable(st['dir'])
        if not st.get('label'):
            label = f"i=1:{L / max(st['rise'], 1):.0f}"
        sh.text(label, P(L / 2, txt_h * 0.9), 'small', 'A-STRS-ARRW', rot=rot)
        return
    if kind == 'steps':
        tread = L / n
        for k in range(0, n + 1):
            ln(P(k * tread, -W / 2), P(k * tread, W / 2), 'A-STRS')
        for w in (-W / 2, W / 2):
            ln(P(0, w), P(L, w), 'A-STRS')
        _arrow(sh, P(min(100, tread * 0.3), 0), P(L - min(80, tread * 0.25), 0), 'A-STRS-ARRW')
        # 文字放箭尾外侧；箭尾落在墙/幕墙/门洞里时改放梯段侧边
        tail = P(-txt_h * 1.1, 0)
        probe = [_add(tail, (dx, dy)) for dx in (-txt_h, 0, txt_h) for dy in (-txt_h * 0.6, 0, txt_h * 0.6)] + \
                [P(-txt_h * 1.1 * f, 0) for f in (0.1, 0.3, 0.5, 0.7)]
        if any(r.contains(q) for r in regs for q in probe):
            tail = P(L / 2, -(W / 2 + txt_h * 1.0))
        sh.text(label, tail, 'note', 'A-STRS-ARRW')
        return
    # 楼梯：本层只画剖切面以下的梯段，到折断线为止
    tread = L / (n - 1) if n > 1 else L
    rise = st['rise'] / n if n else 150
    rail = 50
    up = st.get('up', True)
    k_cut = max(3, int(1300 / max(rise, 1)))
    u_cut = (k_cut + 0.5) * tread
    wb = W / 2 + 150
    dk = 0.35 * W
    broken = up and u_cut + dk < L - tread * 0.5
    kslope = dk / wb

    def u_b(w):
        return u_cut + kslope * w if broken else L

    for k in range(n):
        u = k * tread
        if u > (u_cut + dk if broken else L) + 1:
            break
        wa, wz = -W / 2 + rail, W / 2 - rail
        if broken:
            wmin = (u - u_cut) / kslope
            wa = max(wa, wmin)
        if wz - wa > 1:
            ln(P(u, wa), P(u, wz), 'A-STRS')
    for w in (-W / 2, W / 2):
        ln(P(0, w), P(u_b(w), w), 'A-STRS')
        ww = w - math.copysign(rail, w)
        ln(P(0, ww), P(u_b(ww), ww), 'A-STRS')
    if broken:
        _break_line(sh, P(u_cut - dk, -wb), P(u_cut + dk, wb), 'A-STRS-ARRW')
    tail = P(-sh.p(3.5), 0)
    head = P((u_cut - dk * 0.6) if broken else L - tread * 0.5, 0)
    if not up:
        tail, head = P(L - tread * 0.5, 0), P(-sh.p(3.5), 0)
    _arrow(sh, tail, head, 'A-STRS-ARRW')
    lab_at = P(-sh.p(3.5) - txt_h * 1.1, 0) if up else P(-sh.p(3.5) - txt_h * 1.1, 0)
    sh.text(label, lab_at, 'note', 'A-STRS-ARRW')


# =========================================================================== site

def _platforms(m, segs, bld, lv, stairs):
    """外门入口平台（局部坐标 u 沿墙、w 自外皮向外），自动接上附近坡道/台阶的上端。"""
    site = m.site or {}
    if not bld or not site.get('enabled', True) or not site.get('entrance_steps', True):
        return []
    drop = lv.elevation - site.get('ground_z', -300)
    nsteps = max(0, round(drop / D['step_rise']))
    outs = []
    for s in segs:
        if not s.exterior:
            continue
        for o in _ops_on(m, s.id):
            if o.kind != 'door':
                continue
            sign_out = -_inward(s, bld)
            h = s.thickness / 2
            mg = 900
            pf = {'seg': s, 'out': sign_out, 'h': h, 'u0': o.at - mg, 'u1': o.at + o.width + mg,
                  'w1': D['platform'], 'attach': set(), 'n': nsteps, 'door': o, 'bld': bld, '_win': {}}
            outs.append(pf)
    for pf in outs:
        s = pf['seg']
        for st in stairs:
            if st['kind'] not in ('ramp', 'steps'):
                continue
            P, du, dw = _sframe(st)
            c = P(st['length'] / 2, 0)
            if _pip(c, bld):
                continue
            top = [P(st['length'], -st['width'] / 2), P(st['length'], st['width'] / 2)] if st.get('up', True) \
                else [P(0, -st['width'] / 2), P(0, st['width'] / 2)]
            loc = [(_dot(_sub(p, s.a), s.unit), _dot(_sub(p, s.a), _neg(_rot90(s.unit))) * (1 if pf['out'] < 0 else -1) - pf['h'])
                   for p in top]
            # 点到平台矩形的距离
            def dist(q):
                du_ = max(pf['u0'] - q[0], 0, q[0] - pf['u1'])
                dw_ = max(-q[1], 0, q[1] - pf['w1'])
                return math.hypot(du_, dw_)
            if min(dist(q) for q in loc) > 4500:
                continue
            # 自动贴平台：沿坡道自身方向平移，让上端正好接到平台边（离得远时平台不再被拉长）
            asc = du if st.get('up', True) else _neg(du)
            ow = _neg(_rot90(s.unit)) if pf['out'] < 0 else _rot90(s.unit)
            par = abs(_dot(du, s.unit)) > 0.7
            tu = sum(q[0] for q in loc) / 2
            tw_ = sum(q[1] for q in loc) / 2
            if par:
                c_ = _dot(asc, s.unit)
                shift = ((pf['u0'] if c_ > 0 else pf['u1']) - tu) / c_
            else:
                c_ = _dot(asc, ow)
                shift = (pf['w1'] - tw_) / c_ if abs(c_) > 0.7 else 0.0
            if abs(shift) > 1:
                st['at'] = (st['at'][0] + du[0] * shift, st['at'][1] + du[1] * shift)
                st['snapped'] = round(shift)
                P, du, dw = _sframe(st)
                top = [P(st['length'], -st['width'] / 2), P(st['length'], st['width'] / 2)] if st.get('up', True)                     else [P(0, -st['width'] / 2), P(0, st['width'] / 2)]
                loc = [(_dot(_sub(p, s.a), s.unit), _dot(_sub(p, s.a), _neg(_rot90(s.unit))) * (1 if pf['out'] < 0 else -1) - pf['h'])
                       for p in top]
            us = [q[0] for q in loc]
            ws = [q[1] for q in loc]
            if par:
                if max(us) <= pf['u0'] + 1:
                    pf['attach'].add('u0')
                elif min(us) >= pf['u1'] - 1:
                    pf['attach'].add('u1')
            else:
                pf['attach'].add('front')
            pf['u0'] = min(pf['u0'], min(us))
            pf['u1'] = max(pf['u1'], max(us))
            pf['w1'] = max(pf['w1'], max(ws))
            pf.setdefault('ramps', []).append(st)
    return outs


def _pf_point(pf, u, w):
    s = pf['seg']
    return _loc(s, u, pf['out'] * (pf['h'] + w))


def _pf_win(pf, u):
    """平台在 u 处的内边 w：从外往墙看，碰到建筑外皮的位置（直墙为 0，弧墙两端为负）。
    返回 (w, free)；free=True 表示这里没有墙（平台伸出墙角），内边按 w=0 处理。"""
    key = round(u, 1)
    if key in pf['_win']:
        return pf['_win'][key]
    bld = pf.get('bld')
    res = (0.0, True)
    if bld:
        st0 = 600.0
        S0 = _pf_point(pf, u, st0)
        d = _sub(_pf_point(pf, u, st0 - 1), S0)
        best = None
        n = len(bld)
        for i in range(n):
            a, b = bld[i], bld[(i + 1) % n]
            e = _sub(b, a)
            cr = _cross(d, e)
            if abs(cr) < 1e-12:
                continue
            t = _cross(_sub(a, S0), e) / cr
            v = _cross(_sub(a, S0), d) / cr
            if t > 0 and -1e-9 <= v <= 1 + 1e-9 and (best is None or t < best):
                best = t
        if best is not None:
            w = st0 - best
            if -1500 <= w <= pf['w1'] - 300:
                res = (min(w, 0.0) if abs(w) < 1 else w, False)
    pf['_win'][key] = res
    return res


def _pf_inner(pf, a0, a1, grow=0.0):
    """内边折线（a1→a0 方向，跟随外皮），每 ≤300 mm 取一点。"""
    n = max(1, int(math.ceil(abs(a1 - a0) / 300.0)))
    pts = []
    for i in range(n + 1):
        u = a1 + (a0 - a1) * i / n
        w, _ = _pf_win(pf, u)
        pts.append(_pf_point(pf, u, w - grow))
    return pts


def _pf_rect(pf, grow=0.0, steps=True):
    n = pf['n'] if steps else 0
    e = n * D['step_tread'] + grow
    u0 = pf['u0'] - (0 if 'u0' in pf['attach'] else e)
    u1 = pf['u1'] + (0 if 'u1' in pf['attach'] else e)
    w1 = pf['w1'] + e
    return [_pf_point(pf, u0, w1), _pf_point(pf, u1, w1)] + _pf_inner(pf, u0, u1, grow)


def _draw_platform(sh, pf, ramp_regs, style, bld=None):
    T = D['step_tread']
    if style == 'presentation':
        sh.solid(_pf_rect(pf), 'A-FILL-OUT', rgb=S.PRESENT['grey'])
    keep = _outside(ramp_regs)

    def ln(a, b, **kw):
        for p, q in _clip(a, b, ramp_regs, keep):
            sh.line(p, q, 'A-FLOR-OUTL', **kw)

    u0, u1, w1 = pf['u0'], pf['u1'], pf['w1']
    # 平台边（k=0）和每一级台阶（k=1..n），三面包，接坡道的一侧不做台阶
    for k in range(0, pf['n'] + 1):
        e = k * T
        a0 = u0 - (0 if 'u0' in pf['attach'] else e)
        a1 = u1 + (0 if 'u1' in pf['attach'] else e)
        ln(_pf_point(pf, a0, w1 + e), _pf_point(pf, a1, w1 + e))
        if 'u0' not in pf['attach'] or k == 0:
            ln(_pf_point(pf, a0, _pf_win(pf, a0)[0]), _pf_point(pf, a0, w1 + e))
        if 'u1' not in pf['attach'] or k == 0:
            ln(_pf_point(pf, a1, _pf_win(pf, a1)[0]), _pf_point(pf, a1, w1 + e))
    # 平台内侧边：只有伸出墙角、背后没有墙的那几段要封口（贴墙处墙线就是边）
    if bld:
        breg = Poly(bld)
        m_ = max(1, int(math.ceil((u1 - u0) / 100.0)))
        run = None
        for i in range(m_ + 1):
            u = u0 + (u1 - u0) * i / m_
            free = _pf_win(pf, u)[1] and not breg.contains(_pf_point(pf, u, -2))
            if free and run is None:
                run = u
            if (not free or i == m_) and run is not None:
                if u - run > 1:
                    ln(_pf_point(pf, run, 0), _pf_point(pf, u, 0))
                run = None


def _draw_site(sh, m, bld, pfs, stairs_out, style, lv):
    site = m.site or {}
    if not bld or not site.get('enabled', True):
        return {}
    apron_w = site.get('apron', D['apron'])
    pav_w = site.get('paving', 3000)
    apron = _offset(bld, apron_w) if apron_w > 0 else None
    pav = _offset(bld, apron_w + pav_w) if pav_w > 0 else None
    if site.get('boundary'):
        pav = _dedupe(site['boundary'])
    excl = [Poly(_pf_rect(p)) for p in pfs] + [Poly(_stair_rect(st)) for st in stairs_out]
    stats = {}
    # --- 底色（展板）：铺装带 + 坡道/平台
    if style == 'presentation' and pav:
        sh.solid(pav, 'A-FILL-OUT', rgb=S.PRESENT['outdoor'], holes=[bld])
    # --- 铺装分格（极细）：施工图里尺寸线都落在铺装带上，默认只在展板风格画分格（site.paving_pattern 可强制开/关）
    grid_on = site.get('paving_pattern', style == 'presentation')
    if pav and grid_on:
        G = 1200.0
        regs_in = [Poly(pav)]
        regs_out = ([Poly(apron)] if apron else [Poly(bld)]) + excl
        allr = regs_in + regs_out
        keep = lambda p: regs_in[0].contains(p) and not any(r.contains(p) for r in regs_out)
        xs = [p[0] for p in pav]
        ys = [p[1] for p in pav]
        bx0 = min(p[0] for p in bld)
        by0 = min(p[1] for p in bld)
        nlines = 0
        x = bx0 + math.floor((min(xs) - bx0) / G) * G
        while x <= max(xs):
            for p, q in _clip((x, min(ys) - 1), (x, max(ys) + 1), allr, keep):
                sh.line(p, q, 'A-SITE-PATT')
                nlines += 1
            x += G
        y = by0 + math.floor((min(ys) - by0) / G) * G
        while y <= max(ys):
            for p, q in _clip((min(xs) - 1, y), (max(xs) + 1, y), allr, keep):
                sh.line(p, q, 'A-SITE-PATT')
                nlines += 1
            y += G
        stats['paving_lines'] = nlines
    if pav:     # 铺装外边线
        _lines_clipped(sh, pav, 'A-SITE', excl, closed=True)
    # --- 散水：外边线 + 转角斜线，平台/坡道处断开
    if apron:
        _lines_clipped(sh, apron, 'A-FLOR-OUTL', excl, closed=True, lineweight=18)
        n = len(bld)
        for i in range(n):
            p0, p1, p2 = bld[i - 1], bld[i], bld[(i + 1) % n]
            d1, d2 = _unit(_sub(p1, p0)), _unit(_sub(p2, p1))
            if math.degrees(math.acos(max(-1, min(1, _dot(d1, d2))))) > 30:
                _lines_clipped(sh, [p1, apron[i]], 'A-FLOR-OUTL', excl, lineweight=18)
        stats['apron'] = 1
    return stats


def _grid(sh, poly, gx, gy, layer, excl):
    """多边形内画 gx×gy 分格线，避开 excl（墙、柱、洞口）。"""
    rin = Poly(poly)
    regs = [rin] + list(excl)
    keep = lambda p: rin.contains(p) and not any(x.contains(p) for x in excl)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x = math.floor(min(xs) / gx) * gx + gx
    while x < max(xs):
        for p, q in _clip((x, min(ys) - 1), (x, max(ys) + 1), regs, keep):
            sh.line(p, q, layer)
        x += gx
    y = math.floor(min(ys) / gy) * gy + gy
    while y < max(ys):
        for p, q in _clip((min(xs) - 1, y), (max(xs) + 1, y), regs, keep):
            sh.line(p, q, layer)
        y += gy


# 展板风格的线色（淡彩图里不用 ACI 的高饱和青/黄）
PRES_LINE = {
    'A-GLAZ': '#5E93AE', 'A-GLAZ-FRAM': '#3E4A52', 'A-DOOR': '#4A4038', 'A-DOOR-SWNG': '#8A8278',
    'A-STRS': '#3A3A3A', 'A-STRS-ARRW': '#3A3A3A', 'A-FLOR-OUTL': '#55524C', 'A-SITE': '#9A968E',
    'A-SITE-PATT': '#C4C0B8', 'A-FLOR-PATT': '#C9C2B4', 'A-ROOF-OUTL': '#6A6A6A',
}


class _Pen:
    """包一层 Sheet：展板风格下按图层给线加真彩色，其余照旧。"""

    def __init__(self, sh):
        self._sh = sh
        self._tc = {k: int(v.lstrip('#'), 16) for k, v in PRES_LINE.items()}

    def __getattr__(self, name):
        return getattr(self._sh, name)

    def _kw(self, layer, kw):
        if layer in self._tc and 'true_color' not in kw:
            kw = dict(kw, true_color=self._tc[layer])
        return kw

    def line(self, a, b, layer, **kw):
        return self._sh.line(a, b, layer, **self._kw(layer, kw))

    def poly(self, pts, layer, closed=False, **kw):
        return self._sh.poly(pts, layer, closed=closed, **self._kw(layer, kw))

    def arc(self, center, r, a0, a1, layer, **kw):
        return self._sh.arc(center, r, a0, a1, layer, **self._kw(layer, kw))

    def circle(self, center, r, layer, **kw):
        return self._sh.circle(center, r, layer, **self._kw(layer, kw))

    def text(self, s, at, kind, layer, **kw):
        return self._sh.text(s, at, kind, layer, **self._kw(layer, kw))


# =========================================================================== main

def draw(sh, m, level_id):
    style = getattr(sh, 'style', 'construction')
    if style == 'presentation':
        sh = _Pen(sh)
    lv = _level(m, level_id)
    segs = [s for s in m.segments if s.level == level_id]
    walls = [s for s in segs if s.kind != 'curtain']
    curtains = [s for s in segs if s.kind == 'curtain']
    cols = [c for c in (m.columns or []) if c.get('level') == level_id]
    stairs = [st for st in (m.stairs or []) if st.get('level') == level_id]
    rooms = _rooms(m, level_id)
    bld = _building_outline(m, level_id, segs)
    joints = _joints(segs)
    stats = {}

    polys = _wall_polys(walls, m, joints)
    wall_regs = [Poly(p['pts']) for p in polys]
    col_regs = _col_regions(cols)
    curt_regs = _curtain_regions(curtains, joints)
    solid_regs = wall_regs + col_regs

    stairs_out = [st for st in stairs if bld and not _pip(_sframe(st)[0](st['length'] / 2, 0), bld)]
    pfs = _platforms(m, segs, bld, lv, stairs)

    # ---------------- 1. 底色（展板风格）
    if style == 'presentation' and bld:
        holes = [r['polygon'] for r in rooms if r.get('outdoor')]
        sh.solid(bld, 'A-FILL-IN', rgb=S.PRESENT['indoor'])
        for r in rooms:
            if r.get('outdoor'):
                sh.solid(r['polygon'], 'A-FILL-OUT', rgb=S.PRESENT['outdoor'])
            elif abs(r.get('elevation', lv.elevation) - lv.elevation) >= 50:
                sh.solid(r['polygon'], 'A-FILL-IN', rgb='#EFE7D6')     # 下沉区略深一档

    # ---------------- 2. 场地：铺装、散水、平台台阶
    stats.update(_draw_site(sh, m, bld, pfs, stairs_out, style, lv))
    ramp_regs = [Poly(_stair_rect(st)) for st in stairs_out]
    for pf in pfs:
        _draw_platform(sh, pf, ramp_regs, style, bld)
    stats['platforms'] = len(pfs)
    if style == 'presentation':
        for st in stairs_out:
            sh.solid(_stair_rect(st), 'A-FILL-OUT', rgb=S.PRESENT['grey'])

    # ---------------- 3. 室内地面：下沉区高差边线、防滑地砖网格、室外庭院铺装
    edge_regs = solid_regs + curt_regs
    open_regs = []
    for sg in segs:
        for o in _ops_on(m, sg.id):
            hh = sg.thickness / 2 + 1
            open_regs.append(Poly([_loc(sg, o.at, -hh), _loc(sg, o.at + o.width, -hh),
                                   _loc(sg, o.at + o.width, hh), _loc(sg, o.at, hh)]))
    for r in rooms:
        poly = r['polygon']
        dz = r.get('elevation', lv.elevation) - lv.elevation
        if abs(dz) >= 50 and not r.get('outdoor'):
            stats['level_edges'] = stats.get('level_edges', 0) + \
                _lines_clipped(sh, poly, 'A-FLOR-OUTL', edge_regs, closed=True)
        fl = (r.get('floor') or '')
        if not r.get('outdoor') and ('防滑' in fl or '地砖' in fl):
            _grid(sh, poly, 300.0, 300.0, 'A-FLOR-PATT', solid_regs + open_regs)
            stats['tile_rooms'] = stats.get('tile_rooms', 0) + 1
        if r.get('outdoor'):
            _grid(sh, poly, 600.0, 1200.0, 'A-SITE-PATT', solid_regs + open_regs)

    # ---------------- 4. 楼梯、坡道、台阶
    for st in stairs:
        _draw_stair(sh, st, solid_regs + curt_regs if st not in stairs_out else solid_regs, lv.height)
    stats['stairs'] = len(stairs)

    # ---------------- 5. 幕墙、门窗
    stats['mullions'] = _draw_curtains(sh, m, curtains, joints, solid_regs, bld, rooms, style)
    nd = nw = 0
    for s in walls:
        h = s.thickness / 2
        for o in _ops_on(m, s.id):
            if o.kind == 'door':
                _draw_door(sh, s, o, bld, rooms, h)
                nd += 1
            elif o.kind == 'window':
                _draw_window(sh, s, o, bld, style)
                nw += 1
    nd += sum(1 for s in curtains for o in _ops_on(m, s.id) if o.kind == 'door')
    stats['doors'], stats['windows'] = nd, nw

    # ---------------- 6. 墙：先内墙后外墙（外墙涂实压住 T 接头处的内墙），再画并集轮廓
    for rec in sorted(polys, key=lambda p: p['ext']):
        pts = rec['pts']
        if style == 'presentation':
            sh.solid(pts, 'A-WALL-PATT', rgb=S.PRESENT['wall'] if rec['ext'] else '#8C8C8C')
        elif rec['ext']:
            sh.solid(pts, 'A-WALL-PATT', color=250)
        else:
            sh.hatch(pts, 'A-WALL-PATT', pattern='ANSI31', pscale=2.5, color=8)
    outline = _union_outline(polys, col_regs)
    nl = 0
    for (layer, lw), pieces in outline.items():
        kw = {}
        if lw:
            kw['lineweight'] = lw
        if style == 'presentation' and layer == 'A-WALL':      # 展板：内墙轮廓灰一些，浅灰涂实才看得出来
            kw.update(lineweight=35, true_color=0x505050)
        for p, q in pieces:
            sh.line(p, q, layer, **kw)
            nl += 1
    stats['wall_polys'], stats['wall_lines'] = len(polys), nl

    # ---------------- 7. 柱：涂实 + 粗轮廓
    for c in cols:
        if c['shape'] == 'round':
            r = (c['dia'] or 400) / 2
            pts = _circle_pts(c['at'], r, 64)
            _solid(sh, pts, 'A-COLS-PATT', rgb=S.PRESENT['col'] if style == 'presentation' else None)
            sh.circle(c['at'], r, 'A-COLS')
        else:
            pts = _col_rect(c)
            _solid(sh, pts, 'A-COLS-PATT', rgb=S.PRESENT['col'] if style == 'presentation' else None)
            sh.poly(pts, 'A-COLS', closed=True)
    stats['columns'] = len(cols)

    # ---------------- 8. 屋面挑檐投影（虚线）
    roof = getattr(m, 'roof', None)
    if roof and roof.get('footprint'):
        fp = _dedupe(roof['footprint'])
        # 虚线：LTSCALE=比例×0.5 时 DASHED 周期太短，实体上单独放大，使打印后划约 3 mm、空约 1.5 mm
        sh.poly(fp, 'A-ROOF-OUTL', closed=True, ltscale=12.0)
        # 文字：从长边开始找一个不压平台/坡道/台阶的位置，写在虚线外侧
        n = len(fp)
        cen = (sum(p[0] for p in fp) / n, sum(p[1] for p in fp) / n)
        busy = [Poly(_pf_rect(pf)) for pf in pfs] + [Poly(_stair_rect(st)) for st in stairs]
        best = None
        for i in sorted(range(n), key=lambda k: -math.dist(fp[k], fp[(k + 1) % n])):
            a, b = fp[i], fp[(i + 1) % n]
            if math.dist(a, b) < sh.p(25):
                break
            d = _unit(_sub(b, a))
            nrm = _rot90(d)
            if _dot(_sub(cen, a), nrm) > 0:
                nrm = _neg(nrm)
            for f in (0.5, 0.3, 0.7, 0.15, 0.85):
                c = _add(a, d, math.dist(a, b) * f)
                probe = [_add(_add(c, d, k * sh.p(5)), nrm, sh.p(1.5)) for k in (-1, 0, 1)] +                         [_add(_add(c, d, k * sh.p(5)), nrm, -sh.p(1.5)) for k in (-1, 0, 1)]
                if not any(r.contains(q) for r in busy for q in probe):
                    best = (c, d, nrm)
                    break
            if best:
                break
        if best:
            c, d, nrm = best
            p = _add(c, nrm, sh.p(0.8))
            rot = _readable(_ang(d))
            up = _rot90((math.cos(math.radians(rot)), math.sin(math.radians(rot))))
            sh.text('屋檐投影线', p, 'small', 'A-ROOF-OUTL', align='BOTTOM_CENTER' if _dot(up, nrm) > 0 else 'TOP_CENTER',
                    rot=rot, linetype='Continuous')
        stats['roof_outline'] = 1
    for k, v in stats.items():
        sh.count(f'body.{k}', v if isinstance(v, int) else 1)
    return stats
