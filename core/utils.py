import logging

try:
    import pandas_ta as ta  # original package (if available)
except ImportError:
    import pandas_ta_classic as ta  # vendored fallback (same API)

try:
    from PyQt6.QtWidgets import QTextEdit
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtGui import QFont

    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

    # Dummy classes for headless mode/CI
    class QObject:
        def __init__(self, *args, **kwargs):
            pass

    class pyqtSignal:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            pass

        def disconnect(self, *args, **kwargs):
            pass

    # Dummy QFont
    def QFont(*args, **kwargs):
        return None


class QTextEditLogger(logging.Handler, QObject):
    """A logging handler that emits signals to a QTextEdit widget."""

    appendPlainText = pyqtSignal(str)

    def __init__(self, parent):
        super().__init__()
        QObject.__init__(self)
        if GUI_AVAILABLE:
            self.widget = QTextEdit(parent)
            self.widget.setReadOnly(True)
            self.widget.setFont(QFont("Consolas", 9))
            self.appendPlainText.connect(self.widget.append)
        else:
            self.widget = None

    def emit(self, record):
        try:
            msg = self.format(record)
            self.appendPlainText.emit(msg)
        except RuntimeError:
            pass  # Widget already deleted
        except Exception as e:
            print(f"Logger error: {e}")  # Keep basic print for critical logger errors

    def close(self):
        """Clean shutdown of the logger"""
        try:
            if getattr(self, "widget", None):
                try:
                    self.appendPlainText.disconnect()
                except Exception:
                    pass
                try:
                    self.widget.deleteLater()
                except Exception:
                    pass
                self.widget = None
        except Exception:
            pass
        super().close()


class BackendSignals(QObject):
    """Container for all signals from backend threads to the GUI."""

    account_update = pyqtSignal(dict)
    portfolio_update = pyqtSignal(list)
    strategy_update = pyqtSignal(dict)
    news_update = pyqtSignal(dict)
    scanner_progress = pyqtSignal(int, int)
    scanner_complete = pyqtSignal(list)
    ai_learning_update = pyqtSignal(str)
    ai_learning_complete = pyqtSignal(dict)
    simulation_status = pyqtSignal(dict)
    equity_curve_update = pyqtSignal(float, float)  # (timestamp, equity)
    error_message = pyqtSignal(str, str)  # (title, message)
