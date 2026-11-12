# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Capture, assign and test a custom gesture one step at a time."""

from itertools import pairwise

import ui
import wx
import wx.adv
from gui.guiHelper import BoxSizerHelper

from .preferences import GESTURES
from .strokes import MATCH_LIMIT, distance

_ACTIONS = (
	("nvda", "NVDA action"),
	("app", "Launch an application"),
	("shortcut", "Keyboard shortcut"),
	("windows", "Windows shortcut"),
)


class GestureWizard(wx.adv.Wizard):
	def __init__(self, settings):
		super().__init__(settings, title=settings.t("Create a gesture"))
		self.settings = settings
		self.plugin = settings.plugin
		self.t = settings.t
		self.shape = None
		self.action = None
		self.gestureName = ""
		self.profile = ""
		self.testInApplication = False
		self._first = None
		self._firstDirection = None
		self._conflicts = ()
		self._acceptedConflicts = ()
		self._generation = 0
		self._entered = None
		self.firstPage, self.firstStatus = self._capturePage("Record the first sample")
		self.confirmPage, self.confirmStatus = self._capturePage("Confirm the gesture")
		self.actionPage = self._actionPage()
		self.testPage, self.testStatus = self._capturePage("Test and save the gesture", testing=True)
		pages = (self.firstPage, self.confirmPage, self.actionPage, self.testPage)
		for first, second in pairwise(pages):
			wx.adv.WizardPageSimple.Chain(first, second)
		self.GetPageAreaSizer().Add(self.firstPage)
		self.Bind(wx.adv.EVT_WIZARD_PAGE_SHOWN, self._pageShown)
		self.Bind(wx.adv.EVT_WIZARD_PAGE_CHANGING, self._pageChanging)

	def run(self):
		try:
			accepted = self.RunWizard(self.firstPage)
			self.testInApplication = accepted and self.testInApp.GetValue()
			return accepted
		finally:
			self._generation += 1
			self.plugin.endRecording()

	def _page(self, heading):
		page = wx.adv.WizardPageSimple(self)
		helper = BoxSizerHelper(page, orientation=wx.VERTICAL)
		helper.addItem(wx.StaticText(page, label=self.t(heading)))
		page.SetSizer(helper.sizer)
		return page, helper

	def _capturePage(self, heading, testing=False):
		page, helper = self._page(heading)
		prompt = self.t(
			"Tap or swipe with one to five fingers, or draw with one finger. Lift all fingers when finished."
		)
		text = wx.StaticText(page, label=prompt)
		text.Wrap(430)
		helper.addItem(text)
		status = wx.StaticText(page, label=self.t("Waiting for a touchpad gesture."), size=(430, 48))
		helper.addItem(status)
		retry = wx.Button(page, label=self.t("Record again" if not testing else "Test again"))
		retry.Bind(wx.EVT_BUTTON, lambda event: self._startCapture(page))
		helper.addItem(retry)
		if testing:
			self.actionSummary = wx.StaticText(page, size=(430, 42))
			helper.addItem(self.actionSummary)
			self.testInApp = wx.CheckBox(
				page, label=self.t("Test the assigned action in the target application after saving settings")
			)
			helper.addItem(self.testInApp)
		return page, status

	def _actionPage(self):
		page, helper = self._page("Name the gesture and assign an action")
		self.nameControl = helper.addLabeledControl(self.t("Gesture name"), wx.TextCtrl)
		self.profiles = tuple(self.settings._profiles)
		profileNames = [key + ".exe" if key else self.t("Global defaults") for key in self.profiles]
		self.profileControl = helper.addLabeledControl(
			self.t("Save in profile"), wx.Choice, choices=profileNames
		)
		self.profileControl.SetSelection(self.profiles.index(self.settings._selectedProfile()))
		self.kindControl = helper.addLabeledControl(
			self.t("Choose an action type"), wx.Choice, choices=[self.t(label) for _, label in _ACTIONS]
		)
		self.kindControl.SetSelection(0)
		self.kindControl.Bind(wx.EVT_CHOICE, self._changeActionKind)
		self.catalogPanel = wx.Panel(page)
		catalogSizer = BoxSizerHelper(self.catalogPanel, orientation=wx.VERTICAL)
		self.catalogControl = catalogSizer.addLabeledControl(
			self.t("Available actions"), wx.Choice, size=(430, -1)
		)
		self.catalogPanel.SetSizer(catalogSizer.sizer)
		self.catalogControl.Bind(wx.EVT_CHOICE, self._selectCatalogAction)
		helper.addItem(self.catalogPanel)
		self.chooseButton = wx.Button(page, label=self.t("Choose action…"))
		self.chooseButton.Bind(wx.EVT_BUTTON, self._selectAction)
		helper.addItem(self.chooseButton)
		self.actionStatus = wx.StaticText(page, label=self.t("No action selected."), size=(430, 38))
		helper.addItem(self.actionStatus)
		self._changeActionKind(None)
		return page

	def _changeActionKind(self, event):
		self.action = None
		self.actionStatus.SetLabel(self.t("No action selected."))
		kind = _ACTIONS[self.kindControl.GetSelection()][0]
		choices = self.settings.actionChoices(kind)
		self._catalogKeys = tuple(choices)
		self.catalogControl.SetItems(list(choices.values()))
		self.catalogControl.SetSelection(wx.NOT_FOUND)
		useCatalog = kind in ("nvda", "windows")
		self.catalogPanel.Show(useCatalog)
		self.chooseButton.Show(not useCatalog)
		if not useCatalog:
			self.chooseButton.SetLabel(
				self.t("Set keyboard shortcut…" if kind == "shortcut" else "Choose application…")
			)
		self.actionStatus.GetParent().Layout()

	def _selectCatalogAction(self, event):
		index = self.catalogControl.GetSelection()
		if index != wx.NOT_FOUND:
			kind = _ACTIONS[self.kindControl.GetSelection()][0]
			self._showAction({"kind": kind, "value": self._catalogKeys[index]})

	def _showAction(self, action):
		self.action = action
		label = self.settings.actionLabel(action)
		self.actionStatus.SetLabel(label)
		self.actionStatus.Wrap(430)
		self.actionStatus.GetParent().Layout()
		ui.message(label)

	def _selectAction(self, event):
		kind = _ACTIONS[self.kindControl.GetSelection()][0]
		if (action := self.settings._chooseAction(parent=self, kind=kind)) is not None:
			self._showAction(action)

	def _pageShown(self, event):
		page = event.GetPage()
		if page is not self._entered:
			self._entered = page
			if page is self.actionPage:
				self.nameControl.SetFocus()
			else:
				if page is self.testPage:
					self.actionSummary.SetLabel(
						self.t("Assigned action: {action}").format(
							action=self.settings.actionLabel(self.action)
						)
					)
					self.actionSummary.Wrap(430)
					page.Layout()
					if (button := self.FindWindow(wx.ID_FORWARD)) is not None:
						button.SetLabel(self.t("Save gesture"))
				wx.CallAfter(self._startCapture, page)
		event.Skip()

	def _pageChanging(self, event):
		page = event.GetPage()
		if event.GetDirection():
			if page is self.firstPage and self._first is None:
				ui.message(self.t("Record the first sample before continuing."))
				event.Veto()
				return
			if page is self.confirmPage:
				if self.shape is None:
					ui.message(self.t("Record a matching confirmation sample before continuing."))
					event.Veto()
					return
				if self._conflicts and self._conflicts != self._acceptedConflicts:
					prompt = (
						self._conflictMessage() + " " + self.t("Use this gesture in the selected profile?")
					)
					if (
						wx.MessageBox(
							prompt, self.GetTitle(), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self
						)
						!= wx.YES
					):
						event.Veto()
						return
					self._acceptedConflicts = self._conflicts
			if page is self.actionPage:
				self.gestureName = self.nameControl.GetValue().strip()
				if not self.gestureName or self.action is None:
					ui.message(self.t("Enter a gesture name and choose an action before continuing."))
					event.Veto()
					return
				self.profile = self.profiles[self.profileControl.GetSelection()]
		self._generation += 1
		self.plugin.endRecording()
		event.Skip()

	def _conflictMessage(self):
		labels = ", ".join(self.t(GESTURES[gesture]) for gesture in self._conflicts)
		return self.t("This gesture overlaps a built-in gesture: {gestures}.").format(gestures=labels)

	def _startCapture(self, page):
		if not self or self.GetCurrentPage() is not page:
			return
		self._generation += 1
		generation = self._generation
		if page is self.firstPage:
			self._first = self.shape = None
			self._acceptedConflicts = ()
			status = self.firstStatus
			prompt = "Perform the first sample now."
		elif page is self.confirmPage:
			self.shape = None
			status = self.confirmStatus
			prompt = "Perform the confirmation sample now."
		else:
			status = self.testStatus
			prompt = (
				"Draw the gesture again to test recognition. The action runs in the application after saving."
			)
		if self.plugin.beginRecording(
			lambda shape, direction: self._captured(page, generation, shape, direction)
		):
			message = self.t(prompt)
			receiver = self.plugin._touchpad
			if not (receiver.recordingLocal and receiver.recordingGlobal):
				message += " " + self.t(
					"Windows could not suspend every built-in touchpad gesture on this device."
				)
			self._status(status, message)
		else:
			self._status(status, self.t("The touchpad could not be opened for recording."))

	def _captured(self, page, generation, shape, direction):
		if not self or generation != self._generation or self.GetCurrentPage() is not page:
			return
		if shape is None:
			status = {
				self.firstPage: self.firstStatus,
				self.confirmPage: self.confirmStatus,
				self.testPage: self.testStatus,
			}[page]
			self._status(status, self.t("No gesture was recognized. Lift all fingers and try again."))
			return
		if isinstance(shape, dict) and shape["fingers"] == 1 and shape["gesture"] in GESTURES:
			direction = shape["gesture"]
		if page is self.firstPage:
			self._first, self._firstDirection = shape, direction
			self._status(
				self.firstStatus,
				self.t("First sample recorded. Select Next to confirm.") + " " + self._description(shape),
			)
		elif page is self.confirmPage:
			if distance(self._first, shape) > MATCH_LIMIT:
				self._status(
					self.confirmStatus,
					self.t("Samples did not match. Record the confirmation again or go back."),
				)
				return
			duplicate = next(
				(
					gesture["name"]
					for gesture in self.settings.data["gestures"].values()
					if min(distance(self._first, gesture["shape"]), distance(shape, gesture["shape"]))
					<= MATCH_LIMIT
				),
				None,
			)
			if duplicate is not None:
				message = self.t(
					"This gesture matches the saved gesture {name}. Go back to record a different gesture."
				).format(name=duplicate)
				self._status(self.confirmStatus, message)
				return
			self.shape = self._first
			self._conflicts = tuple(
				dict.fromkeys(sample for sample in (self._firstDirection, direction) if sample in GESTURES)
			)
			message = (
				self.t("Samples match. Select Next to assign an action.") + " " + self._description(shape)
			)
			if self._conflicts:
				message += " " + self._conflictMessage()
			self._status(self.confirmStatus, message)
		else:
			matches = distance(self.shape, shape) <= MATCH_LIMIT
			self._status(
				self.testStatus,
				self.t(
					"Test passed. The gesture was recognized."
					if matches
					else "Gesture not recognized. Select Test again to retry."
				),
			)
			if not matches:
				return
		nextButton = self.FindWindow(wx.ID_FORWARD)
		if nextButton:
			nextButton.SetFocus()

	def _description(self, shape):
		if not isinstance(shape, dict):
			return self.t("One-finger drawing.")
		name = {
			"tap": "Single tap",
			"doubletap": "Double tap",
			"tripletap": "Triple tap",
		}.get(shape["gesture"]) or GESTURES[shape["gesture"]]
		return self.t("Fingers: {count}. {gesture}.").format(count=shape["fingers"], gesture=self.t(name))

	@staticmethod
	def _status(control, text):
		control.SetLabel(text)
		control.Wrap(430)
		control.GetParent().Layout()
		ui.message(text)
