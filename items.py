import os
from typing import Any


def read_item_file(file_path: str) -> Any:
    items = []
    if not os.path.exists(file_path):
        return items
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            items.append(raw)
    return items


