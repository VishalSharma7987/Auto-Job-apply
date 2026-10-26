"""Find free OpenRouter models that actually answer.

  python scripts/find_free_model.py                 # probe every ":free" model, then run the realistic match test
  python scripts/find_free_model.py --write-env     # also set LLM_MODEL / LLM_FALLBACK_MODELS in .env
  python scripts/find_free_model.py --only nvidia/   # probe only ids containing a substring

Phase 1: one tiny real chat call per model (max_tokens=20, timeout=30 s) asking for {"ok": true}.
Phase 2: the real match prompt (llm/prompts.py) with a sample job + your profile on the best candidates, validated
         with the MatchResult pydantic model, using the production client (so fallbacks / json_object handling match).
The API key is read from .env and never printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SKIP = ("code", "coder", "vision", "guard", "embed", "rerank", "tts", "audio")
REASONING_HINTS = ("thinking", "reason", "-r1", "/r1", "qwq")
MIN_CONTEXT = 32_000

SAMPLE_JOB = dict(
    job_key="sample-1", company="Acme AI", title="Junior AI Engineer", url="https://jobs.example/acme/1", source="sample",
    location="Remote - India", remote=True,
    description=("We are hiring a Junior AI Engineer (0-2 years). You will build RAG pipelines with LangChain and "
                 "FastAPI, work with LLM APIs, and ship features on a React front end. Python required. Remote in "
                 "India. Nice to have: Docker, Postgres, vector databases."))


def _call(client: httpx.Client, base: str, key: str, model: str, max_tokens: int) -> dict:
    t0 = time.monotonic()
    try:
        r = client.post(f"{base}/chat/completions", timeout=30,
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": model, "max_tokens": max_tokens, "temperature": 0,
                              "messages": [{"role": "user", "content": 'Reply with exactly this JSON and nothing else: {"ok": true}'}]})
    except httpx.HTTPError as e:
        return {"ok": False, "status": 0, "latency": time.monotonic() - t0, "reply": type(e).__name__}
    dt = time.monotonic() - t0
    if r.status_code != 200:
        try:
            err = r.json().get("error", {})
            msg = err.get("message", "")
        except ValueError:
            msg = r.text
        return {"ok": False, "status": r.status_code, "latency": dt, "reply": f"HTTP {r.status_code} {str(msg)[:70]}"}
    msg = ((r.json().get("choices") or [{}])[0].get("message") or {})
    content = msg.get("content") or ""
    return {"ok": bool(re.search(r'"ok"\s*:\s*true', content)), "status": 200, "latency": dt,
            "reply": content.strip().replace(chr(10), " ")[:40] or ("<empty, reasoning only>" if msg.get("reasoning") else "<empty>")}


def probe(client: httpx.Client, base: str, key: str, model: str) -> dict:
    """max_tokens=20 as specified. A 429 is upstream congestion, so it is retried (3 tries, 5 s apart); an empty reply
    gets one second chance with 300 tokens (reasoning models spend the first tokens thinking) and is flagged."""
    res = _call(client, base, key, model, 20)
    for _ in range(2):
        if res["status"] != 429:
            break
        time.sleep(5)
        res = _call(client, base, key, model, 20)
    if not res["ok"] and res["status"] == 200 and res["reply"].startswith("<empty"):
        again = _call(client, base, key, model, 300)
        again["reply"] = "(300 tok) " + again["reply"]
        again["slow_start"] = True
        res = again
    return res


def is_reasoning(m: dict) -> bool:
    return any(h in m["id"].lower() for h in REASONING_HINTS)


def realistic(settings, profile, model: str, others: list[str]) -> dict:
    from jobagent.llm.client import OpenAICompatClient
    from jobagent.llm.prompts import match_prompt
    from jobagent.models import Job, MatchResult

    llm = OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, model, max_retries=2, backoff=2.0,
                             timeout=settings.llm_timeout_seconds, fallback_models=[])  # no fallbacks: test THIS model
    system, user = match_prompt(Job(**SAMPLE_JOB), profile)
    t0 = time.monotonic()
    detail = ""
    for attempt in range(3):  # upstream 429s come and go: give a rate-limited model three chances, 8 s apart
        try:
            res = llm.complete_json(system, user, MatchResult, temperature=0.0, max_tokens=800)
            return {"ok": True, "latency": time.monotonic() - t0,
                    "detail": f"decision={res.decision} location_ok={res.location_ok} conf={res.confidence} reasons={res.reasons[:2]}"}
        except Exception as e:  # noqa: BLE001
            detail = f"{type(e).__name__}: {str(e)[:120]}"
            if type(e).__name__ != "QuotaExceeded" or attempt == 2:
                break
            time.sleep(8)
    return {"ok": False, "latency": time.monotonic() - t0, "detail": detail}


def write_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if k in updates and not line.lstrip().startswith("#"):
            lines[i] = f"{k}={updates[k]}"
            seen.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="probe only ids containing this substring")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--write-env", action="store_true")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--also", default="", help="comma-separated ids to run through the realistic test even if phase 1 failed (e.g. 429s)")
    ap.add_argument("--top", type=int, default=6, help="how many phase-1 candidates get the realistic test")
    args = ap.parse_args()

    from jobagent.config import get_settings
    from jobagent.profile import load_profile

    s = get_settings()
    base = s.llm_base_url.rstrip("/")
    if not s.llm_api_key:
        print("ERROR: LLM_API_KEY is not set in .env")
        return 2
    client = httpx.Client()
    r = client.get(f"{base}/models", headers={"Authorization": f"Bearer {s.llm_api_key}"}, timeout=30)
    r.raise_for_status()
    models = [m for m in r.json()["data"] if m["id"].endswith(":free")]
    models = [m for m in models if not any(k in m["id"].lower() for k in SKIP) and args.only in m["id"]]
    print(f"{len(models)} free models to probe at {base}\n")

    results = []
    for m in models:
        res = probe(client, base, s.llm_api_key, m["id"])
        ctx = m.get("context_length") or 0
        res.update(id=m["id"], context=ctx, reasoning=is_reasoning(m))
        results.append(res)
        print(f"{'OK  ' if res['ok'] else 'FAIL'} {res['latency']:5.1f}s ctx={ctx // 1000:>4}k {m['id']:<58} {res['reply']}", flush=True)
        time.sleep(args.sleep)

    ok = [x for x in results if x["ok"]]
    print(f"\n{len(ok)} of {len(results)} answered.")
    cands = [x for x in ok if x["context"] >= MIN_CONTEXT]
    cands.sort(key=lambda x: (x["reasoning"], x["latency"]))  # non-reasoning first, then fastest
    by_id = {x["id"]: x for x in results}
    for mid in [m.strip() for m in args.also.split(",") if m.strip()]:
        if mid in by_id and all(c["id"] != mid for c in cands):
            cands.append(by_id[mid])
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    if not cands:
        print("NO free model answered with context >= 32k.")
        return 1

    profile = load_profile(s)
    print(f"\nRealistic match test (production prompt + your profile) on the best {min(args.top, len(cands))} candidates:\n")
    passed, failed_ids = [], set()
    also = {m.strip() for m in args.also.split(",") if m.strip()}
    for x in cands[: args.top] + [c for c in cands[args.top:] if c["id"] in also]:
        time.sleep(args.sleep)
        rr = realistic(s, profile, x["id"], [])
        print(f"{'PASS' if rr['ok'] else 'FAIL'} {rr['latency']:5.1f}s {x['id']:<58} {rr['detail']}", flush=True)
        if rr["ok"]:
            passed.append({**x, "match_latency": rr["latency"]})
        else:
            failed_ids.add(x["id"])
    if not passed:
        print("\nNo candidate passed the realistic test.")
        return 1
    passed.sort(key=lambda x: (x["reasoning"], x["match_latency"]))
    winner, fallbacks = passed[0], [p["id"] for p in passed[1:3]]
    if len(fallbacks) < 2:
        print(f"WARNING: only {len(passed)} model(s) passed the realistic test; fewer than 2 verified fallbacks")
    print(f"\nWINNER   LLM_MODEL={winner['id']}  (probe {winner['latency']:.1f}s, match {winner['match_latency']:.1f}s, ctx {winner['context'] // 1000}k)")
    print(f"FALLBACK LLM_FALLBACK_MODELS={','.join(fallbacks)}")
    if args.write_env:
        write_env(ROOT / ".env", {"LLM_MODEL": winner["id"], "LLM_FALLBACK_MODELS": ",".join(fallbacks)})
        print("wrote LLM_MODEL and LLM_FALLBACK_MODELS to .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
