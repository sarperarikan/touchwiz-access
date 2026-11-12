# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Move keyboard focus to matching controls in the foreground native window."""

from ctypes import byref, sizeof
from dataclasses import dataclass

import api
import eventHandler
import UIAHandler
import watchdog
import winKernel
import winUser
from comtypes import COMError
from controlTypes import Role, State
from NVDAObjects import NVDAObject
from NVDAObjects.IAccessible.sysListView32 import (
	LVIF_STATE,
	LVIS_FOCUSED,
	LVM_FIRST,
	ListItemWithoutColumnSupport,
)
from NVDAObjects.window import Window
from winBindings.kernel32 import GetCurrentThreadId
from windowUtils import findDescendantWindow

from .rawInput import IsHungAppWindow

_EDIT_ROLES = {Role.EDITABLETEXT, Role.PASSWORDEDIT}
_BUTTON_ROLES = {Role.BUTTON, Role.TOGGLEBUTTON, Role.MENUBUTTON, Role.SPLITBUTTON, Role.DROPDOWNBUTTON}
_FORM_ROLES = _EDIT_ROLES | _BUTTON_ROLES | {Role.CHECKBOX, Role.COMBOBOX, Role.SPINBUTTON}
_LIST_ITEMS = {Role.LISTITEM, Role.DATAITEM}
_TREE_ITEMS = {Role.TREEVIEWITEM}
_TABLE_CELLS = {Role.TABLECELL, Role.TABLECOLUMNHEADER, Role.TABLEROWHEADER}
FOCUS_ROLES = {
	"edit": _EDIT_ROLES,
	"button": _BUTTON_ROLES,
	"link": {Role.LINK},
	"formField": _FORM_ROLES,
	"checkBox": {Role.CHECKBOX},
	"comboBox": {Role.COMBOBOX},
	"list": {Role.LIST},
	"listItem": _LIST_ITEMS,
	"tree": {Role.TREEVIEW},
	"treeItem": _TREE_ITEMS,
	"table": {Role.TABLE, Role.DATAGRID},
	"tableRow": {Role.TABLEROW},
	"tableCell": _TABLE_CELLS,
	"toolBar": {Role.TOOLBAR},
}
_CONTAINER_TARGETS = {
	"list": _LIST_ITEMS,
	"tree": _TREE_ITEMS,
	"table": _TABLE_CELLS | FOCUS_ROLES["tableRow"] | {Role.DATAITEM},
	"tableRow": _TABLE_CELLS,
	"toolBar": _FORM_ROLES | {Role.LINK},
}
_UNAVAILABLE = {State.INVISIBLE, State.DEFUNCT, State.UNAVAILABLE}
_OBJECT_ERRORS = (COMError, LookupError, OSError, RuntimeError)
_DESKTOP_CLASSES = ("Progman", "WorkerW")
_NATIVE_FOCUS_CLASSES = {
	"button",
	"edit",
	"combobox",
	"comboboxex32",
	"richedit20w",
	"richedit50w",
	"listbox",
	"syslistview32",
	"systreeview32",
	"toolbarwindow32",
}
_WS_TABSTOP = 0x00010000
_LVM_SETITEMSTATE = LVM_FIRST + 43
_LVM_ENSUREVISIBLE = LVM_FIRST + 19
_MAX_DEPTH = 100
_MAX_CONTROLS = 2500


def _property(obj, name):
	try:
		return getattr(obj, name, None)
	except _OBJECT_ERRORS:
		return None


def _canFocus(obj):
	role, states = obj.role, obj.states
	return (
		not states.intersection(_UNAVAILABLE)
		and bool(states.intersection({State.FOCUSABLE, State.FOCUSED}))
		and (role not in _EDIT_ROLES or State.READONLY not in states)
	)


def _children(obj):
	if getattr(type(obj), "_get_children", None) is NVDAObject._get_children:
		return ()
	return _property(obj, "children") or ()


def _edge(obj, forward):
	child = _property(obj, "firstChild" if forward else "lastChild")
	if child is not None and child != obj:
		return child
	children = _children(obj)
	return next((item for item in (children if forward else reversed(children)) if item != obj), None)


