#!/usr/bin/env python3
"""
Toastmaster Toolbox - Presenter

Features:
 - Mirror webcam preview for presenter.
 - Facial Expression Recognition using `fer` library (no face box drawn).
 - Timing cues with configurable target time and warning thresholds.
 - Ah-Counter integration via Flask server (GET/POST /disfluency) for remote client.
 - Visual + audio cue on disfluency (audio only; no color flash).
 - End-of-speech report generation and save/load to file.
"""

import sys
import time
import threading
from collections import deque, Counter
from typing import Optional, Dict, List

from PyQt5 import QtCore, QtGui, QtWidgets
import cv2

# --- FER library (old simple one you used before) ---
try:
    from fer import FER
except Exception:
    FER = None

# --- Flask for Ah-Counter API ---
from flask import Flask, jsonify

# ======================================================================
# Global bus and Flask API (disfluencies)
# ======================================================================

class Bus(QtCore.QObject):
    disfluencyChanged = QtCore.pyqtSignal(int)  # new count from Flask

bus = Bus()

app = Flask(__name__)
_DISFLUENCY_COUNT = 0


@app.get("/disfluency")
def get_disfluency():
    return jsonify(count=_DISFLUENCY_COUNT)


@app.post("/disfluency")
def add_disfluency():
    global _DISFLUENCY_COUNT
    _DISFLUENCY_COUNT += 1
    bus.disfluencyChanged.emit(_DISFLUENCY_COUNT)
    return jsonify(count=_DISFLUENCY_COUNT)


def run_flask_server():
    """Run Flask on 0.0.0.0 so the Ah-Counter VM can reach it."""
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)


# ======================================================================
# Video widget
# ======================================================================

