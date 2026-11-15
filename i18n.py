"""Tiny helper to switch messages between Japanese and English."""
import os

_lang = None


def detect():
    v = (os.environ.get("DPIRS_LANG") or os.environ.get("LC_ALL")
         or os.environ.get("LANG") or "")
    return "ja" if v.lower().startswith("ja") else "en"


def set_lang(value):
    """value: 'ja', 'en' or 'auto'."""
    global _lang
    value = (value or "auto").strip().lower()
    _lang = value if value in ("ja", "en") else detect()


def tr(ja, en):
    lang = _lang or detect()
    return ja if lang == "ja" else en
