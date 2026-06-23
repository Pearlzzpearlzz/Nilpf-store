from storage.security_engine import SecurityEngine


class SecurityZonesEngine:
    """
    NILPF Enterprise Security Zones Layer
    Controls how data is processed based on zone classification
    """

    def __init__(self):
        self.security = SecurityEngine()

    ZONES = {
        "critical": {
            "encrypt": True,
            "sanitize": True,
            "hash": True,
        },
        "controlled": {
            "encrypt": False,
            "sanitize": True,
            "hash": True,
        },
        "operational": {
            "encrypt": False,
            "sanitize": False,
            "hash": False,
        },
    }

    def process(self, zone: str, payload):
        rules = self.ZONES.get(zone, self.ZONES["operational"])

        # Participant storage is a list of record dictionaries.
        # Preserve the list structure required by Postgres and JSON storage.
        if isinstance(payload, list):
            return [
                self.process(zone, item) if isinstance(item, dict) else item
                for item in payload
            ]

        if not isinstance(payload, dict):
            return payload

        result = dict(payload)

        # SANITIZE
        if rules["sanitize"]:
            result = self._sanitize(result)

        # ENCRYPT
        if rules["encrypt"]:
            result = self._encrypt(result)

        # HASH / AUDIT
        if rules["hash"]:
            result["record_hash"] = self._hash(result)

        result["zone"] = zone
        return result

    def _sanitize(self, data: dict):
        return data

    def _encrypt(self, data: dict):
        if hasattr(self.security, "encrypt"):
            return self.security.encrypt(data)
        return data

    def _hash(self, data: dict):
        if hasattr(self.security, "hash_record"):
            return self.security.hash_record(data)
        return str(hash(str(data)))
