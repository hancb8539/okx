import datetime
import hmac
import base64
import hashlib
import json
from typing import Optional, Dict, Any

import requests

from config import OKX_BASE_URL, OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, USE_SIMULATED_TRADING


def iso_timestamp_ms() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def sign_okx(timestamp: str, method: str, request_path: str, body: str, secret_key: str) -> str:
    prehash = f"{timestamp}{method.upper()}{request_path}{body or ''}"
    signature = hmac.new(secret_key.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(signature).decode()


def build_headers(api_key: str, passphrase: str, sign: Optional[str], timestamp: str, simulated: bool = False) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
    }
    if sign:
        headers["OK-ACCESS-SIGN"] = sign
    if simulated:
        headers["x-simulated-trading"] = "1"
    return headers


def http_get(path: str, params: Optional[Dict[str, Any]] = None, auth: bool = False) -> Dict[str, Any]:
    url = OKX_BASE_URL + path
    if not auth:
        resp = requests.get(url, params=params, timeout=5)
    else:
        ts = iso_timestamp_ms()
        query = ""
        if params:
            query = "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        request_path = f"{path}{query}"
        sign = sign_okx(ts, "GET", request_path, "", OKX_SECRET_KEY)
        headers = build_headers(OKX_API_KEY, OKX_PASSPHRASE, sign, ts, USE_SIMULATED_TRADING)
        resp = requests.get(OKX_BASE_URL + request_path, headers=headers, timeout=5)

    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX API error: code={data.get('code')} msg={data.get('msg')}")
    return data


def get_ticker(inst_id: str) -> Dict[str, Any]:
    path = "/api/v5/market/ticker"
    params = {"instId": inst_id}
    return http_get(path, params=params, auth=False)


def get_candlesticks(inst_id: str, bar: str = "1m", limit: int = 100) -> Dict[str, Any]:
    """
    獲取K線數據
    Args:
        inst_id: 交易對ID，如 "BTC-USDT"
        bar: K線週期，可選值: 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 12H, 1D, 1W, 1M, 3M
        limit: 返回數據條數，最大100
    """
    path = "/api/v5/market/candles"
    params = {
        "instId": inst_id,
        "bar": bar,
        "limit": str(limit)
    }
    return http_get(path, params=params, auth=False)


