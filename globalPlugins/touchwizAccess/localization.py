# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Shared locale choices for TouchWiz Access and its offline guide."""

import builtins
from pathlib import Path

import languageHandler

ADDON_ROOT = Path(__file__).resolve().parents[2]
LANGUAGES = ("auto", "tr", "en")

# A few NVDA catalogs omit the standard spoken Top or Bottom message.
_DOCUMENT_EDGE_FALLBACKS = {
	"af": ("Begin", "Einde"),
	"am": ("መጀመሪያ", "መጨረሻ"),
	"da": ("Øverst", "Nederst"),
	"id": ("Awal", "Akhir"),
	"is": ("Upphaf", "Endir"),
	"kn": ("ಆರಂಭ", "ಅಂತ್ಯ"),
	"ne": ("सुरु", "अन्त्य"),
	"pa": ("ਸ਼ੁਰੂ", "ਅੰਤ"),
	"sq": ("Fillimi", "Fundi"),
	"th": ("จุดเริ่มต้น", "จุดสิ้นสุด"),
	"ur": ("آغاز", "اختتام"),
}


def localeCodes(language):
	"""Return exact and base locale codes for gettext and guide lookup."""
	if language == "auto":
		language = languageHandler.getLanguage()
	language = language.replace("-", "_")
	return tuple(dict.fromkeys((language, language.split("_", 1)[0])))


def documentEdgeAnnouncement(language, atEnd, translate):
	"""Return the announcement and the language to pass to the synthesizer."""
	codes = localeCodes(language)
	base = codes[-1]
	if base in ("tr", "en"):
		message = "Focused at the end" if atEnd else "Focused at the beginning"
		return translate(message), codes[0]
	message = "Bottom" if atEnd else "Top"
	localized = builtins._(message)
	if localized == message and base in _DOCUMENT_EDGE_FALLBACKS:
		localized = _DOCUMENT_EDGE_FALLBACKS[base][atEnd]
	return localized, codes[0]


def guidePath(language):
	"""Use NVDA's exact-locale, base-language and English fallback order."""
	for candidate in dict.fromkeys((*localeCodes(language), "en")):
		path = ADDON_ROOT / "doc" / candidate / "readme.html"
		if path.is_file():
			return path
	return None
