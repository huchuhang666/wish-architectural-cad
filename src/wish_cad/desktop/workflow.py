"""Build drawings, a native SketchUp model and a layered Photoshop board."""

from dataclasses import replace
import json
from pathlib import Path

from .bridge import desktop_lock
from ..cli import build_plan


def run(model, output, ps=False, autocad=False, log=print):
    from . import board, sketchup

    output = output.resolve()
    with desktop_lock():
        output.mkdir(parents=True, exist_ok=False)
        design = replace(model, out_dir=output)
        result = {"ok": False, "warnings": design.warnings, "issues": [], "files": {}}
        report = output / "report.json"
        stage = "CAD"
        try:
            cad, cad_report = build_plan(design, output / "cad")
            result["files"]["cad_report"] = str(cad_report)
            result["issues"].extend(cad["issues"])
            if not cad["ok"]:
                return result, report

            if autocad:
                stage = "AutoCAD"
                from .autocad import readback
                result["autocad"] = readback([sheet["dxf"] for sheet in cad["sheets"].values()])

            stage = "SketchUp"
            su = sketchup.build(design, log=log)
            result["files"].update(su["files"])
            result["sketchup"] = {"save": su["save"], "reopened": su["reopened"]}
            for view, path in su["files"]["views"].items():
                if Path(path).stat().st_size == 0:
                    raise RuntimeError(f"Empty SketchUp view: {view}")

            stage = "board preview"
            floor = next(sheet for label, sheet in cad["sheets"].items() if label.startswith("plan:"))
            composition = board.layout(design, {"views": su["files"]["views"], "plan_png": floor["png"]})
            result["files"]["board_png"] = board.render_png(composition, output / "board.png")
            if ps:
                stage = "Photoshop"
                psd = board.render_psd(composition, output / "board.psd")
                result["files"]["board_psd"] = psd["psd"]
                result["photoshop"] = psd["check"]
            result["ok"] = True
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            result["issues"].append(f"{stage}: {exc}")
        finally:
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result, report
