# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Run NVDA's browse-mode quick navigation commands from the touchpad."""

from weakref import WeakKeyDictionary

import api
import eventHandler
import inputCore
import winUser
from browseMode import BrowseModeTreeInterceptor
from keyboardHandler import KeyboardInputGesture

from .windowFocus import FOCUS_ROLES

# Default single-character shortcuts in the NVDA 2026.1 user guide.
QUICK_NAV_KEYS = {
	"edit": "e",
	"button": "b",
	"link": "k",
	"formField": "f",
	"landmark": "d",
	"heading": "h",
	**{f"heading{level}": str(level) for level in range(1, 10)},
	"list": "l",
	"listItem": "i",
	"table": "t",
	"checkBox": "x",
	"radioButton": "r",
	"comboBox": "c",
	"visitedLink": "v",
	"unvisitedLink": "u",
	"notLinkBlock": "n",
	"blockQuote": "q",
	"separator": "s",
	"frame": "m",
	"graphic": "g",
	"embeddedObject": "o",
	"annotation": "a",
	"textParagraph": "p",
	"error": "w",
}
MODES = ("default", *QUICK_NAV_KEYS)
WINDOW_KEYS = {
	"keyboardFocus": {"swiperight": "tab", "swipeleft": "shift+tab"},
	"arrowKeys": {"swiperight": "rightArrow", "swipeleft": "leftArrow"},
	"explorerTabs": {"swiperight": "control+tab", "swipeleft": "control+shift+tab"},
}
WINDOW_VERTICAL_KEYS = {"swipeup": "upArrow", "swipedown": "downArrow"}


def windowModes(hwnd):
	if hwnd and winUser.getClassName(hwnd) in ("CabinetWClass", "ExploreWClass"):
		return ("default", *FOCUS_ROLES, *WINDOW_KEYS)
	return ("default", *FOCUS_ROLES, "keyboardFocus", "arrowKeys")


def getDocument():
	if eventHandler.isPendingEvents("gainFocus"):
		return None
	hwnd = winUser.getForegroundWindow()
	focus = api.getFocusObject()
	document = focus.treeInterceptor if focus else None
	if (
		hwnd
		and isinstance(document, BrowseModeTreeInterceptor)
		and document.isReady
		and winUser.isDescendantWindow(hwnd, focus.windowHandle)
		and winUser.isDescendantWindow(hwnd, document.rootNVDAObject.windowHandle)
	):
		return document
	return None


class _QuickNavGesture(KeyboardInputGesture):
	def executeScript(self, script):
		# NVDA queues scripts. The document or input mode may have changed in the meantime.
		document = getDocument()
		if (
			document is script.__self__
			and not document.passThrough
			and document.singleLetterNavEnabled
			and winUser.getForegroundWindow() == self.foreground
		):
			inputCore.InputGesture.executeScript(self, script)

	def send(self):
		# The synthesized shortcut must never type into the focused app.
		pass


class _WindowKeyGesture(KeyboardInputGesture):
	def _inWindow(self):
		return self.foreground == winUser.getForegroundWindow() and not eventHandler.isPendingEvents(
			"gainFocus"
		)

	def executeScript(self, script):
		if self._inWindow():
			super().executeScript(script)

	def send(self):
		if self._inWindow():
			super().send()


class Rotor:
	def __init__(self):
		self._documentModes = WeakKeyDictionary()
		self._windowMode = "default"

	def mode(self, document, nativeModes=None):
		if document:
			return self._documentModes.get(document, "default")
		return self._windowMode if nativeModes and self._windowMode in nativeModes else "default"

	def advance(self, document, forward, nativeModes=None):
		modes = MODES if document else nativeModes or MODES[:1]
		index = modes.index(self.mode(document, nativeModes))
		mode = modes[(index + (1 if forward else -1)) % len(modes)]
		if document:
			self._documentModes[document] = mode
		else:
			self._windowMode = mode
		return mode

	@staticmethod
	def moveInWindow(hwnd, mode, swipe):
		key = WINDOW_VERTICAL_KEYS.get(swipe) or WINDOW_KEYS[mode][swipe]
		if winUser.getForegroundWindow() != hwnd:
			return
		gesture = _WindowKeyGesture.fromName(key)
		gesture.foreground = hwnd
		inputCore.manager.emulateGesture(gesture)

	def move(self, document, forward):
		mode = self.mode(document)
		key = QUICK_NAV_KEYS.get(mode)
		if key is None or getDocument() is not document:
			return
		direction = "next" if forward else "previous"
		suffix = mode[0].upper() + mode[1:]
		command = getattr(document, f"script_{direction}{suffix}")
		try:
			gesture = _QuickNavGesture.fromName(key if forward else f"shift+{key}")
		except ValueError:
			return
		gesture.foreground = winUser.getForegroundWindow()
		# Use the same script resolution as a physical key. A gesture assigned
		# to a different command must not move outside the selected category.
		if gesture.script != command:
			return
		try:
			inputCore.manager.executeGesture(gesture)
		except inputCore.NoInputGestureAction:
			pass
