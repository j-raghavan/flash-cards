# SPDX-License-Identifier: AGPL-3.0-only
"""A software WebAuthn authenticator for tests: real P-256 keys and signatures.

It builds the same JSON a browser hands to the server (WebAuthn's JSON encoding,
as @simplewebauthn/browser sends it), so the py_webauthn adapter is exercised
end to end: origin, RP id, challenge, signature and counter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

_UP = 0x01  # user present
_UV = 0x04  # user verified
_AT = 0x40  # attested credential data included


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(self, *, rp_id: str = "localhost", origin: str = "http://localhost:5173") -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.credential_id = os.urandom(16)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.sign_count = 0

    def _rp_hash(self) -> bytes:
        return hashlib.sha256(self.rp_id.encode()).digest()

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin or self.origin}
        ).encode()

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def register(
        self, options: dict[str, object], *, origin: str | None = None
    ) -> dict[str, object]:
        """The response to navigator.credentials.create() with these options."""
        challenge = str(options["challenge"])
        attested = (
            bytes(16)  # AAGUID
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_key()
        )
        flags = bytes([_UP | _UV | _AT])
        auth_data = self._rp_hash() + flags + struct.pack(">I", self.sign_count) + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", challenge, origin)),
                "attestationObject": b64url(attestation),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
        }

    def assert_(
        self,
        options: dict[str, object],
        *,
        origin: str | None = None,
        count: int | None = None,
        credential_id: bytes | None = None,
    ) -> dict[str, object]:
        """The response to navigator.credentials.get(); the counter goes up unless given."""
        challenge = str(options["challenge"])
        if count is None:
            self.sign_count += 1
            count = self.sign_count
        auth_data = self._rp_hash() + bytes([_UP | _UV]) + struct.pack(">I", count)
        client_data = self._client_data("webauthn.get", challenge, origin)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        cred = credential_id or self.credential_id
        return {
            "id": b64url(cred),
            "rawId": b64url(cred),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        }
