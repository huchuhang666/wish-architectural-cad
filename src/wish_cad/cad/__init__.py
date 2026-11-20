"""wish 的 CAD 制图引擎（2026-11-19 重做）。

模块分工（各自只画自己的图层，统一通过 sheet.Sheet）：
- standard.py  图层、线宽、字高、构造尺寸（国标）
- sheet.py     绘图底座
- body.py      建筑本体：墙体涂实、幕墙、柱、门窗细部、室内外（散水、台阶、平台、铺装、标高）、挑檐投影、楼梯坡道
- annot.py     标注：轴网与轴号、三道尺寸、房间名面积、门窗编号与门窗表、指北针、剖切符号、比例尺、图名
- blocks.py    图块：家具、洁具等平面图块
- render.py    出图：DXF → 按线宽打印的 PDF / PNG
- drawing.py   总装：draw_plan(m, level, out_dir)
"""
