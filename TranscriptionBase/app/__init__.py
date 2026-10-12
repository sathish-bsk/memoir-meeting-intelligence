# Transcription Service App

# ── OpenMP fix: must run before any torch or ctranslate2 import ──
# torch and ctranslate2 (faster-whisper) both bundle libiomp5 on macOS.
# Loading both causes a segfault unless:
#   1. KMP_DUPLICATE_LIB_OK allows the duplicate
#   2. ctranslate2 initializes its OpenMP first
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

try:
    import ctranslate2
except ImportError:
    pass
