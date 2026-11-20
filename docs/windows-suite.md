# Windows 桌面联动

一份规格生成 SketchUp 模型和视图、CAD 平立剖、展板 PNG，并可用 Photoshop 输出分层 PSD。仅出二维图可继续用 `wish-cad plan`。

## 安装

需要 Windows、Python 3.10+ 和桌面版 SketchUp。使用 PSD 或 AutoCAD 原生读回时，分别需要已安装且可运行的 Photoshop、AutoCAD。先完成这些软件自己的首次启动和登录。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install ".[desktop]"
.\.venv\Scripts\python tools/package_sketchup.py build/wish-suite-bridge.rbz
```

也可使用 GitHub 版本页附带的 `wish-suite-bridge.rbz`。在 SketchUp 的「扩展程序管理器 → 安装扩展程序」中选择此包，按应用提示审核。扩展包未签名。打开一个空白或已保存的模型，再选「扩展程序 → Wish Suite Bridge → Start Bridge」。

桥接仅监听本机回环地址。启动时在当前用户目录的 `WishSuite/bridge.json` 写入端口和随机访问凭据，Windows 上默认位于 `%LOCALAPPDATA%`。如需换位置，在启动 SketchUp 和调用 CLI 前，对两个进程设同一个 `WISH_SU_STATE` 文件路径。保留凭据在本机。完成后可从同一菜单停止桥接。

## 跑完整样例

```powershell
.\.venv\Scripts\wish-cad desktop-status
.\.venv\Scripts\wish-cad check examples/reading-room.json
.\.venv\Scripts\wish-cad build examples/reading-room.json --output build/reading-room-suite --ps --autocad
```

用一个新的输出目录。SketchUp 当前模型有未保存改动时会暂停建模。参数 `--ps` 要求分层 PSD，`--autocad` 要求 AutoCAD 逐张只读打开生成的 DXF。省略这两个参数仍会生成 SketchUp 模型、CAD 图和展板 PNG。DXF 是毫米制 1:1 模型空间图，原生 DWG 出版需在自己的 AutoCAD 里另存。

查看输出目录的 `report.json`。其中记录 SketchUp 保存重开、CAD 子报告、Photoshop 重开后的文字层和智能对象数量。若某阶段失败，已生成文件和失败明细会保留。遇到 Photoshop 字体错误时，可用 `WISH_PS_FONT` 指定已安装字体的 PostScript 名称，默认 `SimHei`。CAD/PDF 的字型文件另由 `WISH_CAD_FONT` 指定。AutoCAD 有多个版本时可用 `WISH_AUTOCAD_PROGID` 指定本机 COM ProgID。

## Codex MCP

愿意通过 Codex 运行时，按本地路径调整 [配置示例](../examples/codex-windows.toml) 的 `wish` 配置。它提供 `wish_check`、`wish_plan`、`wish_build`。

直接编辑已有 CAD 文件可另外安装 `.[autocad-mcp]` 并启用示例的 `cad_local`，设定允许访问的工作目录。它来自 [U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP)。SketchUp 桥接内复用了 [SAIE](https://github.com/iamahsanmehmood/saie) 的 Ruby 操作，自带原始 MIT 许可。
