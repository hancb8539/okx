import os
import sys
from typing import Any, Optional, Dict

from items import read_item_file
from okx_api import get_ticker


def get_prices_for_items(inst_ids: Any) -> Dict[str, Optional[str]]:
    results: Dict[str, Optional[str]] = {}
    for inst_id in inst_ids:
        try:
            data = get_ticker(inst_id)
            last = data.get("data", [{}])[0].get("last") if data.get("data") else None
            results[inst_id] = last
        except Exception:
            results[inst_id] = None
    return results


def main_cli():
    inst_ids: Any = read_item_file(os.path.join(os.path.dirname(__file__), "item.txt"))
    if not inst_ids:
        print("未提供交易對，請在 item.txt 中填入，例如：BTC-USDT")
        sys.exit(1)
    prices = get_prices_for_items(inst_ids)
    for inst_id, last in prices.items():
        print(f"{inst_id}: {last if last is not None else 'N/A'}")


