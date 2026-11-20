"""出图：DXF → PDF（矢量）/ PNG。

- 按图层线宽真实打印：线宽 mm 换成 pt（×72/25.4），图纸按真实纸面尺寸出，所以 0.7 mm 粗线确实比 0.18 mm 细线粗。
- 颜色：construction 黑白（ACI 8/9 打成灰）；presentation 保留真彩色底色，其余线条黑/灰。
- 纸面：A3/A2/A1/A0 中选最小能放下的（横向优先），画图框（左侧装订边 25）和简单标题栏，图形居中。

    render(dxf_path, out_dir, stem, scale=100, style='construction', title=None, dpi=200)
        -> {'pdf', 'png', 'paper', 'paper_mm'}
"""
from __future__ import annotations

import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from .fonts import selected_font
import ezdxf  # noqa: E402
from ezdxf import bbox  # noqa: E402
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402
from ezdxf.addons.drawing.config import (Configuration, LineweightPolicy, ColorPolicy,  # noqa: E402
                                         BackgroundPolicy, LinePolicy, HatchPolicy)

plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['axes.unicode_minus'] = False

PT = 72.0 / 25.4            # 1 mm = 2.835 pt

# 纸面（横向 W×H，mm）与装订边/其他边距（GB/T 50001：A0~A2 c=10，A3~A4 c=5，a=25）
PAPERS = [('A3', 420, 297, 5), ('A2', 594, 420, 10), ('A1', 841, 594, 10), ('A0', 1189, 841, 10)]
BIND = 25.0
TB_W, TB_H = 140.0, 32.0     # 标题栏
PAD = 8.0                    # 图形到可用区边的最小留白


def _font():
    return FontProperties(fname=str(selected_font()))


def _choose_paper(cw, ch):
    """返回 (name, W, H, c, region)；region 为图形可用区 (x0, y0, x1, y1)。"""
    for name, w, h, c in PAPERS:
        for W, H, orient in ((w, h, '横'), (h, w, '竖')):
            ix0, iy0, ix1, iy1 = BIND, c, W - c, H - c
            # 方案一：图形放在标题栏上方的整幅宽度内
            r1 = (ix0, iy0 + TB_H, ix1, iy1)
            # 方案二：图形放在标题栏左侧的整幅高度内
            r2 = (ix0, iy0, ix1 - TB_W, iy1)
            for r in (r1, r2):
                if cw + 2 * PAD <= r[2] - r[0] and ch + 2 * PAD <= r[3] - r[1]:
                    return f'{name}{orient}', W, H, c, r
    name, w, h, c = PAPERS[-1]
    return f'{name}横（放不下，已缩放）', w, h, c, (BIND, c + TB_H, w - c, h - c)


def _effective_aci(entity, doc):
    try:
        c = entity.dxf.color
    except Exception:
        c = 256
    if c in (256, None):
        try:
            c = doc.layers.get(entity.dxf.layer).color
        except Exception:
            c = 7
    if c == 0:
        c = 7
    return abs(c)


_GREY = {8: '#808080', 9: '#c0c0c0', 250: '#333333', 251: '#505050', 252: '#696969', 253: '#808080',
         254: '#bebebe', 255: '#ffffff'}


def _make_override(doc, style):
    def override(entity, props):
        tc = None
        try:
            if entity.dxf.hasattr('true_color'):
                tc = entity.dxf.true_color
        except Exception:
            tc = None
        if tc is not None:
            r, g, b = (tc >> 16) & 255, (tc >> 8) & 255, tc & 255
            if style == 'presentation':
                return                                    # 保留真彩色（展板底色）
            lum = int(round(0.299 * r + 0.587 * g + 0.114 * b))
            props.color = f'#{lum:02x}{lum:02x}{lum:02x}'
            return
        aci = _effective_aci(entity, doc)
        props.color = _GREY.get(aci, '#000000')
    return override


