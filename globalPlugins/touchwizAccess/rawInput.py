# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Win32 input bindings used by TouchWiz Access."""

from ctypes import POINTER, Structure, WinDLL, byref, c_void_p, sizeof
from ctypes.wintypes import BOOL, DWORD, HANDLE, HWND, RECT, UINT, USHORT, WPARAM

WM_INPUT_DEVICE_CHANGE = 0x00FE
WM_INPUT = 0x00FF
RIM_TYPEHID = 2
RID_INPUT = 0x10000003
RIDI_PREPARSEDDATA = 0x20000005
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RIDEV_DEVNOTIFY = 0x00002000
UINT_ERROR = 0xFFFFFFFF


class RAWINPUTDEVICE(Structure):
	_fields_ = (("usagePage", USHORT), ("usage", USHORT), ("flags", DWORD), ("target", HWND))


class RAWINPUTDEVICELIST(Structure):
	_fields_ = (("device", HANDLE), ("type", DWORD))


class RAWINPUTHEADER(Structure):
	_fields_ = (("type", DWORD), ("size", DWORD), ("device", HANDLE), ("wParam", WPARAM))


class RAWHIDHEADER(Structure):
	_fields_ = (("reportSize", DWORD), ("reportCount", DWORD))


_dll = WinDLL("user32", use_last_error=True)

ClipCursor = _dll.ClipCursor
ClipCursor.argtypes = (POINTER(RECT),)
ClipCursor.restype = BOOL

GetClipCursor = _dll.GetClipCursor
GetClipCursor.argtypes = (POINTER(RECT),)
GetClipCursor.restype = BOOL

SystemParametersInfo = _dll.SystemParametersInfoW
SystemParametersInfo.argtypes = (UINT, UINT, c_void_p, UINT)
SystemParametersInfo.restype = BOOL

_SPI_GETTOUCHPADPARAMETERS = 0x00AE
_SPI_SETTOUCHPADPARAMETERS = 0x00AF
NATIVE_TAP_FLAGS = (1 << 2) | (1 << 3)  # tapEnabled, tapAndDragEnabled
RECORDING_FLAGS = NATIVE_TAP_FLAGS | (1 << 4) | (1 << 7) | (1 << 8)  # two-finger tap, pan, zoom


class TouchpadParameters(Structure):
	"""The version 1 Win32 TOUCHPAD_PARAMETERS layout."""

	_fields_ = (
		("versionNumber", UINT),
		("maxSupportedContacts", UINT),
		("legacyTouchpadFeatures", UINT),
		("status", UINT),
		("options", UINT),
		("sensitivityLevel", UINT),
		("cursorSpeed", UINT),
		("feedbackIntensity", UINT),
		("clickForceSensitivity", UINT),
		("rightClickZoneWidth", UINT),
		("rightClickZoneHeight", UINT),
	)

	@classmethod
	def read(cls):
		parameters = cls(versionNumber=1)
		if SystemParametersInfo(_SPI_GETTOUCHPADPARAMETERS, sizeof(parameters), byref(parameters), 0):
			return parameters
		return None

	def apply(self):
		# Apply for this Windows session without updating the user's stored settings.
		return SystemParametersInfo(_SPI_SETTOUCHPADPARAMETERS, sizeof(self), byref(self), 0)


IsHungAppWindow = _dll.IsHungAppWindow
IsHungAppWindow.argtypes = (HWND,)
IsHungAppWindow.restype = BOOL

RegisterRawInputDevices = _dll.RegisterRawInputDevices
RegisterRawInputDevices.argtypes = (POINTER(RAWINPUTDEVICE), UINT, UINT)
RegisterRawInputDevices.restype = BOOL

GetRawInputDeviceList = _dll.GetRawInputDeviceList
GetRawInputDeviceList.argtypes = (POINTER(RAWINPUTDEVICELIST), POINTER(UINT), UINT)
GetRawInputDeviceList.restype = UINT

GetRawInputDeviceInfo = _dll.GetRawInputDeviceInfoW
GetRawInputDeviceInfo.argtypes = (HANDLE, UINT, c_void_p, POINTER(UINT))
GetRawInputDeviceInfo.restype = UINT

GetRawInputData = _dll.GetRawInputData
GetRawInputData.argtypes = (HANDLE, UINT, c_void_p, POINTER(UINT), UINT)
GetRawInputData.restype = UINT
