"""Encryption shared with the dashboard (WebCrypto): PBKDF2-SHA256 -> AES-256-GCM.

Your portfolios are stored in the repo only in encrypted form (config/portfolios.enc.json).
The passphrase lives in your browser and in the GitHub secret PORTFOLIO_KEY, never in the repo.
"""
import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 200_000


def _key(passphrase: str, salt: bytes, iterations: int = ITER) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations).derive(passphrase.encode())


def encrypt(obj, passphrase: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(passphrase, salt)).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "iter": ITER, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def decrypt(blob: dict, passphrase: str):
    d = lambda k: base64.b64decode(blob[k])
    pt = AESGCM(_key(passphrase, d("salt"), blob.get("iter", ITER))).decrypt(d("iv"), d("ct"), None)
    return json.loads(pt.decode())
