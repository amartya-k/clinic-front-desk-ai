# Clinic Front-Desk AI — missed-call text-back agent

A front-desk AI for small clinics that rescues missed patient calls. When a
call is missed (or a patient texts after hours), the agent texts back
instantly, answers routine questions from a clinic knowledge file, books /
reschedules / cancels appointments conversationally as **requests staff
confirm**, and escalates anything clinical or urgent to staff **before the
model ever sees the message**.

Built for the Meta AI Developer Hackathon (Cerebral Valley), Indie Developer
track. Demo clinic is fictional — Bright Smiles Dental — and no real patient
data is used anywhere.

## Why

Small clinics lose patients at the front desk: calls missed after hours or
during lunch go to voicemail, and most callers never call back. This agent
turns every missed call into a text conversation that can answer the routine
80% (hours, directions, what to bring, refill/paperwork status routing),
capture a booking request, and hand the rest to a human — safely.

## How it works

```
missed call / inbound SMS
        │
        ▼
  guardrails.py ── deterministic classifier, runs FIRST, before any LLM call
        │            urgent / clinical / sensitive-data / "talk to a human"
        │            → escalation script + staff queue; model never sees it
        ▼ allow
  sms_flow.py ──── conversation state, staff takeover / send / release,
        │            STOP opt-out, dashboard analytics
        ├──► booking.py ── mock schedule with real slot logic;
        │                   bookings are REQUESTS, never confirmations;
        │                   a failed reschedule never loses the original slot
        └──► meta_client.py ── Meta Model API (Muse Spark) chat call,
                                grounded by system_prompt.md + the clinic
                                knowledge file only
```

- **Guardrails are deterministic, not model-decided** (`guardrails.py`).
  Urgent symptoms (including Spanish terms and reversed phrasings like
  "my chest is hurting"), clinical asks (diagnosis, dosage, prescriptions),
  sensitive identifiers (SSN / card-shaped numbers), and human-handoff
  requests all escalate with a fixed script. Gibberish gets a clarify reply
  instead of a model call.
- **The agent may only answer from the knowledge file**
  (`clinic_knowledge.json`, rules in `system_prompt.md`). No invented
  prices, insurance, or availability; unknown → say so and offer a staff
  callback.
- **Staff stay in control.** Staff can take over any conversation (the agent
  goes silent), reply as staff, resolve the escalation, and hand back.
  The dashboard snapshot carries an analytics strip: missed calls texted
  back, booking requests, escalations, opt-outs.

## Run it

No dependencies beyond Python 3 (standard library only).

```bash
cd agent-core
python3 demo_runner.py   # the 4 scored demo scenarios, end to end (17/17)
python3 sms_flow.py      # SMS flow + staff takeover self-tests (23/23)
python3 booking.py       # booking / reschedule / cancel self-tests (10/10)
python3 guardrails.py    # guardrail classifier cases (11/11)
```

`demo_runner.py` runs in throwaway temp state and exits 0 only if every
check passes. To reset a manual run, delete `schedule_state.json` /
`sms_state.json` next to the modules.

## Live vs stub mode

`meta_client.py` calls the Meta Model API when both env vars are set, and
otherwise runs in stub mode against the same interface:

```bash
export META_MODEL_API_KEY=...        # set via secure entry, never committed
export META_MODEL_API_URL=...        # endpoint from the organizers' docs
export META_MODEL_NAME=muse-spark    # optional; this is the default
python3 meta_client.py               # prints "mode: live" or "mode: stub"
```

Current status (2026-10-31): the $150 hackathon credits claim has been
submitted and is awaiting batch crediting, so the build is being hardened
and demoed in stub mode; the live flip is only the env vars above.
SMS transport is likewise simulated behind one seam (`send_sms()` in
`sms_flow.py`) — swapping in Twilio is a single-function change.

## Files

| File | Role |
|---|---|
| `guardrails.py` | Deterministic pre-LLM safety classifier + self-tests |
| `sms_flow.py` | Text-back flow, escalation queue, staff takeover, analytics |
| `booking.py` | Mock-schedule booking engine (request-only semantics) |
| `meta_client.py` | Meta Model API client with stub fallback |
| `demo_runner.py` | End-to-end run of the 4 demo scenarios + transcript |
| `system_prompt.md` | The agent's grounding and never-do rules |
| `clinic_knowledge.json` | Fictional clinic facts + mock schedule (only fact source) |

## Safety posture

- No medical advice, diagnosis, or dosage — ever; clinical content escalates.
- Emergencies get the 911 script and an urgent staff flag.
- No sensitive identifiers requested or stored over SMS.
- Fictional demo data only (555 numbers, "Dr. Demo").
