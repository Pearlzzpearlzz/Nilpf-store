import json
import os
from pathlib import Path


def read_json(path, default):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        return default

    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return default
        data = json.loads(text)
        return data
    except Exception:
        return default


def write_json(path, data, indent=2):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    return data
