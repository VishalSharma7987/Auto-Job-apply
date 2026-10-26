"""Create/update GitHub Actions repository secrets from values in .env (encrypted with the repo public key).

  GH_PAT=ghp_... python scripts/push_secrets.py LLM_MODEL LLM_FALLBACK_MODELS
  python scripts/push_secrets.py --list-only LLM_MODEL        # show what would be pushed (no values for *KEY/*TOKEN/*PASSWORD)

Needs a token with repo "Secrets: write" (fine-grained) or the `repo` scope (classic), and `pip install pynacl`.
Repository is taken from the `origin` remote unless GH_REPO=owner/name is set. Values are never printed.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"


def read_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def repo_slug() -> str:
    if os.getenv("GH_REPO"):
        return os.environ["GH_REPO"]
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=ROOT, check=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise SystemExit(f"cannot work out owner/repo from remote {url!r}; set GH_REPO=owner/name")
    return m.group(1)


def encrypt(public_key_b64: str, value: str) -> str:
    from nacl import encoding, public  # lazy: only needed when actually pushing

    key = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(key).encrypt(value.encode())).decode()


def main(argv: list[str]) -> int:
    list_only = "--list-only" in argv
    names = [a for a in argv if not a.startswith("--")]
    if not names:
        print(__doc__)
        return 2
    env = read_env(ROOT / ".env")
    missing = [n for n in names if not env.get(n)]
    if missing:
        print(f"not set in .env: {', '.join(missing)}")
        return 2
    repo = repo_slug()
    if list_only:
        for n in names:
            secretish = re.search(r"KEY|TOKEN|PASSWORD|SECRET|PHONE", n)
            print(f"{n} -> {repo}: {'<hidden>' if secretish else env[n]}")
        return 0
    token = os.getenv("GH_PAT")
    if not token:
        print("GH_PAT is not set. Paste these into GitHub -> Settings -> Secrets and variables -> Actions:")
        for n in names:
            print(f"  {n} = {env[n]}")
        return 1
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    with httpx.Client(headers=h, timeout=30) as c:
        pk = c.get(f"{API}/repos/{repo}/actions/secrets/public-key")
        pk.raise_for_status()
        pk = pk.json()
        for n in names:
            r = c.put(f"{API}/repos/{repo}/actions/secrets/{n}",
                      json={"encrypted_value": encrypt(pk["key"], env[n]), "key_id": pk["key_id"]})
            r.raise_for_status()
            print(f"{n}: {'created' if r.status_code == 201 else 'updated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
