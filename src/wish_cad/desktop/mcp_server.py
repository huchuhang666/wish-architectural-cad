"""Small MCP entrypoint for spec-based Wish jobs."""

from threading import RLock
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP

from .workflow import run
from .. import spec
from ..cli import build_plan, validate_release_spec

server = FastMCP("Wish SU/CAD/PS")
drawing_lock = RLock()


def load(path):
    model = spec.load(Path(path))
    validate_release_spec(model)
    return model


@server.tool()
def wish_check(path: str) -> dict:
    """Validate a local JSON building specification without starting desktop applications."""
    return spec.summary(load(path))


@server.tool()
def wish_plan(path: str, output: str) -> dict:
    """Generate editable DXF, PDF and PNG in a new local output directory."""
    with drawing_lock:
        result, report = build_plan(load(path), Path(output))
    return {"ok": result["ok"], "report": str(report), "issues": result["issues"]}


@server.tool()
def wish_build(path: str, output: str, photoshop: bool = False, autocad: bool = False) -> dict:
    """Build a new SketchUp model, CAD drawings and board. Request Photoshop for an editable PSD."""
    with drawing_lock:
        result, report = run(load(path), Path(output), ps=photoshop, autocad=autocad, log=lambda msg: print(msg, file=sys.stderr))
    return {"ok": result["ok"], "report": str(report), "issues": result["issues"]}


def main():
    server.run()


if __name__ == "__main__":
    main()