class VideoWidget(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self._last_pixmap: Optional[QtGui.QPixmap] = None
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QtGui.QColor(0, 0, 0))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

    def update_frame(self, qimage: QtGui.QImage):
        self._last_pixmap = QtGui.QPixmap.fromImage(qimage)
        self._set_scaled_pixmap()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        self._set_scaled_pixmap()

    def _set_scaled_pixmap(self):
        if self._last_pixmap is None:
            return
        self.setPixmap(
            self._last_pixmap.scaled(
                self.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )


# ======================================================================
# Presenter main window
# ======================================================================

class PresenterWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Toastmaster Toolbox - Presenter")
        self.resize(1100, 700)

        # --- Core state ---
        self.cap: Optional[cv2.VideoCapture] = None
        self.mirror = True
        self.fer_enabled = True
        self.detector: Optional[FER] = None
        self.frame_times = deque(maxlen=60)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._on_timer_frame)

        # FER perf + stabilization
        self._fer_stride = 3
        self._fer_i = 0
        self._down_w = 480
        self._last_box = None
        self._last_seen = 0.0

        # Emotion statistics
        self.emotion_counts: Counter = Counter()
        self.emotion_samples_total = 0

        # Timing state
        self.speech_running = False
        self.speech_start_monotonic: Optional[float] = None
        self.speech_elapsed_sec = 0.0

        # Disfluency state
        self.disfluency_count = 0
        self.disfluency_times: List[float] = []

        # Build GUI, init FER, connect signals, start servers
        self._build_ui()
        self._apply_timing_color("#4caf50")   # initial green
        self._maybe_init_fer()
        bus.disfluencyChanged.connect(self.on_disfluency_from_api)
        self.start_flask_server()
        QtCore.QTimer.singleShot(150, self.open_camera)

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        v_main = QtWidgets.QVBoxLayout(central)

        self.tabs = QtWidgets.QTabWidget()
        v_main.addWidget(self.tabs, 1)

        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)

        # ===== Tab 1: Facial Expressions =====
        tab_fe = QtWidgets.QWidget()
        h_fe = QtWidgets.QHBoxLayout(tab_fe)

        self.video = VideoWidget()
        h_fe.addWidget(self.video, 3)

        panel = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(panel)

        self.cb_mirror = QtWidgets.QCheckBox("Mirror horizontally")
        self.cb_mirror.setChecked(True)
        self.cb_mirror.toggled.connect(self._on_mirror_toggled)

        self.cb_fer = QtWidgets.QCheckBox("Enable Facial Expression Recognition")
        self.cb_fer.setChecked(True)
        self.cb_fer.toggled.connect(self._on_fer_toggled)

        form.addWidget(self.cb_mirror)
        form.addWidget(self.cb_fer)

        self.lbl_fps = QtWidgets.QLabel("FPS: —")
        self.lbl_fps.setStyleSheet("font-family: monospace;")
        form.addWidget(self.lbl_fps)

        form.addSpacing(10)
        form.addWidget(QtWidgets.QLabel("Current Emotion Scores:"))

        self.table_emotions = QtWidgets.QTableWidget(0, 2)
        self.table_emotions.setHorizontalHeaderLabels(["Emotion", "Score"])
        self.table_emotions.verticalHeader().setVisible(False)
        self.table_emotions.horizontalHeader().setStretchLastSection(True)
        self.table_emotions.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table_emotions.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table_emotions.setFixedHeight(250)
        form.addWidget(self.table_emotions)

        form.addStretch(1)
        h_fe.addWidget(panel, 1)
        self.tabs.addTab(tab_fe, "Facial Expressions")

        # ===== Tab 2: Timing =====
        tab_timing = QtWidgets.QWidget()
        v_t = QtWidgets.QVBoxLayout(tab_timing)

        h_target = QtWidgets.QHBoxLayout()
        h_target.addWidget(QtWidgets.QLabel("Target Time:"))

        self.spin_minutes = QtWidgets.QSpinBox()
        self.spin_minutes.setRange(0, 60)
        self.spin_minutes.setValue(5)
        h_target.addWidget(self.spin_minutes)
        h_target.addWidget(QtWidgets.QLabel("min"))

        self.spin_seconds = QtWidgets.QSpinBox()
        self.spin_seconds.setRange(0, 59)
        self.spin_seconds.setValue(0)
        h_target.addWidget(self.spin_seconds)
        h_target.addWidget(QtWidgets.QLabel("sec"))

        h_target.addStretch(1)
        v_t.addLayout(h_target)

        h_warn = QtWidgets.QHBoxLayout()
        h_warn.addWidget(QtWidgets.QLabel("Warn at:"))
        self.spin_warn1 = QtWidgets.QSpinBox()
        self.spin_warn1.setRange(0, 100)
        self.spin_warn1.setValue(75)
        h_warn.addWidget(self.spin_warn1)
        h_warn.addWidget(QtWidgets.QLabel("%"))
        self.spin_warn2 = QtWidgets.QSpinBox()
        self.spin_warn2.setRange(0, 100)
        self.spin_warn2.setValue(90)
        h_warn.addWidget(self.spin_warn2)
        h_warn.addWidget(QtWidgets.QLabel("%"))
        h_warn.addStretch(1)
        v_t.addLayout(h_warn)

        self.lbl_time = QtWidgets.QLabel("Time: 00:00 / 00:00")
        self.lbl_time.setAlignment(QtCore.Qt.AlignCenter)
        f_time = self.lbl_time.font()
        f_time.setPointSize(16)
        self.lbl_time.setFont(f_time)
        v_t.addWidget(self.lbl_time)

        self.progress_time = QtWidgets.QProgressBar()
        self.progress_time.setRange(0, 1000)
        self.progress_time.setValue(0)
        self.progress_time.setTextVisible(False)
        self.progress_time.setStyleSheet("QProgressBar{height:20px;}")
        v_t.addWidget(self.progress_time)

        self.lbl_disfluency = QtWidgets.QLabel("Disfluencies: 0")
        self.lbl_disfluency.setAlignment(QtCore.Qt.AlignLeft)
        v_t.addWidget(self.lbl_disfluency)

        h_buttons = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("Start Speech")
        self.btn_stop = QtWidgets.QPushButton("Stop Speech")
        self.btn_reset = QtWidgets.QPushButton("Reset")

        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_speech)
        self.btn_stop.clicked.connect(self.stop_speech)
        self.btn_reset.clicked.connect(self.reset_speech)

        h_buttons.addWidget(self.btn_start)
        h_buttons.addWidget(self.btn_stop)
        h_buttons.addWidget(self.btn_reset)
        h_buttons.addStretch(1)
        v_t.addLayout(h_buttons)
        v_t.addStretch(1)

        self.tabs.addTab(tab_timing, "Timing")

        # ===== Tab 3: Report =====
        tab_report = QtWidgets.QWidget()
        v_r = QtWidgets.QVBoxLayout(tab_report)

        self.text_report = QtWidgets.QTextEdit()
        v_r.addWidget(self.text_report, 1)

        h_rep_buttons = QtWidgets.QHBoxLayout()
        self.btn_generate_report = QtWidgets.QPushButton("Generate Report")
        self.btn_save_report = QtWidgets.QPushButton("Save Report")
        self.btn_load_report = QtWidgets.QPushButton("Load Report")

        self.btn_generate_report.clicked.connect(self.generate_report)
        self.btn_save_report.clicked.connect(self.save_report)
        self.btn_load_report.clicked.connect(self.load_report)

        h_rep_buttons.addWidget(self.btn_generate_report)
        h_rep_buttons.addWidget(self.btn_save_report)
        h_rep_buttons.addWidget(self.btn_load_report)
        h_rep_buttons.addStretch(1)
        v_r.addLayout(h_rep_buttons)

        self.tabs.addTab(tab_report, "Report")

    # ------------------------------------------------------------------
    # Timing color helper
    # ------------------------------------------------------------------

    def _apply_timing_color(self, color: str):
        """Apply the timing color to both the progress bar and video border."""
        self.progress_time.setStyleSheet(
            f"QProgressBar{{height:20px;}} "
            f"QProgressBar::chunk{{background-color:{color};}}"
        )
        self.video.setStyleSheet(
            f"border: 6px solid {color}; background-color: black;"
        )

    # ------------------------------------------------------------------
    # Camera + FER
    # ------------------------------------------------------------------

    def open_camera(self):
        self.close_camera()
        self.cap = cv2.VideoCapture(0, cv2.CAP_ANY)
        if not self.cap or not self.cap.isOpened():
            self.status_bar.showMessage("Failed to open webcam", 5000)
            self.cap = None
            return

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.frame_times.clear()
        self.timer.start(0)
        self.status_bar.showMessage("Webcam opened", 3000)

    def close_camera(self):
        if self.timer.isActive():
            self.timer.stop()
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def _maybe_init_fer(self):
        if FER is None:
            self.detector = None
            self.status_bar.showMessage("FER unavailable (install 'fer')", 8000)
            return
        try:
            self.detector = FER(mtcnn=False)
            self.status_bar.showMessage("FER initialized", 4000)
        except Exception as e:
            self.detector = None
            self.status_bar.showMessage(f"FER init failed: {e}", 8000)

    def _on_mirror_toggled(self, checked: bool):
        self.mirror = checked

    def _on_fer_toggled(self, checked: bool):
        self.fer_enabled = checked

    def _on_timer_frame(self):
        """Main frame loop: capture, FER, display, timing."""
        if self.cap is None:
            return

        ok, frame = self.cap.read()
        if not ok or frame is None:
            self.status_bar.showMessage("Camera read failed", 4000)
            return

        if self.mirror:
            frame = cv2.flip(frame, 1)

        # ----- FPS -----
        now = time.time()
        self.frame_times.append(now)
        fps = 0.0
        if len(self.frame_times) >= 2:
            dt = self.frame_times[-1] - self.frame_times[0]
            if dt > 0:
                fps = (len(self.frame_times) - 1) / dt
        self.lbl_fps.setText(f"FPS: {fps:4.1f}")

        # ----- FER -----
        expr_scores: Dict[str, float] = {}
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
                    small = cv2.resize(
                        rgb_full,
                        (self._down_w, int(h_img * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                    raw = self.detector.detect_emotions(small) or []
                    for r in raw:
                        box = r.get("box") if isinstance(r, dict) else None
                        emotions = r.get("emotions", {}) if isinstance(r, dict) else {}
                        if box is None:
                            continue
                        x, y, w, h = map(int, box)
                        x = int(x / scale)
                        y = int(y / scale)
                        w = int(w / scale)
                        h = int(h / scale)
                        mapped.append({"box": [x, y, w, h], "emotions": emotions})

                now_ts = time.time()
                results = []
                if mapped:
                    best = max(mapped, key=lambda rr: rr["box"][2] * rr["box"][3])
                    self._last_box, self._last_seen = best, now_ts
                    results = [best]
                elif self._last_box and (now_ts - self._last_seen) < 0.7:
                    results = [self._last_box]

                overlay = frame.copy()
                for r in results:
                    emotions = r["emotions"]
                    for k, v in emotions.items():
                        expr_scores[k] = float(v)
                    if emotions:
                        top_label = max(emotions.items(), key=lambda kv: kv[1])[0]
                        self.emotion_counts[top_label] += 1
                        self.emotion_samples_total += 1

            except Exception as e:
                if int(time.time()) % 2 == 0:
                    self.status_bar.showMessage(f"FER error: {e}", 2000)

        self._update_emotion_table(expr_scores)

        # ----- Display frame -----
        rgb_disp = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_disp.shape
        qimg = QtGui.QImage(
            rgb_disp.data, w, h, ch * w, QtGui.QImage.Format.Format_RGB888
        )
        self.video.update_frame(qimg)

        # ----- Timing UI -----
        self._update_timing_ui()

    def _update_emotion_table(self, scores: Dict[str, float]):
        self.table_emotions.setRowCount(0)
        if not scores:
            return
        for emo, val in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            row = self.table_emotions.rowCount()
            self.table_emotions.insertRow(row)
            self.table_emotions.setItem(row, 0, QtWidgets.QTableWidgetItem(emo))
            self.table_emotions.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{val:.2f}"))

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    def _target_seconds(self) -> float:
        return self.spin_minutes.value() * 60 + self.spin_seconds.value()

    def start_speech(self):
        if self.speech_running:
            return
        self.speech_running = True
        self.speech_start_monotonic = time.monotonic()
        self.speech_elapsed_sec = 0.0
        self.disfluency_times.clear()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_bar.showMessage("Speech started", 3000)

    def stop_speech(self):
        if not self.speech_running:
            return
        self.speech_running = False
        if self.speech_start_monotonic is not None:
            self.speech_elapsed_sec = time.monotonic() - self.speech_start_monotonic
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        QtWidgets.QApplication.beep()
        self.status_bar.showMessage("Speech stopped", 3000)
        self._update_timing_ui()

    def reset_speech(self):
        self.speech_running = False
        self.speech_start_monotonic = None
        self.speech_elapsed_sec = 0.0
        self.disfluency_times.clear()
        self.disfluency_count = 0
        global _DISFLUENCY_COUNT
        _DISFLUENCY_COUNT = 0
        self.lbl_disfluency.setText("Disfluencies: 0")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._update_timing_ui()
        self.status_bar.showMessage("Speech reset", 2000)

    def _update_timing_ui(self):
        target = self._target_seconds()
        if self.speech_running and self.speech_start_monotonic is not None:
            self.speech_elapsed_sec = time.monotonic() - self.speech_start_monotonic

        elapsed = self.speech_elapsed_sec

        def fmt(sec: float) -> str:
            s = int(round(sec))
            return f"{s // 60:02d}:{s % 60:02d}"

        self.lbl_time.setText(f"Time: {fmt(elapsed)} / {fmt(target)}")

        if target > 0:
            frac = max(0.0, min(1.2, elapsed / target))
        else:
            frac = 0.0
        self.progress_time.setValue(int(frac * 1000))

        warn1 = self.spin_warn1.value() / 100.0
        warn2 = self.spin_warn2.value() / 100.0

        # timing-based color only
        color = "#4caf50"   # green
        if frac >= warn2:
            color = "#f44336"   # red
        elif frac >= warn1:
            color = "#ffc107"   # yellow

        self._apply_timing_color(color)

    # ------------------------------------------------------------------
    # Disfluencies from API
    # ------------------------------------------------------------------

    @QtCore.pyqtSlot(int)
    def on_disfluency_from_api(self, count: int):
        """Update count and timeline; no color flash."""
        self.disfluency_count = count
        self.lbl_disfluency.setText(f"Disfluencies: {count}")
        if self.speech_running and self.speech_start_monotonic is not None:
            t_rel = time.monotonic() - self.speech_start_monotonic
            self.disfluency_times.append(t_rel)
        QtWidgets.QApplication.beep()
        # No style changes here; timing alone controls colors

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_report(self):
        target = self._target_seconds()
        actual = self.speech_elapsed_sec
        if self.speech_running and self.speech_start_monotonic is not None:
            actual = time.monotonic() - self.speech_start_monotonic

        diff = actual - target
        if diff > 0:
            timing_result = f"Over time by {diff:.1f} s"
        else:
            timing_result = f"Under time by {abs(diff):.1f} s"

        if self.disfluency_times:
            times_str = ", ".join(f"{t:.1f}s" for t in self.disfluency_times)
        else:
            times_str = "(none)"

        lines = []
        total_samples = self.emotion_samples_total
        if total_samples > 0:
            for emo, cnt in self.emotion_counts.most_common():
                pct = 100.0 * cnt / total_samples
                lines.append(f"  {emo:<8}: {cnt:4d} ({pct:4.1f} %)")
            emo_summary = "\n".join(lines)
        else:
            emo_summary = "  No emotion samples collected."

        report = []
        report.append("Toastmaster Toolbox - Speech Report\n")
        report.append(f"Target Time: {target:.1f} s")
        report.append(f"Actual Time: {actual:.1f} s")
        report.append(f"Timing Result: {timing_result}\n")
        report.append(f"Total Disfluencies: {self.disfluency_count}")
        report.append(f"Disfluency Times: {times_str}\n")
        report.append("Facial Expression Summary:")
        report.append(emo_summary)

        self.text_report.setPlainText("\n".join(report))
        self.tabs.setCurrentIndex(2)

    def save_report(self):
        text = self.text_report.toPlainText()
        if not text.strip():
            QtWidgets.QMessageBox.information(
                self, "Save Report", "No report text to save. Generate a report first."
            )
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Report", "toastmaster_report.txt", "Text Files (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.status_bar.showMessage(f"Report saved to {path}", 5000)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Save Error", f"Failed to save report:\n{e}"
            )

    def load_report(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Report", "", "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            self.text_report.setPlainText(text)
            self.tabs.setCurrentIndex(2)
            self.status_bar.showMessage(f"Report loaded from {path}", 5000)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Load Error", f"Failed to load report:\n{e}"
            )

    # ------------------------------------------------------------------
    # Flask bootstrap + cleanup
    # ------------------------------------------------------------------

    def start_flask_server(self):
        import socket

        ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass

        t = threading.Thread(target=run_flask_server, daemon=True)
        t.start()
        self.status_bar.showMessage(
            f"Ah-Counter server running at http://{ip}:5000", 8000
        )

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self.close_camera()
        super().closeEvent(e)


# ======================================================================
# main
# ======================================================================

def main():
    app_qt = QtWidgets.QApplication(sys.argv)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    win = PresenterWindow()
    win.show()
    sys.exit(app_qt.exec_())


if __name__ == "__main__":
    main()


