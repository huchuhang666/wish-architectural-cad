"""Connect to the Wish SketchUp extension over the authenticated loopback RPC."""

from contextlib import contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import time


def state_path():
    default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WishSuite" / "bridge.json"
    return Path(os.environ.get("WISH_SU_STATE", default)).expanduser().resolve()


class RPC:
    def __enter__(self):
        try:
            config = json.loads(state_path().read_text(encoding="utf-8"))
            self.port = int(config["port"])
            self.token = config["token"]
            if not 1 <= self.port <= 65535 or len(self.token) != 64:
                raise ValueError("Invalid bridge state.")
            self.hello = self.call("hello")["result"]
            if self.hello.get("bridge") != "wish-suite":
                raise ValueError("Unexpected SketchUp bridge.")
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(
                "Open SketchUp and choose Extensions > Wish Suite Bridge > Start Bridge. "
                f"State file: {state_path()}. {exc}"
            ) from exc
        return self

    def __exit__(self, *args):
        return False

    def call(self, method, params=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=45)
        try:
            body = json.dumps({"method": method, "params": params or {}}).encode("utf-8")
            conn.request(
                "POST",
                "/rpc",
                body=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            )
            reply = conn.getresponse()
            if reply.status != 200:
                raise RuntimeError(f"SketchUp bridge returned HTTP {reply.status}.")
            result = json.loads(reply.read())
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(f"SketchUp did not confirm {method}. Inspect the model before retrying.") from exc
        finally:
            conn.close()
        if result.get("error"):
            raise RuntimeError(f"{method}: {result['error']}")
        return result

    def ruby(self, code):
        result = self.call("ops.execute_ruby", {"code": code})["result"]
        if not result.get("success"):
            raise RuntimeError(str(result.get("error") or result))
        return result["result"]

    def wait_job(self, job, timeout=120):
        terminal = {"saved_requires_reopen_validation", "save_failed", "failed_before_mutation"}
        report = Path(job["report"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if job.get("status") in terminal:
                return job
            time.sleep(0.35)
            try:
                job = json.loads(report.read_text(encoding="utf-8-sig"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
        raise TimeoutError(f"SketchUp save is unconfirmed. Check the existing receipt before retrying: {report}")


@contextmanager
def desktop_lock():
    """Prevent concurrent Wish builds from switching the same active SketchUp model."""
    if os.name != "nt":
        raise RuntimeError("The desktop suite requires Windows.")
    import msvcrt

    path = state_path().with_name("build.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Another Wish desktop build is in progress.") from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
