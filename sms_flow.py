"""SMS trigger + text-back wiring and staff escalation queue — hackathon v1.

Built 2026-10-28, ahead of the Thu Oct 29 evening build block in
kickoff-build-plan-2026-10-26.md (v1 must-ship #1: "Missed-call trigger ->
instant SMS text-back", and #5: "Staff dashboard: ... escalation queue").

Extended 2026-10-30 (Friday build day): staff takeover (agent goes silent
on a taken-over conversation, guardrail hits still flagged to staff),
staff_send / staff_release, reschedule-by-text routing, and the dashboard
analytics strip (analytics()). Self-tests now 23/23; demo_runner.py runs
the four scored scenarios end to end (17/17).

Transport is SIMULATED: send_sms() logs to the outbound log instead of
calling a provider. That is the plan's sanctioned fallback ("Twilio or the
demo's simulated line") and needs no account or credentials. To go live,
replace the body of send_sms() with a Twilio Messages create call reading
TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER from env — every
caller in this module goes through that one seam.

Flow rules (from system_prompt.md, enforced deterministically):
- Guardrails run BEFORE anything else on every inbound message. A hit stops
  the agent flow, replies with the escalation script, and queues a staff
  escalation with a summary. The model never sees an escalated message.
- Bookings are requests, never confirmations — wording comes from booking.py.
- STOP opts the patient out; no further outbound messages to that number.
- Fictional demo data only (Bright Smiles Dental, 555 numbers).

State lives in sms_state.json next to this module (conversations, outbound
log, escalation queue). Booking state stays in booking.py's own file.
Delete the state files to reset the demo.
"""
import json
import os
import re

import guardrails
from booking import Schedule

_HERE = os.path.dirname(os.path.abspath(__file__))
_KNOWLEDGE_PATH = os.path.join(_HERE, "clinic_knowledge.json")
_DEFAULT_STATE_PATH = os.path.join(_HERE, "sms_state.json")
_PROMPT_PATH = os.path.join(_HERE, "system_prompt.md")

with open(_KNOWLEDGE_PATH) as f:
    _KNOWLEDGE = json.load(f)
CLINIC = _KNOWLEDGE["clinic"]

ESCALATION_REPLY = (
    "I'm sorry you're dealing with that — that's something our clinical "
    "team needs to handle directly. I've flagged your message as urgent "
    "for our staff with a summary, and if this is an emergency please "
    "call 911. A team member will reach out as soon as possible."
)
HANDOFF_REPLY = (
    "Of course — I'll hand this conversation to our front-desk team. "
    "I've flagged it for staff, and a real person will reply here as "
    "soon as possible."
)
GREETING = (
    "Hi, this is {name} — sorry we missed your call! I can answer quick "
    "questions, or help you book or reschedule a visit right here by text. "
    "What can I do for you?"
)


