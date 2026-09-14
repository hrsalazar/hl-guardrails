"""Generate a VAPID key pair for Web Push. Run once:  python -m hlg.vapid

Put VAPID_PRIVATE_KEY in the repo Actions secrets and the public key in site/config.js.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def main():
    k = ec.generate_private_key(ec.SECP256R1())
    priv = k.private_numbers().private_value.to_bytes(32, "big")
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    print("VAPID_PRIVATE_KEY =", b64u(priv))
    print("VAPID_PUBLIC_KEY  =", b64u(pub))


if __name__ == "__main__":
    main()
