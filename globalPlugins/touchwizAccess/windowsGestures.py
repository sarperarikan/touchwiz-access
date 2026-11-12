# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Use the Windows foreground-process controller during touchpad recording."""

from ctypes import POINTER, WINFUNCTYPE, WinDLL, byref, c_long, c_uint, c_void_p
from ctypes.wintypes import BYTE, LPCWSTR, UINT

from comtypes import GUID

_RUNTIME_CLASS = "Windows.UI.Input.TouchpadGesturesController"
_STATICS = GUID("{207ef171-1a73-51cd-a694-8840e09dbafa}")
_ALL_GLOBAL_GESTURES = 0x3F
_RPC_E_CHANGED_MODE = -2147417850
_runtime = WinDLL("combase")
_runtime.WindowsCreateString.argtypes = (LPCWSTR, UINT, POINTER(c_void_p))
_runtime.WindowsDeleteString.argtypes = (c_void_p,)
_runtime.RoGetActivationFactory.argtypes = (c_void_p, POINTER(GUID), POINTER(c_void_p))
_runtime.RoInitialize.argtypes = (UINT,)
_runtime.RoUninitialize.restype = None


def _method(pointer, index, result, *parameters):
	vtable = POINTER(c_void_p).from_address(pointer.value)
	return WINFUNCTYPE(result, c_void_p, *parameters)(vtable[index])


class WindowsGestureCapture:
	def __init__(self):
		self._controller = c_void_p()
		self._initialized = False

	def start(self):
		status = _runtime.RoInitialize(0)
		if status < 0 and status != _RPC_E_CHANGED_MODE:
			return False
		self._initialized = status >= 0
		name, factory = c_void_p(), c_void_p()
		try:
			if _runtime.WindowsCreateString(_RUNTIME_CLASS, len(_RUNTIME_CLASS), byref(name)) < 0:
				return False
			if _runtime.RoGetActivationFactory(name, byref(_STATICS), byref(factory)) < 0:
				return False
			# The methods start after IInspectable's six ABI entries.
			supported = BYTE()
			if (
				_method(factory, 6, c_long, POINTER(BYTE))(factory, byref(supported)) < 0
				or not supported.value
			):
				return False
			if _method(factory, 7, c_long, POINTER(c_void_p))(factory, byref(self._controller)) < 0:
				return False
			if _method(self._controller, 9, c_long, c_uint)(self._controller, _ALL_GLOBAL_GESTURES) < 0:
				return False
			return _method(self._controller, 7, c_long, BYTE)(self._controller, 1) >= 0
		finally:
			if factory:
				_method(factory, 2, UINT)(factory)
			if name:
				_runtime.WindowsDeleteString(name)

	def close(self):
		if self._controller:
			_method(self._controller, 7, c_long, BYTE)(self._controller, 0)
			_method(self._controller, 2, UINT)(self._controller)
			self._controller = c_void_p()
		if self._initialized:
			_runtime.RoUninitialize()
			self._initialized = False
