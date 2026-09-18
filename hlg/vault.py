"""Encrypt what gets published, so a public repo can host a private dashboard.

GitHub Pages is static: there is no server to check a password, so a login screen written in
JavaScript would stop nobody -- the JSON files are one URL away. Instead the data is encrypted
before it is published, and only ever decrypted in the browser of someone holding the passphrase.

  AES-256-GCM     authenticated encryption: a wrong passphrase or any tampering fails loudly
                  instead of decrypting to garbage
  PBKDF2-SHA256   600,000 iterations (OWASP's 2023 recommendation), so each guess at the
                  passphrase is slow for anyone attacking the published ciphertext offline
  salt            random, generated once and then carried forward from the previous envelope, so
                  every file on every run shares one key -- which is what lets a device unlock once
                  and keep a non-extractable key rather than re-deriving on each refresh
  iv              fresh random 96 bits per encryption; GCM must never reuse one under a key
  aad             the file name, so one ciphertext cannot be served in place of another

The passphrase comes from the DASHBOARD_PASSPHRASE Actions secret and exists only in CI and in the
viewer's browser. The browser half (WebCrypto) lives in pwa/index.html and must stay byte-for-byte
compatible with this: same KDF, iterations, key length, IV length and AAD.
"""
import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ALG, KDF, ITER = "AES-256-GCM", "PBKDF2-SHA256", 600_000


def _b64(b):
    return base64.b64encode(b).decode()


def is_envelope(obj):
    return isinstance(obj, dict) and obj.get("alg") == ALG and "ct" in obj


def salt_of(obj):
    return base64.b64decode(obj["salt"]) if is_envelope(obj) else None


class Vault:
    def __init__(self, passphrase, salt=None, iterations=ITER):
        if not passphrase:
            raise ValueError("empty passphrase")
        self.salt = salt or os.urandom(16)
        self.iterations = iterations
        self._aes = AESGCM(PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=self.salt,
                                      iterations=iterations).derive(passphrase.encode()))

    def seal(self, obj, name):
        iv = os.urandom(12)
        ct = self._aes.encrypt(iv, json.dumps(obj, separators=(",", ":")).encode(), name.encode())
        return {"v": 1, "alg": ALG, "kdf": KDF, "iter": self.iterations,
                "salt": _b64(self.salt), "iv": _b64(iv), "ct": _b64(ct)}

    def unseal(self, env, name):
        """Raises cryptography.exceptions.InvalidTag on a wrong passphrase, a tampered file, or a
        file served under the wrong name."""
        return json.loads(self._aes.decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]),
                                            name.encode()))
