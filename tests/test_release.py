import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wish_cad import spec
from wish_cad.cli import build_plan, inspect_sheet, validate_release_spec

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "reading-room.json"


class ReleaseChecks(unittest.TestCase):
    def test_generation_errors_appear_in_report(self):
        model = spec.load(EXAMPLE)
        with TemporaryDirectory() as root, \
            patch("wish_cad.cad.drawing.draw_plan", side_effect=RuntimeError("plan interrupted")), \
            patch("wish_cad.cad.elevation.write_all", return_value={"section:A": {"error": "section interrupted"}}), \
            patch("wish_cad.cad.fonts.selected_font", return_value=Path("test-font.ttf")), \
            patch("ezdxf.fonts.fonts.load"), \
            patch("ezdxf.fonts.fonts.font_manager.has_font", return_value=True):
            report, path = build_plan(model, Path(root) / "new")
            self.assertFalse(report["ok"])
            self.assertTrue(any("plan interrupted" in s for s in report["issues"]))
            self.assertTrue(any("section interrupted" in s for s in report["issues"]))
            self.assertEqual(report, json.loads(path.read_text(encoding="utf-8")))

    def test_missing_pdf_and_dxf_repair_are_errors(self):
        with TemporaryDirectory() as root:
            dxf = Path(root) / "floor.dxf"
            png = Path(root) / "floor.png"
            dxf.write_text("DXF fixture", encoding="ascii")
            png.write_text("PNG fixture", encoding="ascii")
            problems = inspect_sheet("plan:1F", {"dxf": str(dxf), "png": str(png), "fixes": 1})
        self.assertTrue(any("Missing or empty PDF" in s for s in problems))
        self.assertTrue(any("1 fixes" in s for s in problems))

    def test_section_ids_cannot_escape_or_overwrite(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        section = raw["sections"][0]
        raw["sections"] += [dict(section, id="a"), dict(section, id="../escaped")]
        with self.assertRaisesRegex(ValueError, "Duplicate section ID"):
            validate_release_spec(spec.build_model(raw))
        with self.assertRaisesRegex(ValueError, "Invalid section ID"):
            validate_release_spec(spec.build_model(raw))


if __name__ == "__main__":
    unittest.main()
