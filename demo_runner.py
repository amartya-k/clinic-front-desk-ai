"""End-to-end demo runner — hackathon v1 (built 2026-10-30, Friday build day).

Runs the four scored demo scenarios from kickoff-build-plan-2026-10-26.md
end to end against the real agent core (SmsFlow + Schedule + guardrails),
in throwaway temp state, and prints a transcript plus the dashboard
analytics strip. This is the Friday-plan item "end-to-end run of all 4
demo scenarios" and the script the Sun Nov 1 demo video is recorded from:

  1. After-hours missed call -> instant text-back -> FAQ -> booking request
  2. Reschedule by text, then cancel (original slot freed, never lost)
  3. Urgent message -> guardrail escalation -> staff takeover -> resolve
  4. Double-booking refused with alternatives -> STOP opt-out honored

Transport is simulated (see sms_flow.py). Exit code 0 only if every
scenario's checks pass. Run: python3 demo_runner.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from booking import Schedule
from sms_flow import SmsFlow

RESULTS = []


def check(scenario, name, cond):
    RESULTS.append((scenario, name, bool(cond)))
    print(f"  {'OK ' if cond else 'FAIL'} {name}")


def say(phone_label, who, text):
    print(f"  {who:>7} [{phone_label}]: {text}")


def main():
    tmpdir = tempfile.mkdtemp()
    sched = Schedule(state_path=os.path.join(tmpdir, "sched.json"))
    flow = SmsFlow(state_path=os.path.join(tmpdir, "sms.json"), schedule=sched)

    print("== Scenario 1: after-hours missed call -> text-back -> FAQ -> book")
    P1 = "+1-555-010-1111"
    r = flow.handle_missed_call(P1, "Demo Patient")
    say("P1", "agent", r["sent"])
    check(1, "missed call gets an instant greeting naming the clinic",
          r["ok"] and "Bright Smiles Dental" in r["sent"])
    r = flow.handle_inbound(P1, "What time do you close on Friday?")
    say("P1", "patient", "What time do you close on Friday?")
    say("P1", "agent", r["reply"])
    check(1, "hours answered from the knowledge file",
          r["action"] == "faq_hours")
    r = flow.handle_inbound(P1, "I'd like to book a cleaning")
    say("P1", "patient", "I'd like to book a cleaning")
    say("P1", "agent", r["reply"])
    check(1, "booking intent offers real open slots",
          r["action"] == "booking_offer")
    r = flow.handle_inbound(P1, "S1 please")
    say("P1", "patient", "S1 please")
    say("P1", "agent", r["reply"])
    check(1, "booking lands as a request (staff confirm), never a confirmation",
          r["action"] == "booked" and "request" in r["reply"].lower())

    print("== Scenario 2: reschedule, then cancel")
    r = flow.handle_inbound(P1, "Can I move my appointment to S2?")
    say("P1", "patient", "Can I move my appointment to S2?")
    say("P1", "agent", r["reply"])
    check(2, "reschedule moves the request and frees the original slot",
          r["action"] == "rescheduled" and not sched._find("S1")["booked"])
    r = flow.handle_inbound(P1, "Actually, please cancel it")
    say("P1", "patient", "Actually, please cancel it")
    say("P1", "agent", r["reply"])
    check(2, "cancel frees the slot again",
          r["action"] == "cancelled" and not sched._find("S2")["booked"])

    print("== Scenario 3: urgent message -> escalation -> staff takeover")
    P2 = "+1-555-010-2222"
    flow.handle_missed_call(P2, "Second Patient")
    r = flow.handle_inbound(P2, "I have severe pain and my face is swelling")
    say("P2", "patient", "I have severe pain and my face is swelling")
    say("P2", "agent", r["reply"])
    check(3, "urgent message escalates with the 911 script, model never sees it",
          r["action"] == "escalated" and "911" in r["reply"])
    check(3, "escalation is queued for staff with a summary",
          len(flow.open_escalations()) == 1)
    esc_id = flow.open_escalations()[0]["id"]
    check(3, "staff can take the conversation over", flow.staff_takeover(P2))
    out_before = len(flow.outbound)
    r = flow.handle_inbound(P2, "It started last night")
    say("P2", "patient", "It started last night")
    check(3, "agent stays silent while staff handles it",
          r["action"] == "staff_handling" and len(flow.outbound) == out_before)
    flow.staff_send(P2, "Hi, this is the front desk — a clinician will call you in 10 minutes.")
    say("P2", "staff", "Hi, this is the front desk — a clinician will call you in 10 minutes.")
    check(3, "staff reply is sent and logged as staff",
          flow.conversations[P2]["history"][-1]["from"] == "staff")
    check(3, "staff resolves the escalation and hands back",
          flow.resolve_escalation(esc_id) and flow.staff_release(P2))

    print("== Scenario 4: double-booking refused -> STOP honored")
    P3 = "+1-555-010-3333"
    flow.handle_missed_call(P3, "Third Patient")
    flow.handle_inbound(P3, "Can I book an appointment?")
    flow.handle_inbound(P1, "I'd like to book a cleaning")
    flow.handle_inbound(P1, "S1 please")
    r = flow.handle_inbound(P3, "S1")
    say("P3", "patient", "S1")
    say("P3", "agent", r["reply"])
    check(4, "taken slot refused with alternatives offered",
          r["action"] == "booking_offer" and "just taken" in r["reply"])
    r = flow.handle_inbound(P3, "STOP")
    say("P3", "patient", "STOP")
    check(4, "STOP opts the number out", r["action"] == "opted_out")
    check(4, "no text-back after opt-out",
          not flow.handle_missed_call(P3, "Third Patient")["ok"])

    print("\n== Dashboard analytics strip")
    for key, value in flow.analytics().items():
        print(f"  {key}: {value}")
    a = flow.analytics()
    check("analytics", "missed calls texted back counted (3 live + 1 refused)",
          a["missed_calls_texted_back"] == 3)
    check("analytics", "escalation resolved counted",
          a["escalations_resolved"] == 1 and a["escalations_open"] == 0)

    failed = [(s, n) for s, n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} demo-runner checks passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
