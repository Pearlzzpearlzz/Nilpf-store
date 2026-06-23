class StorageMiddleware:
    """
    Central pipeline controller for NILPF Storage System
    """

    def __init__(self, security, audit, storage):
        self.security = security
        self.audit = audit
        self.storage = storage

    def process(self, zone: str, payload: dict, action: str):
        # -----------------------------
        # 1. SECURITY LAYER
        # -----------------------------
        secured = self.security.process(zone, payload)

        # -----------------------------
        # 2. AUDIT LAYER (MR.IR)
        # -----------------------------
        try:
            self.audit.record({
                "action": action,
                "zone": zone,
                "payload": secured
            })
        except Exception:
            pass  # never block system

        # -----------------------------
        # 3. STORAGE LAYER
        # -----------------------------
        return self.storage.save(action, secured)
