"""制图标准（参照 GB/T 50001 房屋建筑制图统一标准、GB/T 50104 建筑制图标准）。

所有模块只从这里取图层、线宽、字高，保证整张图层次统一。
线宽组取 b = 0.7 mm（1:100 平面常用）：
  粗 b      0.70  剖到的墙、柱轮廓
  中粗 0.7b 0.50  剖到的幕墙框、室外台阶/坡道剖切边
  中 0.5b   0.35  门扇、窗框、看到的室外地坪变化线、楼梯踏步
  细 0.25b  0.18  门开启线、家具、洁具、尺寸线、轴线、填充边
  极细      0.13  铺装/填充图案
字高都是“纸面毫米”，画到模型空间要乘比例（1:100 → ×100）。
"""

B = 0.70
LW = {'b': 0.70, '0.7b': 0.50, '0.5b': 0.35, '0.25b': 0.18, 'hair': 0.13}

# name: (ACI 颜色, 线宽 mm, 线型, 说明)。颜色按常用打印样式：7 黑白、8/9 灰。
LAYERS = {
    'A-WALL':        (7, 0.70, 'Continuous', '剖到的实墙轮廓（粗）'),
    'A-WALL-PATT':   (8, 0.00, 'Continuous', '墙体涂实/填充'),
    'A-WALL-EXT':    (7, 0.70, 'Continuous', '外墙轮廓（粗）'),
    'A-WALL-INSUL':  (8, 0.13, 'Continuous', '外墙保温层'),
    'A-COLS':        (7, 0.70, 'Continuous', '柱轮廓'),
    'A-COLS-PATT':   (7, 0.00, 'Continuous', '柱涂实'),
    'A-GLAZ':        (4, 0.18, 'Continuous', '玻璃线'),
    'A-GLAZ-FRAM':   (4, 0.50, 'Continuous', '窗框、幕墙框与竖梃'),
    'A-DOOR':        (2, 0.35, 'Continuous', '门扇'),
    'A-DOOR-SWNG':   (2, 0.18, 'Continuous', '门开启弧线'),
    'A-FLOR-OUTL':   (7, 0.35, 'Continuous', '看到的地坪边线：台阶、散水、平台、高差'),
    'A-FLOR-PATT':   (8, 0.13, 'Continuous', '地面铺装填充'),
    'A-FLOR-LEVL':   (7, 0.18, 'Continuous', '标高符号'),
    'A-SITE':        (8, 0.25, 'Continuous', '场地边线、室外铺装分区'),
    'A-SITE-PATT':   (9, 0.13, 'Continuous', '室外铺装/草地填充'),
    'A-ROOF-OUTL':   (7, 0.25, 'DASHED', '屋面/挑檐投影线（虚线）'),
    'A-STRS':        (7, 0.35, 'Continuous', '楼梯踏步、坡道'),
    'A-STRS-ARRW':   (7, 0.18, 'Continuous', '上下行箭头与折断线'),
    'A-FURN':        (8, 0.18, 'Continuous', '家具'),
    'A-FIXT':        (8, 0.18, 'Continuous', '洁具'),
    'A-GRID':        (1, 0.18, 'CENTER', '定位轴线（点划线）'),
    'A-GRID-IDEN':   (7, 0.25, 'Continuous', '轴线圆圈与编号'),
    'A-DIMS':        (3, 0.18, 'Continuous', '尺寸标注'),
    'A-AREA-IDEN':   (7, 0.25, 'Continuous', '房间名称、面积'),
    'A-ANNO-TEXT':   (7, 0.25, 'Continuous', '一般文字说明'),
    'A-ANNO-SYMB':   (7, 0.35, 'Continuous', '指北针、剖切符号、索引符号'),
    'A-DOOR-IDEN':   (2, 0.18, 'Continuous', '门窗编号'),
    'A-TTLB':        (7, 0.50, 'Continuous', '图名、比例'),
    'A-FILL-IN':     (9, 0.00, 'Continuous', '展板风格：室内底色'),
    'A-FILL-OUT':    (9, 0.00, 'Continuous', '展板风格：室外/灰空间底色'),
}

# 纸面字高（mm）
TEXT = {
    'title': 7.0,       # 图名
    'room': 4.0,        # 房间名
    'area': 2.5,        # 面积、标高
    'note': 3.0,        # 一般说明
    'dim': 2.5,         # 尺寸数字
    'grid': 5.0,        # 轴号
    'tag': 2.5,         # 门窗编号
    'small': 2.0,
}
GRID_BUBBLE = 8.0       # 轴号圆直径（纸面 mm）
DIM_ROW = 7.0           # 尺寸线间距（纸面 mm）
DIM_FIRST = 10.0        # 第一道尺寸线离外墙外皮（纸面 mm）

FONT = 'simhei.ttf'     # 中文字体（AutoCAD 里显示为 SimHei 等价字体）
TEXT_STYLE = 'CN'

# 展板风格的淡彩（construction 风格不用颜色填充）
PRESENT = {
    'indoor': '#F7F3EA',      # 室内地面
    'outdoor': '#E4E2DC',     # 室外铺装
    'grey': '#EDEBE6',        # 檐下灰空间
    'green': '#DCE5D3',       # 绿地
    'water': '#CFE0E6',       # 水面
    'wall': '#2B2B2B',        # 墙体涂实
    'col': '#111111',
}

# 门窗、构件的默认构造尺寸（模型 mm）
DETAIL = {
    'door_leaf_t': 40,        # 门扇厚
    'window_frame': 60,       # 窗框宽（平面中三/四线的内外线间距用）
    'mullion_step': 1200,     # 幕墙竖梃间距
    'mullion_w': 60,
    'curtain_t': 150,         # 幕墙在平面上的总厚
    'apron': 600,             # 散水宽
    'step_tread': 300, 'step_rise': 150,
    'platform': 1500,         # 入口平台进深
    'insulation': 0,          # 外墙保温厚（0=不画）
}


def text_h(kind, scale):
    """纸面字高 → 模型空间高度。"""
    return TEXT[kind] * scale


def paper(mm, scale):
    """纸面毫米 → 模型毫米。"""
    return mm * scale

# 标注模块追加（2026-11-19）
LAYERS['A-ANNO-TABL'] = (7, 0.18, 'Continuous', '门窗表等表格内线')
