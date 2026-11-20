"""绘图底座：所有绘图模块都只通过 Sheet 往 DXF 里画，坐标一律是模型毫米（1:1）。

    from wish_cad.cad.sheet import Sheet
    sh = Sheet(scale=100, style='construction')
    sh.poly(pts, 'A-WALL', closed=True)
    sh.solid(pts, 'A-WALL-PATT')                 # 涂实
    sh.hatch(pts, 'A-FLOR-PATT', pattern='NET', pscale=1.0, angle=0)  # 图案填充；pscale 为纸面比例
    sh.text('阅览室', (x, y), 'room', 'A-AREA-IDEN', align='MIDDLE_CENTER')
    sh.save(path)

模块约定：每个绘图模块提供 draw(sh, m, level_id) -> dict（统计数），只画自己负责的图层。
"""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

from . import standard as S


class Sheet:
    def __init__(self, scale=100, style='construction'):
        self.scale = float(scale)
        self.style = style
        self.doc = ezdxf.new('R2018', setup=True)      # setup=True: DASHED / CENTER 线型、标准箭头
        self.msp = self.doc.modelspace()
        d = self.doc
        d.header['$INSUNITS'] = 4
        d.header['$MEASUREMENT'] = 1
        d.header['$LTSCALE'] = self.scale * 0.5        # 虚线、点划线在 1:100 出图时的疏密
        d.header['$PSLTSCALE'] = 0
        d.header['$LWDISPLAY'] = 1
        for name, (color, lw, lt, desc) in S.LAYERS.items():
            lay = d.layers.add(name, color=color, linetype=lt)
            lay.dxf.lineweight = int(round(lw * 100)) if lw else 0
            lay.description = desc
        if S.TEXT_STYLE not in d.styles:
            d.styles.add(S.TEXT_STYLE, font=S.FONT)
        self._dimstyle()
        self.stats = {}

    # ------------------------------------------------------------------ basics
    def _dimstyle(self):
        k = self.scale
        ds = self.doc.dimstyles.add('WISH')
        ds.dxf.dimtxt = S.TEXT['dim'] * k
        ds.dxf.dimasz = 1.2 * k          # 斜短线长度
        ds.dxf.dimexe = 2.0 * k          # 尺寸界线超出尺寸线
        ds.dxf.dimexo = 2.0 * k          # 尺寸界线起点偏移
        ds.dxf.dimgap = 0.8 * k
        ds.dxf.dimtxsty = S.TEXT_STYLE
        ds.dxf.dimtad = 1
        ds.dxf.dimtih = 0
        ds.dxf.dimtoh = 0
        ds.dxf.dimblk = 'ARCHTICK'       # 建筑斜短线
        ds.dxf.dimdec = 0
        ds.dxf.dimclrd = 3; ds.dxf.dimclre = 3; ds.dxf.dimclrt = 7
        ds.dxf.dimlwd = 18; ds.dxf.dimlwe = 18
        ds.dxf.dimlunit = 2
        ds.dxf.dimzin = 8
        ds.dxf.dimtmove = 2              # 文字放不下时不带引线，直接移开
        ds.dxf.dimatfit = 3

    def count(self, key, n=1):
        self.stats[key] = self.stats.get(key, 0) + n

    def attrs(self, layer, **kw):
        a = {'layer': layer}
        a.update(kw)
        return a

    def line(self, a, b, layer, **kw):
        return self.msp.add_line(tuple(a[:2]), tuple(b[:2]), dxfattribs=self.attrs(layer, **kw))

    def poly(self, pts, layer, closed=False, **kw):
        return self.msp.add_lwpolyline([tuple(p[:2]) for p in pts], close=closed, dxfattribs=self.attrs(layer, **kw))

    def arc(self, center, r, a0, a1, layer, **kw):
        """a0→a1 逆时针，单位度。"""
        return self.msp.add_arc(tuple(center[:2]), r, a0, a1, dxfattribs=self.attrs(layer, **kw))

    def circle(self, center, r, layer, **kw):
        return self.msp.add_circle(tuple(center[:2]), r, dxfattribs=self.attrs(layer, **kw))

    def solid(self, pts, layer, color=None, rgb=None, holes=()):
        """实心填充（墙、柱涂实，展板底色）。rgb='#RRGGBB' 时用真彩色。"""
        h = self.msp.add_hatch(color=color if color is not None else 7, dxfattribs={'layer': layer})
        if rgb:
            h.rgb = tuple(int(rgb.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
        h.paths.add_polyline_path([tuple(p[:2]) for p in pts], is_closed=True)
        for hole in holes:
            h.paths.add_polyline_path([tuple(p[:2]) for p in hole], is_closed=True)
        return h

    def hatch(self, pts, layer, pattern='ANSI31', pscale=1.0, angle=0.0, color=None, holes=()):
        """图案填充；pscale 是纸面比例，内部乘以出图比例让 1:100 下疏密合适。"""
        h = self.msp.add_hatch(color=color if color is not None else 256, dxfattribs={'layer': layer})
        h.set_pattern_fill(pattern, scale=pscale * self.scale / 100 * 10, angle=angle)
        h.paths.add_polyline_path([tuple(p[:2]) for p in pts], is_closed=True)
        for hole in holes:
            h.paths.add_polyline_path([tuple(p[:2]) for p in hole], is_closed=True)
        return h

    def text(self, s, at, kind, layer, align='MIDDLE_CENTER', rot=0.0, height=None, **kw):
        t = self.msp.add_text(s, height=height or S.text_h(kind, self.scale), rotation=rot,
                              dxfattribs=self.attrs(layer, style=S.TEXT_STYLE, **kw))
        t.set_placement(tuple(at[:2]), align=getattr(TextEntityAlignment, align))
        return t

    def mtext(self, s, at, kind, layer, width=None, attach=5, **kw):
        m = self.msp.add_mtext(s, dxfattribs=self.attrs(layer, style=S.TEXT_STYLE, char_height=S.text_h(kind, self.scale),
                                                        attachment_point=attach, **kw))
        m.set_location(tuple(at[:2]))
        if width:
            m.dxf.width = width
        return m

    def dim(self, p1, p2, base, layer='A-DIMS', angle=None):
        """线性尺寸；base 为尺寸线经过的点；angle=None 表示沿 p1→p2 方向（对齐标注）。"""
        if angle is None:
            ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        else:
            ang = angle
        d = self.msp.add_linear_dim(base=tuple(base[:2]), p1=tuple(p1[:2]), p2=tuple(p2[:2]), angle=ang,
                                    dimstyle='WISH', dxfattribs={'layer': layer})
        d.render()
        return d

    def p(self, mm):
        """纸面 mm → 模型 mm。"""
        return mm * self.scale

    # ------------------------------------------------------------------ blocks
    def block(self, name):
        """取（或新建）一个块定义；返回 (block, created)。"""
        if name in self.doc.blocks:
            return self.doc.blocks.get(name), False
        return self.doc.blocks.new(name=name), True

    def insert(self, name, at, rot=0.0, sx=1.0, sy=1.0, layer='A-FURN'):
        return self.msp.add_blockref(name, tuple(at[:2]), dxfattribs={'layer': layer, 'rotation': rot,
                                                                     'xscale': sx, 'yscale': sy})

    # ------------------------------------------------------------------ output
    def extents(self):
        from ezdxf import bbox
        b = bbox.extents(self.msp, fast=True)
        return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y) if b.has_data else (0, 0, 1000, 1000)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.saveas(path)
        chk = ezdxf.readfile(path)
        aud = chk.audit()
        return {'dxf': str(path), 'errors': len(aud.errors), 'fixes': len(aud.fixes),
                'entities': len(chk.modelspace())}
