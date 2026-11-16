# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""TouchWiz settings and portable gesture layouts."""

import json
import os
from copy import deepcopy
from math import isfinite
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile

from .localization import LANGUAGES

DEFAULT_KEY = "nvda+alt+shift+t"
APP_SWITCH_GESTURES = {"swiperight": "threeFingerSwipeRight", "swipeleft": "threeFingerSwipeLeft"}
GESTURES = {
	"swiperight": "Swipe right",
	"swipeleft": "Swipe left",
	"swipeup": "Swipe up",
	"swipedown": "Swipe down",
	"clockwise": "Turn rotor clockwise",
	"counterclockwise": "Turn rotor counterclockwise",
	"doubletap": "Single-finger double tap",
	"threeFingerSwipeRight": "Three-finger swipe right",
	"threeFingerSwipeLeft": "Three-finger swipe left",
	"threeFingerDoubleTap": "Three-finger double tap",
	"threeFingerTripleTap": "Three-finger triple tap",
}
ACTION_KINDS = ("nvda", "shortcut", "windows", "app", "disabled")
SAMPLE_COUNT = 16
PATTERN_GESTURES = {"tap", "doubletap", "tripletap", "swipeleft", "swiperight", "swipeup", "swipedown"}


def defaultSettings():
	return {"version": 1, "language": "auto", "toggleKey": DEFAULT_KEY, "profiles": {"": {}}, "gestures": {}}


def appKey(name):
	return name.strip().casefold().removesuffix(".exe")


def effectiveBindings(settings, application):
	profiles = settings["profiles"]
	return profiles[""] | profiles.get(appKey(application), {})


def validateSettings(settings):
	if not isinstance(settings, dict) or settings.get("version") != 1:
		raise ValueError("Unsupported settings format")
	if settings.get("language") not in LANGUAGES:
		raise ValueError("Invalid language")
	if not isinstance(settings.get("toggleKey"), str) or not settings["toggleKey"].strip():
		raise ValueError("Invalid startup shortcut")
	profiles, gestures = settings.get("profiles"), settings.get("gestures")
	if not isinstance(gestures, dict) or not isinstance(profiles, dict) or "" not in profiles:
		raise ValueError("Invalid profiles or gestures")
	for gesture, definition in gestures.items():
		if (
			not isinstance(definition, dict)
			or not gesture.startswith("custom-")
			or not isinstance(definition.get("name"), str)
			or not definition["name"].strip()
		):
			raise ValueError("Invalid gesture")
		shape = definition.get("shape")
		if isinstance(shape, dict):
			if (
				type(shape.get("fingers")) is not int
				or not 1 <= shape["fingers"] <= 5
				or not isinstance(shape.get("gesture"), str)
				or shape["gesture"] not in PATTERN_GESTURES
				or set(shape) != {"fingers", "gesture"}
			):
				raise ValueError("Invalid gesture pattern")
			continue
		if (
			not isinstance(shape, (tuple, list))
			or len(shape) != SAMPLE_COUNT
			or any(
				not isinstance(point, (tuple, list))
				or len(point) != 2
				or any(
					type(value) not in (int, float) or abs(value) > 1 or not isfinite(value)
					for value in point
				)
				for point in shape
			)
		):
			raise ValueError("Invalid gesture shape")
	for app, bindings in profiles.items():
		if appKey(app) != app or not isinstance(bindings, dict):
			raise ValueError("Invalid profile")
		for gesture, action in bindings.items():
			if (gesture not in GESTURES and gesture not in gestures) or not isinstance(action, dict):
				raise ValueError("Invalid gesture assignment")
			kind, value = action.get("kind"), action.get("value")
			if (
				kind not in ACTION_KINDS
				or not isinstance(value, str)
				or (kind != "disabled" and not value.strip())
			):
				raise ValueError("Invalid gesture action")
			if kind == "app":
				path = PureWindowsPath(value)
				if not path.is_absolute() or path.suffix.lower() != ".exe":
					raise ValueError("Invalid application path")
			elif kind == "nvda":
				parts = value.split(":")
				if len(parts) != 3 or not all(parts) or not parts[2].isidentifier():
					raise ValueError("Invalid NVDA action")
	return settings


def readSettings(path):
	with Path(path).open(encoding="utf-8-sig") as source:
		return validateSettings(json.load(source))


def writeSettings(path, settings):
	validateSettings(settings)
	writeJson(path, settings)


def writeJson(path, data):
	path = Path(path)
	with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
		temporary = Path(output.name)
		try:
			json.dump(data, output, ensure_ascii=False, indent=2)
			output.flush()
			os.fsync(output.fileno())
		except (OSError, ValueError, TypeError):
			output.close()
			temporary.unlink(missing_ok=True)
			raise
	try:
		temporary.replace(path)
	finally:
		temporary.unlink(missing_ok=True)


class Settings:
	def __init__(self, path):
		self.path = Path(path) if path else None
		try:
			self.data = readSettings(self.path) if self.path else defaultSettings()
		except (OSError, ValueError, TypeError):
			self.data = defaultSettings()

	def save(self, data):
		if self.path:
			writeSettings(self.path, data)
		self.data = deepcopy(data)
