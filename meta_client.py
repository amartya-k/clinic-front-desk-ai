"""Meta Model API client — hackathon v1 (2026-10-27, live since 2026-10-31).

Calls Muse Spark on the Meta Model API. Two transports:
- "env" (default): direct call using META_MODEL_API_KEY / META_MODEL_API_URL
  from the environment — how judges run it with their own key.
- "connector": routes through the author's stored Meta Model Api credential
  so the key never sits in a file or environment variable.
With neither configured it falls back to stub mode, returning canned replies
so the rest of the system (guardrails, booking, SMS flow, demo) still runs
and tests end to end offline.
Endpoint/model are read from env so they can be corrected from the
organizers' docs without code changes.

Spend safety (Amartya, 2026-10-31: "limit it to 150$, i dont wanna spend
my money"): two local hard guards so a bug can never run past the free
credits — (1) every reply is capped at MAX_TOKENS output tokens, and
(2) a local usage ledger estimates cost at Standard rates
($1.25 / 1M input, $4.25 / 1M output) and refuses live calls once the
estimated total reaches BUDGET_USD (default $140, a buffer under the
$150 credits). Set META_MODEL_BUDGET_USD to change it. The Billing-page
spend-limit notification on his account is the account-level backstop.
"""
import os, json, subprocess, urllib.request

API_KEY = os.environ.get("META_MODEL_API_KEY", "")
ENDPOINT = os.environ.get("META_MODEL_API_URL", "")
MODEL = os.environ.get("META_MODEL_NAME", "muse-spark-1.3")
# "env" = direct call with META_MODEL_API_KEY/URL (how judges run it).
# "connector" = route through Muse's stored Meta Model Api credential
# (custom.meta-model-api helper) — how Amartya's own demo runs live without
# the key ever sitting in a file or env var.
TRANSPORT = os.environ.get("META_CLIENT_TRANSPORT", "env")
_HELPER = os.path.expanduser(
    "~/workspace/custom-connectors/custom.meta-model-api/bin/meta_api.py")
MAX_TOKENS = int(os.environ.get("META_MODEL_MAX_TOKENS", "600"))
BUDGET_USD = float(os.environ.get("META_MODEL_BUDGET_USD", "140"))
_LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".usage_ledger.json")
_INPUT_RATE = 1.25 / 1_000_000   # USD per token, Standard tier
_OUTPUT_RATE = 4.25 / 1_000_000  # USD per token, Standard tier

def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def _ledger_total() -> float:
    try:
        with open(_LEDGER) as f:
            return float(json.load(f).get("estimated_usd", 0.0))
    except Exception:
        return 0.0

def _ledger_add(usd: float) -> None:
    try:
        with open(_LEDGER, "w") as f:
            json.dump({"estimated_usd": _ledger_total() + usd}, f)
    except Exception:
        pass

def chat(system: str, user: str) -> str:
    live_env = bool(API_KEY and ENDPOINT)
    if not live_env and TRANSPORT != "connector":
        return _stub(system, user)
    if _ledger_total() >= BUDGET_USD:
        return ("[BUDGET STOP — estimated spend reached the $%.0f safety cap "
                "under the $150 free credits; refusing further live calls.] "
                % BUDGET_USD) + _stub(system, user)
    if not live_env:  # connector transport
        out = subprocess.run(
            ["python3", _HELPER, "chat", user, "--system", system,
             "--model", MODEL, "--max-tokens", str(MAX_TOKENS),
             "--reasoning-effort", "minimal"],
            capture_output=True, text=True, timeout=90)
        data = json.loads(out.stdout)
        if "error" in data:
            raise RuntimeError(f"Meta Model API via connector: {data}")
        reply = data["reply"]
        _ledger_add(_est_tokens(system + user) * _INPUT_RATE
                    + _est_tokens(reply or "") * _OUTPUT_RATE)
        return reply
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": "minimal",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=payload, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    reply = data["choices"][0]["message"]["content"]
    _ledger_add(_est_tokens(system + user) * _INPUT_RATE
                + _est_tokens(reply) * _OUTPUT_RATE)
    return reply

def _stub(system: str, user: str) -> str:
    return ("[STUB — no META_MODEL_API_KEY/URL set] I received your message: "
            f"\"{user}\" — once the API key is live this reply comes from {MODEL}.")

if __name__ == "__main__":
    print(chat("You are a test harness.", "hello"))
    live = (API_KEY and ENDPOINT) or TRANSPORT == "connector"
    print("mode:", "live" if live else "stub",
          f"(transport: {TRANSPORT})" if live else "")
