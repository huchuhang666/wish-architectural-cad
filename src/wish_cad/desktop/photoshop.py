"""Run the suite's generated JSX in the local Photoshop process."""

from _ctypes import COMError
import json
from pathlib import Path
import sys
import time


def main():
    from photoshop import api

    script, receipt = map(Path, sys.argv[1:])
    source = script.read_text(encoding="utf-8-sig")
    deadline = time.monotonic() + 25
    app = None
    while True:
        try:
            if app is None:
                app = api.Application()
            version = app.version
            result = app.doJavaScript(source)
            break
        except COMError as exc:
            # RPC_E_SERVERCALL_RETRYLATER confirms COM rejected the invocation.
            if exc.hresult != -2147417846:
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError("Photoshop is busy. Close any dialogs and try a new output directory.") from exc
            time.sleep(0.5)
    receipt.write_text(json.dumps({"version": version, "returned": result}), encoding="utf-8")


if __name__ == "__main__":
    main()
