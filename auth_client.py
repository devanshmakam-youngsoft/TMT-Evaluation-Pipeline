"""Logs into the target backend (local or deployed) so eval calls can hit
the real, protected /foundry/generate endpoint - same login flow a real
user goes through, just scripted."""
import requests

from .config import LOGIN_EMAIL, LOGIN_PASSWORD


def login(base_url: str) -> str:
    resp = requests.post(f"{base_url}/auth/login", json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"login failed: {data.get('error')}")
    return data["token"]
