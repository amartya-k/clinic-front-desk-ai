"""Local demo website for the Clinic Front-Desk AI — hackathon v1 (2026-10-31).

Serves a single-page website that puts the real agent core on screen:
a patient phone (simulated SMS) on the left and the staff dashboard on
the right. Every button calls the same SmsFlow / Schedule / guardrails
code the tests exercise — nothing is mocked in the page itself.

Run:  python3 ui_server.py            (then open http://localhost:8765)
Port: PORT env var, default 8765. State lives in a temp dir and can be
wiped from the page (Reset demo) so video takes start clean.

Model mode follows meta_client.py: live when META_MODEL_API_KEY/URL are
set (or META_CLIENT_TRANSPORT=connector on the author's machine), stub
otherwise. Standard library only.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meta_client
from booking import Schedule
from sms_flow import SmsFlow

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.Lock()
_state = {}


def _fresh_flow():
    tmpdir = tempfile.mkdtemp(prefix="clinic-ui-")
    sched = Schedule(state_path=os.path.join(tmpdir, "sched.json"))
    flow = SmsFlow(state_path=os.path.join(tmpdir, "sms.json"), schedule=sched)
    _state["flow"] = flow
    _state["sched"] = sched


def _snapshot():
    flow, sched = _state["flow"], _state["sched"]
    snap = flow.dashboard_snapshot()
    snap["slots"] = sched.slots
    snap["requests"] = sched.requests
    snap["opted_out"] = flow.opted_out
    live = bool(meta_client.API_KEY and meta_client.ENDPOINT) \
        or meta_client.TRANSPORT == "connector"
    snap["mode"] = "live" if live else "stub"
    snap["model"] = meta_client.MODEL
    return snap


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console clean for the video
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        size = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(size) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(_HERE, "ui.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            with _LOCK:
                self._json(_snapshot())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            data = self._body()
            flow = _state["flow"]
            with _LOCK:
                if self.path == "/api/reset":
                    _fresh_flow()
                    return self._json({"ok": True, "state": _snapshot()})
                if self.path == "/api/missed-call":
                    r = flow.handle_missed_call(data["phone"], data.get("name", "Patient"))
                    return self._json({"result": r, "state": _snapshot()})
                if self.path == "/api/message":
                    r = flow.handle_inbound(data["phone"], data["text"])
                    return self._json({"result": r, "state": _snapshot()})
                if self.path == "/api/staff/takeover":
                    ok = flow.staff_takeover(data["phone"])
                    return self._json({"result": {"ok": ok}, "state": _snapshot()})
                if self.path == "/api/staff/release":
                    ok = flow.staff_release(data["phone"])
                    return self._json({"result": {"ok": ok}, "state": _snapshot()})
                if self.path == "/api/staff/send":
                    r = flow.staff_send(data["phone"], data["text"])
                    return self._json({"result": r, "state": _snapshot()})
                if self.path == "/api/escalation/resolve":
                    ok = flow.resolve_escalation(data["id"])
                    return self._json({"result": {"ok": ok}, "state": _snapshot()})
            self._json({"error": "not found"}, 404)
        except Exception as exc:  # surface errors to the page, never crash
            self._json({"error": str(exc)}, 500)


if __name__ == "__main__":
    _fresh_flow()
    port = int(os.environ.get("PORT", "8765"))
    print(f"Clinic Front-Desk AI demo at http://localhost:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
