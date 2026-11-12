# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Temporarily disable the current user's Explorer touchpad actions."""

import json
import msvcrt
import sys
import winreg
from ctypes import POINTER, byref, c_int
from ctypes.wintypes import DWORD

from comtypes import CLSCTX_LOCAL_SERVER, COMMETHOD, GUID, HRESULT, COMError, IUnknown
from comtypes.client import CreateObject
from logHandler import log

from .preferences import writeJson

_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\PrecisionTouchPad"
_NAMES = (
	"ThreeFingerTapEnabled",
	"FourFingerTapEnabled",
	"ThreeFingerSlideEnabled",
	"FourFingerSlideEnabled",
)
# Explorer's internal setting IDs vary by Windows build.
# https://github.com/nvaccess/nvda/discussions/19841
_FIRST_ID = {22000: 14, 22621: 15, 22631: 15, 26100: 15, 26200: 15, 26300: 15}
_SHELL = GUID("{c2f03a33-21f5-47fa-b4bb-156362a2f239}")
_CACHE_SERVICE = GUID("{53660488-8855-460b-a9ab-5cfc6b5012ca}")
_FAILURES = (COMError, OSError, ValueError, TypeError, RuntimeError)


class _SettingsCache(IUnknown):
	_iid_ = GUID("{4214f6fa-eb36-4e2f-9ca2-23fdc1832df7}")
	_methods_ = (
		COMMETHOD([], HRESULT, "OnSettingChanged", (["in"], c_int, "setting")),
		COMMETHOD(
			[], HRESULT, "GetBOOL", (["in"], c_int, "setting"), (["out", "retval"], POINTER(c_int), "value")
		),
		COMMETHOD(
			[], HRESULT, "GetDWORD", (["in"], c_int, "setting"), (["out", "retval"], POINTER(DWORD), "value")
		),
	)


class _ServiceProvider(IUnknown):
	_iid_ = GUID("{6d5140c1-7436-11ce-8034-00aa006009fa}")
	_methods_ = (
		COMMETHOD(
			[],
			HRESULT,
			"QueryService",
			(["in"], POINTER(GUID), "service"),
			(["in"], POINTER(GUID), "iid"),
			(["out", "retval"], POINTER(POINTER(_SettingsCache)), "result"),
		),
	)


def _cache():
	shell = CreateObject(_SHELL, interface=_ServiceProvider, clsctx=CLSCTX_LOCAL_SERVER)
	return shell.QueryService(byref(_CACHE_SERVICE), byref(_SettingsCache._iid_))


def _value(key, name):
	try:
		value, kind = winreg.QueryValueEx(key, name)
	except FileNotFoundError:
		return None
	if kind != winreg.REG_DWORD:
		raise ValueError("Unexpected touchpad setting type")
	return value


def _openRegistry():
	return winreg.OpenKey(
		winreg.HKEY_CURRENT_USER, _REGISTRY_PATH, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
	)


class ShellGestureOverride:
	def __init__(self, directory):
		self._directory = directory
		self._journal = directory / "windows-gestures.json" if directory else None
		self._lock = None
		first = _FIRST_ID.get(sys.getwindowsversion().build)
		self._ids = dict(zip(_NAMES, range(first, first + len(_NAMES)), strict=True)) if first else {}

	def _acquire(self):
		if not self._directory or not self._ids:
			return False
		self._directory.mkdir(parents=True, exist_ok=True)
		lock = (self._directory / "windows-gestures.lock").open("a+b")
		try:
			lock.seek(0)
			msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
		except OSError:
			lock.close()
			return False
		self._lock = lock
		return True

	def recover(self):
		if self._journal and self._journal.is_file():
			try:
				if self._acquire():
					self.stop()
			except _FAILURES:
				log.debugWarning("Could not recover Windows touchpad settings", exc_info=True)

	def start(self):
		if self._lock is not None:
			return True
		try:
			if not self._acquire():
				return False
			cache = _cache()
			if self._journal.is_file():
				self._restore(cache)
			with _openRegistry() as key:
				before = {
					name: [_value(key, name), cache.GetDWORD(setting)] for name, setting in self._ids.items()
				}
				# Keep a recoverable copy before changing any user setting.
				writeJson(self._journal, before)
				for name, setting in self._ids.items():
					if before[name] != [0, 0]:
						winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0)
						cache.OnSettingChanged(setting)
				if any(cache.GetDWORD(setting) for setting in self._ids.values()):
					raise RuntimeError("Windows did not apply every touchpad setting")
			return True
		except _FAILURES:
			log.debugWarning("Could not disable Windows shell touchpad gestures", exc_info=True)
			self.stop()
			return False

	def _restore(self, cache):
		with self._journal.open(encoding="utf-8") as source:
			before = json.load(source)
		if (
			not isinstance(before, dict)
			or set(before) != set(self._ids)
			or any(
				not isinstance(entry, list)
				or len(entry) != 2
				or (entry[0] is not None and (type(entry[0]) is not int or not 0 <= entry[0] <= 0xFFFFFFFF))
				or type(entry[1]) is not int
				or not 0 <= entry[1] <= 0xFFFFFFFF
				for entry in before.values()
			)
		):
			raise ValueError("Invalid Windows touchpad settings backup")
		failed = False
		with _openRegistry() as key:
			for name, setting in self._ids.items():
				try:
					original, effective = before[name]
					current = _value(key, name)
					# A nonzero edit made while TouchWiz was on belongs to the user.
					if current == 0 and original != 0:
						if original is None:
							winreg.DeleteValue(key, name)
						else:
							winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, original)
						current = original
					cache.OnSettingChanged(setting)
					expected = effective if current == original else current
					if expected is not None and cache.GetDWORD(setting) != expected:
						failed = True
				except _FAILURES:
					failed = True
		if failed:
			raise RuntimeError("Windows did not restore every touchpad setting")
		self._journal.unlink()

	def stop(self):
		if self._lock is None:
			return
		try:
			if self._journal.is_file():
				self._restore(_cache())
		except _FAILURES:
			log.debugWarning("Could not restore Windows touchpad gestures", exc_info=True)
		finally:
			self._lock.close()
			self._lock = None
