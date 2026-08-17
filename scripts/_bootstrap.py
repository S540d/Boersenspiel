"""Fügt src/ dem Python-Pfad hinzu, damit die CLI-Scripts ohne Installation
des Pakets laufen (importiert von jedem Script in diesem Verzeichnis)."""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
