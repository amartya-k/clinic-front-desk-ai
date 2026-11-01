"""Booking / reschedule flow against the mock schedule — hackathon v1.

Built 2026-10-28, ahead of the Wed-evening build block in
kickoff-build-plan-2026-10-26.md (v1 must-ship #3: "Book / reschedule flow
against a mock schedule with real slot logic").

Rules carried from system_prompt.md:
- A booking made here is a REQUEST, never a confirmed appointment — staff
  confirm. Every result string says so; nothing here may be worded as a
  confirmation.
- Fictional demo data only (Bright Smiles Dental). No real patient data.
- State is kept in a separate JSON file (default: schedule_state.json next
  to this module) so clinic_knowledge.json — the agent's only source of
  facts — is never mutated by booking activity. Delete the state file to
  reset the demo.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_KNOWLEDGE_PATH = os.path.join(_HERE, "clinic_knowledge.json")
_DEFAULT_STATE_PATH = os.path.join(_HERE, "schedule_state.json")


class Schedule:
    def __init__(self, state_path: str = _DEFAULT_STATE_PATH):
        self.state_path = state_path
        with open(_KNOWLEDGE_PATH) as f:
            knowledge = json.load(f)
        base_slots = knowledge["mock_schedule"]["slots"]
        self.timezone = knowledge["mock_schedule"].get("timezone", "")
        if os.path.exists(state_path):
            with open(state_path) as f:
                saved = json.load(f)
            self.slots = saved["slots"]
            self.requests = saved.get("requests", [])
        else:
            self.slots = [dict(s) for s in base_slots]
            self.requests = []

    def _save(self):
        with open(self.state_path, "w") as f:
            json.dump({"slots": self.slots, "requests": self.requests}, f, indent=2)

    # -- queries ---------------------------------------------------------
    def open_slots(self, date: str = None, slot_type: str = None):
        """Open slots, optionally filtered by date (YYYY-MM-DD) and type."""
        out = []
        for s in self.slots:
            if s["booked"]:
                continue
            if date and s["date"] != date:
                continue
            if slot_type and s["type"] != slot_type:
                continue
            out.append(dict(s))
        return sorted(out, key=lambda s: (s["date"], s["time"]))

    def _find(self, slot_id: str):
        for s in self.slots:
            if s["id"] == slot_id:
                return s
        return None

    # -- actions ---------------------------------------------------------
    def book(self, slot_id: str, patient_label: str) -> dict:
        """Mark a slot requested. Returns a result dict; 'ok' False with a
        'reason' when the slot is unknown or already taken."""
        slot = self._find(slot_id)
        if slot is None:
            return {"ok": False, "reason": f"unknown slot {slot_id!r}"}
        if slot["booked"]:
            return {"ok": False, "reason": f"slot {slot_id} is already booked",
                    "alternatives": self.open_slots(slot["date"], slot["type"])}
        slot["booked"] = True
        slot["patient"] = patient_label
        req = {"slot_id": slot_id, "patient": patient_label, "status": "requested"}
        self.requests.append(req)
        self._save()
        return {"ok": True, "request": req, "slot": dict(slot),
                "message": (f"Requested {slot['date']} at {slot['time']} with "
                            f"{slot['provider']} ({slot['type']}). This is a "
                            "request — staff will confirm.")}

    def reschedule(self, from_slot_id: str, to_slot_id: str) -> dict:
        """Move a booked slot's patient to a new open slot. Frees the old
        slot only when the new booking succeeds, so a failed move never
        loses the original appointment request."""
        old = self._find(from_slot_id)
        if old is None or not old.get("booked"):
            return {"ok": False, "reason": f"slot {from_slot_id!r} is not booked"}
        patient = old.get("patient", "patient")
        result = self.book(to_slot_id, patient)
        if not result["ok"]:
            return result
        old["booked"] = False
        old.pop("patient", None)
        for r in self.requests:
            if r["slot_id"] == from_slot_id and r["status"] == "requested":
                r["status"] = "moved"
        self._save()
        return {"ok": True, "moved_from": from_slot_id, "request": result["request"],
                "slot": result["slot"],
                "message": (f"Moved your request to {result['slot']['date']} at "
                            f"{result['slot']['time']}. Staff will confirm the "
                            "new time.")}

    def cancel_request(self, slot_id: str) -> dict:
        slot = self._find(slot_id)
        if slot is None or not slot.get("booked"):
            return {"ok": False, "reason": f"slot {slot_id!r} is not booked"}
        slot["booked"] = False
        slot.pop("patient", None)
        for r in self.requests:
            if r["slot_id"] == slot_id and r["status"] == "requested":
                r["status"] = "cancelled"
        self._save()
        return {"ok": True, "message": f"Request for slot {slot_id} cancelled."}


if __name__ == "__main__":
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "state.json")
    sched = Schedule(state_path=tmp)
    tests = []

    def check(name, cond):
        tests.append((name, cond))
        print(f"{'OK ' if cond else 'FAIL'} {name}")

    check("3 open slots initially (S4 pre-booked)", len(sched.open_slots()) == 3)
    check("open_slots filters by type", len(sched.open_slots(slot_type="cleaning")) == 2)
    r = sched.book("S1", "Demo Patient")
    check("book S1 ok, worded as request", r["ok"] and "request" in r["message"].lower())
    check("double-book S1 refused with alternatives",
          not sched.book("S1", "Other")["ok"])
    check("book unknown slot refused", not sched.book("ZZ", "X")["ok"])
    r = sched.reschedule("S1", "S3")
    check("reschedule S1->S3 ok", r["ok"])
    check("old slot freed after move", any(s["id"] == "S1" for s in sched.open_slots()))
    check("failed reschedule keeps original",
          not sched.reschedule("S3", "S4")["ok"] and sched._find("S3")["booked"])
    # persistence: a fresh Schedule over the same state file sees the move
    sched2 = Schedule(state_path=tmp)
    check("state persists across reload", sched2._find("S3").get("patient") == "Demo Patient")
    check("cancel frees the slot", sched2.cancel_request("S3")["ok"]
          and any(s["id"] == "S3" for s in sched2.open_slots()))

    failed = [n for n, ok in tests if not ok]
    print(f"\n{len(tests) - len(failed)}/{len(tests)} booking tests passed")
    if failed:
        raise SystemExit(1)
