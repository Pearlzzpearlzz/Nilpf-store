# NILPF Security Engine v1
# Classification + Redaction + Encryption Layer

import re
import hashlib
import os
from cryptography.fernet import Fernet


class SecurityEngine:
    """
    NILPF PHI/PII protection layer
    """

    SENSITIVE_PATTERNS = [
        r"ssn",
        r"dob",
        r"hopwa",
        r"psh",
        r"chronic",
    ]

    def __init__(self):
        key = os.environ.get("FERNET_KEY")

        if not key:
            raise RuntimeError("FERNET_KEY missing from environment")

        self.cipher = Fernet(key.encode())

    # -------------------------
    # CLASSIFICATION
    # -------------------------
    def classify(self, payload: dict) -> dict:
        flags = {
            "pii_detected": False,
            "phi_detected": False,
        }

        raw = str(payload).lower()

        for pattern in self.SENSITIVE_PATTERNS:
            if re.search(pattern, raw):
                flags["pii_detected"] = True

        return flags

    # -------------------------
    # REDACTION
    # -------------------------
    def sanitize(self, payload: dict) -> dict:
        cleaned = {}

        for k, v in payload.items():
            key_lower = k.lower()

            if any(p in key_lower for p in self.SENSITIVE_PATTERNS):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = v

        return cleaned

    # -------------------------
    # ENCRYPTION
    # -------------------------
    def encrypt_field(self, value: str) -> str:
        if value is None:
            return None
        return self.cipher.encrypt(str(value).encode()).decode()

    def decrypt_field(self, value: str) -> str:
        if value is None:
            return None
        return self.cipher.decrypt(value.encode()).decode()

    # -------------------------
    # HASHING (audit integrity)
    # -------------------------
    def hash_record(self, record: dict) -> str:
        record_str = str(sorted(record.items())).encode()
        return hashlib.sha256(record_str).hexdigest()
