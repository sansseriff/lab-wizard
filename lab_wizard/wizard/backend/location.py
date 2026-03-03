from pathlib import Path
import os
import sys


THISS = "32"

# BASE_DIR = Path(__file__).resolve().parent

if getattr(sys, "frozen", False):
    # inside a PyInstaller bundle
    BASE_DIR = sys._MEIPASS

else:
    # normal Python execution
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


CONFIG_DIR = os.path.join(BASE_DIR, "config")
WEB_DIR = os.path.join(BASE_DIR, "static")
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), "logs")