class SmsFlow:
    def __init__(self, state_path: str = _DEFAULT_STATE_PATH, schedule: Schedule = None):
        self.state_path = state_path
        self.schedule = schedule if schedule is not None else Schedule()
        if os.path.exists(state_path):
            with open(state_path) as f:
                saved = json.load(f)
            self.conversations = saved.get("conversations", {})
            self.outbound = saved.get("outbound", [])
            self.escalations = saved.get("escalations", [])
            self.opted_out = saved.get("opted_out", [])
            self.stats = saved.get("stats", {"missed_calls": 0})
        else:
            self.conversations = {}
            self.outbound = []
            self.escalations = []
            self.opted_out = []
            self.stats = {"missed_calls": 0}

    def _save(self):
        with open(self.state_path, "w") as f:
            json.dump({
                "conversations": self.conversations,
                "outbound": self.outbound,
                "escalations": self.escalations,
                "opted_out": self.opted_out,
                "stats": self.stats,
            }, f, indent=2)

    # -- transport seam ---------------------------------------------------
    def send_sms(self, phone: str, text: str):
        """Simulated transport: records the message instead of sending it.
        Swap this body for the real provider call to go live."""
        self.outbound.append({"to": phone, "text": text, "transport": "simulated"})
        self._save()
        return {"ok": True, "transport": "simulated"}

    # -- triggers ---------------------------------------------------------
    def handle_missed_call(self, phone: str, patient_label: str = "Patient") -> dict:
        """The missed-call trigger: instant text-back greeting, unless the
        number has opted out."""
        if phone in self.opted_out:
            return {"ok": False, "reason": "number opted out (STOP)"}
        conv = self.conversations.setdefault(phone, {
            "patient": patient_label, "awaiting": None, "history": [],
        })
        conv["patient"] = patient_label
        self.stats["missed_calls"] = self.stats.get("missed_calls", 0) + 1
        text = GREETING.format(name=CLINIC["name"])
        conv["history"].append({"from": "agent", "text": text})
        self._save()
        self.send_sms(phone, text)
        return {"ok": True, "sent": text}

    # -- inbound handling ---------------------------------------------------
    def handle_inbound(self, phone: str, message: str) -> dict:
        conv = self.conversations.setdefault(phone, {
            "patient": "Patient", "awaiting": None, "history": [],
        })
        conv["history"].append({"from": "patient", "text": message})

        # STOP first after logging: opt out and confirm once, nothing after.
        if message.strip().upper() == "STOP":
            if phone not in self.opted_out:
                self.opted_out.append(phone)
            conv["awaiting"] = None
            self._save()
            return {"action": "opted_out",
                    "reply": "You've been unsubscribed and won't receive more texts."}

        # Staff takeover: once a human has taken the conversation, the
        # agent logs but never auto-replies. A guardrail hit is still
        # reported in the result so staff sees the flag immediately.
        if conv.get("taken_over"):
            action, reason = guardrails.classify(message)
            self._save()
            return {"action": "staff_handling", "reply": None,
                    "guardrail": reason if action == "escalate" else None}

        # Guardrails before the model / any flow logic.
        action, reason = guardrails.classify(message)
        if action == "escalate":
            esc = self._escalate(phone, conv, message, reason)
            conv["awaiting"] = None
            self._save()
            reply = HANDOFF_REPLY if reason.startswith("human requested") \
                else ESCALATION_REPLY
            self.send_sms(phone, reply)
            return {"action": "escalated", "reply": reply,
                    "escalation": esc}

        reply, act = self._route(phone, conv, message)
        conv["history"].append({"from": "agent", "text": reply})
        self._save()
        self.send_sms(phone, reply)
        return {"action": act, "reply": reply}

    def _route(self, phone: str, conv: dict, message: str):
        text = message.strip()
        low = text.lower()

        # Slot choice reply while a booking offer is outstanding.
        if conv.get("awaiting") == "slot_choice":
            slot_id = self._parse_slot_choice(text, conv.get("offered", []))
            if not slot_id:
                # Patient named a real slot that wasn't offered because it
                # is already taken — treat it as a booking attempt on it so
                # they get the taken + alternatives reply, not a clarify.
                m = re.search(r"\b([A-Za-z]\d+)\b", text)
                if m:
                    cand = self.schedule._find(m.group(1).upper())
                    if cand is not None and cand.get("booked"):
                        slot_id = cand["id"]
            if slot_id:
                result = self.schedule.book(slot_id, conv["patient"])
                conv["awaiting"] = None
                if result["ok"]:
                    return result["message"] + " Anything else I can help with?", "booked"
                alts = result.get("alternatives", [])
                if alts:
                    conv["awaiting"] = "slot_choice"
                    conv["offered"] = [s["id"] for s in alts]
                    return ("That time was just taken — sorry! Still open: "
                            + self._slot_list(alts)
                            + " Reply with a slot ID (like S2) to request it."), "booking_offer"
                return ("That time was just taken and nothing else is open "
                        "that day. A team member can help find a time — "
                        "I've noted your request."), "booking_failed"
            return ("I didn't catch that — reply with a slot ID from the "
                    "list (like S1) and I'll request it for you."), "booking_clarify"

        if re.search(r"\b(book|appointment|cleaning|schedule|visit)\b", low) and \
                not re.search(r"\b(reschedule|move|cancel)\b", low):
            slots = self.schedule.open_slots()
            if not slots:
                return ("We're fully booked in the current schedule — a team "
                        "member can help find a time. I've noted your request."), "booking_none"
            conv["awaiting"] = "slot_choice"
            conv["offered"] = [s["id"] for s in slots]
            return ("Happy to help! Open times: " + self._slot_list(slots)
                    + " Reply with a slot ID (like S1) to request one — "
                    "staff confirm every request."), "booking_offer"

        if re.search(r"\b(reschedule|move|change)\b", low):
            mine = [r for r in self.schedule.requests
                    if r["patient"] == conv["patient"] and r["status"] == "requested"]
            m = re.search(r"\b([A-Za-z]\d+)\b", text)
            if mine and m:
                res = self.schedule.reschedule(mine[-1]["slot_id"],
                                               m.group(1).upper())
                if res["ok"]:
                    return res["message"] + " Anything else I can help with?", "rescheduled"
                alts = res.get("alternatives", [])
                if alts:
                    return ("That time isn't open — still available: "
                            + self._slot_list(alts)
                            + " Tell me a slot ID and I'll move your "
                            "request there."), "reschedule_failed"
                return ("I couldn't move that request — a team member "
                        "can help find a new time."), "reschedule_failed"
            if mine:
                slots = self.schedule.open_slots()
                return ("Sure — open times: " + self._slot_list(slots)
                        + " Tell me a slot ID and I'll move your request "
                        "there."), "reschedule_offer"
            return ("I don't see an open appointment request under your "
                    "name here — a team member can help with changes."), "reschedule_none"

        if re.search(r"\b(cancel)\b", low):
            mine = [r for r in self.schedule.requests
                    if r["patient"] == conv["patient"] and r["status"] == "requested"]
            if mine:
                res = self.schedule.cancel_request(mine[-1]["slot_id"])
                if res["ok"]:
                    return ("Done — I've cancelled that appointment request. "
                            "Anything else I can help with?"), "cancelled"
            return ("I don't see an open appointment request under your "
                    "name here — a team member can help with cancellations."), "cancel_none"

        if re.search(r"\b(hour|open|close|closing)\b", low):
            h = CLINIC["hours"]
            return (f"Our hours: Mon–Thu {h['mon_thu']}, Fri {h['fri']}, "
                    f"weekends {h['sat_sun'].lower()}. ({h['lunch']}.) "
                    "Anything else I can help with?"), "faq_hours"

        if re.search(r"\b(park|address|located|directions)\b", low):
            return (f"We're at {CLINIC['address']}. Free parking is in the "
                    "lot directly in front of the suite. "
                    "Anything else I can help with?"), "faq_location"

        # Empty / gibberish fallback (hardening 2026-10-31): never send a
        # content-free message to the model — ask a clarifying question.
        words = set(re.findall(r"[a-z']+", low))
        _COMMON = {"i", "you", "what", "when", "where", "how", "can", "do",
                   "does", "is", "are", "my", "me", "to", "the", "a", "an",
                   "please", "thanks", "hi", "hello", "it", "this", "that",
                   "have", "has", "need", "want", "like", "would", "for",
                   "and", "or", "your", "we", "am", "was", "will", "get"}
        if not words or not (words & _COMMON):
            return ("Sorry, I didn't catch that — could you tell me a "
                    "little more? I can help with booking, rescheduling, "
                    "hours, or directions."), "clarify"

        # Fallback: the model (stub until the Meta credits/key land).
        import meta_client
        with open(_PROMPT_PATH) as f:
            prompt = f.read().replace("{clinic_name}", CLINIC["name"])
        return meta_client.chat(prompt, message), "model_reply"

    @staticmethod
    def _slot_list(slots):
        return "; ".join(
            f"{s['id']}: {s['date']} at {s['time']} ({s['type']}, {s['provider']})"
            for s in slots) + "."

    @staticmethod
    def _parse_slot_choice(text: str, offered: list):
        m = re.search(r"\b([A-Za-z]\d+)\b", text)
        if m and m.group(1).upper() in offered:
            return m.group(1).upper()
        return None

    # -- escalation queue (the staff-dashboard data layer) ------------------
    def _escalate(self, phone: str, conv: dict, message: str, reason: str) -> dict:
        esc = {
            "id": f"E{len(self.escalations) + 1}",
            "patient": conv["patient"],
            "phone": phone,
            "reason": reason,
            "message": message,
            "summary": f"{conv['patient']} ({phone}): {reason}. Message: {message!r}",
            "status": "open",
        }
        self.escalations.append(esc)
        return esc

    def open_escalations(self):
        return [e for e in self.escalations if e["status"] == "open"]

    def resolve_escalation(self, escalation_id: str) -> bool:
        for e in self.escalations:
            if e["id"] == escalation_id and e["status"] == "open":
                e["status"] = "resolved"
                self._save()
                return True
        return False

    # -- staff takeover + analytics (dashboard actions) ---------------------
    def staff_takeover(self, phone: str) -> bool:
        """A staff member takes the conversation: the agent stops
        auto-replying to this number until staff hands it back."""
        conv = self.conversations.get(phone)
        if conv is None:
            return False
        conv["taken_over"] = True
        self._save()
        return True

    def staff_release(self, phone: str) -> bool:
        """Hand the conversation back to the agent."""
        conv = self.conversations.get(phone)
        if conv is None:
            return False
        conv["taken_over"] = False
        self._save()
        return True

    def staff_send(self, phone: str, text: str) -> dict:
        """Staff replies directly (through the same transport seam)."""
        conv = self.conversations.setdefault(phone, {
            "patient": "Patient", "awaiting": None, "history": [],
        })
        conv["history"].append({"from": "staff", "text": text})
        self._save()
        return self.send_sms(phone, text)

    def analytics(self) -> dict:
        """The dashboard analytics strip: what the agent rescued and did."""
        requests = self.schedule.requests
        return {
            "missed_calls_texted_back": self.stats.get("missed_calls", 0),
            "conversations": len(self.conversations),
            "booking_requests_open": sum(
                1 for r in requests if r["status"] == "requested"),
            "booking_requests_total": len(requests),
            "escalations_open": len(self.open_escalations()),
            "escalations_resolved": sum(
                1 for e in self.escalations if e["status"] == "resolved"),
            "numbers_opted_out": len(self.opted_out),
            "messages_sent": len(self.outbound),
        }

    def dashboard_snapshot(self) -> dict:
        """Everything the staff dashboard renders, in one read."""
        return {
            "conversations": self.conversations,
            "open_escalations": self.open_escalations(),
            "outbound": self.outbound,
            "analytics": self.analytics(),
        }


