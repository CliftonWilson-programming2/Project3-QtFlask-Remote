#!/usr/bin/env python3
# Webcam + FER (Qt) — single camera, fast, stable

import os
os.environ.pop("QT_PLUGIN_PATH", None)
os.environ["QT_QPA_PLATFORM"] = "xcb"

from PyQt5 import QtCore, QtGui, QtWidgets
import cv2
import sys, time
from collections import deque
from typing import Optional, Dict

try:
    from fer import FER
except Exception:
    FER = None


# Video on Qt GUI
class VideoWidget(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self._last_pixmap: Optional[QtGui.QPixmap] = None
        pal = self.palette(); pal.setColor(self.backgroundRole(), QtGui.QColor(20,20,20))
        self.setPalette(pal); self.setAutoFillBackground(True)

    def update_frame(self, qimage: QtGui.QImage):
        self._last_pixmap = QtGui.QPixmap.fromImage(qimage)
        self._set_scaled_pixmap()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e); self._set_scaled_pixmap()

    def _set_scaled_pixmap(self):
        if self._last_pixmap is None: return
        self.setPixmap(self._last_pixmap.scaled(
            self.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        ))


# Control panel on GUI
class ControlPanel(QtWidgets.QWidget):
    mirrorToggled = QtCore.pyqtSignal(bool)
    ferToggled = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel("Controls"); title.setStyleSheet("font-weight:600; font-size:16px;")
        self.mirror_cb = QtWidgets.QCheckBox("Mirror horizontally")
        self.fer_cb    = QtWidgets.QCheckBox("Enable FER (expressions)"); self.fer_cb.setChecked(True)

        self.fps_label = QtWidgets.QLabel("FPS: —"); self.fps_label.setStyleSheet("font-family: monospace;")
        self.status_label = QtWidgets.QLabel("Status: Ready"); self.status_label.setWordWrap(True)

        expr_title = QtWidgets.QLabel("Expressions"); expr_title.setStyleSheet("font-weight:600;")
        self.expr_view = QtWidgets.QTableWidget(0, 2)
        self.expr_view.setHorizontalHeaderLabels(["Emotion","Score"])
        self.expr_view.horizontalHeader().setStretchLastSection(True)
        self.expr_view.verticalHeader().setVisible(False)
        self.expr_view.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.expr_view.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.expr_view.setFixedHeight(220)

        self.open_btn  = QtWidgets.QPushButton("Open Camera")
        self.close_btn = QtWidgets.QPushButton("Close Camera")

        for w in (title, self.mirror_cb, self.fer_cb): lay.addWidget(w)
        lay.addSpacing(6); lay.addWidget(self.fps_label); lay.addWidget(self.status_label)
        lay.addSpacing(10); lay.addWidget(expr_title); lay.addWidget(self.expr_view)
        lay.addStretch(1); lay.addWidget(self.open_btn); lay.addWidget(self.close_btn)

        self.mirror_cb.toggled.connect(self.mirrorToggled)
        self.fer_cb.toggled.connect(self.ferToggled)

    def set_expressions(self, scores: Dict[str,float]):
        self.expr_view.setRowCount(0)
        if not scores: return
        for emo, val in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            r = self.expr_view.rowCount(); self.expr_view.insertRow(r)
            self.expr_view.setItem(r, 0, QtWidgets.QTableWidgetItem(emo))
            self.expr_view.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{val:.2f}"))


