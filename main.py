import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox
from PyQt5.uic import loadUi

import os
from PyQt5.uic import loadUi

class GradeCalc(QMainWindow):
    def __init__(self):
        super().__init__()
        ui_path = os.path.join(os.path.dirname(__file__), "gradeCalc.ui")
        loadUi("gradeCalc.ui", self)

        # Connect the calculate button
        self.btn_calculate.clicked.connect(self.on_calculate)

        # Optional: placeholders
        for le in [self.le_homework, self.le_projects, self.le_exam1,
                   self.le_midterm, self.le_exam2, self.le_final]:
            le.setPlaceholderText("0–100")

        # Clear result at start
        self.lbl_result.setText("")
        self.lbl_result.setStyleSheet("")

    # ------- Helpers -------
    @staticmethod
    def _parse_pct(line_edit):
        """
        Read a % from a QLineEdit.
        Empty -> None (ignored in calc)
        Invalid / out of range -> raises ValueError
        """
        s = line_edit.text().strip()
        if s == "":
            return None
        v = float(s)  # may raise ValueError
        if not 0 <= v <= 100:
            raise ValueError("Percent must be 0–100")
        return v

    @staticmethod
    def _letter_for(numeric):
        """
        Return (letter, color) based on syllabus thresholds.
        """
        if numeric >= 89.5:
            return "A", "green"
        elif numeric >= 79.5:
            return "B", "blue"
        elif numeric >= 69.5:
            return "C", "yellow"
        elif numeric >= 59.5:
            return "D", "orange"
        else:
            return "F", "red"

    # ------- Main action -------
    def on_calculate(self):
        # Reset any red borders from a previous run
        for le in [self.le_homework, self.le_projects, self.le_exam1,
                   self.le_midterm, self.le_exam2, self.le_final]:
            le.setStyleSheet("")

        # Weights from your syllabus (sum = 1.00)
        weights = {
            "homework": 0.30,
            "projects": 0.09,
            "exam1":   0.15,
            "midterm": 0.10,
            "exam2":   0.15,
            "final":   0.21,
        }

        # Read values safely (None = missing)
        fields = {
            "homework": (self.le_homework, None),
            "projects": (self.le_projects, None),
            "exam1":    (self.le_exam1, None),
            "midterm":  (self.le_midterm, None),
            "exam2":    (self.le_exam2, None),
            "final":    (self.le_final, None),
        }

        try:
            for k, (le, _) in fields.items():
                try:
                    val = self._parse_pct(le)
                    fields[k] = (le, val)
                except ValueError:
                    le.setStyleSheet("border: 2px solid #d33;")
                    raise
        except Exception as e:
            QMessageBox.warning(self, "Invalid input",
                                f"Please enter numbers 0–100 (or leave blank).\n\n{e}")
            self.lbl_result.setText("")
            self.lbl_result.setStyleSheet("")
            return

        # Keep only the entries the user provided (support partial data)
        provided = {k: v for k, (_, v) in fields.items() if v is not None}
        if not provided:
            QMessageBox.information(self, "No data",
                                    "Enter at least one grade to calculate a partial final.")
            return

        # Re-normalize weights over the provided items
        w_sum = sum(weights[k] for k in provided.keys())
        numeric = sum(provided[k] * weights[k] for k in provided.keys()) / w_sum

        letter, color = self._letter_for(numeric)
        self.lbl_result.setText(f"{numeric:.1f}%  —  {letter}")
        self.lbl_result.setStyleSheet(f"color: {color}; font-size: 22pt;")

def main():
    app = QApplication(sys.argv)
    win = GradeCalc()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()