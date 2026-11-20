"""Validate a building specification and produce an auditable 2D drawing set."""

import argparse
import json
import math
from pathlib import Path
import re
import sys

from . import spec


def validate_release_spec(model):
    problems = []
    if not model.segments:
        problems.append("At least one wall or an outline is required to draw a floor plan.")
    layer_ids = [floor.id.casefold() for floor in model.levels]
    if len(set(layer_ids)) != len(layer_ids):
        problems.append("Level IDs must be unique, ignoring case.")
    section_ids = set()
    for section in model.sections:
        key = section["id"]
        if not re.fullmatch(r"[\w-]+", key):
            problems.append(f"Invalid section ID: {key!r}. Use letters, digits, underscores or hyphens.")
        if key.casefold() in section_ids:
            problems.append(f"Duplicate section ID: {key!r}.")
        section_ids.add(key.casefold())
        if section["a"] == section["b"] or section["look"] not in {"left", "right"}:
            problems.append(f"Section {key!r} needs distinct endpoints and look set to left or right.")
    scale = model.drawing["scale"]
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        problems.append("drawing.scale must be a finite positive number.")
    if problems:
        raise ValueError("\n".join(problems))


def inspect_sheet(label, result):
    issues = []
    if result.get("error"):
        issues.append(result["error"])
    if result.get("render") and not result.get("pdf"):
        issues.append(result["render"])
    if result.get("missing_modules"):
        issues.append(f"Missing drawing modules: {', '.join(result['missing_modules'])}.")
    if result.get("errors") or result.get("fixes"):
        issues.append(f"DXF audit reported {result.get('errors', 0)} errors and {result.get('fixes', 0)} fixes.")
    if result.get("entities") == 0:
        issues.append("The DXF model space is empty.")
    for kind in ("dxf", "pdf", "png"):
        value = result.get(kind)
        if not value or not Path(value).is_file() or Path(value).stat().st_size == 0:
            issues.append(f"Missing or empty {kind.upper()}.")
    return [f"{label}: {issue}" for issue in issues]


def build_plan(model, output):
    from .cad import standard
    from .cad.fonts import selected_font
    from ezdxf.fonts import fonts

    font = selected_font()
    fonts.load()
    if not fonts.font_manager.has_font(font.name):
        fonts.font_manager.build([str(font.parent)], support_dirs=False)
    if not fonts.font_manager.has_font(font.name):
        raise ValueError(f"ezdxf could not load the selected CAD font: {font}")
    standard.FONT = font.name

    from .cad.drawing import draw_plan
    from .cad.elevation import write_all as draw_elevations

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sheets = {}
    for floor in model.levels:
        if not any(segment.level == floor.id for segment in model.segments):
            continue
        label = f"plan:{floor.id}"
        try:
            sheets[label] = draw_plan(model, floor.id, output, name=f"平面_{floor.id}")
        except Exception as exc:
            sheets[label] = {"error": f"{type(exc).__name__}: {exc}"}
    sides = ("S", "N", "E", "W") if model.drawing.get("elevations", True) else ()
    sheets.update(draw_elevations(model, output, sides=sides))

    issues = [message for label, result in sheets.items() for message in inspect_sheet(label, result)]
    report = {
        "ok": not issues,
        "warnings": model.warnings,
        "issues": issues,
        "sheets": sheets,
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, report_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Validate a JSON specification.").add_argument("spec", type=Path)
    plan = commands.add_parser("plan", help="Generate DXF, PDF and PNG drawing sheets.")
    plan.add_argument("spec", type=Path)
    plan.add_argument("--output", type=Path, help="New output directory. Existing directories are preserved.")
    args = parser.parse_args(argv)

    try:
        model = spec.load(args.spec)
        validate_release_spec(model)
        if args.command == "check":
            print(json.dumps(spec.summary(model), ensure_ascii=False, indent=2))
            return 0
        report, report_path = build_plan(model, args.output or model.out_dir)
        print(f"{len(report['sheets'])} sheet(s). Report: {report_path}")
        for issue in report["issues"]:
            print(issue, file=sys.stderr)
        return 0 if report["ok"] else 1
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        print(f"wish-cad: {exc}", file=sys.stderr)
        return 1