# Main window
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Webcam + FER (Qt)")
        self.resize(1080, 640)

        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        hbox = QtWidgets.QHBoxLayout(central)

        self.video = VideoWidget(); hbox.addWidget(self.video, stretch=3)
        self.panel = ControlPanel();  hbox.addWidget(self.panel, stretch=1)

        self.cap: Optional[cv2.VideoCapture] = None
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self._on_timer)
        self.frame_times = deque(maxlen=60)

        self.mirror = False
        self.fer_enabled = True
        self.detector: Optional[FER] = None

        # FER perf knobs
        self._fer_stride = 3
        self._fer_i = 0
        self._down_w = 480

        # Stabilization
        self._last_box = None
        self._last_seen = 0.0

        self._maybe_init_fer()

        # Wire buttons (this is critical)
        self.panel.open_btn.clicked.connect(self.open_camera)
        self.panel.close_btn.clicked.connect(self.close_camera)
        self.panel.mirrorToggled.connect(self._on_mirror)
        self.panel.ferToggled.connect(self._on_fer)

        # Auto-open on launch
        QtCore.QTimer.singleShot(150, self.open_camera)

    # Camera control
    def open_camera(self):
        self.close_camera()
        index = 0  # default webcam; label as “Camera 1”
        self.cap = cv2.VideoCapture(index, cv2.CAP_ANY)
        if self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
            self.panel.status_label.setText("Status: Camera 1 opened")
            self.frame_times.clear()
            self.timer.start(0)
        else:
            self.panel.status_label.setText("Status: Failed to open Camera 1")
            self.cap = None

    def close_camera(self):
        if self.timer.isActive(): self.timer.stop()
        if self.cap is not None:
            try: self.cap.release()
            except Exception: pass
            self.cap = None
        self.panel.status_label.setText("Status: Camera closed")

    # Toggles
    def _on_mirror(self, checked: bool): self.mirror = checked
    def _on_fer(self, checked: bool):    self.fer_enabled = checked

    def _maybe_init_fer(self):
        if FER is None:
            self.detector = None
            self.panel.status_label.setText("Status: FER unavailable (install 'fer' + TF)")
            return
        try:
            self.detector = FER(mtcnn=False)  # faster
            self.panel.status_label.setText("Status: FER initialized")
        except Exception as e:
            self.detector = None
            self.panel.status_label.setText(f"Status: FER init failed: {e}")

    # Frame loop
    def _on_timer(self):
        if self.cap is None: return
        ok, frame = self.cap.read()
        if not ok or frame is None:
            self.panel.status_label.setText("Status: Camera read failed"); return

        if self.mirror: frame = cv2.flip(frame, 1)

        # FPS
        now = time.time(); self.frame_times.append(now); fps = 0.0
        if len(self.frame_times) >= 2:
            dt = self.frame_times[-1] - self.frame_times[0]
            if dt > 0: fps = (len(self.frame_times) - 1) / dt
        self.panel.fps_label.setText(f"FPS: {fps:5.1f}")

        # FER (strided + downscaled, single-face with hold)
        expr_scores: Dict[str,float] = {}
        overlay = frame
        if self.fer_enabled and self.detector is not None:
            try:
                rgb_full = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self._fer_i += 1
                run_fer = (self._fer_i % self._fer_stride) == 0
                mapped = []
                if run_fer:
                    h_img, w_img, _ = rgb_full.shape
                    scale = self._down_w / float(w_img)
                    small = cv2.resize(rgb_full, (self._down_w, int(h_img*scale)), interpolation=cv2.INTER_AREA)
                    raw = self.detector.detect_emotions(small)
                    for r in raw:
                        box = r.get('box'); 
                        if box is None: continue
                        x,y,w,h = map(int, box)
                        x = int(x/scale); y = int(y/scale); w = int(w/scale); h = int(h/scale)
                        mapped.append({'box':[x,y,w,h], 'emotions': r.get('emotions',{})})

                now_ts = time.time(); results = []
                if mapped:
                    best = max(mapped, key=lambda rr: rr['box'][2] * rr['box'][3])
                    self._last_box, self._last_seen = best, now_ts
                    results = [best]
                elif self._last_box and (now_ts - self._last_seen) < 0.7:
                    results = [self._last_box]

                overlay = frame.copy()
                for r in results:
                    x,y,w,h = r['box']; emotions = r['emotions']
                    for k,v in emotions.items(): expr_scores[k] = float(v)
                    top = max(emotions.items(), key=lambda kv: kv[1])[0] if emotions else "?"
                    cv2.rectangle(overlay, (x,y), (x+w,y+h), (0,255,0), 2)
                    cv2.putText(overlay, top, (x, max(0, y-8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
            except Exception as e:
                if int(time.time()) % 2 == 0:
                    self.panel.status_label.setText(f"Status: FER error: {e}")

        self.panel.set_expressions(expr_scores)

        # Display
        try: rgb_disp = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
        except Exception: rgb_disp = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h,w,ch = rgb_disp.shape
        qimg = QtGui.QImage(rgb_disp.data, w, h, ch*w, QtGui.QImage.Format.Format_RGB888)
        self.video.update_frame(qimg)

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self.close_camera(); super().closeEvent(e)


def main():
    app = QtWidgets.QApplication(sys.argv)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    win = MainWindow(); win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()


