"""总装：一层平面 = 本体 + 图块 + 标注 → DXF → PDF/PNG。

    from wish_cad.cad.drawing import draw_plan
    res = draw_plan(m, '1F', out_dir)   # -> {'dxf','pdf','png','stats','errors'}
"""
from __future__ import annotations

from pathlib import Path

from . import annot, blocks, body, render
from .sheet import Sheet

ORDER = (('body', body), ('blocks', blocks), ('annot', annot))     # 先画本体（墙、地坪），再图块，最后标注压在上面


def level_title(m, level_id):
    names = ['首层', '二层', '三层', '四层', '五层', '六层', '七层', '八层', '九层', '十层']
    ids = [lv.id for lv in m.levels]
    i = ids.index(level_id) if level_id in ids else 0
    return f'{names[i] if i < len(names) else level_id}平面图'


def draw_plan(m, level_id, out_dir, name=None):
    dr = getattr(m, 'drawing', None) or {}
    sh = Sheet(scale=dr.get('scale', 100), style=dr.get('style', 'construction'))
    sh.model, sh.level = m, level_id
    stats = {name: module.draw(sh, m, level_id) or {} for name, module in ORDER}
    out_dir = Path(out_dir)
    stem = name or f'平面_{level_id}'
    res = sh.save(out_dir / f'{stem}.dxf')
    res['stats'] = stats
    res.update(render.render(out_dir / f'{stem}.dxf', out_dir, stem, scale=sh.scale,
                             style=sh.style, title=dr.get('title') or level_title(m, level_id)))
    return res