def _neighbor(obj, parent, forward):
	sibling = _property(obj, "next" if forward else "previous")
	if sibling is not None and sibling != obj and sibling != parent:
		return sibling
	children = _children(parent)
	for index, item in enumerate(children):
		if item == obj:
			target = index + (1 if forward else -1)
			return children[target] if 0 <= target < len(children) else None
	return None


@dataclass(frozen=True)
class _WindowScope:
	foreground: int
	handle: int
	root: NVDAObject

	def contains(self, obj):
		handle = _property(obj, "windowHandle")
		return bool(handle and winUser.isDescendantWindow(self.handle, handle))

	def isCurrent(self):
		return self.foreground == winUser.getForegroundWindow()

	def includesFocus(self, obj, ancestor=None):
		ancestor = self.root if ancestor is None else ancestor
		for _ in range(_MAX_DEPTH):
			if not self.contains(obj):
				return False
			if obj == ancestor:
				return True
			obj = _property(obj, "parent")
		return False

	def last(self, obj):
		for _ in range(_MAX_DEPTH):
			child = _edge(obj, False)
			if not self.contains(child):
				break
			obj = child
		return obj

	def step(self, obj, forward, boundary=None, *, descend=True):
		boundary = self.root if boundary is None else boundary
		if forward:
			if descend and (child := _edge(obj, True)) is not None:
				return child
			for _ in range(_MAX_DEPTH):
				if obj == boundary or not self.contains(obj):
					break
				parent = _property(obj, "parent")
				if not self.contains(parent):
					break
				sibling = _neighbor(obj, parent, True)
				if sibling is not None:
					return sibling
				obj = parent
			return None
		if obj == boundary:
			return None
		parent = _property(obj, "parent")
		if not self.contains(parent):
			return None
		sibling = _neighbor(obj, parent, False)
		return self.last(sibling) if self.contains(sibling) else parent

	def enclosingContainer(self, obj, mode):
		for _ in range(_MAX_DEPTH):
			if not self.contains(obj) or obj == self.root:
				break
			if self.matchesRole(obj, mode):
				return obj
			obj = _property(obj, "parent")
		return None

	@staticmethod
	def matchesRole(obj, mode):
		if obj.role not in FOCUS_ROLES[mode]:
			return False
		# MSAA also reports the SysListView column header control as a list.
		return mode != "list" or winUser.getClassName(obj.windowHandle).lower() != "sysheader32"

	def focusTarget(self, obj, mode, currentFocus):
		if obj == currentFocus or not self.matchesRole(obj, mode) or obj.states.intersection(_UNAVAILABLE):
			return None
		roles = _CONTAINER_TARGETS.get(mode)
		if roles and self.includesFocus(currentFocus, ancestor=obj):
			return None
		if _canFocus(obj):
			return obj
		if roles:
			child = _edge(obj, True)
			first = child
			for _ in range(_MAX_CONTROLS):
				if not self.contains(child) or child == obj or not self.isCurrent():
					break
				if child != currentFocus and child.role in roles and _canFocus(child):
					return child
				child = self.step(child, True, boundary=obj)
				if child == first:
					break
		return None


def _desktopView(host):
	def under(hwnd):
		try:
			return findDescendantWindow(hwnd, visible=True, className="SHELLDLL_DefView")
		except LookupError:
			return None

	if host:
		return under(host)
	view = None

	def isDesktopHost(hwnd):
		nonlocal view
		if winUser.getClassName(hwnd) in _DESKTOP_CLASSES:
			view = under(hwnd)
		return bool(view)

	winUser.findTopLevelWindow(isDesktopHost)
	return view


def _scope(hwnd):
	handle = hwnd
	if not hwnd or hwnd == winUser.getDesktopWindow():
		handle = _desktopView(None)
	elif winUser.getClassName(hwnd) in _DESKTOP_CLASSES:
		handle = _desktopView(hwnd) or hwnd
	if not handle:
		return None
	root = api.getForegroundObject()
	if _property(root, "windowHandle") != handle:
		root = NVDAObject.objectInForeground() if handle == hwnd else None
	if _property(root, "windowHandle") != handle:
		root = Window(windowHandle=handle)
	if _property(root, "windowHandle") == handle:
		return _WindowScope(hwnd, handle, root)
	return None


