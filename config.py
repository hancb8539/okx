import os
from dotenv import load_dotenv

load_dotenv()
# Base URL 可依需要切換（正式/模擬）。
OKX_BASE_URL = "https://www.okx.com"

# 金鑰（目前行情為公開端點不需用到，但保留以便擴充私有端點）
OKX_API_KEY = os.getenv("OKX_API_KEY", "YOUR_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "YOUR_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "YOUR_PASSPHRASE")
USE_SIMULATED_TRADING = os.getenv("OKX_SIMULATED", "0") == "1"
# GUI 自動刷新間隔（毫秒）
GUI_REFRESH_INTERVAL_MS = 60_000

# 時區設定（用於K線圖時間顯示）
# 可選時區: 'Asia/Taipei', 'Asia/Shanghai', 'Asia/Hong_Kong', 'UTC', 'America/New_York' 等
TIMEZONE = 'Asia/Taipei'


