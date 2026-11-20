"""Build a spec in SketchUp through the bridge, save it, reopen it, verify it, capture views.

Safety: only ever builds in a NEW model it creates itself. If the open model has
unsaved changes it stops. Never overwrites an
existing .skp: a new revision name is chosen.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from .bridge import RPC
from ..spec import Model
from .. import curve

TAGS = {'wall': 'WALL', 'opening': 'OPENING', 'slab': 'SLAB', 'roof': 'ROOF', 'box': 'FURN'}


class BuildError(RuntimeError):
    pass


def _res(r):
    """Bridge returns domain errors inside result; surface them."""
    res = r.get('result', r)
    if isinstance(res, dict) and res.get('error'):
        raise BuildError(str(res['error']))
    return res


def next_revision(path: Path) -> Path:
    if not path.exists():
        return path
    k = 2
    while True:
        p = path.with_name(f'{path.stem}_v{k}{path.suffix}')
        if not p.exists():
            return p
        k += 1


def cameras(m: Model):
    (x0, y0, _), (x1, y1, z1) = m.bounds()
    cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, z1 * 0.4
    size = max(x1 - x0, y1 - y0, z1, 1000)
    dist = size * 1.7
    persp = lambda eye, tgt, fov=35: {'eye_mm': eye, 'target_mm': tgt, 'up': [0, 0, 1], 'perspective': True, 'fov_deg': fov}
    ortho = lambda eye, tgt, up, h: {'eye_mm': eye, 'target_mm': tgt, 'up': up, 'perspective': False, 'height_mm': h}
    return {
        'iso': persp([cx - dist * 0.75, cy - dist * 0.95, cz + dist * 0.6], [cx, cy, cz]),
        'iso_open': persp([cx - dist * 0.5, cy - dist * 0.62, cz + dist * 0.62], [cx, cy, 0], 40),
        'inside': persp([x0 + (x1 - x0) * 0.12, y0 + (y1 - y0) * 0.16, 1600], [x0 + (x1 - x0) * 0.7, y0 + (y1 - y0) * 0.75, 1300], 62),
        'iso_back': persp([cx + dist * 0.8, cy + dist * 0.9, cz + dist * 0.55], [cx, cy, cz]),
        'eye': persp([cx - (x1 - x0) * 0.2, y0 - size * 0.9, 1600], [cx, cy, 1600 + z1 * 0.15], 50),
        'front': ortho([cx, y0 - dist * 2, z1 / 2], [cx, cy, z1 / 2], [0, 0, 1], max(z1, (x1 - x0) * 0.62) * 1.25),
        'back': ortho([cx, y1 + dist * 2, z1 / 2], [cx, cy, z1 / 2], [0, 0, 1], max(z1, (x1 - x0) * 0.62) * 1.25),
        'left': ortho([x0 - dist * 2, cy, z1 / 2], [cx, cy, z1 / 2], [0, 0, 1], max(z1, (y1 - y0) * 0.62) * 1.25),
        'right': ortho([x1 + dist * 2, cy, z1 / 2], [cx, cy, z1 / 2], [0, 0, 1], max(z1, (y1 - y0) * 0.62) * 1.25),
        'plan': ortho([cx, cy, z1 + dist * 3], [cx, cy, 0], [0, 1, 0], max(y1 - y0, (x1 - x0) * 0.72) * 1.12),
    }


STYLE_RUBY = '''
m = Sketchup.active_model
ro = m.rendering_options
{"DisplayProfiles" => true, "EdgeColorMode" => 0, "DisplaySky" => true,
 "ForegroundColor" => Sketchup::Color.new(60, 60, 60)}.each { |k, v| (ro[k] = v) rescue nil }
si = m.shadow_info
{"DisplayShadows" => %s, "UseSunForAllShading" => true, "Light" => 85, "Dark" => 50,
 "ShadowTime" => Time.new(2026, 6, 21, 12, 30, 0, "+08:00")}.each { |k, v| (si[k] = v) rescue nil }
true
'''


def build(m: Model, *, views=True, width=1800, height=1200, shadows=True, log=print):
    t0 = time.perf_counter()
    out = m.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / 'views').mkdir(exist_ok=True)
    (out / 'receipts').mkdir(exist_ok=True)
    report = {'steps': [], 'files': {}, 'warnings': list(m.warnings)}

    def step(name, fn):
        s = time.perf_counter()
        r = fn()
        report['steps'].append({'step': name, 'seconds': round(time.perf_counter() - s, 2)})
        log(f'  [SU] {name} ({time.perf_counter() - s:.1f}s)')
        return r

    with RPC() as c:
        info = _res(c.call('lifecycle.model_info'))
        if info.get('modified'):
            raise BuildError(f'SketchUp 当前模型「{info.get("title")}」有未保存的改动，为安全起见没有新建。'
                             '请先在 SketchUp 里保存或关闭它。')
        step('新建空白模型', lambda: _res(c.call('lifecycle.new', {})))
        time.sleep(0.5)
        # Fresh model made by us: clear template people etc.
        c.ruby('m=Sketchup.active_model; m.entities.to_a.each { |e| e.erase! if e.valid? && '
               '(e.is_a?(Sketchup::ComponentInstance) || e.is_a?(Sketchup::Group)) }; WishSuiteBridge::AI_ID.reset; true')

        def mats():
            for key, hexv in m.materials.items():
                p = {'id': f'MAT_{key}', 'color_hex': hexv}
                if key == 'glass':
                    p['alpha'] = 0.45
                _res(c.call('ops.material.upsert', p))
            for t in TAGS.values():
                _res(c.call('ops.layer.upsert', {'id': t}))
        step('材质和标签', mats)

        def walls():
            for s in m.segments:
                _res(c.call('ops.wall.create', {
                    'ai_id': s.id, 'centerline': [list(s.built_a), list(s.built_b)],
                    'thickness_mm': s.thickness, 'height_mm': s.height, 'base_z_mm': s.base_z, 'level': s.level}))
        step(f'墙 {len(m.segments)} 段', walls)

        def openings():
            for s in m.segments:
                ops = [o for o in m.openings if o.wall == s.id]
                if not ops:
                    continue
                _res(c.call('ops.opening.batch_cut', {'wall_id': s.id, 'openings': [{
                    'ai_id': o.id, 'offset_mm': o.at + s.ext_start, 'width_mm': o.width, 'height_mm': o.height,
                    'sill_mm': o.sill, 'kind': o.kind,
                    'generate_frame': (o.kind if o.frame and o.kind in ('door', 'window') else None)} for o in ops]}))
        if m.openings:
            step(f'门窗 {len(m.openings)} 个', openings)
        if m.arcs:
            step('弧墙接缝柔化', lambda: _soften_arc_joints(c, m))

        def slabs():
            for sl in m.slabs:
                _res(c.call('ops.slab.create', {'ai_id': sl['id'], 'polygon': [list(p) for p in sl['polygon']],
                                                'thickness_mm': sl['thickness'], 'base_z_mm': sl['top_z'], 'top_or_bottom': 'top'}))
        if m.slabs:
            step(f'楼板 {len(m.slabs)} 块', slabs)

        fills = []  # infill solids between flat wall tops and a curved roof
        if m.roof and m.roof['kind'] in curve.SURFACE_KINDS:
            r = m.roof
            s_ = r.get('surface') or {}
            f_roof, _ = curve.height_fn(r['kind'], r['footprint'], r['rise'], s_.get('axis'), s_.get('wavelength'),
                                        s_.get('phase', 0.0), s_.get('shape'), s_.get('heights'))
            info_r = step(f'曲面屋顶（{r["kind"]}）', lambda: _surface_roof(c, r, f_roof))
            report['roof_mesh'] = info_r
            if abs(info_r['area_ratio'] - 1) > 0.01:
                report['warnings'].append(f'曲面屋顶网格面积和轮廓面积相差 {abs(info_r["area_ratio"] - 1) * 100:.1f}%，'
                                          '轮廓可能有自交或过尖的角，请检查屋顶')
            if (m.raw.get('roof') or {}).get('fill_walls', True):
                fills = _wall_fills(m, f_roof, info_r['cell_mm'])
                if fills:
                    def do_fills():
                        _build_fills(c, fills)
                        _soften_arc_joints(c, m, '__fill', {fl['id'] for fl in fills})
                    step(f'墙顶补到屋顶 {len(fills)} 段', do_fills)
        elif m.roof:
            r = m.roof
            step('屋顶', lambda: _res(c.call('ops.roof.create', {
                'ai_id': r['id'], 'kind': r['kind'], 'footprint': [list(p) for p in r['footprint']],
                'pitch_deg': r['pitch'], 'base_z_mm': r['base_z'],
                'ridge_height_mm': r['thickness'] if r['kind'] == 'flat' else 0})))

        def boxes():
            for b in m.boxes:
                _res(c.call('ops.primitive.create', {
                    'ai_id': b.id, 'kind': 'box',
                    'dimensions': {'width_mm': b.size[0], 'depth_mm': b.size[1], 'height_mm': b.size[2]},
                    'transform': {'position_mm': list(b.origin()), 'rotation_deg': [0, 0, b.rotation]}}))
        if m.boxes:
            step(f'体块 {len(m.boxes)} 个', boxes)

        def columns():  # 2026-11-19: 柱（方柱 box / 圆柱 cylinder）
            for col in m.columns:
                x, y = col['at']
                if col['shape'] == 'round':
                    _res(c.call('ops.primitive.create', {
                        'ai_id': col['id'], 'kind': 'cylinder',
                        'dimensions': {'radius_mm': col['dia'] / 2, 'height_mm': col['height'], 'segments': 32},
                        'transform': {'position_mm': [x, y, col['base_z']], 'rotation_deg': [0, 0, 0]}}))
                else:
                    w, d = col['size']
                    _res(c.call('ops.primitive.create', {
                        'ai_id': col['id'], 'kind': 'box',
                        'dimensions': {'width_mm': w, 'depth_mm': d, 'height_mm': col['height']},
                        'transform': {'position_mm': [x - w / 2, y - d / 2, col['base_z']], 'rotation_deg': [0, 0, col['rotation']]}}))
        if getattr(m, 'columns', None):
            step(f'柱 {len(m.columns)} 根', columns)

        def paint():
            groups = {}
            for s in m.segments:
                groups.setdefault(s.material, []).append(s.id)
            for fl in fills:
                groups.setdefault(fl['material'], []).append(fl['id'])
            for sl in m.slabs:
                groups.setdefault(sl['material'], []).append(sl['id'])
            if m.roof:
                groups.setdefault(m.roof['material'], []).append(m.roof['id'])
            for b in m.boxes:
                groups.setdefault(b.material, []).append(b.id)
            for col in getattr(m, 'columns', []):
                groups.setdefault(col['material'], []).append(col['id'])
            frames = [f'{o.id}__frame' for o in m.openings if o.frame and o.kind in ('door', 'window')]
            if frames:
                groups.setdefault('dark', []).extend(frames)
            for key, ids in groups.items():
                _res(c.call('ops.material.assign', {'material_id': f'MAT_{key}', 'target_ids': ids}))
            _res(c.call('ops.layer.assign', {'layer_id': TAGS['slab'], 'target_ids': [s['id'] for s in m.slabs]}))
            if m.roof:
                _res(c.call('ops.layer.assign', {'layer_id': TAGS['roof'], 'target_ids': [m.roof['id']]}))
            _res(c.call('ops.layer.assign', {'layer_id': TAGS['box'], 'target_ids': [b.id for b in m.boxes]}))
            _res(c.call('ops.layer.assign', {'layer_id': TAGS['opening'], 'target_ids': frames}))
            if fills:
                _res(c.call('ops.layer.assign', {'layer_id': TAGS['wall'], 'target_ids': [fl['id'] for fl in fills]}))
        step('上材质、分标签', paint)

        expected = [s.id for s in m.segments] + [o.id for o in m.openings] + [s['id'] for s in m.slabs] \
            + ([m.roof['id']] if m.roof else []) + [b.id for b in m.boxes] + [fl['id'] for fl in fills] \
            + [col['id'] for col in getattr(m, 'columns', [])]
        v = _res(c.call('query.verify', {'expected_ids': expected}))
        missing = v.get('missing', [])
        report['verify_before_save'] = {'expected': len(expected), 'missing': missing}
        if missing:
            raise BuildError(f'建模后缺少这些对象：{missing}')

        step('显示样式', lambda: c.ruby(STYLE_RUBY % ('true' if shadows else 'false')))

        # Views first, so the camera change is part of the save and the
        # model ends up unmodified after the reopen check.
        if views:
            cams = cameras(m)

            def shots():
                files = {}
                for name in m.views:
                    p = out / 'views' / f'{name}.png'
                    no_roof = name in ('plan', 'iso_open') and m.roof
                    if no_roof:  # look inside: hide the roof tag for this shot only
                        c.ruby('l=Sketchup.active_model.layers["ROOF"]; l.visible=false if l; true')
                    try:
                        _res(c.call('quality.capture', {'path': p.as_posix(), 'camera': cams[name],
                                                        'width': width, 'height': height}))
                    finally:
                        if no_roof:
                            c.ruby('l=Sketchup.active_model.layers["ROOF"]; l.visible=true if l; true')
                    files[name] = str(p)
                return files
            report['files']['views'] = step(f'出图 {len(m.views)} 张', shots)

        # Our own fresh model: purging unused items is safe and avoids the
        # hidden "清除未使用项？" save prompt.
        c.ruby('m=Sketchup.active_model; m.definitions.purge_unused; m.materials.purge_unused; '
               'm.layers.purge_unused; m.styles.purge_unused if m.styles.respond_to?(:purge_unused); true')

        skp = next_revision(out / f'{_safe(m.name)}.skp')

        def save():
            job = _res(c.call('quality.save.start', {'path': skp.as_posix(), 'output_dir': (out / 'receipts').as_posix()}))
            done = c.wait_job(job, timeout=180)
            if done.get('status') != 'saved_requires_reopen_validation':
                raise BuildError(f'保存没有完成：{done.get("status")} {done.get("error", "")}；报告 {job.get("report")}')
            return done
        saved = step('保存', save)
        report['files']['skp'] = str(skp)
        report['save'] = {k: saved.get(k) for k in ('status', 'bytes', 'sha256')}

        # Reopen from disk and check the objects really are in the file.
        def reopen():
            _res(c.call('lifecycle.new', {}))
            time.sleep(0.5)
            _res(c.call('lifecycle.open', {'path': skp.as_posix()}))
            time.sleep(1.0)
            vv = _res(c.call('query.verify', {'expected_ids': expected}))
            if vv.get('missing'):
                raise BuildError(f'重新打开后缺少：{vv["missing"]}')
            return _res(c.call('query.scene_summary'))
        summ = step('重新打开核对', reopen)
        report['reopened'] = {'title': summ.get('title'), 'ai_entity_total': summ.get('ai_entity_total'),
                              'counts': summ.get('ai_entity_counts')}


    backup = skp.with_suffix('.skb')
    if backup.is_file() and backup.stat().st_size == 0:
        backup.unlink()

    report['seconds'] = round(time.perf_counter() - t0, 1)
    return report


MESH_RUBY = '''
m = Sketchup.active_model
m.start_operation('Create mesh groups', true)
res = [%(items)s].map do |gid, ps, ts, attrs|
pts = ps.unpack1('m').unpack('e*')
tri = ts.unpack1('m').unpack('V*')
old = WishSuiteBridge::AI_ID.find(gid)
if old && old.valid?
  old.erase!; WishSuiteBridge::AI_ID.evict(gid)
end
mesh = Geom::PolygonMesh.new(pts.length / 3, tri.length / 3)
idx = Array.new(pts.length / 3) { |i| mesh.add_point(Geom::Point3d.new(pts[3*i].mm, pts[3*i+1].mm, pts[3*i+2].mm)) }
(tri.length / 3).times { |k| mesh.add_polygon(idx[tri[3*k]], idx[tri[3*k+1]], idx[tri[3*k+2]]) }
g = m.active_entities.add_group
g.entities.fill_from_mesh(mesh, true, Geom::PolygonMesh::AUTO_SOFTEN | Geom::PolygonMesh::SMOOTH_SOFT_EDGES)
# AUTO_SOFTEN softens everything; keep rims and corners (faces meeting at a sharp angle) crisp
lim = %(hard)s.degrees
g.entities.grep(Sketchup::Edge).each do |e|
  fs = e.faces
  next unless fs.length == 2 && fs[0].normal.angle_between(fs[1].normal) > lim
  e.soft = false; e.smooth = false
end
WishSuiteBridge::AI_ID.set(g, gid)
attrs.each { |k, v| g.set_attribute('su_mcp_bridge', k, v) }
fs = g.entities.grep(Sketchup::Face)
es = g.entities.grep(Sketchup::Edge)
[fs.length, es.length, es.count { |e| e.soft? }, es.count { |e| e.faces.length != 2 }, g.manifold?]
end
$wish_mesh = nil
m.commit_operation
res
'''

CHUNK = 4_000_000  # base64 chars per bridge call
INLINE = 200_000   # smaller payloads go inside the build call itself


def _mesh_groups(c, items, hard_deg):
    """SketchUp groups from triangle meshes (mm), smooth except where faces meet at > hard_deg; all in one
    bridge call / one undo step. items = [(gid, points, tris, attrs)]. Mesh data travels as base64
    float32 / uint32; big arrays are sent ahead in chunks into $wish_mesh."""
    import base64
    import json
    import struct
    q = lambda v: json.dumps(str(v), ensure_ascii=False).replace('#', r'\#')  # a JSON string literal is a valid Ruby double-quoted literal here
    rows, kb, staged = [], 0.0, 0
    for gid, points, tris, attrs in items:
        flat_p = [v for p in points for v in p]
        flat_t = [i for t in tris for i in t]
        src = []
        for fmt, flat in ((f'<{len(flat_p)}f', flat_p), (f'<{len(flat_t)}I', flat_t)):
            data = base64.b64encode(struct.pack(fmt, *flat)).decode('ascii')
            kb += len(data) / 1024
            if len(data) <= INLINE:
                src.append(f'"{data}"')
                continue
            if not staged:
                c.ruby('$wish_mesh = []; true')
            c.ruby('$wish_mesh << String.new; true')
            for k in range(0, len(data), CHUNK):  # chunk big meshes so no single message gets huge
                c.ruby(f'$wish_mesh[{staged}] << "{data[k:k + CHUNK]}"; true')
            src.append(f'$wish_mesh[{staged}]')
            staged += 1
        rattrs = '{' + ', '.join(f'{q(k)} => {q(v)}' for k, v in attrs.items()) + '}'
        rows.append(f'[{q(gid)}, {src[0]}, {src[1]}, {rattrs}]')
    res = c.ruby(MESH_RUBY % {'items': ',\n'.join(rows), 'hard': float(hard_deg)})
    try:
        vals = res if isinstance(res, list) else json.loads(str(res).replace('nil', 'null'))
        out = []
        for (gid, *_), (faces, edges, soft, open_edges, manifold) in zip(items, vals):
            if not faces:
                raise BuildError(f'{gid} 没有生成任何面')
            out.append({'faces': faces, 'edges': edges, 'soft_edges': soft, 'open_edges': open_edges,
                        'manifold': manifold})
    except BuildError:
        raise
    except Exception:
        raise BuildError(f'网格对象生成后返回异常：{res}')
    if out:
        out[0]['payload_kb'] = round(kb, 1)
    return out


def _surface_roof(c, r, f):
    """Curved thick-shell roof (vault / wave / dome) from curve.surface_mesh."""
    s = r.get('surface') or {}
    mesh = curve.surface_mesh(r['footprint'], f, r['base_z'], r['thickness'], s.get('cell'))
    st = _mesh_groups(c, [(r['id'], mesh['points'], mesh['tris'], {'type': 'roof', 'roof_kind': r['kind']})], 40)[0]
    return {'points': len(mesh['points']), 'tris': len(mesh['tris']), 'cell_mm': round(mesh['cell']),
            'area_ratio': round(mesh['area_ratio'], 4), **st}


SOFTEN_JOINTS_RUBY = '''
require 'json'
tol = %(tol)s.mm
hits = 0
walk = lambda do |ents, tr, pts|
  ents.each do |e|
    if e.is_a?(Sketchup::Edge)
      a = e.start.position.transform(tr); b = e.end.position.transform(tr)
      at = lambda { |q| pts.index { |p| (q.x - p[0]).abs < tol && (q.y - p[1]).abs < tol } }
      ia = at.call(a)
      next unless ia
      vertical = (a.x - b.x).abs < tol && (a.y - b.y).abs < tol && (a.z - b.z).abs > tol
      # 2026-11-20: also the short edge across the wall top/bottom at the joint: both ends are the SAME joint's
      # inner/outer corner (points come in pairs), so the chord's long top edges are never hidden
      ib = at.call(b)
      across = !vertical && (a.z - b.z).abs < tol && ib && ib != ia && ib / 2 == ia / 2
      next unless vertical || across
      # hidden too: the chord's end face makes this corner a silhouette, and profiles are drawn on soft edges
      # not smooth: these edges border the 90-degree end cap, smoothing them shades each chord like a flute (2026-11-20)
      e.soft = true; e.smooth = false; e.hidden = true; hits += 1
    elsif e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance)
      d = e.respond_to?(:definition) ? e.definition : nil
      walk.call(e.is_a?(Sketchup::Group) ? e.entities : d.entities, tr * e.transformation, pts)
    end
  end
end
m = Sketchup.active_model
m.start_operation('Soften arc joints', true)
miss = []
JSON.parse(%(data)s).each do |id, pts|
  g = WishSuiteBridge::AI_ID.find(id)
  unless g && g.valid?
    miss << id; next
  end
  ipts = pts.map { |x, y| [x.mm, y.mm] }
  walk.call(g.respond_to?(:entities) ? g.entities : g.definition.entities, g.transformation, ipts)
end
m.commit_operation
[hits, miss]
'''


def _joint_points(m, suffix='', only=None):
    """{group id: [plan corner points at its chord joints]} for every joint inside a curved wall."""
    out = {}
    for aw in m.arcs:
        ch = aw.chords
        for k in range(len(ch) - 1):
            for s, end in ((ch[k], 'b'), (ch[k + 1], 'a')):
                gid = s.id + suffix
                if only is not None and gid not in only:
                    continue
                ux, uy = s.unit
                nx, ny = -uy, ux
                px, py = s.built_b if end == 'b' else s.built_a
                h = s.thickness / 2
                out.setdefault(gid, []).extend([[round(px + nx * h, 3), round(py + ny * h, 3)],
                                                [round(px - nx * h, 3), round(py - ny * h, 3)]])
    return out


def _soften_arc_joints(c, m, suffix='', only=None):
    """Hide the vertical lines where neighbouring chords of a curved wall meet (free ends and jambs stay crisp:
    jambs sit >= 3 mm inside the built end because chords are extended to close the joint)."""
    import json
    data = _joint_points(m, suffix, only)
    if not data:
        return [0, []]
    res = c.ruby(SOFTEN_JOINTS_RUBY % {'tol': 1.0, 'data': json.dumps(json.dumps(list(data.items()), ensure_ascii=False), ensure_ascii=False).replace('#', r'\#')})
    return res


def _wall_fills(m, f, cell, eps=3.0):
    """Infill solids from each top-level exterior wall's flat top up to the curved roof underside.
    Each is a closed triangle mesh whose top follows the roof underside on both wall faces (so nothing
    sits buried in the roof); runs where the roof touches the wall (f < eps) split it into pieces.
    Returns [{id, wall, material, points, tris}] in mm."""
    base = m.roof['base_z']
    step_ = min(600.0, max(300.0, float(cell or 450)))
    out = []
    for s in m.segments:
        if not s.exterior or abs(s.base_z + s.height - base) > 1:
            continue
        (ax, ay), (bx, by) = s.built_a, s.built_b
        L = math.dist((ax, ay), (bx, by))
        if L < 1:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = -uy, ux
        h = s.thickness / 2
        n = max(1, math.ceil(L / step_))
        ds = [L * i / n for i in range(n + 1)]

        def tops(d):   # roof underside height above the wall top on side A (-n) and side B (+n)
            x, y = ax + ux * d, ay + uy * d
            return f(x - nx * h, y - ny * h), f(x + nx * h, y + ny * h)
        g = [max(tops(d)) - eps for d in ds]
        if max(g) <= 0:
            continue
        runs, run = [], None    # run = [d values]; ends interpolated where the roof meets the wall
        for i, d in enumerate(ds):
            if g[i] > 0:
                if run is None:
                    run = [0.0] if i == 0 else [ds[i - 1] + (d - ds[i - 1]) * (-g[i - 1]) / (g[i] - g[i - 1])]
                if d > run[-1] + 1e-6:
                    run.append(d)
            elif run is not None:
                run.append(ds[i - 1] + (d - ds[i - 1]) * g[i - 1] / (g[i - 1] - g[i]))
                runs.append(run); run = None
        if run is not None:
            runs.append(run)

        pts, tris, vid = [], [], {}

        def V(p):
            k = (round(p[0], 2), round(p[1], 2), round(p[2], 2))
            if k not in vid:
                vid[k] = len(pts); pts.append(k)
            return vid[k]

        def quad(q):   # q is wound so its normal points out of the solid; collapsed corners drop out
            for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
                if len(set(t)) == 3:
                    tris.append(t)

        for run in runs:
            if run[-1] - run[0] < 5:
                continue
            cols = []
            for j, d in enumerate(run):
                x, y = ax + ux * d, ay + uy * d
                ta, tb = tops(d)
                if j in (0, len(run) - 1) and max(ta, tb) - eps <= 1e-6:
                    ta = tb = 0.0      # interpolated end where the roof meets the wall: collapse to the bottom
                ta, tb = (0.0 if ta < 1.0 else ta), (0.0 if tb < 1.0 else tb)   # no sub-mm slivers
                pa, pb = (x - nx * h, y - ny * h), (x + nx * h, y + ny * h)
                cols.append([V((*pa, base)), V((*pa, base + ta)), V((*pb, base)), V((*pb, base + tb))])
            # side A is at -n, B at +n, d runs along u, z up (n = z x u): outward windings below
            for j in range(len(cols) - 1):
                ab0, at0, bb0, bt0 = cols[j]
                ab1, at1, bb1, bt1 = cols[j + 1]
                quad((ab0, ab1, at1, at0))      # side A, normal -n
                quad((bb0, bt0, bt1, bb1))      # side B, normal +n
                quad((ab0, bb0, bb1, ab1))      # bottom, normal -z
                quad((at0, at1, bt1, bt0))      # top (follows the roof underside), normal +z
            ab, at, bb, bt = cols[0]
            quad((ab, at, bt, bb))              # start cap, normal -u
            ab, at, bb, bt = cols[-1]
            quad((ab, bb, bt, at))              # end cap, normal +u
        if tris:
            out.append({'id': f'{s.id}__fill', 'wall': s.id, 'material': s.material, 'points': pts, 'tris': tris})
    return out


def _build_fills(c, fills):
    return _mesh_groups(c, [(fl['id'], fl['points'], fl['tris'], {'type': 'wall_fill', 'wall': fl['wall']})
                            for fl in fills], 30)


def _safe(name):
    import re
    return re.sub(r'[\\/:*?"<>|\s]+', '_', name).strip('_') or 'model'
