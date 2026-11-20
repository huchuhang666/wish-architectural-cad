"""Presentation board from the generated views + plan.

One layout, two renderers:
  * Pillow PNG (default, ~1 s, no app needed)
  * Photoshop PSD (optional): same layout as editable layers — images as smart
    objects, Chinese text as text layers (SimHei), via local Photoshop automation.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..spec import Model, summary
from ..cad.fonts import selected_font
import os

W, H = 3508, 2480          # A3 landscape at 300 dpi short side ~ 210 dpi; crisp on screen and print
BG, INK, SUB, ACCENT, PANEL = (243, 241, 236), (34, 40, 44), (110, 118, 122), (47, 59, 64), (255, 255, 255)
def _font(px):
    return ImageFont.truetype(str(selected_font()), px)


def layout(m: Model, files: dict) -> dict:
    """Panels: [{'name','path','box':[x0,y0,x1,y1],'caption'}] and texts."""
    views = files.get('views', {})
    plan = files.get('plan_png')
    b = m.board
    hero = views.get(b.get('hero', '')) or views.get('iso') or views.get('eye') or next(iter(views.values()), None)
    side = [p for k, p in views.items() if p != hero and k in ('inside', 'iso_open', 'eye', 'iso_back', 'front', 'back')][:2]
    s = summary(m)
    panels = []
    M, top = 90, 300
    left_w = int(W * 0.58)
    if hero:
        panels.append({'name': '主效果图', 'path': hero, 'box': [M, top, M + left_w, H - 420], 'caption': '① 鸟瞰效果'})
    rx0, rx1 = M + left_w + 60, W - M
    if plan:
        panels.append({'name': '平面图', 'path': plan, 'box': [rx0, top, rx1, top + 980], 'caption': '② 首层平面（CAD 同步生成）'})
    sy = top + 980 + 110
    for i, p in enumerate(side):
        w_ = (rx1 - rx0 - 40) // max(len(side), 1)
        x0 = rx0 + i * (w_ + 40)
        panels.append({'name': f'视图{i + 1}', 'path': p, 'box': [x0, sy, x0 + w_, H - 420],
                       'caption': f'{"③④"[i]} {Path(p).stem.replace("inside", "室内").replace("iso_open", "去屋顶鸟瞰").replace("iso_back", "背面鸟瞰").replace("eye", "人视").replace("front", "正立面").replace("back", "背立面")}'})
    size = s['size_mm']
    facts = f'外包 {size[0] / 1000:.1f} × {size[1] / 1000:.1f} m · 高 {size[2] / 1000:.1f} m · {len(s["levels"])} 层 · 门 {s["doors"]} · 窗 {s["windows"]}'
    if s['rooms']:
        facts += ' · ' + ' / '.join(f'{n} {a:g}㎡' for n, a in s['rooms'][:4])
    texts = [
        {'name': '标题', 'text': b.get('title', m.name), 'xy': [M, 70], 'px': 120, 'color': 'FFFFFF', 'bold': True},
        {'name': '副标题', 'text': b.get('subtitle', ''), 'xy': [M, 215], 'px': 46, 'color': 'C9D3D6'},
        {'name': '指标', 'text': facts.replace('㎡', ' 平方米'), 'xy': [M, H - 330], 'px': 44, 'color': '222A2E'},
    ]
    for i, n in enumerate(b.get('notes', [])[:4]):
        texts.append({'name': f'说明{i + 1}', 'text': f'· {n}', 'xy': [M + (i % 2) * 1500, H - 240 + (i // 2) * 70], 'px': 42, 'color': '4A5458'})
    return {'size': [W, H], 'band_h': 290, 'panels': panels, 'texts': texts}


def _fit(img, box):
    x0, y0, x1, y1 = box
    k = min((x1 - x0) / img.width, (y1 - y0 - 70) / img.height)
    im = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    return im, (x0 + (x1 - x0 - im.width) // 2, y0 + (y1 - y0 - 70 - im.height) // 2)


def render_png(lay: dict, out: Path) -> str:
    img = Image.new('RGB', tuple(lay['size']), BG)
    g = ImageDraw.Draw(img)
    g.rectangle([0, 0, W, lay['band_h']], fill=ACCENT)
    for p in lay['panels']:
        x0, y0, x1, y1 = p['box']
        g.rectangle([x0, y0, x1, y1], fill=PANEL, outline=(222, 218, 210), width=2)
        try:
            src = Image.open(p['path']).convert('RGB')
            im, (px, py) = _fit(src, p['box'])
            img.paste(im, (px, py))
        except OSError:
            g.text((x0 + 30, y0 + 30), f'缺图：{p["path"]}', fill=(200, 40, 40), font=_font(36))
        g.text((x0 + 20, y1 - 55), p['caption'], fill=INK, font=_font(38))
    for t in lay['texts']:
        g.text(tuple(t['xy']), t['text'], fill='#' + t['color'], font=_font(t['px']))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, dpi=(300, 300))
    return str(out)


def _js(s):
    return json.dumps(s, ensure_ascii=False)


def render_psd(lay: dict, out_psd: Path, timeout=600) -> dict:
    """Same layout in Photoshop: editable PSD + PNG. Never overwrites an existing PSD."""
    if out_psd.exists():
        raise FileExistsError(f'不覆盖已有 PSD：{out_psd}')
    jsx = f'''(function(){{
var old=app.preferences.rulerUnits; app.preferences.rulerUnits=Units.PIXELS; var d=null,r=null;
try {{
 d=app.documents.add({W},{H},300,{_js(out_psd.stem)},NewDocumentMode.RGB,DocumentFill.WHITE);
 function color(h){{var c=new SolidColor();c.rgb.hexValue=h;return c;}}
 function rect(n,x0,y0,x1,y1,h){{var l=d.artLayers.add();l.name=n;d.activeLayer=l;d.selection.select([[x0,y0],[x1,y0],[x1,y1],[x0,y1]]);d.selection.fill(color(h));d.selection.deselect();return l;}}
 function text(n,s,x,y,px,h){{var l=d.artLayers.add();l.name=n;l.kind=LayerKind.TEXT;var t=l.textItem;t.font={_js(os.environ.get('WISH_PS_FONT', 'SimHei'))};t.size=UnitValue(px*72/300,'pt');t.contents=s;t.color=color(h);t.position=[UnitValue(x,'px'),UnitValue(y+px*0.85,'px')];return l;}}
 function place(p,n,x0,y0,x1,y1){{var a=new ActionDescriptor();a.putPath(charIDToTypeID('null'),new File(p));a.putEnumerated(charIDToTypeID('FTcs'),charIDToTypeID('QCSt'),charIDToTypeID('Qcsa'));executeAction(charIDToTypeID('Plc '),a,DialogModes.NO);
  var l=d.activeLayer;l.name=n;var b=l.bounds,w=b[2].as('px')-b[0].as('px'),h=b[3].as('px')-b[1].as('px');var k=Math.min((x1-x0)/w,(y1-y0-70)/h)*100;l.resize(k,k,AnchorPosition.TOPLEFT);
  b=l.bounds;w=b[2].as('px')-b[0].as('px');h=b[3].as('px')-b[1].as('px');l.translate(UnitValue((x0+x1-w)/2-b[0].as('px'),'px'),UnitValue((y0+y1-70-h)/2-b[1].as('px'),'px'));return l;}}
 rect('底色',0,0,{W},{H},'F3F1EC'); rect('顶部色带',0,0,{W},{lay["band_h"]},'2F3B40');
'''
    for p in lay['panels']:
        x0, y0, x1, y1 = p['box']
        jsx += f" rect({_js(p['name'] + '_底')},{x0},{y0},{x1},{y1},'FFFFFF'); place({_js(Path(p['path']).as_posix())},{_js(p['name'])},{x0},{y0},{x1},{y1});"
        jsx += f" text({_js(p['name'] + '_图注')},{_js(p['caption'])},{x0 + 20},{y1 - 55},38,'22282C');\n"
    for t in lay['texts']:
        jsx += f" text({_js(t['name'])},{_js(t['text'])},{t['xy'][0]},{t['xy'][1]},{t['px']},'{t['color']}');\n"
    png = out_psd.with_suffix('.psd.png')
    jsx += f''' var so=new PhotoshopSaveOptions();so.layers=true;so.embedColorProfile=true;d.saveAs(new File({_js(out_psd.as_posix())}),so,true,Extension.LOWERCASE);
 d.saveAs(new File({_js(png.as_posix())}),new PNGSaveOptions(),true,Extension.LOWERCASE);
 d.close(SaveOptions.DONOTSAVECHANGES); d=null;
 r=app.open(new File({_js(out_psd.as_posix())})); var texts=0,smart=0;
 for(var i=0;i<r.layers.length;i++) {{ if(r.layers[i].kind==LayerKind.TEXT) texts++; if(r.layers[i].kind==LayerKind.SMARTOBJECT) smart++; }}
 var res='layers='+r.layers.length+';text_layers='+texts+';smart_objects='+smart; r.close(SaveOptions.DONOTSAVECHANGES); r=null; return res;
}} finally {{
 if(r) r.close(SaveOptions.DONOTSAVECHANGES);
 if(d) d.close(SaveOptions.DONOTSAVECHANGES);
 app.preferences.rulerUnits=old;
}}
}})();'''
    js_path = out_psd.with_suffix('.jsx')
    js_path.write_text(jsx, encoding='utf-8-sig')
    receipt = out_psd.with_suffix('.ps-receipt.json')
    try:
        run = subprocess.run([sys.executable, '-m', 'wish_cad.desktop.photoshop', str(js_path), str(receipt)],
                             capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Photoshop timed out. Check its document state before retrying.") from exc
    if run.returncode or not out_psd.is_file() or not receipt.is_file():
        raise RuntimeError('Photoshop failed: ' + run.stderr.decode('utf-8', 'replace')[-800:])
    result = json.loads(receipt.read_text(encoding='utf-8'))
    counts = dict(item.split('=', 1) for item in result['returned'].split(';'))
    evidence = {name: int(value) for name, value in counts.items()}
    evidence['version'] = result['version']
    if evidence['text_layers'] < 1 or evidence['smart_objects'] != len(lay['panels']):
        raise RuntimeError(f'Photoshop readback had unexpected layer counts: {evidence}')
    return {'psd': str(out_psd), 'png': str(png), 'check': evidence}
