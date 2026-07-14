import os
import sys


if getattr(sys, "frozen", False):
    # inside a PyInstaller bundle
    BASE_DIR = sys._MEIPASS

else:
    # normal Python execution
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


WEB_DIR = os.path.join(BASE_DIR, "static")
