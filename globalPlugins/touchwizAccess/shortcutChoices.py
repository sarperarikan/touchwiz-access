# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Named Windows shortcuts and a keyboard shortcut editor."""

import ui
import wx
from gui.guiHelper import BoxSizerHelper
from keyboardHandler import KeyboardInputGesture

# Standard shortcuts documented in Microsoft's Windows keyboard shortcut guide.
WINDOWS_SHORTCUTS = {
	"windows+e": "Open File Explorer",
	"windows+i": "Open Windows Settings",
	"windows+u": "Open Accessibility settings",
	"windows+a": "Open Quick Settings",
	"windows+s": "Open Windows Search",
	"windows+r": "Open Run",
	"windows+d": "Show or hide the desktop",
	"windows+tab": "Open Task View",
	"alt+tab": "Switch to the next open application",
	"alt+shift+tab": "Switch to the previous open application",
	"control+shift+escape": "Open Task Manager",
	"windows+v": "Open clipboard history",
	"windows+.": "Open the emoji panel",
	"windows+control+d": "Create a virtual desktop",
	"windows+control+rightArrow": "Switch to the next virtual desktop",
	"windows+control+leftArrow": "Switch to the previous virtual desktop",
	"windows+leftArrow": "Snap the window to the left",
	"windows+rightArrow": "Snap the window to the right",
	"windows+upArrow": "Maximize the active window",
	"windows+downArrow": "Restore or minimize the active window",
	"alt+f4": "Close the active window",
	"control+tab": "Move to the next tab",
	"control+shift+tab": "Move to the previous tab",
	"alt+d": "Focus the address bar in File Explorer",
	"alt+upArrow": "Open the parent folder in File Explorer",
}
MODIFIERS = {
	"control": "Ctrl",
	"shift": "Shift",
	"alt": "Alt",
	"windows": "Windows",
	"nvda": "NVDA",
}
MAIN_KEYS = {
	"tab": "Tab",
	"enter": "Enter",
	"space": "Space",
	"escape": "Escape",
	"backspace": "Backspace",
	"delete": "Delete",
	"insert": "Insert",
	"home": "Home",
	"end": "End",
	"pageup": "Page Up",
	"pagedown": "Page Down",
	"leftarrow": "Left Arrow",
	"rightarrow": "Right Arrow",
	"uparrow": "Up Arrow",
	"downarrow": "Down Arrow",
	**{f"f{number}": f"F{number}" for number in range(1, 13)},
}


def shortcutLabel(value, translate):
	return "+".join(
		translate(MODIFIERS.get(key.lower()) or MAIN_KEYS.get(key.lower()) or key.upper())
		for key in value.split("+")
	)


def composeShortcut(modifiers, mainKey):
	mainKey = mainKey.strip().casefold()
	if mainKey == "+":
		mainKey = "plus"
	if mainKey not in MAIN_KEYS and mainKey != "plus" and len(mainKey) != 1:
		raise ValueError("A single main key is required")
	return "+".join((*modifiers, mainKey))


class KeyboardShortcutDialog(wx.Dialog):
	def __init__(self, parent, translate):
		super().__init__(parent, title=translate("Keyboard shortcut"))
		self.t = translate
		self.value = None
		helper = BoxSizerHelper(self, orientation=wx.VERTICAL)
		helper.addItem(wx.StaticText(self, label=self.t("Select modifiers if needed.")))
		self.modifiers = {}
		for key, name in MODIFIERS.items():
			control = wx.CheckBox(self, label=self.t(name))
			self.modifiers[key] = control
			helper.addItem(control)
		self._keyNames = {self.t(label).casefold(): key for key, label in MAIN_KEYS.items()}
		self.mainKey = helper.addLabeledControl(
			self.t("Main key. Choose a key or type one letter or digit."),
			wx.ComboBox,
			choices=[self.t(label) for label in MAIN_KEYS.values()],
			style=wx.CB_DROPDOWN,
		)
		helper.addDialogDismissButtons(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL))
		self.SetSizerAndFit(helper.sizer)
		self.Bind(wx.EVT_BUTTON, self._save, id=wx.ID_OK)
		next(iter(self.modifiers.values())).SetFocus()

	def _save(self, event):
		modifiers = tuple(key for key, control in self.modifiers.items() if control.GetValue())
		text = self.mainKey.GetValue().strip()
		try:
			value = composeShortcut(modifiers, self._keyNames.get(text.casefold(), text))
			KeyboardInputGesture.fromName(value)
		except ValueError:
			ui.message(self.t("Choose one main key or type one letter or digit."))
			self.mainKey.SetFocus()
			return
		self.value = value
		self.EndModal(wx.ID_OK)
