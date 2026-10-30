"""sandboxd persistent Python kernel driver.

Protocol: requests are JSON lines on stdin ({"id": n, "code": "..."}); responses are
JSON lines on fd 3 ({"id": n, "ok": bool, "outputs": [...]}) so that anything user code
writes to the real stdout/stderr file descriptors cannot corrupt the protocol.
"""
import ast
import base64
import io
import json
import os
import sys
import traceback

proto = os.fdopen(3, "w", buffering=1)
user_globals = {"__name__": "__main__", "__builtins__": __builtins__}

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
except Exception:  # matplotlib optional
    _plt = None


class _Capture(io.TextIOBase):
    def __init__(self, name, outputs):
        self.name = name
        self.outputs = outputs

    def writable(self):
        return True

    def write(self, s):
        if not s:
            return 0
        last = self.outputs[-1] if self.outputs else None
        if last and last.get("type") == "stream" and last.get("name") == self.name:
            last["text"] += s
        else:
            self.outputs.append({"type": "stream", "name": self.name, "text": s})
        return len(s)

    def flush(self):
        pass


def _figures(outputs):
    if _plt is None:
        return
    for num in _plt.get_fignums():
        fig = _plt.figure(num)
        buf = io.BytesIO()
        try:
            fig.savefig(buf, format="png", bbox_inches="tight")
            outputs.append({"type": "image", "mime": "image/png", "data": base64.b64encode(buf.getvalue()).decode()})
        except Exception:
            pass
    _plt.close("all")


def _run(code, outputs):
    tree = ast.parse(code, "<cell>", "exec")
    last_expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = ast.Expression(tree.body.pop().value)
    exec(compile(tree, "<cell>", "exec"), user_globals)
    if last_expr is not None:
        value = eval(compile(last_expr, "<cell>", "eval"), user_globals)
        if value is not None:
            if _plt is not None and type(value).__module__.startswith("matplotlib"):
                return
            outputs.append({"type": "stream", "name": "stdout", "text": repr(value) + "\n"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        outputs = []
        ok = True
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Capture("stdout", outputs), _Capture("stderr", outputs)
        try:
            _run(req.get("code", ""), outputs)
        except BaseException as e:  # noqa: BLE001 - report everything incl. SystemExit
            ok = False
            tb = traceback.format_exception(type(e), e, e.__traceback__)
            outputs.append({"type": "error", "ename": type(e).__name__, "evalue": str(e), "traceback": "".join(tb)})
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        try:
            _figures(outputs)
        except Exception:
            pass
        proto.write(json.dumps({"id": req.get("id"), "ok": ok, "outputs": outputs}) + "\n")


if __name__ == "__main__":
    main()