def _focusNativeControl(obj, scope):
	# Some Win32 controls expose IAccessible focus but do not implement accSelect.
	handle = _property(obj, "windowHandle")
	if (
		not handle
		or handle == scope.handle
		or _property(obj, "IAccessibleChildID") not in (None, 0)
		or winUser.getClassName(handle).lower() not in _NATIVE_FOCUS_CLASSES
		or not winUser.isWindowVisible(handle)
		or not winUser.isWindowEnabled(handle)
		or not winUser.getWindowStyle(handle) & _WS_TABSTOP
		or IsHungAppWindow(handle)
	):
		return False
	element = _property(obj, "UIAElement")
	if element is not None and _property(element, "cachedNativeWindowHandle") != handle:
		return False
	_, thread = winUser.getWindowThreadProcessID(handle)
	if not thread:
		return False
	info = winUser.getGUIThreadInfo(thread)
	if info.hwndFocus == handle:
		return True
	if info.flags & (
		winUser.GUI_INMENUMODE
		| winUser.GUI_SYSTEMMENUMODE
		| winUser.GUI_POPUPMENUMODE
		| winUser.GUI_INMOVESIZE
	):
		return False
	if scope.isCurrent() and scope.contains(obj):
		if thread == GetCurrentThreadId():
			winUser.setFocus(handle)
			return winUser.getGUIThreadInfo(thread).hwndFocus == handle
		elif UIAHandler.handler is not None:
			element = UIAHandler.handler.clientObject.ElementFromHandle(handle)
			if element and scope.isCurrent():
				element.setFocus()
				return True
	return False


def _focusListViewItem(obj, scope):
	# Win32 list-view MSAA providers can ignore accSelect(TAKEFOCUS).
	# Change the item's focus bit only, leaving its selection state intact.
	handle, index = obj.windowHandle, obj.IAccessibleChildID - 1
	if index < 0 or not scope.isCurrent() or not scope.contains(obj) or IsHungAppWindow(handle):
		return False
	item = obj.LVITEM(mask=LVIF_STATE, state=LVIS_FOCUSED, stateMask=LVIS_FOCUSED)
	process = obj.processHandle
	address = winKernel.virtualAllocEx(
		process, None, sizeof(item), winKernel.MEM_COMMIT, winKernel.PAGE_READWRITE
	)
	try:
		winKernel.writeProcessMemory(process, address, byref(item), sizeof(item), None)
		if not scope.isCurrent() or not watchdog.cancellableSendMessage(
			handle, _LVM_SETITEMSTATE, index, address
		):
			return False
	finally:
		winKernel.virtualFreeEx(process, address, 0, winKernel.MEM_RELEASE)
	if not scope.isCurrent():
		return False
	watchdog.cancellableSendMessage(handle, _LVM_ENSUREVISIBLE, index, 0)
	return _focusNativeControl(obj.parent, scope)


def moveFocus(hwnd, mode, forward):
	try:
		scope = _scope(hwnd)
		if scope is None or not scope.isCurrent():
			return False
		focus = api.getFocusObject()
		anchor = focus if scope.includesFocus(focus) else None
		if anchor is not None:
			container = scope.enclosingContainer(anchor, mode) if mode in _CONTAINER_TARGETS else None
			target = scope.step(container or anchor, forward, descend=container is None)
		else:
			target = _edge(scope.root, True) if forward else scope.last(scope.root)
		first = target
		for _ in range(_MAX_CONTROLS):
			if target is None or target == scope.root or target == anchor or not scope.isCurrent():
				break
			if not scope.contains(target):
				break
			if (match := scope.focusTarget(target, mode, focus)) is not None:
				if eventHandler.isPendingEvents("gainFocus") or not scope.isCurrent():
					return False
				if (
					isinstance(match, ListItemWithoutColumnSupport)
					and winUser.getClassName(match.windowHandle).lower() == "syslistview32"
				):
					return _focusListViewItem(match, scope)
				try:
					match.setFocus()
					requested = True
				except _OBJECT_ERRORS:
					requested = False
				try:
					requested = _focusNativeControl(match, scope) or requested
				except _OBJECT_ERRORS:
					pass
				if requested:
					return True
			target = scope.step(target, forward)
			if target == first:
				break
	except _OBJECT_ERRORS:
		return False
	return False
