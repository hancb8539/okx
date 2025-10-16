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
    code = data.get("code")
    if code != "0":
        # 直接拋出包含錯誤碼的例外，GUI 端可辨識 50011/50013/50113 等認證/權限錯誤
        raise RuntimeError(f"OKX API error: code={code} msg={data.get('msg')}")
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


# ===== 資產 / 帳戶 私有端點 =====
def get_account_balance(ccy: Optional[str] = None) -> Dict[str, Any]:
    """
    查詢交易帳戶餘額與淨值。
    返回 fields：totalEq、details[ { ccy, eq, cashBal, availEq, upl, ... } ]
    需私有認證
    """
    path = "/api/v5/account/balance"
    params = {"ccy": ccy} if ccy else None
    return http_get(path, params=params, auth=True)


def get_asset_balances(ccy: Optional[str] = None) -> Dict[str, Any]:
    """
    查詢資金帳戶餘額。
    返回各幣種可用/凍結等; 需私有認證
    """
    path = "/api/v5/asset/balances"
    params = {"ccy": ccy} if ccy else None
    return http_get(path, params=params, auth=True)


def get_account_bills(
    ccy: Optional[str] = None,
    type: Optional[str] = None,
    subType: Optional[str] = None,
    after: Optional[str] = None,
    before: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    查詢帳戶資金流水（用於已實現損益彙總）。
    可依 type/subType 過濾成交盈虧、結算、資金費、手續費等。
    需私有認證
    """
    path = "/api/v5/account/bills"
    params: Dict[str, Any] = {"limit": str(limit)}
    if ccy: params["ccy"] = ccy
    if type: params["type"] = type
    if subType: params["subType"] = subType
    if after: params["after"] = after
    if before: params["before"] = before
    return http_get(path, params=params, auth=True)


def calc_spot_realized_pnl(bills: dict) -> dict:
    """
    分析 account/bills 回傳，彙總所有 type=1(交易) 產生的 balChg，分幣顯示已實現現貨損益
    回傳 {'total': 總和, 'by_ccy': { 幣名: 小計 }}，單位 USD
    """
    total = 0.0
    by_ccy = {}
    if bills and bills.get('code') == '0':
        for item in bills.get('data', []):
            if str(item.get('type')) == '1':  # 交易
                try:
                    amt = float(item.get('balChg', 0))
                    ccy = item.get('ccy', 'UNKNOWN')
                    total += amt
                    if ccy not in by_ccy:
                        by_ccy[ccy] = 0.0
                    by_ccy[ccy] += amt
                except Exception:
                    pass
    return {'total': total, 'by_ccy': by_ccy}

