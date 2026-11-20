"""Design spec -> exact geometry (mm). Shared by the SketchUp builder, the CAD plan and the board.

A spec is a small JSON file (see examples/). Everything is in millimetres, plan
coordinates are [x, y], z is up. Validation happens here, before any app is
touched, so mistakes cost one quick round instead of a rebuild.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import curve

HEX = re.compile(r'^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')

DEFAULT_MATERIALS = {
    'wall': '#EEE9DF', 'floor': '#B8A68C', 'roof': '#5B5B5B',
    'interior': '#F4F1EA', 'wood': '#A47448', 'glass': '#9CC3D5', 'dark': '#3C3C3C',
}


class SpecError(ValueError):
    """Raised with a list of Chinese messages; nothing has been built yet."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__('规格有问题，没有动任何软件：\n' + '\n'.join(f'  - {p}' for p in problems))


@dataclass
class Level:
    id: str
    elevation: float
    height: float


@dataclass
class Segment:
    id: str            # e.g. EXT.0, IW1, IW1.2
    level: str
    a: tuple           # centerline start (as given)
    b: tuple           # centerline end
    thickness: float
    height: float
    base_z: float
    ext_start: float = 0.0   # extension added before a (corner closure)
    ext_end: float = 0.0
    exterior: bool = False
    material: str = 'wall'
    arc: str | None = None   # id of the curved wall this chord belongs to
    kind: str = 'wall'       # wall | curtain（玻璃幕墙：平面画成幕墙，SU 里用玻璃材质）

    @property
    def length(self):
        return math.dist(self.a, self.b)

    @property
    def unit(self):
        L = self.length
        return ((self.b[0] - self.a[0]) / L, (self.b[1] - self.a[1]) / L)

    @property
    def built_a(self):
        ux, uy = self.unit
        return (self.a[0] - ux * self.ext_start, self.a[1] - uy * self.ext_start)

    @property
    def built_b(self):
        ux, uy = self.unit
        return (self.b[0] + ux * self.ext_end, self.b[1] + uy * self.ext_end)

    def point_at(self, d):
        ux, uy = self.unit
        return (self.a[0] + ux * d, self.a[1] + uy * d)


@dataclass
class Opening:
    id: str
    wall: str
    kind: str          # door / window / void
    at: float          # from the wall segment's start point (as given, not the built end)
    width: float
    height: float
    sill: float
    frame: bool = True
    type: str = ''     # door: single|double|sliding|mother（子母门）; window: casement|fixed|sliding|strip
    code: str = ''     # 门窗编号，如 M1021 / C1518（不写自动生成）


@dataclass
class Box:
    id: str
    name: str
    at: tuple          # [x, y, z] min corner of the placed (rotated) footprint
    size: tuple        # [dx, dy, dz] before rotation (for assets: the final scaled size)
    rotation: float = 0.0
    material: str = 'wood'

    def _rot(self):
        r = math.radians(self.rotation)
        cs, sn = math.cos(r), math.sin(r)
        pts = [(px * cs - py * sn, px * sn + py * cs) for px, py in ((0, 0), (self.size[0], 0), (self.size[0], self.size[1]), (0, self.size[1]))]
        mx, my = min(p[0] for p in pts), min(p[1] for p in pts)
        return pts, mx, my

    def footprint(self):
        """World plan corners; the rotated footprint's min corner sits at `at`."""
        pts, mx, my = self._rot()
        return [(self.at[0] + x - mx, self.at[1] + y - my) for x, y in pts]

    def origin(self):
        """Where the un-rotated local (0,0,0) lands, for SketchUp primitives."""
        _, mx, my = self._rot()
        return (self.at[0] - mx, self.at[1] - my, self.at[2])


@dataclass
class Model:
    name: str
    out_dir: Path
    levels: list
    segments: list
    openings: list
    slabs: list        # dicts: id, polygon, top_z, thickness
    roof: dict | None
    boxes: list
    rooms: list        # dicts: name, level, polygon, area_m2
    materials: dict
    views: list
    board: dict
    warnings: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    arcs: list = field(default_factory=list)   # curve.ArcWall (already split into chords)
    columns: list = field(default_factory=list)  # dicts: id, level, at(x,y), size(w,d) | dia, shape rect|round, rotation
    stairs: list = field(default_factory=list)   # dicts: id, level, kind stair|ramp, at, dir, width, length, steps, rise, label
    site: dict = field(default_factory=dict)     # ground_z, apron, paving, boundary, entrance_steps
    grid: dict | None = None                     # 轴网 {'x': [..], 'y': [..]}，None = 自动
    sections: list = field(default_factory=list) # 剖切符号 {id, a, b, look}
    drawing: dict = field(default_factory=dict)  # scale, style, paper, title

    def segment(self, sid):
        return next((s for s in self.segments if s.id == sid), None)

    def bounds(self):
        pts = [p for s in self.segments for p in (s.built_a, s.built_b)]
        if self.roof:
            pts += [tuple(p) for p in self.roof['footprint']]
        xs = [p[0] for p in pts] or [0]
        ys = [p[1] for p in pts] or [0]
        top = max([lv.elevation + lv.height for lv in self.levels] + [0])
        if self.roof:
            top = self.roof['base_z'] + self.roof.get('rise', 0) + self.roof['thickness']
        return (min(xs), min(ys), 0.0), (max(xs), max(ys), top)