if __name__ == "__main__":
    import tempfile
    tmpdir = tempfile.mkdtemp()
    sched = Schedule(state_path=os.path.join(tmpdir, "sched.json"))
    flow = SmsFlow(state_path=os.path.join(tmpdir, "sms.json"), schedule=sched)
    tests = []

    def check(name, cond):
        tests.append((name, cond))
        print(f"{'OK ' if cond else 'FAIL'} {name}")

    P1, P2 = "+1-555-010-1111", "+1-555-010-2222"

    r = flow.handle_missed_call(P1, "Demo Patient")
    check("missed call sends instant greeting naming the clinic",
          r["ok"] and "Bright Smiles Dental" in r["sent"])
    check("greeting went through the (simulated) transport",
          flow.outbound and flow.outbound[-1]["to"] == P1)

    r = flow.handle_inbound(P1, "What time do you close on Friday?")
    check("hours FAQ answered from the knowledge file",
          r["action"] == "faq_hours" and "2:00 PM" in r["reply"])

    r = flow.handle_inbound(P1, "I'd like to book a cleaning")
    check("booking intent offers open slots",
          r["action"] == "booking_offer" and "S1" in r["reply"])
    r = flow.handle_inbound(P1, "S1 please")
    check("slot choice books a request, worded as a request",
          r["action"] == "booked" and "request" in r["reply"].lower()
          and sched._find("S1")["booked"])

    flow.handle_missed_call(P2, "Second Patient")
    flow.handle_inbound(P2, "Can I book an appointment?")
    r = flow.handle_inbound(P2, "S1")
    check("double-booking refused with alternatives offered",
          r["action"] == "booking_offer" and "just taken" in r["reply"])

    r = flow.handle_inbound(P2, "I have severe pain and my face is swelling")
    check("urgent message escalates with the 911 script",
          r["action"] == "escalated" and "911" in r["reply"])
    check("escalation queued for staff with a summary",
          len(flow.open_escalations()) == 1
          and "Second Patient" in flow.open_escalations()[0]["summary"])

    r = flow.handle_inbound(P1, "My SSN is 123-45-6789, is my paperwork ready?")
    check("sensitive identifier escalates before any flow",
          r["action"] == "escalated" and len(flow.open_escalations()) == 2)

    check("staff can resolve an escalation",
          flow.resolve_escalation("E1") and len(flow.open_escalations()) == 1)

    r = flow.handle_inbound(P1, "STOP")
    check("STOP opts the number out", r["action"] == "opted_out")
    check("no text-back after opt-out",
          not flow.handle_missed_call(P1, "Demo Patient")["ok"])

    snap = flow.dashboard_snapshot()
    check("dashboard snapshot carries conversations + queue + outbound",
          P2 in snap["conversations"] and len(snap["open_escalations"]) == 1
          and len(snap["outbound"]) >= 5)

    # Staff takeover + analytics (added 2026-10-30, Friday build day).
    P3 = "+1-555-010-3333"
    flow.handle_missed_call(P3, "Third Patient")
    check("staff takeover engages on a live conversation",
          flow.staff_takeover(P3))
    out_before = len(flow.outbound)
    r = flow.handle_inbound(P3, "What time do you close on Friday?")
    check("agent stays silent while staff handles the conversation",
          r["action"] == "staff_handling" and r["reply"] is None
          and len(flow.outbound) == out_before)
    r = flow.handle_inbound(P3, "I have severe pain and swelling")
    check("guardrail hit is still flagged to staff during takeover",
          r["action"] == "staff_handling" and r["guardrail"])
    flow.staff_send(P3, "Hi, this is the front desk — taking over from here.")
    check("staff reply goes out through the transport and is logged as staff",
          flow.outbound[-1]["to"] == P3
          and flow.conversations[P3]["history"][-1]["from"] == "staff")
    check("staff release hands the conversation back",
          flow.staff_release(P3))
    r = flow.handle_inbound(P3, "What time do you close on Friday?")
    check("agent answers again after release", r["action"] == "faq_hours")

    r = flow.handle_inbound(P1, "Can I move my appointment to S2?")
    check("reschedule by text moves the request, original freed",
          r["action"] == "rescheduled"
          and sched._find("S2")["booked"] and not sched._find("S1")["booked"])

    a = flow.analytics()
    check("analytics counts missed calls texted back (3)",
          a["missed_calls_texted_back"] == 3)
    check("analytics counts open + resolved escalations",
          a["escalations_open"] == 1 and a["escalations_resolved"] == 1)
    check("analytics snapshot rides along in the dashboard snapshot",
          snap["analytics"]["conversations"] == 2)

    failed = [n for n, ok in tests if not ok]
    print(f"\n{len(tests) - len(failed)}/{len(tests)} sms-flow tests passed")
    if failed:
        raise SystemExit(1)
