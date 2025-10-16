import os
import datetime
import pandas as pd
from typing import Any, Optional, Dict

from PyQt5 import QtWidgets, QtCore, QtGui
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import mplfinance as mpf

from items import read_item_file
from okx_api import get_ticker, get_candlesticks, get_account_balance, get_account_bills, calc_spot_realized_pnl
from config import GUI_REFRESH_INTERVAL_MS, TIMEZONE


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


def get_candlestick_data(inst_id: str, bar: str = "1m", limit: int = 100) -> Optional[pd.DataFrame]:
    """
    獲取K線數據並轉換為DataFrame格式
    """
    try:
        data = get_candlesticks(inst_id, bar, limit)
        if not data.get("data"):
            return None
        
        # OKX K線數據格式: [timestamp, open, high, low, close, volume, ...]
        df = pd.DataFrame(data["data"], columns=[
            "timestamp", "open", "high", "low", "close", "volume", 
            "volCcy", "volCcyQuote", "confirm"
        ])
        
        # 轉換數據類型
        # 將時間戳轉換為UTC時間，然後轉換為配置的時區
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        # 轉換為配置的時區
        df["timestamp"] = df["timestamp"].dt.tz_convert(TIMEZONE)
        # 移除時區信息，保留本地時間
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        
        # 設置時間戳為索引
        df.set_index("timestamp", inplace=True)
        
        # 只保留OHLCV數據
        df = df[["open", "high", "low", "close", "volume"]]
        
        return df
    except Exception:
        return None


class PriceWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, inst_ids: Any):
        super().__init__()
        self.inst_ids = inst_ids

    def run(self):
        try:
            results = get_prices_for_items(self.inst_ids)
            self.finished.emit(results)
        except Exception as e:
            self.failed.emit(str(e))


class PriceWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OKX Market Data")
        self.resize(720, 620)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        # 主分頁
        self.tabs = QtWidgets.QTabWidget(self)

        # ---- 市場頁 ----
        marketTab = QtWidgets.QWidget(self)
        marketLayout = QtWidgets.QVBoxLayout()

        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Symbol", "Price", "30m Change"])
        self.table.horizontalHeader().setStretchLastSection(True)

        self.refreshBtn = QtWidgets.QPushButton("Refresh", self)
        self.statusLabel = QtWidgets.QLabel("Ready", self)

        # 圖表區
        self.figure = Figure(figsize=(6, 4))
        self.canvas = FigureCanvas(self.figure)
        # 初始化時不創建ax，在draw_plot中動態創建

        # 圖表標的一覽
        self.symbolLabel = QtWidgets.QLabel("Symbol:")
        self.symbolSelect = QtWidgets.QComboBox()

        # K線週期選擇
        self.periodLabel = QtWidgets.QLabel("Period:")
        self.periodSelect = QtWidgets.QComboBox()
        self.periodSelect.addItems(["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D"])
        self.periodSelect.setCurrentText("1m")
        
        # 時區顯示
        self.timezoneLabel = QtWidgets.QLabel(f"Timezone: {TIMEZONE}")
        self.timezoneLabel.setStyleSheet("color: gray; font-size: 10px;")

        vbox = marketLayout
        vbox.addWidget(self.table)
        chartCtl = QtWidgets.QHBoxLayout()
        chartCtl.addWidget(self.symbolLabel)
        chartCtl.addWidget(self.symbolSelect)
        chartCtl.addWidget(self.periodLabel)
        chartCtl.addWidget(self.periodSelect)
        chartCtl.addStretch(1)
        chartCtl.addWidget(self.timezoneLabel)
        vbox.addLayout(chartCtl)
        vbox.addWidget(self.canvas)

        # 提醒控制區
        alertCtl = QtWidgets.QHBoxLayout()
        self.alertEnable = QtWidgets.QCheckBox("Enable Price Alert")
        self.alertEnable.setChecked(False)
        self.alertThreshold = QtWidgets.QDoubleSpinBox()
        self.alertThreshold.setDecimals(2)
        self.alertThreshold.setSuffix(" %")
        self.alertThreshold.setRange(0.00, 10000.00)
        self.alertThreshold.setValue(2.00)
        alertCtl.addWidget(self.alertEnable)
        alertCtl.addWidget(QtWidgets.QLabel("Threshold"))
        alertCtl.addWidget(self.alertThreshold)
        alertCtl.addStretch(1)

        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(self.refreshBtn)
        ctrl.addStretch(1)
        ctrl.addWidget(self.statusLabel)
        vbox.addLayout(alertCtl)
        vbox.addLayout(ctrl)
        marketTab.setLayout(vbox)

        # ---- 資產頁 ----
        assetsTab = QtWidgets.QWidget(self)
        assetsLayout = QtWidgets.QVBoxLayout()

        self.assetsSummary = QtWidgets.QLabel("Total Equity: -", self)
        self.assetsTable = QtWidgets.QTableWidget(self)
        self.assetsTable.setColumnCount(4)
        self.assetsTable.setHorizontalHeaderLabels(["Currency", "Equity", "Available", "Unrealized PnL"])
        self.assetsTable.horizontalHeader().setStretchLastSection(True)

        assetsCtrl = QtWidgets.QHBoxLayout()
        self.assetsRefreshBtn = QtWidgets.QPushButton("Refresh Assets", self)
        self.assetsStatus = QtWidgets.QLabel("Ready", self)
        assetsCtrl.addWidget(self.assetsRefreshBtn)
        assetsCtrl.addStretch(1)
        assetsCtrl.addWidget(self.assetsStatus)

        assetsLayout.addWidget(self.assetsSummary)
        assetsLayout.addWidget(self.assetsTable)
        # 刪除：分幣 realizedTable 與標題
        assetsLayout.addLayout(assetsCtrl)
        assetsTab.setLayout(assetsLayout)

        # 加入分頁
        self.tabs.addTab(marketTab, "Market")
        self.tabs.addTab(assetsTab, "Assets")

        rootLayout = QtWidgets.QVBoxLayout()
        rootLayout.addWidget(self.tabs)
        central.setLayout(rootLayout)

        self.inst_ids = read_item_file(os.path.join(os.path.dirname(__file__), "item.txt"))
        self._populate_table()
        self._init_plot_state()
        # 提醒冷卻追蹤（避免連續彈窗）：{inst: datetime}
        self._lastAlertAt: Dict[str, datetime.datetime] = {}
        self._alertCooldownMinutes = 10

        self.refreshBtn.clicked.connect(self.refresh)
        self.symbolSelect.currentIndexChanged.connect(self.draw_plot)
        self.periodSelect.currentIndexChanged.connect(self.draw_plot)
        self.assetsRefreshBtn.clicked.connect(self.refresh_assets)

        # 每分鐘自動刷新
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(GUI_REFRESH_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        # 每 30 分鐘更新一次漲幅欄（即使沒有新價格也重算）
        self.changeTimer = QtCore.QTimer(self)
        self.changeTimer.setInterval(30 * 60 * 1000)
        self.changeTimer.timeout.connect(self.update_change_column)
        self.changeTimer.start()

        # 啟動時立即查一次
        QtCore.QTimer.singleShot(0, self.refresh)
        QtCore.QTimer.singleShot(0, self.refresh_assets)

    def _populate_table(self):
        self.table.setRowCount(len(self.inst_ids))
        for row, inst in enumerate(self.inst_ids):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(inst))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem("-"))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("-"))

    def _init_plot_state(self):
        # 歷史緩存: {inst_id: list[tuple[datetime, float]]} - 保留用於漲幅計算
        self.histories: Dict[str, list] = {inst: [] for inst in self.inst_ids}
        self.symbolSelect.clear()
        self.symbolSelect.addItems(self.inst_ids)
        self.draw_plot()

    def refresh(self):
        if not self.inst_ids:
            self.statusLabel.setText("No trading pairs in item.txt")
            return
        self.refreshBtn.setEnabled(False)
        self.statusLabel.setText("Querying...")
        self.worker = PriceWorker(self.inst_ids)
        self.worker.finished.connect(self.on_results)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_results(self, results: Dict[str, Optional[str]]):
        for row, inst in enumerate(self.inst_ids):
            price = results.get(inst)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(price if price is not None else "N/A"))
            # 更新歷史
            try:
                if price is not None:
                    now = datetime.datetime.now()
                    self.histories.setdefault(inst, []).append((now, float(price)))
                    if len(self.histories[inst]) > 200:
                        self.histories[inst] = self.histories[inst][-200:]
            except Exception:
                pass
        self.statusLabel.setText("Complete")
        self.refreshBtn.setEnabled(True)
        self.update_change_column()
        self.draw_plot()

    def on_failed(self, err: str):
        self.statusLabel.setText(f"Error: {err}")
        self.refreshBtn.setEnabled(True)

    def draw_plot(self):
        if not self.inst_ids:
            return
        symbol = self.symbolSelect.currentText() if self.symbolSelect.count() else None
        if not symbol:
            return
        
        period = self.periodSelect.currentText()
        
        # 清除當前圖形
        self.figure.clear()
        
        # 獲取K線數據
        df = get_candlestick_data(symbol, period, 100)
        
        if df is not None and not df.empty:
            try:
                mpf.plot(df, type='candle', style='charles', 
                        title=f"{symbol} - {period} Candlestick Chart ({TIMEZONE})",
                        ylabel="Price (USDT)",
                        volume=True,
                        figsize=(6, 4),
                        fig=self.figure,
                        datetime_format='%H:%M',
                        tight_layout=True)
            except Exception as e:
                ax = self.figure.add_subplot(111)
                ax.grid(True, linestyle=":", linewidth=0.5)
                ax.set_xlabel("Time")
                ax.set_ylabel("Price")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                if 'close' in df.columns:
                    ax.plot(df.index, df['close'], linewidth=1.2, color='blue')
                    ax.set_title(f"{symbol} - {period} (Close Price)")
                else:
                    ax.set_title(f"{symbol} - {period} No Data")
                self.figure.tight_layout(pad=2.0)
        else:
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"{symbol} - {period}\nNo K-line Data", 
                   ha='center', va='center', transform=ax.transAxes, fontsize=12)
            ax.set_title(f"{symbol} - {period} No Data")
        self.figure.tight_layout(pad=3.0)
        self.figure.subplots_adjust(bottom=0.15)
        self.figure.autofmt_xdate()
        self.canvas.draw_idle()

    def update_change_column(self):
        # 針對每個標的，計算相對 30 分鐘前的漲幅百分比
        if not self.inst_ids:
            return
        now = datetime.datetime.now()
        threshold = now - datetime.timedelta(minutes=30)
        for row, inst in enumerate(self.inst_ids):
            series = self.histories.get(inst, [])
            if not series or len(series) < 2:
                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("N/A"))
                continue
            # 取得當前價
            _, current_val = series[-1]
            # 尋找最接近且不晚於 threshold 的舊資料
            baseline_val = None
            last_before_or_at = None
            for t, v in series:
                if t <= threshold:
                    last_before_or_at = v
                else:
                    break
            if last_before_or_at is not None:
                baseline_val = last_before_or_at
            else:
                # 若沒有早於閾值的資料，則以最早一筆作為基準（資料不足 30 分鐘）
                baseline_val = series[0][1]
            try:
                pct = None
                if baseline_val and baseline_val > 0:
                    pct = (current_val - baseline_val) / baseline_val * 100.0
                    text = f"{pct:+.2f}%"
                else:
                    text = "N/A"
            except Exception:
                text = "N/A"
                pct = None
            item = self.table.item(row, 2)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, 2, item)
            item.setText(text)
            # 顏色標示與提醒
            default_bg = QtGui.QBrush()
            if pct is None:
                item.setBackground(default_bg)
                continue
            abs_threshold = self.alertThreshold.value()
            triggered = abs(pct) >= abs_threshold
            if triggered:
                color = QtGui.QColor(220, 20, 60) if pct < 0 else QtGui.QColor(0, 128, 0)
                item.setBackground(QtGui.QBrush(color).color())
                self._maybe_alert(inst, pct)
            else:
                item.setBackground(default_bg)

    def _maybe_alert(self, inst: str, pct: float):
        if not self.alertEnable.isChecked():
            return
        now = datetime.datetime.now()
        last_at = self._lastAlertAt.get(inst)
        if last_at and (now - last_at) < datetime.timedelta(minutes=self._alertCooldownMinutes):
            return
        self._lastAlertAt[inst] = now
        try:
            QtWidgets.QMessageBox.information(
                self,
                "Price Alert",
                f"{inst} 30-minute change reached {pct:+.2f}%"
            )
        except Exception:
            pass
    # ===== 資產：GUI 更新 =====
    def refresh_assets(self):
        self.assetsRefreshBtn.setEnabled(False)
        self.assetsStatus.setText("Querying...")
        self.accWorker = AccountsWorker()
        self.accWorker.finished.connect(self.on_assets)
        self.accWorker.failed.connect(self.on_assets_failed)
        self.accWorker.start()

    def on_assets(self, payload: Dict[str, dict]):
        def safe_num(val):
            try:
                fval = float(val)
                if abs(fval) < 1e-9:
                    return "0"
                return f"{fval:.8f}".rstrip('0').rstrip('.') if '.' in f"{fval:.8f}" else f"{fval:.8f}"
            except Exception:
                return "0"
        try:
            bal = payload.get("balance", {})
            # 刪除分幣損益處理，不再 show bills/realizedMap
            if bal.get("code") == "0":
                d0 = (bal.get("data") or [{}])[0]
                totalEq = safe_num(d0.get("totalEq", 0))
                details = d0.get("details") or []
                self.assetsSummary.setText(f"Total Equity: {totalEq}")
                self.assetsTable.setRowCount(len(details))
                for r, d in enumerate(details):
                    ccy = d.get("ccy", "")
                    eq = safe_num(d.get("eq"))
                    avail = safe_num(d.get("availEq"))
                    upl = safe_num(d.get("upl"))
                    self.assetsTable.setItem(r, 0, QtWidgets.QTableWidgetItem(ccy))
                    self.assetsTable.setItem(r, 1, QtWidgets.QTableWidgetItem(eq))
                    self.assetsTable.setItem(r, 2, QtWidgets.QTableWidgetItem(avail))
                    self.assetsTable.setItem(r, 3, QtWidgets.QTableWidgetItem(upl))
            else:
                self.assetsSummary.setText("Total Equity: N/A")
                self.assetsTable.setRowCount(0)
        finally:
            self.assetsStatus.setText("Complete")
            self.assetsRefreshBtn.setEnabled(True)

    def on_assets_failed(self, err: str):
        self.assetsSummary.setText("Total Equity: N/A")
        self.assetsTable.setRowCount(0)
        self.assetsStatus.setText("Error")
        self.assetsRefreshBtn.setEnabled(True)

    # ===== 資產：查詢執行緒 =====
class AccountsWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def run(self):
        try:
            bal = get_account_balance()
            bills = get_account_bills(limit=100)
            self.finished.emit({"balance": bal, "bills": bills})
        except Exception as e:
            self.failed.emit(str(e))

    def draw_plot(self):
        if not self.inst_ids:
            return
        symbol = self.symbolSelect.currentText() if self.symbolSelect.count() else None
        if not symbol:
            return
        
        period = self.periodSelect.currentText()
        
        # 清除當前圖形
        self.figure.clear()
        
        # 獲取K線數據
        df = get_candlestick_data(symbol, period, 100)
        
        if df is not None and not df.empty:
            # 使用mplfinance繪製K線圖
            try:
                # 使用mplfinance繪製K線圖到當前figure
                mpf.plot(df, type='candle', style='charles', 
                        title=f"{symbol} - {period} Candlestick Chart ({TIMEZONE})",
                        ylabel="Price (USDT)",
                        volume=True,
                        figsize=(6, 4),
                        fig=self.figure,
                        datetime_format='%H:%M',
                        tight_layout=True)
                
            except Exception as e:
                # 如果mplfinance失敗，回退到簡單折線圖
                ax = self.figure.add_subplot(111)
                ax.grid(True, linestyle=":", linewidth=0.5)
                ax.set_xlabel("Time")
                ax.set_ylabel("Price")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                
                if 'close' in df.columns:
                    ax.plot(df.index, df['close'], linewidth=1.2, color='blue')
                    ax.set_title(f"{symbol} - {period} (Close Price)")
                else:
                    ax.set_title(f"{symbol} - {period} No Data")
                
                # 為折線圖也添加布局調整
                self.figure.tight_layout(pad=2.0)
        else:
            # 沒有數據時顯示提示
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"{symbol} - {period}\nNo K-line Data", 
                   ha='center', va='center', transform=ax.transAxes, fontsize=12)
            ax.set_title(f"{symbol} - {period} No Data")
        
        # 調整圖形布局，確保x軸標題不被覆蓋
        self.figure.tight_layout(pad=3.0)
        self.figure.subplots_adjust(bottom=0.15)  # 為x軸標題留出更多空間
        self.figure.autofmt_xdate()
        self.canvas.draw_idle()

    def update_change_column(self):
        # 針對每個標的，計算相對 30 分鐘前的漲幅百分比
        if not self.inst_ids:
            return
        now = datetime.datetime.now()
        threshold = now - datetime.timedelta(minutes=30)
        for row, inst in enumerate(self.inst_ids):
            series = self.histories.get(inst, [])
            if not series or len(series) < 2:
                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("N/A"))
                continue
            # 取得當前價
            _, current_val = series[-1]
            # 尋找最接近且不晚於 threshold 的舊資料
            baseline_val = None
            last_before_or_at = None
            for t, v in series:
                if t <= threshold:
                    last_before_or_at = v
                else:
                    break
            if last_before_or_at is not None:
                baseline_val = last_before_or_at
            else:
                # 若沒有早於閾值的資料，則以最早一筆作為基準（資料不足 30 分鐘）
                baseline_val = series[0][1]
            try:
                pct = None
                if baseline_val and baseline_val > 0:
                    pct = (current_val - baseline_val) / baseline_val * 100.0
                    text = f"{pct:+.2f}%"
                else:
                    text = "N/A"
            except Exception:
                text = "N/A"
                pct = None
            item = self.table.item(row, 2)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, 2, item)
            item.setText(text)
            # 顏色標示與提醒
            default_bg = QtGui.QBrush()
            if pct is None:
                item.setBackground(default_bg)
                continue
            abs_threshold = self.alertThreshold.value()
            triggered = abs(pct) >= abs_threshold
            if triggered:
                color = QtGui.QColor(220, 20, 60) if pct < 0 else QtGui.QColor(0, 128, 0)
                item.setBackground(QtGui.QBrush(color).color())
                self._maybe_alert(inst, pct)
            else:
                item.setBackground(default_bg)

    def _maybe_alert(self, inst: str, pct: float):
        if not self.alertEnable.isChecked():
            return
        now = datetime.datetime.now()
        last_at = self._lastAlertAt.get(inst)
        if last_at and (now - last_at) < datetime.timedelta(minutes=self._alertCooldownMinutes):
            return
        self._lastAlertAt[inst] = now
        try:
            QtWidgets.QMessageBox.information(
                self,
                "Price Alert",
                f"{inst} 30-minute change reached {pct:+.2f}%"
            )
        except Exception:
            pass

    # 已移除：持倉查詢執行緒

    def on_results(self, results: Dict[str, Optional[str]]):
        for row, inst in enumerate(self.inst_ids):
            price = results.get(inst)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(price if price is not None else "N/A"))
            # 更新歷史
            try:
                if price is not None:
                    now = datetime.datetime.now()
                    self.histories.setdefault(inst, []).append((now, float(price)))
                    if len(self.histories[inst]) > 200:
                        self.histories[inst] = self.histories[inst][-200:]
            except Exception:
                pass
        self.statusLabel.setText("Complete")
        self.refreshBtn.setEnabled(True)
        self.update_change_column()
        self.draw_plot()

    def on_failed(self, err: str):
        self.statusLabel.setText(f"Error: {err}")
        self.refreshBtn.setEnabled(True)

    # ===== 新增：持倉回調，更新 UPL 與 UPL Ratio 欄位 =====
    def on_positions(self, by_inst: Dict[str, dict]):
        # 嘗試用表中顯示的交易對去對應返回的 instId（SWAP 產品為三段）
        inst_map: Dict[str, tuple] = {}
        for row, inst in enumerate(self.inst_ids):
            # 優先匹配完全相同的鍵
            if inst in by_inst:
                inst_map[inst] = (row, inst)
                continue
            # 退而求其次：BTC-USDT 對 BTC-USDT-SWAP / FUTURES 進行模糊匹配
            for key in by_inst.keys():
                if key.startswith(inst + "-"):
                    inst_map[inst] = (row, key)
                    break

        for inst, (row, key) in inst_map.items():
            pos = by_inst.get(key, {})
            upl = pos.get("upl")
            uplRatio = pos.get("uplRatio")
            try:
                upl_text = f"{float(upl):.4f}" if upl is not None else "N/A"
            except Exception:
                upl_text = "N/A"
            try:
                ratio_text = f"{float(uplRatio)*100:.2f}%" if uplRatio is not None else "N/A"
            except Exception:
                ratio_text = "N/A"
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(upl_text))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(ratio_text))

    def on_positions_failed(self, err: str):
        # 無權限或未設置金鑰時，將 UPL 欄位標記為 N/A
        for row, _ in enumerate(self.inst_ids):
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem("N/A"))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem("N/A"))

    def draw_plot(self):
        if not self.inst_ids:
            return
        symbol = self.symbolSelect.currentText() if self.symbolSelect.count() else None
        if not symbol:
            return
        
        period = self.periodSelect.currentText()
        
        # 清除當前圖形
        self.figure.clear()
        
        # 獲取K線數據
        df = get_candlestick_data(symbol, period, 100)
        
        if df is not None and not df.empty:
            # 使用mplfinance繪製K線圖
            try:
                # 使用mplfinance繪製K線圖到當前figure
                mpf.plot(df, type='candle', style='charles', 
                        title=f"{symbol} - {period} Candlestick Chart ({TIMEZONE})",
                        ylabel="Price (USDT)",
                        volume=True,
                        figsize=(6, 4),
                        fig=self.figure,
                        datetime_format='%H:%M',
                        tight_layout=True)
                
            except Exception as e:
                # 如果mplfinance失敗，回退到簡單折線圖
                ax = self.figure.add_subplot(111)
                ax.grid(True, linestyle=":", linewidth=0.5)
                ax.set_xlabel("Time")
                ax.set_ylabel("Price")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                
                if 'close' in df.columns:
                    ax.plot(df.index, df['close'], linewidth=1.2, color='blue')
                    ax.set_title(f"{symbol} - {period} (Close Price)")
                else:
                    ax.set_title(f"{symbol} - {period} No Data")
                
                # 為折線圖也添加布局調整
                self.figure.tight_layout(pad=2.0)
        else:
            # 沒有數據時顯示提示
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"{symbol} - {period}\nNo K-line Data", 
                   ha='center', va='center', transform=ax.transAxes, fontsize=12)
            ax.set_title(f"{symbol} - {period} No Data")
        
        # 調整圖形布局，確保x軸標題不被覆蓋
        self.figure.tight_layout(pad=3.0)
        self.figure.subplots_adjust(bottom=0.15)  # 為x軸標題留出更多空間
        self.figure.autofmt_xdate()
        self.canvas.draw_idle()

    def update_change_column(self):
        # 針對每個標的，計算相對 30 分鐘前的漲幅百分比
        if not self.inst_ids:
            return
        now = datetime.datetime.now()
        threshold = now - datetime.timedelta(minutes=30)
        for row, inst in enumerate(self.inst_ids):
            series = self.histories.get(inst, [])
            if not series or len(series) < 2:
                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("N/A"))
                continue
            # 取得當前價
            _, current_val = series[-1]
            # 尋找最接近且不晚於 threshold 的舊資料
            baseline_val = None
            last_before_or_at = None
            for t, v in series:
                if t <= threshold:
                    last_before_or_at = v
                else:
                    break
            if last_before_or_at is not None:
                baseline_val = last_before_or_at
            else:
                # 若沒有早於閾值的資料，則以最早一筆作為基準（資料不足 30 分鐘）
                baseline_val = series[0][1]
            try:
                pct = None
                if baseline_val and baseline_val > 0:
                    pct = (current_val - baseline_val) / baseline_val * 100.0
                    text = f"{pct:+.2f}%"
                else:
                    text = "N/A"
            except Exception:
                text = "N/A"
                pct = None
            item = self.table.item(row, 2)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, 2, item)
            item.setText(text)
            # 顏色標示與提醒
            default_bg = QtGui.QBrush()
            if pct is None:
                item.setBackground(default_bg)
                continue
            abs_threshold = self.alertThreshold.value()
            triggered = abs(pct) >= abs_threshold
            if triggered:
                color = QtGui.QColor(220, 20, 60) if pct < 0 else QtGui.QColor(0, 128, 0)
                item.setBackground(QtGui.QBrush(color).color())
                self._maybe_alert(inst, pct)
            else:
                item.setBackground(default_bg)

    def _maybe_alert(self, inst: str, pct: float):
        if not self.alertEnable.isChecked():
            return
        now = datetime.datetime.now()
        last_at = self._lastAlertAt.get(inst)
        if last_at and (now - last_at) < datetime.timedelta(minutes=self._alertCooldownMinutes):
            return
        self._lastAlertAt[inst] = now
        try:
            QtWidgets.QMessageBox.information(
                self,
                "Price Alert",
                f"{inst} 30-minute change reached {pct:+.2f}%"
            )
        except Exception:
            pass


