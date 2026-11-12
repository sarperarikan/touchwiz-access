# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""The native TouchWiz Access preferences dialog."""

import os
import re
from copy import deepcopy
from uuid import uuid4

import ui
import wx
import wx.adv
from gui.guiHelper import BoxSizerHelper
from keyboardHandler import KeyboardInputGesture

from .actions import commandChoices
from .gestureWizard import GestureWizard
from .localization import LANGUAGES, guidePath
from .preferences import ACTION_KINDS, DEFAULT_KEY, GESTURES, appKey, readSettings, writeSettings
from .shortcutChoices import WINDOWS_SHORTCUTS, KeyboardShortcutDialog, shortcutLabel

_APP_SWITCH_LABELS = {
	"threeFingerSwipeRight": "Switch to the next open window",
	"threeFingerSwipeLeft": "Switch to the previous open window",
}


class TouchWizSettingsDialog(wx.Dialog):
	def __init__(self, parent, plugin):
		super().__init__(parent, title="TouchWiz Access", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.plugin = plugin
		self.data = deepcopy(plugin.settings.data)
		self.t = plugin.translator()
		self._commands = commandChoices()
		self.testAfterSave = None
		self.tabs = wx.Notebook(self)
		for title, build in (
			("General", self._general),
			("Gesture customization", self._customization),
			("Create a gesture", self._create),
			("About", self._about),
		):
			panel = wx.Panel(self.tabs)
			helper = BoxSizerHelper(panel, orientation=wx.VERTICAL)
			build(panel, helper)
			panel.SetSizer(helper.sizer)
			self.tabs.AddPage(panel, self.t(title))
		layout = wx.BoxSizer(wx.VERTICAL)
		layout.Add(self.tabs, 1, wx.EXPAND | wx.ALL, 12)
		buttons = wx.StdDialogButtonSizer()
		ok = wx.Button(self, id=wx.ID_OK, label=self.t("OK"))
		cancel = wx.Button(self, id=wx.ID_CANCEL, label=self.t("Cancel"))
		buttons.AddButton(ok)
		buttons.AddButton(cancel)
		buttons.Realize()
		ok.SetDefault()
		layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
		self.SetSizerAndFit(layout)
		self.SetMinSize(self.GetSize())
		self.Bind(wx.EVT_BUTTON, self._save, id=wx.ID_OK)
		self._loadControls()
		self.language.SetFocus()

	def _button(self, panel, helper, label, handler):
		button = wx.Button(panel, label=self.t(label))
		button.Bind(wx.EVT_BUTTON, handler)
		helper.addItem(button)
		return button

	def _general(self, panel, helper):
		self.language = helper.addLabeledControl(
			self.t("Interface and guide language"),
			wx.Choice,
			choices=[self.t("Follow NVDA"), "Türkçe", "English"],
		)
		self.shortcut = helper.addLabeledControl(self.t("Touchpad navigation shortcut"), wx.TextCtrl)
		self._button(
			panel, helper, "Restore default shortcut", lambda event: self.shortcut.SetValue(DEFAULT_KEY)
		)
		self._button(panel, helper, "Import settings and layouts…", self._import)
		self._button(panel, helper, "Export settings and layouts…", self._export)
		self._button(panel, helper, "Open user guide…", self._openGuide)

	def _openGuide(self, event):
		path = guidePath(LANGUAGES[self.language.GetSelection()])
		try:
			if path is None:
				raise FileNotFoundError
			os.startfile(str(path))
		except OSError:
			self._notice("The user guide could not be opened.")

	def _customization(self, panel, helper):
		self.profile = helper.addLabeledControl(self.t("Application profile"), wx.Choice)
		self.profile.Bind(wx.EVT_CHOICE, lambda event: self._refreshGestures())
		self._button(panel, helper, "Add application profile…", self._addProfile)
		self._button(panel, helper, "Remove application profile…", self._removeProfile)
		self.gestures = helper.addLabeledControl(
			self.t("Gestures and assigned actions"),
			wx.ListBox,
			size=(590, 225),
		)
		self._button(panel, helper, "Assign action…", self._assign)
		self._button(panel, helper, "Restore inherited assignment", self._clearAssignment)
		self._button(panel, helper, "Delete custom gesture…", self._deleteGesture)

	def _create(self, panel, helper):
		self._button(panel, helper, "Start gesture wizard…", self._startWizard)

	def _startWizard(self, event):
		with GestureWizard(self) as wizard:
			if not wizard.run():
				return
			key = "custom-" + uuid4().hex
			self.data["gestures"][key] = {"name": wizard.gestureName, "shape": wizard.shape}
			self.data["profiles"][wizard.profile][key] = wizard.action
			if wizard.testInApplication:
				self.testAfterSave = (key, wizard.profile)
			profile = wizard.profile
		self._refreshProfiles(profile)
		self._refreshGestures(key)
		self.tabs.SetSelection(1)
		self.gestures.SetFocus()
		self._notice("Gesture added. Press OK to save changes.")

	def _about(self, panel, helper):
		helper.addItem(
			wx.StaticText(
				panel,
				label=self.t("TouchWiz Access was developed by Sarper Arıkan using agentic engineering."),
			)
		)
		for label, url in (
			("Email Sarper Arıkan (sarperarikan@gmail.com)", "mailto:sarperarikan@gmail.com"),
			("Personal website (sarperarikan.net)", "https://sarperarikan.net"),
			("Erişilebilir AI (erisilebilirai.com)", "https://erisilebilirai.com"),
			("Uygulama Akademisi (uygulamaakademisi.com)", "https://uygulamaakademisi.com"),
			("Sarper Arıkan on GitHub (sarperarikan)", "https://github.com/sarperarikan"),
		):
			helper.addItem(wx.adv.HyperlinkCtrl(panel, label=self.t(label), url=url))

	def _loadControls(self):
		self.language.SetSelection(LANGUAGES.index(self.data["language"]))
		self.shortcut.SetValue(self.data["toggleKey"])
		self._refreshProfiles()

	def _refreshProfiles(self, selected=""):
		self._profiles = sorted(self.data["profiles"])
		names = [name + ".exe" if name else self.t("Global defaults") for name in self._profiles]
		self.profile.SetItems(names)
		self.profile.SetSelection(self._profiles.index(selected))
		self._refreshGestures()

	def _selectedProfile(self, control=None):
		control = self.profile if control is None else control
		return self._profiles[max(0, control.GetSelection())]

	def _selectedGesture(self):
		index = self.gestures.GetSelection()
		return self._gestureIds[index] if index != wx.NOT_FOUND else None

	def _refreshGestures(self, select=None):
		selected = self._selectedProfile()
		own = self.data["profiles"][selected]
		inherited = self.data["profiles"][""] if selected else {}
		custom = self.data["gestures"]
		self._gestureIds = [*GESTURES, *custom]
		rows = []
		for gesture in self._gestureIds:
			name = custom[gesture]["name"] if gesture in custom else self.t(GESTURES[gesture])
			action = own.get(gesture) or inherited.get(gesture)
			if action:
				description = self.actionLabel(action)
				if gesture not in own:
					description += " " + self.t("(inherited)")
			else:
				description = self.t(
					_APP_SWITCH_LABELS.get(
						gesture, "Unassigned" if gesture in custom else "Built-in behavior"
					)
				)
			rows.append(f"{name} — {description}")
		self.gestures.SetItems(rows)
		if rows:
			self.gestures.SetSelection(self._gestureIds.index(select) if select in self._gestureIds else 0)

	def _text(self, title, prompt, initial="", parent=None):
		with wx.TextEntryDialog(parent or self, self.t(prompt), self.t(title), value=initial) as dialog:
			if dialog.ShowModal() == wx.ID_OK:
				return dialog.GetValue().strip()
		return None

	def _confirm(self, prompt):
		return wx.MessageBox(self.t(prompt), "TouchWiz Access", wx.YES_NO | wx.NO_DEFAULT, self) == wx.YES

	def _notice(self, message):
		ui.message(self.t(message))

	def _addProfile(self, event):
		value = self._text("Application profile", "Executable name, for example notepad.exe")
		if value is None:
			return
		if not re.fullmatch(r"[^\\/:*?\"<>|]+\.exe", value, re.IGNORECASE):
			self._notice("Enter an executable name ending in .exe.")
			return
		key = appKey(value)
		self.data["profiles"].setdefault(key, {})
		self._refreshProfiles(key)
		self.profile.SetFocus()

	def _removeProfile(self, event):
		key = self._selectedProfile()
		if key and self._confirm("Delete this application profile and its assignments?"):
			del self.data["profiles"][key]
			self._refreshProfiles()
			self.profile.SetFocus()

	def actionChoices(self, kind):
		if kind == "nvda":
			return dict(sorted(self._commands.items(), key=lambda item: item[1]))
		if kind == "windows":
			return {
				key: f"{self.t(label)} ({shortcutLabel(key, self.t)})"
				for key, label in WINDOWS_SHORTCUTS.items()
			}
		return {}

	def actionLabel(self, action):
		kind, value = action["kind"], action["value"]
		if kind == "disabled":
			return self.t("Disabled")
		if kind == "nvda":
			return self._commands.get(value, value)
		if kind == "windows" and value in WINDOWS_SHORTCUTS:
			return f"{self.t(WINDOWS_SHORTCUTS[value])} ({shortcutLabel(value, self.t)})"
		if kind == "shortcut":
			return shortcutLabel(value, self.t)
		return value

	def _chooseAction(self, parent=None, kind=None):
		parent = parent or self
		labels = ("NVDA action", "Keyboard shortcut", "Windows shortcut", "Launch an application", "Disabled")
		if kind is None:
			with wx.SingleChoiceDialog(
				parent, self.t("Choose an action type"), "TouchWiz Access", [self.t(s) for s in labels]
			) as choice:
				if choice.ShowModal() != wx.ID_OK:
					return None
				kind = ACTION_KINDS[choice.GetSelection()]
		if kind in ("nvda", "windows"):
			choices = self.actionChoices(kind)
			items = list(choices)
			title = "Choose an NVDA action" if kind == "nvda" else "Choose a Windows shortcut"
			with wx.SingleChoiceDialog(
				parent,
				self.t(title),
				"TouchWiz Access",
				list(choices.values()),
			) as choice:
				value = items[choice.GetSelection()] if items and choice.ShowModal() == wx.ID_OK else None
		elif kind == "app":
			with wx.FileDialog(
				parent,
				self.t("Choose an application"),
				wildcard=self.t("Windows applications") + " (*.exe)|*.exe",
				style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
			) as choice:
				value = choice.GetPath() if choice.ShowModal() == wx.ID_OK else None
		elif kind == "disabled":
			value = ""
		else:
			with KeyboardShortcutDialog(parent, self.t) as dialog:
				value = dialog.value if dialog.ShowModal() == wx.ID_OK else None
		return {"kind": kind, "value": value} if value is not None else None

	def _assign(self, event):
		gesture = self._selectedGesture()
		if gesture and (action := self._chooseAction()) is not None:
			self.data["profiles"][self._selectedProfile()][gesture] = action
			self._refreshGestures(gesture)

	def _clearAssignment(self, event):
		gesture = self._selectedGesture()
		if gesture:
			self.data["profiles"][self._selectedProfile()].pop(gesture, None)
			self._refreshGestures(gesture)

	def _deleteGesture(self, event):
		gesture = self._selectedGesture()
		if gesture in self.data["gestures"] and self._confirm(
			"Delete this custom gesture from all profiles?"
		):
			del self.data["gestures"][gesture]
			for bindings in self.data["profiles"].values():
				bindings.pop(gesture, None)
			self._refreshGestures()

	@staticmethod
	def _validKey(value):
		try:
			KeyboardInputGesture.fromName(value)
			return bool(value)
		except ValueError:
			return False

	def _updateGeneral(self):
		key = self.shortcut.GetValue().strip()
		if not self._validKey(key):
			self._notice("Invalid shortcut.")
			self.tabs.SetSelection(0)
			self.shortcut.SetFocus()
			return False
		self.data["language"] = LANGUAGES[self.language.GetSelection()]
		self.data["toggleKey"] = key
		return True

	def _save(self, event):
		if not self._updateGeneral():
			return
		try:
			self.plugin.saveSettings(self.data)
		except (OSError, ValueError):
			self._notice("Settings could not be saved.")
			return
		if self.testAfterSave:
			gesture, profile = self.testAfterSave
			if gesture not in self.data["profiles"].get(profile, {}):
				self.testAfterSave = None
		self.EndModal(wx.ID_OK)

	def _layoutPath(self, exporting):
		style = wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT if exporting else wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
		title = "Export settings and layouts…" if exporting else "Import settings and layouts…"
		with wx.FileDialog(
			self,
			self.t(title),
			defaultFile="touchwiz-access.json",
			wildcard="JSON (*.json)|*.json",
			style=style,
		) as dialog:
			return dialog.GetPath() if dialog.ShowModal() == wx.ID_OK else None

	def _import(self, event):
		if (path := self._layoutPath(False)) is None:
			return
		try:
			data = readSettings(path)
			if not self._validKey(data["toggleKey"]):
				raise ValueError
			for bindings in data["profiles"].values():
				if any(
					action["kind"] in ("shortcut", "windows") and not self._validKey(action["value"])
					for action in bindings.values()
				):
					raise ValueError
		except (OSError, ValueError, TypeError):
			self._notice("The settings and layouts file could not be imported.")
			return
		self.testAfterSave = None
		self.data = data
		self._loadControls()
		self._notice("Settings and layouts imported. Press OK to save changes.")

	def _export(self, event):
		if not self._updateGeneral() or (path := self._layoutPath(True)) is None:
			return
		try:
			writeSettings(path, self.data)
		except (OSError, ValueError):
			self._notice("The settings and layouts file could not be exported.")
			return
		self._notice("Settings and layouts exported.")