def render(dxf_path, out_dir, stem, scale=100, style='construction', title=None, dpi=200):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    b = bbox.extents(msp, fast=True)
    if not b.has_data:
        return {'render': '图是空的，没有出 PDF/PNG'}
    mx0, my0, mx1, my1 = b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y
    cw, ch = (mx1 - mx0) / scale, (my1 - my0) / scale
    pname, W, H, c, reg = _choose_paper(cw, ch)
    k = 1.0
    rw, rh = reg[2] - reg[0] - 2 * PAD, reg[3] - reg[1] - 2 * PAD
    if cw > rw or ch > rh:                         # A0 也放不下：缩小
        k = min(rw / cw, rh / ch)
    eff = scale / k                                # 每纸面 mm 对应的模型 mm
    # 图形中心对准可用区中心
    pcx, pcy = (reg[0] + reg[2]) / 2, (reg[1] + reg[3]) / 2
    mcx, mcy = (mx0 + mx1) / 2, (my0 + my1) / 2
    xlim = (mcx - pcx * eff, mcx + (W - pcx) * eff)
    ylim = (mcy - pcy * eff, mcy + (H - pcy) * eff)

    fig = plt.figure(figsize=(W / 25.4, H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    cfg = Configuration(lineweight_policy=LineweightPolicy.ABSOLUTE,
                        lineweight_scaling=PT * k,
                        min_lineweight=0.10 * PT,
                        color_policy=ColorPolicy.COLOR,
                        background_policy=BackgroundPolicy.WHITE,
                        line_policy=LinePolicy.ACCURATE,
                        hatch_policy=HatchPolicy.NORMAL)
    backend = MatplotlibBackend(ax, adjust_figure=False)
    fe = Frontend(ctx, backend, config=cfg)
    fe.push_property_override_function(_make_override(doc, style))
    fe.draw_layout(msp, finalize=True)
    ax.set_aspect('auto')
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_facecolor('white')

    # ---- 图框与标题栏（纸面坐标 mm）
    ov = fig.add_axes([0, 0, 1, 1], facecolor='none')
    ov.set_xlim(0, W)
    ov.set_ylim(0, H)
    ov.axis('off')
    fp = _font()
    frame_lw = (1.4 if W >= 841 or H >= 841 else 1.0) * PT
    ix0, iy0, ix1, iy1 = BIND, c, W - c, H - c
    ov.plot([ix0, ix1, ix1, ix0, ix0], [iy0, iy0, iy1, iy1, iy0], color='black', lw=frame_lw, solid_joinstyle='miter')
    tx0, ty0 = ix1 - TB_W, iy0
    ov.plot([tx0, ix1, ix1, tx0, tx0], [ty0, ty0, ty0 + TB_H, ty0 + TB_H, ty0], color='black', lw=0.7 * PT)
    cols = [tx0, tx0 + 18, tx0 + 78, tx0 + 96, ix1]
    rows_y = [ty0 + TB_H, ty0 + TB_H * 2 / 3, ty0 + TB_H / 3, ty0]
    for y in rows_y[1:-1]:
        ov.plot([tx0, ix1], [y, y], color='black', lw=0.35 * PT)
    for x in cols[1:-1]:
        ov.plot([x, x], [ty0, ty0 + TB_H], color='black', lw=0.35 * PT)
    today = datetime.date.today().isoformat()
    cells = [('图名', title or stem, '比例', f'1:{int(round(scale / k))}' if k == 1.0 else f'1:{scale / k:.0f}'),
             ('日期', today, '图幅', pname),
             ('制图', 'wish 自动生成', '图号', stem.split('_')[1] if '_' in stem else '01')]
    for i, row in enumerate(cells):
        yc = (rows_y[i] + rows_y[i + 1]) / 2
        for j, s in enumerate(row):
            xc = (cols[j] + cols[j + 1]) / 2
            size = (5.0 if (i == 0 and j == 1) else 3.2) * PT
            ov.text(xc, yc, s, fontproperties=fp, fontsize=size, ha='center', va='center')

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf, png = out_dir / f'{stem}.pdf', out_dir / f'{stem}.png'
    fig.savefig(pdf, facecolor='white')
    fig.savefig(png, dpi=dpi, facecolor='white')
    plt.close(fig)
    return {'pdf': str(pdf), 'png': str(png), 'paper': pname, 'paper_mm': [W, H],
            'shrink': round(k, 3)}
