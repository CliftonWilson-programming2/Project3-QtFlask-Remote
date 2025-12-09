#!/usr/bin/env python3
"""
Toastmaster Toolbox - Ah-Counter Client

Runs on the *Ah-Counter* VM / laptop.

This GUI lets the Ah-Counter press a big "AH / UM" button every time
a speech disfluency is heard.  Each press sends a POST request to the
presenter machine, which keeps the master count and updates its display.

REST API on presenter (already implemented there):
    GET  /disfluency    ->  {"count": <int>}
    POST /disfluency    ->  {"count": <int>}  (increments by 1)

Requirements:
    pip install pyqt5 requests
"""

import sys                  # for command-line args + exit
import requests             # HTTP client library
from PyQt5 import QtWidgets, QtCore   # PyQt5 GUI widgets + core

# ----------------------------------------------------------------------
# Configuration: default presenter address
# ----------------------------------------------------------------------
# CHANGE THIS if your presenter shows a different IP in its status bar.
# Example when presenter says: "Ah-Counter server running at http://10.0.3.3:5000"
DEFAULT_URL = "http://10.0.3.3:5000"


class AhCounterClient(QtWidgets.QMainWindow):
    """Main window for the Ah-Counter client."""

    def __init__(self):
        # Call parent constructor (QMainWindow)
        super().__init__()

        # ---------- Window basics ----------
        self.setWindowTitle("Toastmaster Toolbox - Ah-Counter")   # title bar text
        self.setFixedSize(500, 330)                               # fixed window size

        # Central widget that will hold everything
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        # Main vertical layout for the whole window
        vbox = QtWidgets.QVBoxLayout(central)

        # ---------- Row 1: Server entry + Refresh button ----------
        row_server = QtWidgets.QHBoxLayout()                      # horizontal layout row

        lbl_server = QtWidgets.QLabel("Server:")                  # static label "Server:"
        row_server.addWidget(lbl_server)                          # add left side

        # Text box to type or view current server URL
        self.le_url = QtWidgets.QLineEdit(DEFAULT_URL)
        # Gray hint text when empty
        self.le_url.setPlaceholderText("http://<presenter-ip>:5000")
        row_server.addWidget(self.le_url, 1)                      # stretch factor 1 (grows)

        # Button to manually re-read current count from server
        self.btn_refresh = QtWidgets.QPushButton("Refresh Count")
        # When clicked → call our refresh_count() method
        self.btn_refresh.clicked.connect(self.refresh_count)
        row_server.addWidget(self.btn_refresh)

        # Add this horizontal row to the main vertical layout
        vbox.addLayout(row_server)

        # ---------- Big "Disfluencies: N" label ----------
        # Start with an em dash until we successfully connect
        self.lbl_count = QtWidgets.QLabel("Disfluencies: —")
        # Center text in label
        self.lbl_count.setAlignment(QtCore.Qt.AlignCenter)
        # Make the font large and readable from a distance
        f = self.lbl_count.font()
        f.setPointSize(24)
        self.lbl_count.setFont(f)
        vbox.addWidget(self.lbl_count)

        # ---------- Big AH / UM button ----------
        # Tall button so it’s easy to hit quickly
        self.btn_ah = QtWidgets.QPushButton("AH / UM")
        self.btn_ah.setMinimumHeight(120)

        # Make the text large, bold, and centered
        f2 = self.btn_ah.font()
        f2.setPointSize(26)
        f2.setBold(True)
        self.btn_ah.setFont(f2)

        # When clicked → send a POST /disfluency to presenter
        self.btn_ah.clicked.connect(self.send_disfluency)

        vbox.addWidget(self.btn_ah)

        # Add some stretch at the bottom so layout looks nicer
        vbox.addStretch(1)

        # ---------- Status bar ----------
        # Thin bar at the bottom for messages (errors, connected, etc.)
        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)

        # On startup, try to contact server and show current count
        self.refresh_count()

    # ==================================================================
    # Helper: build full URL for an endpoint
    # ==================================================================
    def url(self, path: str) -> str:
        """
        Combine the server base URL from the text box with an endpoint path.
        Example:
            self.le_url.text() -> "http://10.0.3.3:5000"
            path               -> "/disfluency"
            return             -> "http://10.0.3.3:5000/disfluency"
        """
        return self.le_url.text().rstrip("/") + path

    # ==================================================================
    # Helper: HTTP GET
    # ==================================================================
    def get_json(self, path: str):
        """
        Perform a GET request to the presenter and parse JSON.
        Raises requests exceptions if connection fails.
        """
        # Send GET to the full URL with a short timeout
        r = requests.get(self.url(path), timeout=3.0)
        # Raise if HTTP status != 200 (for example 404 or 500)
        r.raise_for_status()
        # Parse body as JSON and return Python dict
        return r.json()

    # ==================================================================
    # Helper: HTTP POST
    # ==================================================================
    def post_json(self, path: str, payload: dict | None = None):
        """
        Perform a POST request with optional JSON payload.
        Raises requests exceptions if connection fails.
        """
        r = requests.post(self.url(path), json=payload or {}, timeout=3.0)
        r.raise_for_status()
        return r.json()

    # ==================================================================
    # Action: refresh disfluency count from presenter
    # ==================================================================
    def refresh_count(self):
        """
        Read the current disfluency count from the presenter via GET /disfluency.
        Update big label and status bar.
        """
        try:
            # Ask presenter for JSON: {"count": <int>}
            data = self.get_json("/disfluency")
            # Extract "count" from response
            count = data.get("count", 0)
            # Update text label
            self.lbl_count.setText(f"Disfluencies: {count}")
            # Show success message briefly (2 seconds)
            self.status.showMessage("Connected to presenter", 2000)
        except Exception as e:
            # If anything went wrong (no server, wrong IP, etc.)
            self.lbl_count.setText("Disfluencies: —")
            # Show error message for 5 seconds
            self.status.showMessage(f"GET error: {e}", 5000)

    # ==================================================================
    # Action: send one disfluency event
    # ==================================================================
    def send_disfluency(self):
        """
        Notify the presenter that one disfluency occurred via POST /disfluency.
        After successful POST we call refresh_count() to update our display.
        """
        try:
            # POST with empty JSON body; presenter increments its global count
            self.post_json("/disfluency")
            # After success, re-fetch the current count so label is in sync
            self.refresh_count()
        except Exception as e:
            # Show any network / HTTP error in the status bar
            self.status.showMessage(f"POST error: {e}", 5000)


# ======================================================================
# Application entry point
# ======================================================================

def main():
    """Create Qt application and show the Ah-Counter window."""
    app = QtWidgets.QApplication(sys.argv)
    win = AhCounterClient()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

