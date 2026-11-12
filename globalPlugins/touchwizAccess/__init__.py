# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

import os
from gettext import translation
from pathlib import Path

import addonHandler
import api
import eventHandler
import globalPluginHandler
import globalVars
import gui
import inputCore
import speech
import ui
import winUser
import wx
from browseMode import reportPassThrough
from keyboardHandler import KeyboardInputGesture
from logHandler import log
from scriptHandler import script
from speech.priorities import Spri
from utils.security import isLockScreenModeActive, isRunningOnSecureDesktop, post_sessionLockStateChanged

from .actions import runAction
from .localization import ADDON_ROOT
from .preferences import DEFAULT_KEY, GESTURES, Settings, appKey, effectiveBindings
from .rotor import Rotor, getDocument, windowModes
from .shellGestures import ShellGestureOverride
from .strokes import MATCH_LIMIT, distance
from .touchpad import TouchpadInput
from .windowFocus import FOCUS_ROLES, moveFocus

addonHandler.initTranslation()

_SWIPE_KEYS = {"swiperight": "downArrow", "swipeleft": "upArrow", "swipeup": "shift+tab", "swipedown": "tab"}
_APP_SWITCH_KEYS = {"threeFingerSwipeRight": "alt+escape", "threeFingerSwipeLeft": "alt+shift+escape"}
_ROTOR_ANNOUNCEMENT_DELAY_MS = 35
_MODE_LABELS = {
	"default": "Default navigation",
	"keyboardFocus": "Keyboard focus",
	"arrowKeys": "Arrow keys",
	"explorerTabs": "File Explorer tabs",
	"button": "Buttons",
	"link": "Links",
	"formField": "Form fields",
	"landmark": "Landmarks",
	"heading": "Headings",
	"edit": "Text fields",
	"checkBox": "Checkboxes",
	"radioButton": "Radio buttons",
	"comboBox": "Combo boxes",
	"list": "Lists",
	"listItem": "List items",
	"tree": "Tree views",
	"treeItem": "Tree items",
	"table": "Tables",
	"tableRow": "Table rows",
	"tableCell": "Table cells",
	"toolBar": "Toolbars",
	"graphic": "Images",
	"visitedLink": "Visited links",
	"unvisitedLink": "Unvisited links",
	"notLinkBlock": "Text outside links",
	"blockQuote": "Block quotes",
	"separator": "Separators",
	"frame": "Frames",
	"embeddedObject": "Embedded objects",
	"annotation": "Annotations",
	"textParagraph": "Text paragraphs",
	"error": "Spelling errors",
	**{f"heading{level}": f"Level {level} headings" for level in range(1, 10)},
}


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = "TouchWiz Access"

	def __init__(self):
		super().__init__()
		self._touchpad: TouchpadInput | None = None
		self._rotor = Rotor()
		self._browseState = None
		self._announcement: wx.CallLater | None = None
		self._speakingRotor = False
		self._allowRotorActionSpeech = False
		self._recordingCallback = None
		self._recordingOnly = False
		self._testGesture = None
		self._testTimer = None
		self._testOnly = False
		self._settingsDialog = None
		self._menuItem = None
		configPath = getattr(globalVars.appArgs, "configPath", None)
		self.settings = Settings(Path(configPath) / "touchwiz-access.json" if configPath else None)
		localAppData = os.environ.get("LOCALAPPDATA")
		self._shellGestures = ShellGestureOverride(
			Path(localAppData) / "TouchWizAccess" if configPath and localAppData else None
		)
		if not isRunningOnSecureDesktop() and not isLockScreenModeActive():
			self._shellGestures.recover()
		self._settingsChanged()
		self._tray = getattr(gui.mainFrame, "sysTrayIcon", None)
		if self._tray:
			self._menuItem = self._tray.preferencesMenu.Append(wx.ID_ANY, "TouchWiz Access…")
			self._tray.Bind(wx.EVT_MENU, self._openSettings, self._menuItem)
		post_sessionLockStateChanged.register(self._onSessionLock)
		speech.filter_speechSequence.register(self._filterRotorSpeech)

	def terminate(self):
		if self._settingsDialog:
			self._settingsDialog.EndModal(wx.ID_CANCEL)
		self._stopNavigation()
		if self._menuItem:
			self._tray.Unbind(wx.EVT_MENU, source=self._menuItem)
			self._tray.preferencesMenu.Delete(self._menuItem.GetId())
		speech.filter_speechSequence.unregister(self._filterRotorSpeech)
		post_sessionLockStateChanged.unregister(self._onSessionLock)
		super().terminate()

	def translator(self, language=None):
		language = language or self.settings.data["language"]
		if language == "auto":
			return _
		return translation(
			"nvda",
			localedir=ADDON_ROOT / "locale",
			languages=[language],
			fallback=True,
		).gettext

	def _settingsChanged(self):
		self._t = self.translator()
		self.clearGestureBindings()
		key = self.settings.data["toggleKey"]
		try:
			if key != DEFAULT_KEY:
				KeyboardInputGesture.fromName(key)
		except ValueError:
			key = DEFAULT_KEY
		self.bindGesture(f"kb:{key}", "toggleTouchpadNavigation")
		self._updateStrokeHandler()

	def _openSettings(self, event):
		from .settingsDialog import TouchWizSettingsDialog

		if self._settingsDialog:
			self._settingsDialog.Raise()
			return
		gui.mainFrame.prePopup()
		test = None
		try:
			with TouchWizSettingsDialog(gui.mainFrame, self) as dialog:
				self._settingsDialog = dialog
				if dialog.ShowModal() == wx.ID_OK:
					test = dialog.testAfterSave
		finally:
			self.endRecording()
			self._settingsDialog = None
			gui.mainFrame.postPopup()
		if test:
			wx.CallAfter(self.armGestureTest, *test)

	def saveSettings(self, data):
		self.settings.save(data)
		self._settingsChanged()

	def beginRecording(self, callback):
		if self._touchpad is None:
			if not self._startNavigation(announce=False):
				return False
			self._recordingOnly = True
		self._recordingCallback = callback
		receiver = self._touchpad
		receiver.startRecording()
		if self._touchpad is not receiver:
			return False
		receiver.reset()
		self._updateStrokeHandler()
		return True

	def endRecording(self):
		self._recordingCallback = None
		if self._recordingOnly:
			self._recordingOnly = False
			self._stopNavigation()
		elif self._touchpad and self._touchpad.recording:
			self._touchpad.stopRecording()
			self._touchpad.reset()
			self._updateStrokeHandler()

	def _updateStrokeHandler(self):
		if self._touchpad is not None:
			needed = self._touchpad.recording or self.settings.data["gestures"]
			self._touchpad.strokeHandler = self._handleStroke if needed else None

	@staticmethod
	def _application():
		focus = api.getFocusObject()
		return appKey(getattr(getattr(focus, "appModule", None), "appName", ""))

	def _bindings(self):
		return effectiveBindings(self.settings.data, self._application())

	def _handleStroke(self, shape, direction=None, drawing=None):
		if self._touchpad.recording:
			if self._recordingCallback:
				callback = self._recordingCallback
				if shape is not None:
					self._recordingCallback = None
				wx.CallAfter(callback, shape, direction)
			return None
		definitions = self.settings.data["gestures"]
		matches = (
			(
				min(
					distance(sample, definitions[gesture]["shape"])
					for sample in (shape, drawing)
					if sample is not None
				),
				gesture,
			)
			for gesture in self._bindings()
			if gesture in definitions
		)
		score, gesture = min(matches, default=(float("inf"), None))
		return gesture if score <= MATCH_LIMIT else None

	def armGestureTest(self, gesture, profile):
		self._finishGestureTest()
		temporary = self._touchpad is None
		if temporary and not self._startNavigation(announce=False):
			return
		self._testOnly = temporary
		self._testGesture = (gesture, profile)
		self._testTimer = wx.CallLater(30000, self._gestureTestExpired)
		ui.message(self._t("Switch to the target application and draw the gesture within 30 seconds."))

	def _clearGestureTest(self):
		self._testGesture = None
		if self._testTimer:
			self._testTimer.Stop()
			self._testTimer = None

	def _gestureTestExpired(self):
		self._finishGestureTest()
		ui.message(self._t("The action test timed out."))

	def _finishGestureTest(self):
		if self._testOnly:
			self._stopNavigation()
		else:
			self._clearGestureTest()

	def _onSessionLock(self, isNowLocked):
		if isNowLocked:
			self.endRecording()
		self._finishGestureTest()
		self._cancelAnnouncement()
		self._restoreBrowseSettings()
		if self._touchpad is not None:
			self._touchpad.fastHorizontal = False
			self._touchpad.reset()
			if isNowLocked:
				self._touchpad.restoreNativeGestures()
				self._shellGestures.stop()
			else:
				self._touchpad.suspendNativeGestures()
				self._shellGestures.start()
				wx.CallAfter(self._syncBrowseDocument)

	def event_foreground(self, obj, nextHandler):
		if self._touchpad is not None and self._touchpad.foregroundChanged():
			self._cancelAnnouncement()
		nextHandler()

	def event_gainFocus(self, obj, nextHandler):
		nextHandler()
		if self._touchpad is not None and not self._touchpad.recording:
			wx.CallAfter(self._syncBrowseDocument)

	def _syncBrowseDocument(self):
		if (
			self._touchpad is None
			or self._touchpad.recording
			or isLockScreenModeActive()
			or isRunningOnSecureDesktop()
			or eventHandler.isPendingEvents("gainFocus")
		):
			return
		document = getDocument()
		if self._browseState is None or self._browseState[0] is not document:
			self._useBrowseMode(document)

	def _useBrowseMode(self, document):
		if self._browseState is not None and self._browseState[0] is not document:
			self._restoreBrowseSettings()
		if document is None:
			return
		if self._browseState is None:
			self._browseState = (document, document.disableAutoPassThrough, document.singleLetterNavEnabled)
		if document.passThrough:
			document.passThrough = False
			reportPassThrough(document)
		if not document.disableAutoPassThrough:
			document.disableAutoPassThrough = True
		if not document.singleLetterNavEnabled:
			document.singleLetterNavEnabled = True

	def _restoreBrowseSettings(self):
		if self._browseState is None:
			return
		document, autoPassThroughDisabled, singleLetterNav = self._browseState
		self._browseState = None
		if document.disableAutoPassThrough and not autoPassThroughDisabled:
			document.disableAutoPassThrough = False
		if document.singleLetterNavEnabled and not singleLetterNav:
			document.singleLetterNavEnabled = False

	def _isRotorSpeechBlocked(self):
		return self._touchpad is not None and self._touchpad.rotorSpeechBlocked

	def event_mouseMove(self, obj, nextHandler, x, y):
		# NVDA's mouse event can cancel speech before speaking the hovered item.
		if self._touchpad is None or not self._touchpad.mouseReportingBlocked:
			nextHandler()

	def _filterRotorSpeech(self, sequence):
		if not self._speakingRotor and not self._allowRotorActionSpeech and self._isRotorSpeechBlocked():
			return []
		return sequence

	def _stopNavigation(self):
		self._testOnly = False
		self._clearGestureTest()
		self._cancelAnnouncement()
		self._restoreBrowseSettings()
		try:
			if self._touchpad is not None:
				self._touchpad.close()
		finally:
			self._touchpad = None
			self._shellGestures.stop()
		self._rotor = Rotor()

	def _cancelAnnouncement(self):
		if self._announcement is not None:
			self._announcement.Stop()
			self._announcement = None

	def _announceRotorMode(self, mode):
		self._announcement = None
		if mode.startswith("heading") and mode != "heading":
			label = self._t("Level {level} headings").format(level=mode.removeprefix("heading"))
		else:
			label = self._t(_MODE_LABELS[mode])
		self._speakRotorMessage(label)

	def _speakRotorMessage(self, text):
		self._speakingRotor = True
		try:
			ui.message(text, speechPriority=Spri.NOW)
		finally:
			self._speakingRotor = False

	def _handleGesture(self, gesture):
		if gesture == "rotorStarted":
			self._allowRotorActionSpeech = False
			if self._isRotorSpeechBlocked():
				speech.cancelSpeech()
			return
		if eventHandler.isPendingEvents("gainFocus"):
			return
		early = gesture.endswith("Early")
		gesture = gesture.removesuffix("Early")
		if self._touchpad is not None and self._touchpad.recording:
			return
		action = self._bindings().get(gesture)
		if action:
			if gesture in ("clockwise", "counterclockwise"):
				self._allowRotorActionSpeech = True
			definition = self.settings.data["gestures"].get(gesture)
			name = definition["name"] if definition else self._t(GESTURES[gesture])
			test = self._testGesture in ((gesture, ""), (gesture, self._application()))
			if test:
				self._clearGestureTest()
			try:
				if not runAction(action, name):
					ui.message(self._t("This NVDA action is unavailable in the current application."))
				elif test:
					ui.message(self._t("Test gesture recognized. The assigned action was sent."))
			except (OSError, ValueError, inputCore.NoInputGestureAction):
				ui.message(self._t("The assigned action could not be run."))
			finally:
				if test:
					self._finishGestureTest()
			return
		if gesture in _APP_SWITCH_KEYS:
			self._cancelAnnouncement()
			KeyboardInputGesture.fromName(_APP_SWITCH_KEYS[gesture]).send()
			return
		if gesture == "doubletap":
			inputCore.manager.emulateGesture(KeyboardInputGesture.fromName("enter"))
			return
		document = getDocument()
		self._useBrowseMode(document)
		hwnd = winUser.getForegroundWindow()
		nativeModes = None if document else windowModes(hwnd)
		mode = self._rotor.mode(document, nativeModes)
		if gesture in ("clockwise", "counterclockwise"):
			self._cancelAnnouncement()
			mode = self._rotor.advance(document, forward=gesture == "clockwise", nativeModes=nativeModes)
			# Coalesce closely spaced steps so speech follows the latest selection.
			self._announcement = wx.CallLater(_ROTOR_ANNOUNCEMENT_DELAY_MS, self._announceRotorMode, mode)
		elif mode == "default":
			if not early:
				inputCore.manager.emulateGesture(KeyboardInputGesture.fromName(_SWIPE_KEYS[gesture]))
		elif document is None:
			if mode in FOCUS_ROLES:
				if gesture in ("swiperight", "swipeleft"):
					forward = gesture == "swiperight"
					if not moveFocus(hwnd, mode, forward):
						ui.message(
							self._t(
								"No next control of this type in the active window."
								if forward
								else "No previous control of this type in the active window."
							)
						)
			else:
				self._rotor.moveInWindow(hwnd, mode, gesture)
		elif gesture in ("swiperight", "swipeleft"):
			self._rotor.move(document, forward=gesture == "swiperight")
		if self._touchpad is not None:
			self._touchpad.fastHorizontal = mode != "default"

	@script(
		description=_("Toggles touchpad navigation"),
		gesture="kb:NVDA+alt+shift+t",
	)
	def script_toggleTouchpadNavigation(self, gesture):
		if isRunningOnSecureDesktop() or isLockScreenModeActive():
			return
		if self._touchpad is not None:
			self._stopNavigation()
			ui.message(self._t("Touchpad navigation off."))
			return
		self._startNavigation()

	def _startNavigation(self, announce=True):
		if isRunningOnSecureDesktop() or isLockScreenModeActive():
			return False
		try:
			touchpad = TouchpadInput(self._handleGesture)
		except (OSError, RuntimeError):
			log.debugWarning("Could not start TouchWiz Access raw input", exc_info=True)
			ui.message(self._t("Could not start touchpad navigation."))
			return False
		if not touchpad.hasCompatibleDevice():
			touchpad.close()
			ui.message(self._t("No compatible Windows Precision Touchpad found."))
			return False
		self._touchpad = touchpad
		self._updateStrokeHandler()
		if announce:
			self._syncBrowseDocument()
		message = self._t("Touchpad navigation on. Swipe with one finger to use the arrow and tab keys.")
		if not touchpad.suspendNativeGestures():
			message += " " + self._t("Windows tap and scroll gestures could not be suspended.")
		if not self._shellGestures.start():
			message += " " + self._t("Windows three- and four-finger gestures could not be suspended.")
		if announce:
			ui.message(message)
		return True
