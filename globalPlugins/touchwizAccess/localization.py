# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Shared locale choices for TouchWiz Access and its offline guide."""

from pathlib import Path

import languageHandler

ADDON_ROOT = Path(__file__).resolve().parents[2]
LANGUAGES = ("auto", "tr", "en")


def localeCodes(language):
	"""Return exact and base locale codes for gettext and guide lookup."""
	if language == "auto":
		language = languageHandler.getLanguage()
	language = language.replace("-", "_")
	return tuple(dict.fromkeys((language, language.split("_", 1)[0])))


def guidePath(language):
	"""Use NVDA's exact-locale, base-language and English fallback order."""
	for candidate in dict.fromkeys((*localeCodes(language), "en")):
		path = ADDON_ROOT / "doc" / candidate / "readme.html"
		if path.is_file():
			return path
	return None
