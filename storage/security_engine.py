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

    def is_encrypted(self, value) -> bool:
        return isinstance(value, str) and value.startswith("gAAAA")

    def encrypt_dict(self, payload: dict, fields=None) -> dict:
        """
        Encrypt selected dictionary values while preserving the record shape.
        Existing Fernet ciphertext is not encrypted twice.
        """
        if not isinstance(payload, dict):
            return payload

        selected = set(fields or payload.keys())
        encrypted = dict(payload)

        for key in selected:
            if key not in encrypted:
                continue

            value = encrypted.get(key)

            if value in (None, ""):
                continue

            if self.is_encrypted(value):
                continue

            encrypted[key] = self.encrypt_field(value)

        return encrypted

    def decrypt_dict(self, payload: dict, fields=None) -> dict:
        """
        Decrypt selected Fernet values. Plaintext legacy values remain usable.
        """
        if not isinstance(payload, dict):
            return payload

        selected = set(fields or payload.keys())
        decrypted = dict(payload)

        for key in selected:
            value = decrypted.get(key)

            if not self.is_encrypted(value):
                continue

            try:
                decrypted[key] = self.decrypt_field(value)
            except Exception:
                decrypted[key] = ""

        return decrypted

    # -------------------------
    # HASHING (audit integrity)
    # -------------------------
    def hash_record(self, record: dict) -> str:
        record_str = str(sorted(record.items())).encode()
        return hashlib.sha256(record_str).hexdigest()
