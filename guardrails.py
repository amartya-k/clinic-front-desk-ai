"""Rule-based guardrail classifier — hackathon v1 (2026-10-27).

Runs BEFORE the LLM call. Any clinical/urgent/ambiguous hit escalates to
staff and the agent flow stops — this is the guardrail the hackathon
application promised, so it is deterministic, not model-decided.
Returns (action, reason): action in {"allow", "escalate"}.
"""
import re

URGENT_PATTERNS = [
    r"\b(chest pain|can'?t breathe|trouble breathing|unconscious|fainted)\b",
    r"\bchest\s+(is\s+)?(hurts|hurting|hurt)\b",
    r"\b(severe|unbearable|worst)\s+(pain|bleeding|swelling)\b",
    r"\b(bleeding won'?t stop|won'?t stop bleeding|knocked out (my )?tooth)\b",
    r"\btooth\s+(got\s+|was\s+)?knocked out\b",
    r"\b(fever|swelling).{0,30}(face|jaw|neck)\b",
    r"\b911|emergency\b",
    # Spanish urgent/clinical terms (hardening 2026-10-31): a Spanish
    # emergency must escalate deterministically, never fall to the model.
    r"\b(sangrando|sangre|hinchada|hinchaz[oó]n|fiebre|emergencia)\b",
    r"\bno puedo respirar\b",
    r"\bmucho dolor\b",
]
HUMAN_PATTERNS = [
    r"\b(real person|human being|actual person)\b",
    r"\b(speak|talk) to (a |the )?(human|person|someone|staff|receptionist)\b",
    r"\bwant a human\b",
    r"\bnot a bot\b",
    # Spanish human request.
    r"\b(quiero|necesito) (hablar con )?(una persona|un humano)\b",
]
CLINICAL_PATTERNS = [
    r"\b(should i take|dosage|dose of|prescribe|prescription for)\b",
    r"\b(is this (an )?infection|do i have|diagnos)\b",
    r"\b(antibiotic|opioid|painkiller|ibuprofen|amoxicillin)\b",
    r"\b(treatment|procedure).{0,20}(recommend|suggest|best)\b",
]
SENSITIVE_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",          # SSN-shaped
    r"\b\d{13,19}\b",                  # card-number-shaped
]

def classify(message: str) -> tuple[str, str]:
    text = message.lower()
    for pat in URGENT_PATTERNS:
        if re.search(pat, text):
            return "escalate", f"urgent pattern: {pat}"
    for pat in CLINICAL_PATTERNS:
        if re.search(pat, text):
            return "escalate", f"clinical pattern: {pat}"
    for pat in SENSITIVE_PATTERNS:
        if re.search(pat, message):
            return "escalate", "sensitive identifier shared — staff to handle securely"
    for pat in HUMAN_PATTERNS:
        if re.search(pat, text):
            return "escalate", f"human requested staff handoff: {pat}"
    return "allow", "no guardrail hit"

if __name__ == "__main__":
    tests = [
        ("What time do you close on Friday?", "allow"),
        ("I have severe pain and my face is swelling", "escalate"),
        ("Should I take ibuprofen for this toothache?", "escalate"),
        ("Can I move my cleaning to Thursday morning?", "allow"),
        ("My bleeding won't stop after the extraction", "escalate"),
        # Hardening cases added 2026-10-31 (were all FAILING on 2026-10-30).
        ("Estoy sangrando mucho y no para", "escalate"),
        ("Tengo mucho dolor y la cara hinchada", "escalate"),
        ("my tooth got knocked out playing soccer", "escalate"),
        ("my chest is hurting bad", "escalate"),
        ("I want to speak to a real person, not a bot", "escalate"),
        ("This is f***ing useless, I want a human right now", "escalate"),
    ]
    for msg, expected in tests:
        action, reason = classify(msg)
        status = "OK " if action == expected else "FAIL"
        print(f"{status} [{action:9}] {msg!r} -> {reason}")
