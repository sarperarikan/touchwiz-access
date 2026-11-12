# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Resolve configured actions against the current NVDA input context."""

import os

import api
import inputCore
import scriptHandler
from keyboardHandler import KeyboardInputGesture


class TouchWizGesture(inputCore.InputGesture):
	def __init__(self, name):
		self.name = name

	def _get_identifiers(self):
		return ("touchwiz:custom",)

	def _get_displayName(self):
		return self.name


def commandChoices():
	commands = {}
	for category, entries in inputCore.manager.getAllGestureMappings().items():
		for label, item in entries.items():
			if item.scriptName.startswith("kb:"):
				continue
			identifier = f"{item.moduleName}:{item.className}:{item.scriptName}"
			commands[identifier] = f"{category} — {label}"
	return commands


def _resolveCommand(gesture, identifier):
	if not api.getFocusObject():
		return None
	module, className, name = identifier.split(":", 2)
	for owner, filterCommand in scriptHandler._yieldObjectsForFindScript(gesture):
		if owner is None or not any(
			cls.__module__ == module and cls.__name__ == className for cls in type(owner).__mro__
		):
			continue
		command = getattr(owner, f"script_{name}", None)
		if command:
			return filterCommand(command, owner, gesture) if filterCommand else command
	return None


def runAction(action, name):
	kind, value = action["kind"], action["value"]
	if kind == "disabled":
		return True
	if kind == "app":
		os.startfile(value)
	elif kind in ("shortcut", "windows"):
		key = KeyboardInputGesture.fromName(value)
		if kind == "windows":
			key.send()
		else:
			inputCore.manager.emulateGesture(key)
	else:
		gesture = TouchWizGesture(name)
		gesture.script = _resolveCommand(gesture, value)
		if gesture.script is None:
			return False
		inputCore.manager.executeGesture(gesture)
	return True
