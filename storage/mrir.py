import json
import os
from datetime import datetime


class MRIR:
    """
    Minimal MR.IR Audit Engine
    - Records system events
    - Writes to append-only JSON log
    - Never blocks core system
    """

    def __init__(self, log_file="data/mrir_audit_log.json"):
        self.log_file = log_file
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)

        if not os.path.exists(self.log_file):
            with open(self.log_file, "w") as f:
                json.dump([], f)

    def record(self, event: dict):
        try:
            with open(self.log_file, "r") as f:
                logs = json.load(f)

            event["timestamp"] = datetime.utcnow().isoformat()
            logs.append(event)

            with open(self.log_file, "w") as f:
                json.dump(logs, f, indent=2)

        except Exception:
            # NEVER break storage system
            pass
