import sys
from PyQt5 import QtWidgets
from cli import main_cli
from gui import PriceWindow


if __name__ == "__main__":
	if "--cli" in sys.argv:
		main_cli()
	else:
		app = QtWidgets.QApplication(sys.argv)
		win = PriceWindow()
		win.show()
		sys.exit(app.exec_())
