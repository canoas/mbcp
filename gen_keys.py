"""Generate the RSA key pair for bank onboarding and export the public JWK.

Writes (refusing to overwrite an existing private key):
  keys/private.pem       RSA 2048 private key, mode 600. Never share or commit.
  keys/public.jwk.json   public key as a JWK: send this to the bank
  keys/jwks.json         same key wrapped as a JWKS ({"keys": [...]})

The kid is the RFC 7638 JWK thumbprint. Requires the openssl CLI.
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

KEYS = Path(__file__).with_name("keys")
PRIVATE = KEYS / "private.pem"


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def int_bytes(n):
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def public_jwk(private_pem, alg):
    text = subprocess.run(
        ["openssl", "pkey", "-in", str(private_pem), "-pubout", "-text_pub", "-noout"],
        check=True, capture_output=True, text=True,
    ).stdout
    hex_modulus = re.search(r"Modulus:\s*([0-9a-f:\s]+)Exponent", text).group(1)
    modulus = int(re.sub(r"[:\s]", "", hex_modulus), 16)
    exponent = int(re.search(r"Exponent:\s*(\d+)", text).group(1))
    jwk = {"kty": "RSA", "n": b64url(int_bytes(modulus)), "e": b64url(int_bytes(exponent))}
    # RFC 7638 thumbprint: SHA-256 over the required members, sorted, no whitespace.
    canonical = json.dumps({k: jwk[k] for k in ("e", "kty", "n")}, separators=(",", ":"))
    jwk["kid"] = b64url(hashlib.sha256(canonical.encode()).digest())
    jwk.update({"use": "sig", "alg": alg})
    return jwk


def main():
    alg = sys.argv[1] if len(sys.argv) > 1 else "RS256"
    KEYS.mkdir(mode=0o700, exist_ok=True)
    if PRIVATE.exists():
        print(f"{PRIVATE} already exists; re-exporting its public JWK only")
    else:
        old_umask = os.umask(0o077)
        try:
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(PRIVATE)],
                check=True, capture_output=True,
            )
        finally:
            os.umask(old_umask)
        print(f"generated {PRIVATE}")
    jwk = public_jwk(PRIVATE, alg)
    (KEYS / "public.jwk.json").write_text(json.dumps(jwk, indent=2) + "\n")
    (KEYS / "jwks.json").write_text(json.dumps({"keys": [jwk]}, indent=2) + "\n")
    print(f"wrote {KEYS / 'public.jwk.json'} and {KEYS / 'jwks.json'} (kid {jwk['kid']})")


if __name__ == "__main__":
    main()
