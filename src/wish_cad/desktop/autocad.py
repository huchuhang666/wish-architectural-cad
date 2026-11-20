"""Read freshly generated sheets with the installed Windows AutoCAD COM host."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def readback(drawings):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "wish_cad.desktop.autocad", *drawings],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("AutoCAD read-back timed out. Check the application for a dialog.") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-1200:] or "AutoCAD read-back failed.")
    return json.loads(result.stdout)


def main():
    from win32com.client import Dispatch

    app = Dispatch(os.environ.get("WISH_AUTOCAD_PROGID", "AutoCAD.Application"))
    from pywintypes import com_error

    # Dispatch can return before the application's Documents collection is ready.
    deadline = time.monotonic() + 25
    while True:
        try:
            opener = app.Documents.Open
            if app.GetAcadState().IsQuiescent:
                break
        except (AttributeError, com_error):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("AutoCAD is busy or still starting. Open it and close any dialogs.")
        time.sleep(0.5)
    reports = []
    for filename in sys.argv[1:]:
        path = Path(filename).resolve()
        doc = opener(str(path), True)
        try:
            count = int(doc.ModelSpace.Count)
            if count < 1:
                raise RuntimeError(f"AutoCAD read an empty drawing: {path.name}")
            reports.append({"drawing": path.name, "entities": count, "version": str(app.Version)})
        finally:
            doc.Close(False)
    print(json.dumps(reports, ensure_ascii=True))


if __name__ == "__main__":
    main()
