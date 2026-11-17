# Copyright (C) 2026 JQ7EAH (GitHub: Nickel2Plus)
#
# This file is part of DPiRS.
#
# DPiRS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# DPiRS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
# SPDX-License-Identifier: GPL-3.0-only

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
