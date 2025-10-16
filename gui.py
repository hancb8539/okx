import os
import datetime
from typing import Any, Optional, Dict

from PyQt5 import QtWidgets, QtCore, QtGui
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates

from items import read_item_file
from okx_api import get_ticker
from config import GUI_REFRESH_INTERVAL_MS


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
        self.setWindowTitle("OKX 行情查詢")
        self.resize(720, 560)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["交易對", "最新價", "30分漲幅"])
        self.table.horizontalHeader().setStretchLastSection(True)

        self.refreshBtn = QtWidgets.QPushButton("重新整理", self)
        self.statusLabel = QtWidgets.QLabel("就緒", self)

        # 圖表區
        self.figure = Figure(figsize=(5, 3))
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.grid(True, linestyle=":", linewidth=0.5)
        self.ax.set_xlabel("time")
        self.ax.set_ylabel("price")
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

        # 圖表標的一覽
        self.symbolLabel = QtWidgets.QLabel("圖表標的:")
        self.symbolSelect = QtWidgets.QComboBox()

        vbox = QtWidgets.QVBoxLayout()
        vbox.addWidget(self.table)
        chartCtl = QtWidgets.QHBoxLayout()
        chartCtl.addWidget(self.symbolLabel)
        chartCtl.addWidget(self.symbolSelect)
        chartCtl.addStretch(1)
        vbox.addLayout(chartCtl)
        vbox.addWidget(self.canvas)

        # 提醒控制區
        alertCtl = QtWidgets.QHBoxLayout()
        self.alertEnable = QtWidgets.QCheckBox("啟用漲幅提醒")
        self.alertEnable.setChecked(False)
        self.alertThreshold = QtWidgets.QDoubleSpinBox()
        self.alertThreshold.setDecimals(2)
        self.alertThreshold.setSuffix(" %")
        self.alertThreshold.setRange(0.00, 10000.00)
        self.alertThreshold.setValue(2.00)
        alertCtl.addWidget(self.alertEnable)
        alertCtl.addWidget(QtWidgets.QLabel("閾值"))
        alertCtl.addWidget(self.alertThreshold)
        alertCtl.addStretch(1)

        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(self.refreshBtn)
        ctrl.addStretch(1)
        ctrl.addWidget(self.statusLabel)
        vbox.addLayout(alertCtl)
        vbox.addLayout(ctrl)
        central.setLayout(vbox)

        self.inst_ids = read_item_file(os.path.join(os.path.dirname(__file__), "item.txt"))
        self._populate_table()
        self._init_plot_state()
        # 提醒冷卻追蹤（避免連續彈窗）：{inst: datetime}
        self._lastAlertAt: Dict[str, datetime.datetime] = {}
        self._alertCooldownMinutes = 10

        self.refreshBtn.clicked.connect(self.refresh)
        self.symbolSelect.currentIndexChanged.connect(self.draw_plot)

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

    def _populate_table(self):
        self.table.setRowCount(len(self.inst_ids))
        for row, inst in enumerate(self.inst_ids):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(inst))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem("-"))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("-"))

    def _init_plot_state(self):
        # 歷史緩存: {inst_id: list[tuple[datetime, float]]}
        self.histories: Dict[str, list] = {inst: [] for inst in self.inst_ids}
        self.symbolSelect.clear()
        self.symbolSelect.addItems(self.inst_ids)
        self.draw_plot()

    def refresh(self):
        if not self.inst_ids:
            self.statusLabel.setText("item.txt 無交易對")
            return
        self.refreshBtn.setEnabled(False)
        self.statusLabel.setText("查詢中...")
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
        self.statusLabel.setText("完成")
        self.refreshBtn.setEnabled(True)
        self.update_change_column()
        self.draw_plot()

    def on_failed(self, err: str):
        self.statusLabel.setText(f"錯誤: {err}")
        self.refreshBtn.setEnabled(True)

    def draw_plot(self):
        if not self.inst_ids:
            return
        symbol = self.symbolSelect.currentText() if self.symbolSelect.count() else None
        if not symbol:
            return
        series = self.histories.get(symbol, [])
        self.ax.clear()
        self.ax.grid(True, linestyle=":", linewidth=0.5)
        self.ax.set_xlabel("time")
        self.ax.set_ylabel("price")
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        if series:
            xs = [t for t, _ in series]
            ys = [v for _, v in series]
            self.ax.plot(xs, ys, marker="o", linewidth=1.2)
            self.ax.set_title(f"{symbol}")
            self.figure.autofmt_xdate()
        else:
            self.ax.set_title(f"{symbol} 無資料")
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
                "漲幅提醒",
                f"{inst} 30 分漲幅達到 {pct:+.2f}%"
            )
        except Exception:
            pass