# --------------------------------------------------------------------------- helpers

def polygon_area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2.0


def signed_area(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly))) / 2.0


def offset_polygon(poly, d):
    """Miter offset of a simple polygon; d > 0 grows it outward."""
    n = len(poly)
    ccw = signed_area(poly) > 0
    out = []
    for i in range(n):
        p0, p1, p2 = poly[i - 1], poly[i], poly[(i + 1) % n]
        def normal(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            # outward normal: right-hand for CCW polygons
            return (dy / L, -dx / L) if ccw else (-dy / L, dx / L)
        n1, n2 = normal(p0, p1), normal(p1, p2)
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        bl = math.hypot(bx, by)
        if bl < 1e-9:
            out.append((p1[0] + n1[0] * d, p1[1] + n1[1] * d))
            continue
        bx, by = bx / bl, by / bl
        cos_half = bx * n1[0] + by * n1[1]
        k = d / max(cos_half, 0.2)  # cap very sharp corners
        out.append((p1[0] + bx * k, p1[1] + by * k))
    return out


def _num(v, what, problems, positive=True):
    try:
        x = float(v)
    except (TypeError, ValueError):
        problems.append(f'{what} 应该是数字，现在是 {v!r}')
        return 0.0
    if positive and x <= 0:
        problems.append(f'{what} 必须大于 0，现在是 {x:g}')
    return x


def _pts(v, what, problems, closed=False):
    if not isinstance(v, list) or len(v) < (3 if closed else 2):
        problems.append(f'{what} 至少要 {3 if closed else 2} 个点，格式 [[x,y],...]')
        return []
    out = []
    for i, p in enumerate(v):
        if not (isinstance(p, (list, tuple)) and len(p) >= 2):
            problems.append(f'{what} 第 {i} 个点格式不对：{p!r}')
            continue
        out.append((float(p[0]), float(p[1])))
    if closed and len(out) > 2 and math.dist(out[0], out[-1]) < 1e-6:
        out = out[:-1]  # tolerate a repeated first point
    return out


def _inside(p, poly, tol=1.0):
    """点在多边形内或边上（边上容差 tol mm）。"""
    x, y = p
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
        if math.hypot(x - x1 - t * dx, y - y1 - t * dy) <= tol:
            return True
    c = False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _curve_poly(v, what, problems):
    pts, thr = curve.parse_path(v, what, problems, closed=True)
    if not pts:
        return []
    return curve.dense(pts, curve.edge_arcs(pts, thr, True, what, problems), True)


def _color(v):
    return isinstance(v, str) and bool(HEX.match(v.strip()))


def load(path_or_dict) -> Model:
    if isinstance(path_or_dict, (str, Path)):
        p = Path(path_or_dict)
        raw = json.loads(p.read_text(encoding='utf-8-sig'))
    else:
        raw = dict(path_or_dict)
    return build_model(raw)


def build_model(raw: dict) -> Model:
    problems, warnings = [], []
    name = str(raw.get('name') or '未命名方案').strip()
    safe = re.sub(r'[^\w.-]+', '_', name).strip(' ._') or 'project'
    if Path(safe).is_reserved():
        safe = f"project-{safe}"
    out_dir = Path(raw.get('out_dir') or Path('build') / safe)

    d = raw.get('defaults', {})
    wall_t = _num(d.get('wall_thickness', 240), 'defaults.wall_thickness', problems)
    wall_h = _num(d.get('wall_height', 3600), 'defaults.wall_height', problems)
    slab_t = _num(d.get('slab_thickness', 150), 'defaults.slab_thickness', problems)

    # ---- levels
    levels = []
    for i, lv in enumerate(raw.get('levels') or [{'id': '1F', 'elevation': 0, 'height': wall_h}]):
        lid = str(lv.get('id', f'{i + 1}F'))
        if not re.match(r'^[A-Za-z0-9_\-]+$', lid):
            problems.append(f'楼层 id 只能用字母数字（用作 SketchUp 标签），现在是 {lid!r}')
        levels.append(Level(lid, float(lv.get('elevation', sum(l.height for l in levels))),
                            _num(lv.get('height', wall_h), f'楼层 {lid} 的 height', problems)))
    level_ids = {lv.id for lv in levels}
    lvl = {lv.id: lv for lv in levels}

    # ---- material colors
    materials = dict(DEFAULT_MATERIALS)
    for key, value in (raw.get('materials') or {}).items():
        if not _color(value):
            problems.append(f"Material {key!r} must be a hexadecimal color, for example #AABBCC.")
            continue
        materials[key] = '#' + value.strip().lstrip('#')

    def mat(key, where):
        if _color(key):
            materials[key] = '#' + key.strip().lstrip('#')
        elif not isinstance(key, str) or key not in materials:
            problems.append(f"{where}: unknown material {key!r}. Define a color in materials.")
        return key

    # ---- exterior outline -> closed wall loop on each level
    segments = []
    arcs = []
    outline, out_arcs = [], []
    if raw.get('outline'):
        outline, thr = curve.parse_path(raw['outline'], 'outline', problems, closed=True)
        out_arcs = curve.edge_arcs(outline, thr, True, 'outline', problems)
    outline_dense = curve.dense(outline, out_arcs, True) if outline else []
    outline_levels = raw.get('outline_levels') or [lv.id for lv in levels]
    if outline:
        if signed_area(outline) < 0:
            pass  # either winding is fine
        n = len(outline)
        for lid in outline_levels:
            if lid not in level_ids:
                problems.append(f'outline_levels 里的 {lid} 不是已定义楼层')
                continue
            lv = lvl[lid]
            for i in range(n):
                a, b = outline[i], outline[(i + 1) % n]
                if math.dist(a, b) < 1:
                    problems.append(f'outline 第 {i} 和 {i + 1} 个点重合')
                    continue
                # Corner closure: with an even edge count, alternate edges run
                # through the corners -> no overlap. Odd count: all extend.
                ext = wall_t / 2 if (n % 2 == 1 or i % 2 == 0) else 0.0
                sid = f'EXT.{i}' if len(outline_levels) == 1 else f'{lid}.EXT.{i}'
                if out_arcs[i]:
                    arcs.append(curve.ArcWall(sid, lid, out_arcs[i], wall_t, lv.height, lv.elevation, ext, ext, True,
                                              mat(raw.get('wall_material', 'wall'), 'outline')))
                    continue
                segments.append(Segment(sid, lid, a, b, wall_t, lv.height, lv.elevation, ext, ext, True,
                                        mat(raw.get('wall_material', 'wall'), 'outline')))

    # ---- extra walls (open or closed polylines)
    for wi, w in enumerate(raw.get('walls') or []):
        wid = str(w.get('id') or f'W{wi + 1}')
        lid = str(w.get('level', levels[0].id))
        if lid not in level_ids:
            problems.append(f'墙 {wid} 的 level {lid} 不存在')
            continue
        lv = lvl[lid]
        raw_path, closed = w.get('path'), bool(w.get('closed'))
        if w.get('arc'):
            raw_path, full = curve.from_arc_spec(w['arc'], f'墙 {wid}', problems)
            closed = closed or full
            if raw_path is None:
                continue
        path, thr = curve.parse_path(raw_path, f'墙 {wid} 的 path', problems, closed=closed)
        w_arcs = curve.edge_arcs(path, thr, closed, f'墙 {wid}', problems)
        t = _num(w.get('thickness', wall_t), f'墙 {wid} 的 thickness', problems)
        h = _num(w.get('height', lv.height), f'墙 {wid} 的 height', problems)
        pairs = list(zip(path, path[1:])) + ([(path[-1], path[0])] if closed and len(path) >= 2 else [])
        w = dict(w, closed=closed)
        for k, (a, b) in enumerate(pairs):
            sid = wid if len(pairs) == 1 else f'{wid}.{k}'
            # Inner joints of a polyline overlap by t/2 so corners close.
            es = t / 2 if (k > 0 or w.get('closed')) else float(w.get('extend_start', 0))
            ee = t / 2 if (k < len(pairs) - 1 or w.get('closed')) else float(w.get('extend_end', 0))
            if k < len(w_arcs) and w_arcs[k]:
                arcs.append(curve.ArcWall(sid, lid, w_arcs[k], t, h, lv.elevation + float(w.get('base_offset', 0)),
                                          es, ee, bool(w.get('exterior')),
                                          mat(w.get('material', 'interior' if not w.get('exterior') else 'wall'), f'墙 {sid}')))
                continue
            if math.dist(a, b) < 1:
                problems.append(f'墙 {sid} 长度为 0')
                continue
            segments.append(Segment(sid, lid, a, b, t, h, lv.elevation + float(w.get('base_offset', 0)), es, ee,
                                    False, mat(w.get('material', 'interior' if not w.get('exterior') else 'wall'), f'墙 {sid}')))

    curtain_ids = set(map(str, raw.get('curtain_walls') or []))
    for wi, w in enumerate(raw.get('walls') or []):
        if str(w.get('kind', 'wall')) == 'curtain':
            wid = str(w.get('id') or f'W{wi + 1}')
            curtain_ids |= {x.id for x in segments + arcs if x.id == wid or x.id.startswith(wid + '.')}
    for x in segments + arcs:
        x.kind = 'curtain' if x.id in curtain_ids else 'wall'
        if x.kind == 'curtain':
            x.material = mat('glass', f'幕墙 {x.id}')   # SU 里幕墙用玻璃材质
    ids = [s.id for s in segments] + [a.id for a in arcs]
    unknown_c = curtain_ids - set(ids)
    if unknown_c:
        problems.append(f'curtain_walls 里的 {sorted(unknown_c)} 不是已有的墙 id')
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        problems.append(f'墙编号重复：{sorted(dup)}')
    seg_by_id = {s.id: s for s in segments + arcs}

    # ---- openings (with repeat)
    openings = []
    auto = 0
    for oi, o in enumerate(raw.get('openings') or []):
        wall_id = str(o.get('wall', ''))
        if wall_id not in seg_by_id:
            hint = '，外墙用 EXT.0、EXT.1…（outline 第 i 条边：点 i → 点 i+1）' if wall_id.startswith('EXT') or not wall_id else ''
            problems.append(f'开洞 #{oi}（{o.get("id", "")}）的 wall {wall_id!r} 不存在{hint}；现有墙：{ids[:20]}')
            continue
        kind = str(o.get('kind', 'window'))
        if kind not in ('door', 'window', 'void'):
            problems.append(f'开洞 #{oi} 的 kind 只能是 door / window / void，现在是 {kind!r}')
        seg = seg_by_id[wall_id]
        w_ = _num(o.get('width', 900 if kind == 'door' else 1500), f'开洞 #{oi} width', problems)
        h_ = _num(o.get('height', 2100 if kind == 'door' else 1500), f'开洞 #{oi} height', problems)
        sill = float(o.get('sill', 0 if kind == 'door' else 900))
        rep = o.get('repeat')
        if rep:
            count = int(rep.get('count', 1))
            step = float(rep.get('step', w_ + 600))
            start = float(rep.get('start', o.get('at', 0)))
            if rep.get('center'):  # spread evenly, centred on the wall
                span = (count - 1) * step + w_
                start = (seg.length - span) / 2
            ats = [start + k * step for k in range(count)]
        else:
            if 'at' not in o and o.get('center'):
                ats = [(seg.length - w_) / 2]
            else:
                ats = [float(o.get('at', (seg.length - w_) / 2))]
        for k, at in enumerate(ats):
            auto += 1
            oid = str(o.get('id') or f'{"D" if kind == "door" else "C"}{auto}')
            if len(ats) > 1:
                oid = f'{oid}_{k + 1}'
            typ = str(o.get('type') or ('double' if kind == 'door' and w_ >= 1500 else 'single' if kind == 'door' else 'casement'))
            okt = {'door': ('single', 'double', 'sliding', 'mother'), 'window': ('casement', 'fixed', 'sliding', 'strip'), 'void': ('', 'casement')}
            if typ not in okt.get(kind, ()) and kind != 'void':
                problems.append(f'开洞 {oid} 的 type {typ!r} 不认识；门：single/double/sliding/mother，窗：casement/fixed/sliding/strip')
            code = str(o.get('code') or (('M' if kind == 'door' else 'C') + f'{round(w_ / 100):02d}{round(h_ / 100):02d}' if kind != 'void' else ''))
            openings.append(Opening(oid, wall_id, kind, at, w_, h_, sill, bool(o.get('frame', kind != 'void')), typ, code))

    # validate openings per wall (curved walls: `at` is measured along the arc)
    for seg in segments + arcs:
        ops = sorted([o for o in openings if o.wall == seg.id], key=lambda o: o.at)
        for o in ops:
            margin = seg.thickness / 2 if seg.exterior else 0
            if o.at < margin - 1 or o.at + o.width > seg.length - margin + 1:
                problems.append(f'开洞 {o.id} 超出墙 {seg.id}：墙长 {seg.length:.0f}，洞口范围 {o.at:.0f}～{o.at + o.width:.0f}'
                                f'（外墙两端要各留 {margin:.0f} 墙角）')
            if o.sill + o.height > seg.height + 1:
                problems.append(f'开洞 {o.id} 顶高 {o.sill + o.height:.0f} 超过墙高 {seg.height:.0f}')
            if o.sill < 0:
                problems.append(f'开洞 {o.id} 的 sill 不能为负')
            if seg.height - (o.sill + o.height) < 150 and o.sill + o.height < seg.height - 1:
                warnings.append(f'开洞 {o.id} 上方只剩 {seg.height - o.sill - o.height:.0f} mm 墙，可能显得很薄')
        for o1, o2 in zip(ops, ops[1:]):
            if o2.at < o1.at + o1.width + 100:
                problems.append(f'墙 {seg.id} 上 {o1.id} 和 {o2.id} 重叠或间距不足 100 mm')
    oids = [o.id for o in openings]
    if len(set(oids)) != len(oids):
        problems.append(f'开洞编号重复：{sorted({x for x in oids if oids.count(x) > 1})}')

    # ---- curved walls -> straight chords; each opening becomes exactly one chord
    for aw in arcs:
        ops = [o for o in openings if o.wall == aw.id]
        brk = aw.breaks(ops)
        R = aw.arc[2]
        spans = list(zip(brk, brk[1:]))
        angs = [(b - a) / R for a, b in spans]
        for k, (u0, u1) in enumerate(spans):
            a, b = aw.point_at(u0), aw.point_at(u1)
            # close the joints between neighbouring chords: extend by t/2 * tan(deflection/2)
            es = aw.ext_start if k == 0 else aw.thickness / 2 * math.tan((angs[k - 1] + angs[k]) / 4) + 0.5
            ee = aw.ext_end if k == len(spans) - 1 else aw.thickness / 2 * math.tan((angs[k] + angs[k + 1]) / 4) + 0.5
            seg = Segment(f'{aw.id}.a{k}', aw.level, a, b, aw.thickness, aw.height, aw.base_z, es, ee,
                          aw.exterior, aw.material, aw.id, getattr(aw, 'kind', 'wall'))
            aw.chords.append(seg)
            segments.append(seg)
            for o in ops:
                if abs(o.at - u0) < 0.6:
                    sg = curve.sag(R, o.width)
                    if sg > aw.thickness / 2:
                        warnings.append(f'{o.id} 在弧墙 {aw.id} 上太宽：平直的窗/门会凸出墙面 {sg:.0f} mm，建议拆成几扇窄的')
                    o.wall, o.at, o.width = seg.id, 0.0, seg.length
    seg_by_id = {s.id: s for s in segments}

    # ---- slabs: one per level under the outline (covers the walls), plus extras
    slabs = []
    if outline and raw.get('floor', True):
        floor_poly = offset_polygon(outline_dense, wall_t / 2)
        for lv in levels:
            if lv.id in outline_levels:
                slabs.append({'id': f'SLAB_{lv.id}', 'polygon': floor_poly, 'top_z': lv.elevation,
                              'thickness': slab_t, 'material': mat(raw.get('floor_material', 'floor'), 'floor')})
    for si, s in enumerate(raw.get('slabs') or []):
        lid = s.get('level', levels[0].id)
        top = float(s.get('top_z', lvl[lid].elevation if lid in lvl else 0))
        slabs.append({'id': str(s.get('id', f'SLAB_X{si + 1}')),
                      'polygon': _curve_poly(s.get('polygon'), f'楼板 {si} polygon', problems),
                      'top_z': top, 'thickness': _num(s.get('thickness', slab_t), f'楼板 {si} thickness', problems),
                      'material': mat(s.get('material', 'floor'), f'楼板 {si}')})

    # ---- roof
    roof = None
    r = raw.get('roof')
    if r:
        kind = str(r.get('kind', 'flat'))
        if kind not in ('flat', 'gable', 'hip', 'shed') + curve.SURFACE_KINDS:
            problems.append(f'roof.kind 只能是 flat / gable / hip / shed / vault（筒拱）/ wave（波浪）/ dome（穹顶）/ surface（控制点自由曲面），现在是 {kind!r}')
        base_poly = _curve_poly(r['footprint'], 'roof.footprint', problems) if r.get('footprint') else outline_dense
        if not base_poly:
            problems.append('有 roof 但没有 outline 或 roof.footprint')
        else:
            over = float(r.get('overhang', 300))
            fp = offset_polygon(base_poly, (wall_t / 2 if not r.get('footprint') else 0) + over)
            top_level = max(levels, key=lambda l: l.elevation + l.height)
            thick = _num(r.get('thickness', 200), 'roof.thickness', problems)
            pitch = float(r.get('pitch', 25))
            xs, ys = [p[0] for p in fp], [p[1] for p in fp]
            span = min(max(xs) - min(xs), max(ys) - min(ys))
            rise = 0 if kind == 'flat' else (span / 2 if kind != 'shed' else span) * math.tan(math.radians(pitch))
            surface = None
            if kind in curve.SURFACE_KINDS:
                heights = None
                if kind == 'surface':
                    hs = r.get('heights')
                    ok_ = (isinstance(hs, list) and len(hs) >= 2 and all(isinstance(row, list) for row in hs)
                           and len({len(row) for row in hs}) == 1 and len(hs[0]) >= 2)
                    if not ok_:
                        problems.append('roof.kind = surface 要写 heights：至少 2×2 的高度表（mm，相对墙顶），'
                                        '每行一样长；行从南到北，列从西到东，均匀铺满屋顶外包框')
                        heights = [[0, 0], [0, 0]]
                    else:
                        try:
                            heights = [[float(v) for v in row] for row in hs]
                        except (TypeError, ValueError):
                            problems.append('roof.heights 里只能写数字（mm）')
                            heights = [[0, 0], [0, 0]]
                    if min(min(row) for row in heights) < 0:
                        warnings.append('roof.heights 有负数：低于墙顶的部分会按 0（贴墙顶）处理')
                    rise = max(1.0, max(max(row) for row in heights))
                else:
                    rise = _num(r.get('rise', {'vault': span * 0.25, 'wave': 1200, 'dome': span * 0.3}[kind]), 'roof.rise', problems)
                axis = r.get('axis')
                if axis not in (None, 'x', 'y'):
                    problems.append("roof.axis 只能是 'x' 或 'y'（筒拱/波浪的走向）")
                wl = r.get('wavelength')
                shape = r.get('shape')
                if shape not in (None, 'round', 'pillow'):
                    problems.append("roof.shape 只能是 'round'（圆/椭圆穹顶）或 'pillow'（矩形枕形穹顶）；不写会按平面自动选")
                surface = {'axis': axis, 'wavelength': float(wl) if wl else None, 'phase': float(r.get('phase', 0)),
                           'cell': float(r['mesh']) if r.get('mesh') else None, 'shape': shape,
                           'heights': heights}
            roof = {'id': 'ROOF', 'kind': kind, 'footprint': fp, 'base_z': top_level.elevation + top_level.height,
                    'thickness': thick, 'pitch': pitch, 'rise': rise, 'surface': surface,
                    'material': mat(r.get('material', 'roof'), 'roof')}
            if kind in ('gable', 'hip') and len(fp) != 4:
                warnings.append('坡屋顶按外包矩形近似生成；非矩形平面建议用 flat 或自己给 roof.footprint')

    # ---- boxes (furniture / massing)
    boxes = []
    for bi, b in enumerate(raw.get('boxes') or []):
        bid = str(b.get('id') or f'BOX{bi + 1}')
        at = b.get('at', [0, 0, 0])
        size = b.get('size')
        if b.get('asset'):
            problems.append(f"Box {bid!r}: use name and size instead of an asset library ID.")
            continue
        if not (isinstance(size, list) and len(size) == 3):
            problems.append(f'体块 {bid} 的 size 要写 [长x, 宽y, 高z]')
            continue
        for k, v in enumerate(size):
            _num(v, f'体块 {bid} size[{k}]', problems)
        lid = b.get('level')
        z0 = float(at[2]) if len(at) > 2 else 0.0
        if lid in lvl:
            z0 += lvl[lid].elevation
        rep = b.get('repeat')
        positions = [(float(at[0]), float(at[1]), z0)]
        if rep:
            dx, dy = (rep.get('step') or [0, 0])[:2]
            positions = [(float(at[0]) + k * dx, float(at[1]) + k * dy, z0) for k in range(int(rep.get('count', 1)))]
        for k, pos in enumerate(positions):
            boxes.append(Box(bid if len(positions) == 1 else f'{bid}_{k + 1}', str(b.get('name', bid)), pos,
                             tuple(float(v) for v in size), float(b.get('rotation', 0)),
                             mat(b.get('material', 'wood'), f'Box {bid}')))
    bids = [b.id for b in boxes]
    if len(set(bids)) != len(bids):
        problems.append(f'体块编号重复：{sorted({x for x in bids if bids.count(x) > 1})}')

    # boxes vs walls (plan check, axis-aligned boxes only)
    for b in boxes:
        fp = b.footprint()
        x0, y0 = min(p[0] for p in fp), min(p[1] for p in fp)
        x1, y1 = max(p[0] for p in fp), max(p[1] for p in fp)
        for s in segments:
            if b.at[2] >= s.base_z + s.height or b.at[2] + b.size[2] <= s.base_z:
                continue  # different storey
            if _segment_hits_rect(s.built_a, s.built_b, s.thickness / 2, (x0, y0, x1, y1)):
                warnings.append(f'体块 {b.id}（{b.name}）和墙 {s.id} 在平面上相交，可能穿墙')
                break

    # furniture vs door swings, and furniture vs furniture (2026-10-27)
    ext_pts = [s.a for s in segments if s.exterior]
    for o in openings:
        if o.kind != 'door':
            continue
        s = seg_by_id[o.wall]
        sign = 1
        if s.exterior and ext_pts:
            cx, cy = sum(p[0] for p in ext_pts) / len(ext_pts), sum(p[1] for p in ext_pts) / len(ext_pts)
            mx, my = s.point_at(s.length / 2)
            ux, uy = s.unit
            sign = 1 if ((-uy) * (cx - mx) + ux * (cy - my)) > 0 else -1
        ux, uy = s.unit
        h = s.thickness / 2
        loc = lambda u, v: (s.a[0] + ux * u - uy * v, s.a[1] + uy * u + ux * v)
        zone = [loc(o.at, sign * h), loc(o.at + o.width, sign * h),
                loc(o.at + o.width, sign * (h + o.width)), loc(o.at, sign * (h + o.width))]
        for b in boxes:
            if b.at[2] - s.base_z < o.height and _quads_overlap(zone, b.footprint()):
                warnings.append(f'{b.name}（{b.id}）挡住了门 {o.id} 的开启范围，建议挪开')
    for i, b1 in enumerate(boxes):
        for b2 in boxes[i + 1:]:
            if b1.at[2] >= b2.at[2] + b2.size[2] or b2.at[2] >= b1.at[2] + b1.size[2]:
                continue  # different heights (e.g. a pendant lamp over a table)
            if _quads_overlap(b1.footprint(), b2.footprint(), shrink=20):
                warnings.append(f'家具 {b1.name}（{b1.id}）和 {b2.name}（{b2.id}）重叠')

    # ---- rooms (labels + area)
    rooms = []
    for ri, rm in enumerate(raw.get('rooms') or []):
        poly = _curve_poly(rm.get('polygon'), f'房间 {rm.get("name", ri)} polygon', problems)
        if poly:
            rooms.append({'name': str(rm.get('name', f'房间{ri + 1}')), 'level': rm.get('level', levels[0].id),
                          'polygon': poly, 'area_m2': round(polygon_area(poly) / 1e6, 1)})
    # 房间里套着别的房间（如大厅里的下沉阅读区）：面积扣掉里面那块，避免重复计算
    for r1 in rooms:
        inner = [r2 for r2 in rooms if r2 is not r1 and r2['level'] == r1['level']
                 and polygon_area(r2['polygon']) < polygon_area(r1['polygon'])
                 and all(_inside(p, r1['polygon']) for p in r2['polygon'])]
        if inner:
            net = polygon_area(r1['polygon']) - sum(polygon_area(r2['polygon']) for r2 in inner)
            r1['area_m2'] = round(net / 1e6, 2)
            r1['contains'] = [r2['name'] for r2 in inner]

    # ---- columns / stairs / site / grid / sections / drawing (CAD 用；2026-11-19)
    columns = []
    for ci, c in enumerate(raw.get('columns') or []):
        cid = str(c.get('id') or f'Z{ci + 1}')
        pts_ = c.get('at')
        lid = str(c.get('level', levels[0].id))
        rep_ = c.get('repeat')
        base_pts = [pts_] if pts_ and not isinstance(pts_[0], (list, tuple)) else (pts_ or [])
        if rep_:
            dx, dy = (rep_.get('step') or [0, 0])[:2]
            base_pts = [[base_pts[0][0] + k * dx, base_pts[0][1] + k * dy] for k in range(int(rep_.get('count', 1)))]
        for k, pt in enumerate(base_pts):
            if not (isinstance(pt, (list, tuple)) and len(pt) >= 2):
                problems.append(f'柱 {cid} 的 at 要写 [x,y] 或 [[x,y],...]')
                continue
            d = c.get('dia')
            columns.append({'id': cid if len(base_pts) == 1 else f'{cid}_{k + 1}', 'level': lid, 'at': (float(pt[0]), float(pt[1])),
                            'shape': 'round' if d else 'rect', 'dia': float(d) if d else None,
                            'size': tuple(float(v) for v in (c.get('size') or [400, 400]))[:2], 'rotation': float(c.get('rotation', 0)),
                            'height': float(c.get('height', lvl[lid].height if lid in lvl else wall_h)),
                            'base_z': lvl[lid].elevation if lid in lvl else 0.0, 'material': mat(c.get('material', 'wall'), f'柱 {cid}')})
    stairs = []
    for si_, st in enumerate(raw.get('stairs') or []):
        kind_ = str(st.get('kind', 'stair'))
        sid = str(st.get('id') or f'{"LT" if kind_ == "stair" else "PD"}{si_ + 1}')
        if kind_ not in ('stair', 'ramp', 'steps'):
            problems.append(f'楼梯 {sid} 的 kind 只能是 stair（楼梯）/ ramp（坡道）/ steps（室外台阶）')
            continue
        if not (isinstance(st.get('at'), (list, tuple)) and len(st['at']) >= 2):
            problems.append(f'楼梯 {sid} 要写 at [x,y]（梯段起步线中点）')
            continue
        lid = str(st.get('level', levels[0].id))
        rise = float(st.get('rise', lvl[lid].height if (lid in lvl and kind_ == 'stair') else 450))
        width = _num(st.get('width', 1200 if kind_ != 'steps' else 3000), f'楼梯 {sid} width', problems)
        if kind_ == 'stair':
            steps = int(st.get('steps', max(2, round(rise / 160))))
            tread = float(st.get('tread', 280))
            length = float(st.get('length', (steps - 1) * tread))
            if rise / steps > 175 + 1:
                warnings.append(f'楼梯 {sid} 踏步高 {rise / steps:.0f} mm 偏高（公共建筑宜 ≤ 150～175）')
        elif kind_ == 'ramp':
            steps = 0
            slope = float(st.get('slope', 12))      # 1:12
            length = float(st.get('length', rise * slope))
            if length / max(rise, 1) < 12 - 0.01:
                warnings.append(f'坡道 {sid} 坡度陡于 1:12，无障碍坡道不合规')
        else:
            steps = int(st.get('steps', max(1, round(rise / 150))))
            length = float(st.get('length', steps * 300))
        stairs.append({'id': sid, 'level': lid, 'kind': kind_, 'at': (float(st['at'][0]), float(st['at'][1])),
                       'dir': float(st.get('dir', 90)), 'width': width, 'length': length, 'steps': steps, 'rise': rise,
                       'label': str(st.get('label', '上' if kind_ != 'steps' else '')), 'up': bool(st.get('up', True))})
    sraw = raw.get('site') or {}
    site = {'ground_z': float(sraw.get('ground_z', -300)), 'apron': float(sraw.get('apron', 600)),
            'paving': float(sraw.get('paving', 3000)), 'entrance_steps': bool(sraw.get('entrance_steps', True)),
            'boundary': _curve_poly(sraw['boundary'], 'site.boundary', problems) if sraw.get('boundary') else None,
            'north': float(sraw.get('north', 90)), 'enabled': bool(sraw.get('enabled', True))}
    grid = None
    if raw.get('grid'):
        g = raw['grid']
        grid = {'x': [float(v) for v in g.get('x', [])], 'y': [float(v) for v in g.get('y', [])]}
    sections = []
    for k, sc in enumerate(raw.get('sections') or []):
        try:
            sections.append({'id': str(sc.get('id', k + 1)), 'a': tuple(map(float, sc['from'][:2])), 'b': tuple(map(float, sc['to'][:2])),
                             'look': str(sc.get('look', 'left'))})
        except (KeyError, TypeError, ValueError):
            problems.append(f'剖切符号 #{k} 要写 {{"id":"1","from":[x,y],"to":[x,y],"look":"left|right"}}')
    draw = dict({'scale': 100, 'style': 'construction', 'paper': 'auto'}, **(raw.get('drawing') or {}))
    if draw['style'] not in ('construction', 'presentation'):
        problems.append("drawing.style 只能是 construction（施工图，黑白）或 presentation（展板，淡彩）")
    for rm in rooms:
        src = next((x for x in (raw.get('rooms') or []) if str(x.get('name')) == rm['name']), {})
        lvz = lvl[rm['level']].elevation if rm['level'] in lvl else 0.0
        rm['elevation'] = float(src.get('elevation', lvz))
        rm['outdoor'] = bool(src.get('outdoor', False))
        rm['floor'] = str(src.get('floor', ''))

    views = raw.get('views') or ['iso', 'iso_open', 'eye', 'front', 'plan']
    for v in views:
        if v not in VIEW_NAMES:
            problems.append(f'views 里的 {v!r} 不认识，可用：{", ".join(VIEW_NAMES)}')

    if not segments and not boxes and not slabs:
        problems.append('规格里什么都没有：至少给 outline、walls 或 boxes 之一')
    # 柱子压到门窗洞口：提醒（2026-11-20）
    seg_by = {s.id: s for s in segments}
    for c in columns:
        if c.get('dia'):
            hx = hy = c['dia'] / 2
        else:
            hx, hy = c['size'][0] / 2, c['size'][1] / 2
        cx, cy = c['at']
        for o in openings:
            s = seg_by.get(o.wall)
            if not s or s.level != c['level']:
                continue
            dx, dy = s.b[0] - s.a[0], s.b[1] - s.a[1]
            L = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux, uy = dx / L, dy / L
            u = (cx - s.a[0]) * ux + (cy - s.a[1]) * uy
            v = abs(-(cx - s.a[0]) * uy + (cy - s.a[1]) * ux)
            ru = abs(ux) * hx + abs(uy) * hy        # 柱沿墙方向的半宽
            rv = abs(uy) * hx + abs(ux) * hy
            if v - rv < s.thickness / 2 and u + ru > o.at + 1 and u - ru < o.at + o.width - 1:
                warnings.append(f'柱 {c["id"]} 压到了 {o.id}（{s.id} 上 {o.at:.0f}～{o.at + o.width:.0f}），挪一下门窗或柱')

    if problems:
        raise SpecError(problems)

    return Model(name, out_dir, levels, segments, openings, slabs, roof, boxes, rooms, materials, views,
                 raw.get('board') or {}, warnings, raw, arcs, columns, stairs, site, grid, sections, draw)


VIEW_NAMES = ('iso', 'iso_open', 'inside', 'iso_back', 'front', 'back', 'left', 'right', 'plan', 'eye')


def _quads_overlap(p, q, shrink=0.0):
    """Separating-axis test for two convex quads; shrink (mm) ignores mere touching."""
    def shrunk(poly):
        if not shrink:
            return poly
        cx, cy = sum(x for x, _ in poly) / 4, sum(y for _, y in poly) / 4
        out = []
        for x, y in poly:
            dx, dy = x - cx, y - cy
            L = math.hypot(dx, dy) or 1
            out.append((x - dx / L * shrink, y - dy / L * shrink))
        return out
    p, q = shrunk(p), shrunk(q)
    for poly in (p, q):
        for i in range(len(poly)):
            x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = y2 - y1, x1 - x2
            a = [nx * x + ny * y for x, y in p]
            b = [nx * x + ny * y for x, y in q]
            if max(a) < min(b) or max(b) < min(a):
                return False
    return True


def _segment_hits_rect(a, b, half_t, rect, steps=40):
    x0, y0, x1, y1 = rect
    x0 += 1; y0 += 1; x1 -= 1; y1 -= 1
    for k in range(steps + 1):
        t = k / steps
        px, py = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        if x0 - half_t < px < x1 + half_t and y0 - half_t < py < y1 + half_t:
            return True
    return False


def summary(m: Model) -> dict:
    (x0, y0, _), (x1, y1, z1) = m.bounds()
    return {
        'name': m.name, 'out_dir': str(m.out_dir),
        'size_mm': [round(x1 - x0), round(y1 - y0), round(z1)],
        'levels': [lv.id for lv in m.levels], 'walls': len([s for s in m.segments if not s.arc]) + len(m.arcs),
        'curved_walls': [f'{a.id} R{a.arc[2]:.0f} {abs(math.degrees(a.arc[4])):.0f}°（{len(a.chords)} 段）' for a in m.arcs],
        'openings': len(m.openings),
        'doors': sum(o.kind == 'door' for o in m.openings), 'windows': sum(o.kind == 'window' for o in m.openings),
        'slabs': len(m.slabs), 'roof': m.roof['kind'] if m.roof else None, 'boxes': len(m.boxes),
        'rooms': [(r['name'], r['area_m2']) for r in m.rooms],
        'warnings': m.warnings,
    }
