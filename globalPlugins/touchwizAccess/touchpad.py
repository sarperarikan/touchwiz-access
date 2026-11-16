# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Swipe and rotor gestures from Windows Precision Touchpad HID reports."""

from collections import deque
from ctypes import WinError, byref, c_ulong, create_string_buffer, get_last_error, sizeof
from ctypes.wintypes import POINT, RECT, UINT, USHORT
from dataclasses import dataclass
from math import atan2, hypot, pi, radians, tan
from time import monotonic

import hidpi
import winUser
import wx
from hwIo.hid import HidPError, check_HidP_status
from logHandler import log
from utils.security import isLockScreenModeActive, isRunningOnSecureDesktop
from winAPI.winUser.constants import SystemMetrics
from winBindings import hid, user32
from windowUtils import CustomWindow

from . import rawInput
from .preferences import APP_SWITCH_GESTURES
from .rotor import getDocument
from .strokes import MAX_FINGERS, TAP_SERIES_MS, ReportAssembler, Stroke
from .windowsGestures import WindowsGestureCapture

_DIGITIZER = 0x0D
_TOUCHPAD = 0x05
_DESKTOP = 0x01
_BUTTON = 0x09
_INPUT = hidpi.HIDP_REPORT_TYPE.INPUT
_MAX_PACKET = 65536
_MAX_SWIPE_SECONDS = 0.85
_MIN_HORIZONTAL_SWIPE = 0.08
_MIN_VERTICAL_SWIPE = 0.11
_FAST_HORIZONTAL_DISTANCE = 0.13
_FAST_HORIZONTAL_MIN_SECONDS = 0.06
_MAX_SWIPE_SLOPE = tan(radians(40))
_MIN_ROTOR_DISTANCE = 0.07
_MIN_ROTOR_TURN = radians(27)
_ROTOR_ANGLE_JITTER = radians(3)
_ROTOR_SPEECH_GRACE_SECONDS = 0.1
_MOUSE_EVENT_GRACE_SECONDS = 0.1
_POINTER_HOLD_TIMEOUT_MS = 1500
_TAP_MAX_SECONDS = 0.22
_TAP_MAX_TRAVEL = 0.035
_DOUBLE_TAP_SECONDS = 0.4
_MODIFIERS = (winUser.VK_SHIFT, winUser.VK_CONTROL, winUser.VK_MENU, winUser.VK_LWIN, winUser.VK_RWIN)
_BUILT_IN_TAPS = {
	(1, "doubletap"): "doubletap",
	(3, "doubletap"): "threeFingerDoubleTap",
	(3, "tripletap"): "threeFingerTripleTap",
}


@dataclass(frozen=True)
class Contact:
	id: int
	x: float
	y: float
	confident: bool = True


@dataclass(frozen=True)
class Frame:
	scan: int
	count: int
	contact: Contact | None = None
	anyTip: bool = False
	clicked: bool = False
	slots: int = 1
	contacts: tuple[Contact, ...] = ()


class SwipeRecognizer:
	"""Recognize one predominantly horizontal or vertical swipe per contact."""

	def __init__(self, aspect=1.0):
		self._aspect = aspect
		self.reset()

	def reset(self):
		self._start = self._last = None
		self._startedAt = self._travelX = self._travelY = self._maxX = self._maxY = 0.0
		self._blocked = False
		self._splitScan = None
		self._remaining = 0
		self._multiHasTip = False

	def cancel(self):
		self._start = self._last = None
		self._blocked = True

	def feed(self, frame: Frame, now: float, fastHorizontal=False, recognizeTaps=False) -> str | None:
		if frame.count > 1:
			# Hybrid devices put the count in the first packet and zero in continuation packets.
			self._splitScan = frame.scan
			self._remaining = frame.count - frame.slots
			self._multiHasTip = frame.anyTip
			self.cancel()
			if self._remaining <= 0:
				self._finishMultiFrame()
			return None
		if frame.count == 0 and frame.scan == self._splitScan:
			self._remaining -= frame.slots
			self._multiHasTip |= frame.anyTip
			if self._remaining <= 0:
				self._finishMultiFrame()
			return None
		self._splitScan = None
		if frame.clicked or (frame.contact and not frame.contact.confident):
			self.cancel()
		if not frame.anyTip:
			direction = None if self._blocked else self._finish(now)
			if (
				recognizeTaps
				and direction is None
				and not self._blocked
				and self._start is not None
				and now - self._startedAt <= _TAP_MAX_SECONDS
				and hypot(self._travelX, self._travelY * self._aspect) <= _TAP_MAX_TRAVEL
			):
				direction = "tap"
			self.reset()
			return direction
		if self._blocked:
			return None
		contact = frame.contact
		if frame.count != 1 or contact is None:
			self.cancel()
			return None
		if self._start is None:
			self._start = self._last = contact
			self._startedAt = now
			return None
		if contact.id != self._start.id:
			self.cancel()
			return None
		self._travelX += abs(contact.x - self._last.x)
		self._travelY += abs(contact.y - self._last.y)
		self._maxX = max(self._maxX, abs(contact.x - self._start.x))
		self._maxY = max(self._maxY, abs(contact.y - self._start.y))
		self._last = contact
		if (
			fastHorizontal
			and now - self._startedAt >= _FAST_HORIZONTAL_MIN_SECONDS
			and abs(contact.x - self._start.x) >= _FAST_HORIZONTAL_DISTANCE
			and (direction := self._finish(now)) in ("swiperight", "swipeleft")
		):
			self._blocked = True
			return f"{direction}Early"
		return None

	def _finishMultiFrame(self):
		self._splitScan = None
		if not self._multiHasTip:
			self.reset()

	def _finish(self, now: float) -> str | None:
		if self._start is None or now - self._startedAt > _MAX_SWIPE_SECONDS:
			return None
		dx, dy = self._last.x - self._start.x, self._last.y - self._start.y
		# HID axes are normalized independently. Compare their physical lengths
		# so a wide pad does not exaggerate a swipe's vertical angle.
		if abs(dx) >= abs(dy) * self._aspect:
			distance, travel, minimum = dx, self._travelX, _MIN_HORIZONTAL_SWIPE
			along, across = abs(dx), self._maxY * self._aspect
			positive, negative = "swiperight", "swipeleft"
		else:
			distance, travel, minimum = dy, self._travelY, _MIN_VERTICAL_SWIPE
			along, across = abs(dy) * self._aspect, self._maxX
			positive, negative = "swipedown", "swipeup"
		if abs(distance) < minimum or across > along * _MAX_SWIPE_SLOPE or abs(distance) < travel * 0.7:
			return None
		return positive if distance > 0 else negative


class GestureRecognizer:
	"""Recognize single-contact swipes and one rotor step per two-finger contact."""

	def __init__(self, aspect=1.0):
		self._aspect = aspect
		self._swipe = SwipeRecognizer(aspect)
		self.reset()

	def reset(self):
		self._swipe.reset()
		self._pending = None
		self.inContact = False
		self._resetTurn()

	def _resetTurn(self):
		self._pair = None
		self._blocked = False
		self._rotation = self._travel = 0.0

	@property
	def rotorInContact(self):
		return self._pair is not None and self._travel > 0

	def cancel(self):
		self._swipe.cancel()
		self._pending = None
		self._pair = None
		self._blocked = True

	def feed(self, frame: Frame, now: float, fastHorizontal=False, recognizeTaps=False) -> str | None:
		swipe = self._swipe.feed(frame, now, fastHorizontal, recognizeTaps)
		if self._pending is not None:
			first = self._pending
			self._pending = None
			if frame.count == 0 and frame.scan == first.scan:
				frame = Frame(
					frame.scan,
					first.count,
					anyTip=first.anyTip or frame.anyTip,
					clicked=first.clicked or frame.clicked,
					slots=first.slots + frame.slots,
					contacts=first.contacts + frame.contacts,
				)
		if frame.count == 2 and frame.slots == 1:
			self._pending = frame
			self.inContact |= frame.anyTip
			return None
		self.inContact = frame.anyTip
		if frame.clicked or frame.count > 2:
			self._blocked = True
		if not frame.anyTip:
			self._resetTurn()
			return swipe
		if frame.count == 1 and self._pair is None:
			return swipe
		if self._blocked:
			return None
		if len(frame.contacts) != 2:
			self._blocked = self._pair is not None
			return None
		first, second = sorted(frame.contacts, key=lambda contact: contact.id)
		if first.id == second.id:
			self._blocked = True
			return None
		ids = (first.id, second.id)
		dx, dy = second.x - first.x, (second.y - first.y) * self._aspect
		distance = hypot(dx, dy)
		if distance < _MIN_ROTOR_DISTANCE or not (first.confident and second.confident):
			return None
		angle = atan2(dy, dx)
		if self._pair is None:
			self._pair = ids
			self._angle, self._distance = angle, distance
			return None
		if ids != self._pair or not 0.55 <= distance / self._distance <= 1.8:
			self._blocked = True
			return None
		# HID Y increases downward, so positive angles are clockwise.
		change = (angle - self._angle + pi) % (2 * pi) - pi
		if abs(change) < _ROTOR_ANGLE_JITTER:
			return None
		self._angle = angle
		self._rotation += change
		self._travel += abs(change)
		rotation = abs(self._rotation)
		if rotation >= _MIN_ROTOR_TURN and rotation >= self._travel * 0.7:
			# Keep this contact consumed until a complete frame shows no fingers.
			self._blocked = True
			return "clockwise" if self._rotation > 0 else "counterclockwise"
		return None


def _indices(cap):
	if cap.IsAlias:
		return
	if cap.IsRange:
		bounds = cap.u1.Range
		if bounds.UsageMax - bounds.UsageMin == bounds.DataIndexMax - bounds.DataIndexMin:
			yield from zip(
				range(bounds.UsageMin, bounds.UsageMax + 1),
				range(bounds.DataIndexMin, bounds.DataIndexMax + 1),
				strict=True,
			)
	else:
		yield cap.u1.NotRange.Usage, cap.u1.NotRange.DataIndex


@dataclass(frozen=True)
class _Axis:
	index: int
	minimum: int
	maximum: int
	bits: int
	physicalSpan: int
	unit: int
	unitExponent: int

	def read(self, data: dict[int, int]) -> float:
		value = data[self.index]
		if self.minimum < 0 and value & (1 << (self.bits - 1)):
			value -= 1 << self.bits
		if not self.minimum <= value <= self.maximum:
			raise ValueError("Touchpad axis out of range")
		return (value - self.minimum) / (self.maximum - self.minimum)


@dataclass(frozen=True)
class _Slot:
	id: int
	tip: int
	confidence: int | None
	x: _Axis
	y: _Axis


@dataclass(frozen=True)
class _Report:
	count: int
	scan: int
	slots: tuple[_Slot, ...]
	buttons: tuple[int, ...]

	def frame(self, data: dict[int, int]) -> Frame:
		count, scan = data[self.count], data[self.scan]
		tips = [slot for slot in self.slots if data.get(slot.tip)]
		clicked = any(data.get(button) for button in self.buttons)
		contacts = tuple(
			Contact(
				data[slot.id],
				slot.x.read(data),
				slot.y.read(data),
				slot.confidence is None or bool(data.get(slot.confidence)),
			)
			for slot in tips
			if count <= MAX_FINGERS
		)
		contact = contacts[0] if count == len(contacts) == 1 else None
		return Frame(scan, count, contact, bool(tips), clicked, len(self.slots), contacts)


class _TouchpadDevice:
	def __init__(self, device):
		size = UINT()
		query = rawInput.GetRawInputDeviceInfo
		if query(device, rawInput.RIDI_PREPARSEDDATA, None, byref(size)) == rawInput.UINT_ERROR:
			raise WinError(get_last_error())
		if not 0 < size.value <= _MAX_PACKET:
			raise ValueError("Invalid HID descriptor size")
		self._descriptor = create_string_buffer(size.value)
		if query(device, rawInput.RIDI_PREPARSEDDATA, self._descriptor, byref(size)) == rawInput.UINT_ERROR:
			raise WinError(get_last_error())
		caps = hidpi.HIDP_CAPS()
		check_HidP_status(hid.HidP_GetCaps, self._descriptor, byref(caps))
		if (caps.UsagePage, caps.Usage) != (_DIGITIZER, _TOUCHPAD):
			raise ValueError("Not a precision touchpad")
		self._reportSize = caps.InputReportByteLength
		self._reports = self._makeReports(caps)
		maxItems = hid.HidP_MaxDataListLength(_INPUT, self._descriptor)
		if not self._reports or not 0 < maxItems <= 4096:
			raise ValueError("Unsupported touchpad HID reports")
		self._items = (hidpi.HIDP_DATA * maxItems)()
		self.foreground = None
		self.document = None
		self.lastTap = None
		self.stroke = Stroke()
		self.assembler = ReportAssembler()
		self.customTaps = None
		self.aspect = 1.0
		slot = next(iter(self._reports.values())).slots[0]
		x, y = slot.x, slot.y
		aspect = 1.0
		if x.physicalSpan > 0 and y.physicalSpan > 0 and (x.unit, x.unitExponent) == (y.unit, y.unitExponent):
			aspect = y.physicalSpan / x.physicalSpan
		self.aspect = aspect
		self.recognizer = GestureRecognizer(aspect)

	def _makeReports(self, caps) -> dict[int, _Report]:
		values = (hidpi.HIDP_VALUE_CAPS * caps.NumberInputValueCaps)()
		valueCount = USHORT(len(values))
		check_HidP_status(hid.HidP_GetValueCaps, _INPUT, values, byref(valueCount), self._descriptor)
		buttons = (hidpi.HIDP_BUTTON_CAPS * caps.NumberInputButtonCaps)()
		buttonCount = USHORT(len(buttons))
		if buttonCount.value:
			check_HidP_status(hid.HidP_GetButtonCaps, _INPUT, buttons, byref(buttonCount), self._descriptor)
		fields = {}
		axes = {}
		for cap in [*values[: valueCount.value], *buttons[: buttonCount.value]]:
			reportID = cap.ReportID & 0xFF
			for usage, index in _indices(cap):
				key = (cap.UsagePage, usage)
				links = fields.setdefault(reportID, {})
				links.setdefault(cap.LinkCollection, {})[key] = index
				if (
					isinstance(cap, hidpi.HIDP_VALUE_CAPS)
					and key in ((_DESKTOP, 0x30), (_DESKTOP, 0x31))
					and cap.LogicalMax > cap.LogiclMin
					and 0 < cap.BitSize <= 32
				):
					axes[(reportID, index)] = _Axis(
						index,
						cap.LogiclMin,
						cap.LogicalMax,
						cap.BitSize,
						cap.PhysicalMax - cap.PhysicalMin,
						cap.Units,
						cap.UnitsExp,
					)
		reports = {}
		for reportID, links in fields.items():
			count = scan = None
			slots = []
			clicks = []
			for link in links.values():
				if (_DIGITIZER, 0x54) in link:
					count = link[_DIGITIZER, 0x54]
				if (_DIGITIZER, 0x56) in link:
					scan = link[_DIGITIZER, 0x56]
				clicks.extend(index for (page, _usage), index in link.items() if page == _BUTTON)
				try:
					slots.append(
						_Slot(
							link[_DIGITIZER, 0x51],
							link[_DIGITIZER, 0x42],
							link.get((_DIGITIZER, 0x47)),
							axes[reportID, link[_DESKTOP, 0x30]],
							axes[reportID, link[_DESKTOP, 0x31]],
						)
					)
				except KeyError:
					continue
			if count is not None and scan is not None and slots:
				reports[reportID] = _Report(count, scan, tuple(slots), tuple(clicks))
		return reports

	def read(self, report: bytes) -> Frame | None:
		if len(report) != self._reportSize or not report or (layout := self._reports.get(report[0])) is None:
			return None
		dataCount = c_ulong(len(self._items))
		try:
			check_HidP_status(
				hid.HidP_GetData,
				_INPUT,
				self._items,
				byref(dataCount),
				self._descriptor,
				report,
				len(report),
			)
			return layout.frame({item.DataIndex: item.u1.RawValue for item in self._items[: dataCount.value]})
		except (HidPError, KeyError, ValueError):
			self.recognizer.cancel()
			self.assembler.pending = None
			self.stroke = Stroke()
			self.customTaps = None
			return None


def _deviceHandles() -> tuple[int, ...]:
	count = UINT()
	itemSize = sizeof(rawInput.RAWINPUTDEVICELIST)
	if rawInput.GetRawInputDeviceList(None, byref(count), itemSize) == rawInput.UINT_ERROR:
		raise WinError(get_last_error())
	devices = (rawInput.RAWINPUTDEVICELIST * count.value)()
	length = rawInput.GetRawInputDeviceList(devices, byref(count), itemSize)
	if length == rawInput.UINT_ERROR:
		raise WinError(get_last_error())
	return tuple(
		item.device for item in devices[:length] if item.type == rawInput.RIM_TYPEHID and item.device
	)


class _PointerHold:
	def __init__(self):
		self._bounds = None
		self._timeout = None

	@staticmethod
	def _clipBounds():
		rect = RECT()
		if rawInput.GetClipCursor(byref(rect)):
			return rect.left, rect.top, rect.right, rect.bottom
		return None

	def start(self):
		if self._bounds is not None:
			return
		left = user32.GetSystemMetrics(SystemMetrics.X_VIRTUAL_SCREEN)
		top = user32.GetSystemMetrics(SystemMetrics.Y_VIRTUAL_SCREEN)
		right = left + user32.GetSystemMetrics(SystemMetrics.CX_VIRTUAL_SCREEN)
		bottom = top + user32.GetSystemMetrics(SystemMetrics.CY_VIRTUAL_SCREEN)
		# Leave any cursor confinement set by the foreground app in place.
		if self._clipBounds() != (left, top, right, bottom):
			return
		point = POINT()
		if not user32.GetPhysicalCursorPos(byref(point)):
			return
		bounds = (point.x, point.y, point.x, point.y)
		if rawInput.ClipCursor(byref(RECT(*bounds))):
			self._bounds = bounds
			self._timeout = wx.CallLater(_POINTER_HOLD_TIMEOUT_MS, self.release)

	def release(self):
		if self._timeout is not None:
			self._timeout.Stop()
			self._timeout = None
		if self._bounds is not None:
			if self._clipBounds() == self._bounds:
				rawInput.ClipCursor(None)
			self._bounds = None


def _feedStroke(receiver, device, frame, active, buttonDown, now, direction):
	stroke = getattr(device, "stroke", None)
	strokeHandler = receiver.strokeHandler
	started = stroke.started if stroke is not None else None
	sample = None
	if stroke is not None and (strokeHandler or frame.count >= 3 or started is not None):
		sample = stroke.feed(frame, device.aspect, buttonDown, now)
	recording = getattr(receiver, "recording", False)
	if sample is not None:
		pattern = sample.pattern
		inWindow = device.foreground == winUser.getForegroundWindow()
		if isinstance(pattern, dict) and pattern["gesture"] == "tap":
			if (strokeHandler or pattern["fingers"] == 3) and inWindow:
				TouchpadInput._queueTap(receiver, device, pattern["fingers"], started, now)
			else:
				device.customTaps = None
			return None
		device.customTaps = None
		if isinstance(pattern, dict) and pattern["fingers"] == 3:
			direction = APP_SWITCH_GESTURES.get(pattern["gesture"], direction)
		if strokeHandler and inWindow:
			direction = strokeHandler(pattern, direction, sample.drawing) or direction
	elif recording and strokeHandler and active and not device.recognizer.inContact:
		if device.foreground == winUser.getForegroundWindow():
			device.customTaps = None
			strokeHandler(None, None, None)
	return None if recording else direction


class TouchpadInput(CustomWindow):
	className = "TouchWizAccessInput"

	def __init__(self, handleGesture):
		self._devices: dict[int, _TouchpadDevice | None] = {}
		self._generation = 0
		self._handleGesture = handleGesture
		self._gestures = deque()
		self._dispatching = False
		self.fastHorizontal = False
		self.tapActivation = False
		self.strokeHandler = None
		self.recording = False
		self._recordingFlags = 0
		self.recordingLocal = False
		self.recordingGlobal = False
		self._windowsGestureCapture = WindowsGestureCapture()
		self._originalNativeFlags = 0
		self._rotorQuietUntil = 0.0
		self._mouseQuietUntil = 0.0
		self._pointerHold = _PointerHold()
		self._foreground = winUser.getForegroundWindow()
		super().__init__(parent=-3)  # HWND_MESSAGE
		try:
			self._register(rawInput.RIDEV_INPUTSINK | rawInput.RIDEV_DEVNOTIFY, self.handle)
		except OSError:
			self.destroy()
			raise

	@staticmethod
	def _register(flags, target):
		registration = rawInput.RAWINPUTDEVICE(_DIGITIZER, _TOUCHPAD, flags, target)
		if not rawInput.RegisterRawInputDevices(byref(registration), 1, sizeof(registration)):
			raise WinError(get_last_error())

	def close(self):
		self._pointerHold.release()
		self.stopRecording()
		self.restoreNativeGestures()
		if not self.handle:
			return
		try:
			self._register(rawInput.RIDEV_REMOVE, None)
		except OSError:
			log.debugWarning("Could not unregister TouchWiz Access raw input", exc_info=True)
		finally:
			self.destroy()
			self._devices.clear()
			self._gestures.clear()

	def reset(self):
		self._pointerHold.release()
		self._generation += 1
		self._gestures.clear()
		self._rotorQuietUntil = 0.0
		self._mouseQuietUntil = 0.0
		for device in self._devices.values():
			if device is not None:
				device.lastTap = None
				if hasattr(device, "stroke"):
					device.stroke = Stroke()
					device.customTaps = None
					if hasattr(device, "assembler"):
						device.assembler.pending = None
				if device.recognizer.inContact:
					device.recognizer.cancel()
				else:
					device.recognizer.reset()

	def foregroundChanged(self):
		foreground = winUser.getForegroundWindow()
		if foreground == self._foreground:
			return False
		self._foreground = foreground
		self.fastHorizontal = False
		self.reset()
		return True

	def suspendNativeGestures(self):
		parameters = rawInput.TouchpadParameters.read()
		if parameters is None:
			return False
		flags = parameters.options & rawInput.RECORDING_FLAGS
		if flags:
			parameters.options &= ~rawInput.RECORDING_FLAGS
			if not parameters.apply():
				return False
			self._originalNativeFlags |= flags
		self.tapActivation = True
		return True

	def restoreNativeGestures(self):
		self.tapActivation = False
		if self._originalNativeFlags:
			parameters = rawInput.TouchpadParameters.read()
			if parameters is not None:
				parameters.options |= self._originalNativeFlags
				if parameters.apply():
					self._originalNativeFlags = 0

	def startRecording(self):
		if self.recording:
			return
		self.recording = True
		parameters = rawInput.TouchpadParameters.read()
		if parameters is not None:
			flags = parameters.options & rawInput.RECORDING_FLAGS
			parameters.options &= ~rawInput.RECORDING_FLAGS
			self.recordingLocal = not flags or bool(parameters.apply())
			if self.recordingLocal:
				self._recordingFlags |= flags
		try:
			self.recordingGlobal = self._windowsGestureCapture.start()
		except OSError:
			log.debugWarning("Could not register Windows touchpad recording gestures", exc_info=True)
		if not self.recordingGlobal:
			self._windowsGestureCapture.close()

	def stopRecording(self):
		if self.recording:
			self.recording = False
			self._windowsGestureCapture.close()
		if self._recordingFlags:
			parameters = rawInput.TouchpadParameters.read()
			if parameters is not None:
				parameters.options |= self._recordingFlags
				if parameters.apply():
					self._recordingFlags = 0
		self.recordingLocal = self.recordingGlobal = False

	@property
	def rotorSpeechBlocked(self):
		return self._foreground == winUser.getForegroundWindow() and (
			monotonic() < self._rotorQuietUntil
			or any(device and device.recognizer.rotorInContact for device in self._devices.values())
		)

	@property
	def mouseReportingBlocked(self):
		return monotonic() < self._mouseQuietUntil or any(
			device and device.recognizer.inContact for device in self._devices.values()
		)

	def _getDevice(self, handle):
		if handle not in self._devices:
			try:
				self._devices[handle] = _TouchpadDevice(handle)
			except (HidPError, OSError, ValueError):
				self._devices[handle] = None
		return self._devices[handle]

	def hasCompatibleDevice(self):
		try:
			return any(self._getDevice(handle) is not None for handle in _deviceHandles())
		except OSError:
			return False

	def windowProc(self, hwnd, msg, wParam, lParam):
		# Fall through to DefWindowProc to release foreground WM_INPUT resources.
		if msg == rawInput.WM_INPUT_DEVICE_CHANGE:
			device = self._devices.pop(lParam, None)
			if device is not None and device.recognizer.inContact:
				self._pointerHold.release()
		elif msg == rawInput.WM_INPUT:
			self._onRawInput(lParam)

	def _onRawInput(self, inputHandle):
		if isLockScreenModeActive():
			return
		self.foregroundChanged()
		size = UINT()
		headerSize = sizeof(rawInput.RAWINPUTHEADER)
		read = rawInput.GetRawInputData
		if read(inputHandle, rawInput.RID_INPUT, None, byref(size), headerSize) != 0:
			return
		offset = headerSize + sizeof(rawInput.RAWHIDHEADER)
		if not offset < size.value <= _MAX_PACKET:
			return
		buffer = create_string_buffer(size.value)
		length = read(inputHandle, rawInput.RID_INPUT, buffer, byref(size), headerSize)
		if length == rawInput.UINT_ERROR or length < offset:
			return
		header = rawInput.RAWINPUTHEADER.from_buffer(buffer)
		if header.type != rawInput.RIM_TYPEHID or not header.device:
			return
		payload = rawInput.RAWHIDHEADER.from_buffer(buffer, headerSize)
		if (
			not payload.reportSize
			or not payload.reportCount
			or offset + payload.reportSize * payload.reportCount > length
		):
			return
		device = self._getDevice(header.device)
		if device is None:
			return
		for index in range(payload.reportCount):
			start = offset + index * payload.reportSize
			frame = device.read(buffer[start : start + payload.reportSize])
			if frame is None:
				continue
			self._feedDevice(device, frame)

	def _feedDevice(self, device, frame):
		if (assembler := getattr(device, "assembler", None)) is not None:
			frame = assembler.feed(frame)
			if frame is None:
				return
		recognizer = device.recognizer
		active = recognizer.inContact
		rotorWasInContact = recognizer.rotorInContact
		buttonDown = frame.clicked or any(
			winUser.getAsyncKeyState(key) & 0x8000
			for key in (winUser.VK_LBUTTON, winUser.VK_RBUTTON, winUser.VK_MBUTTON)
		)
		if buttonDown:
			recognizer.cancel()
			self._pointerHold.release()
		now = monotonic()
		strokeHandler = self.strokeHandler
		direction = recognizer.feed(
			frame, now, self.fastHorizontal and not strokeHandler, self.tapActivation and not strokeHandler
		)
		if buttonDown or direction:
			device.customTaps = None
		if recognizer.inContact and not active:
			if not buttonDown:
				self._pointerHold.start()
			device.foreground = winUser.getForegroundWindow()
			device.document = getDocument()
		elif active and not recognizer.inContact:
			self._pointerHold.release()
			# Windows can deliver the final pointer move after the contact ends.
			self._mouseQuietUntil = now + _MOUSE_EVENT_GRACE_SECONDS
		if (
			recognizer.rotorInContact
			and not rotorWasInContact
			and device.foreground is not None
			and not self.recording
		):
			wx.CallAfter(self._dispatch, "rotorStarted", self._generation, device.foreground, device.document)
		elif rotorWasInContact and not recognizer.rotorInContact:
			self._rotorQuietUntil = now + _ROTOR_SPEECH_GRACE_SECONDS
		if self.tapActivation and not strokeHandler:
			if direction == "tap":
				direction = self._pairTap(device, now)
			elif buttonDown or frame.count > 1 or direction or (active and not recognizer.inContact):
				device.lastTap = None
		direction = _feedStroke(self, device, frame, active, buttonDown, now, direction)
		if direction and device.foreground is not None:
			wx.CallAfter(self._dispatch, direction, self._generation, device.foreground, device.document)
		if not recognizer.inContact:
			device.foreground = None
			device.document = None

	def _queueTap(self, device, fingers, started=None, now=None):
		scope = (device.foreground, device.document, self._generation)
		previous = getattr(device, "customTaps", None)
		if now is None:
			now = monotonic()
		touchStart = now if started is None else started
		repetitions = 1
		if (
			previous
			and previous[0] == scope
			and previous[1] == fingers
			and previous[2] < 3
			and touchStart - previous[3] <= TAP_SERIES_MS / 1000
		):
			repetitions = previous[2] + 1
		pending = (scope, fingers, repetitions, now)
		device.customTaps = pending
		callback = getattr(self, "_finishTaps", None) or (
			lambda dev, item: TouchpadInput._finishTaps(self, dev, item)
		)
		wx.CallLater(0 if repetitions == 3 else TAP_SERIES_MS, callback, device, pending)

	def _finishTaps(self, device, pending):
		if not self.handle or getattr(device, "customTaps", None) is not pending:
			return
		(foreground, document, generation), fingers, repetitions, lastTapAt = pending
		if (
			generation != self._generation
			or foreground != winUser.getForegroundWindow()
			or document is not getDocument()
			or (self.strokeHandler is None and fingers != 3)
		):
			device.customTaps = None
			return
		if device.recognizer.inContact:
			if monotonic() - lastTapAt <= _POINTER_HOLD_TIMEOUT_MS / 1000:
				callback = getattr(self, "_finishTaps", None) or (
					lambda dev, item: TouchpadInput._finishTaps(self, dev, item)
				)
				wx.CallLater(60, callback, device, pending)
			else:
				device.customTaps = None
			return
		device.customTaps = None
		gesture = ("tap", "doubletap", "tripletap")[repetitions - 1]
		builtIn = _BUILT_IN_TAPS.get((fingers, gesture))
		direction = (
			self.strokeHandler({"fingers": fingers, "gesture": gesture}, builtIn, None)
			if self.strokeHandler
			else None
		)
		if not getattr(self, "recording", False) and (
			direction or (builtIn and (builtIn != "doubletap" or getattr(self, "tapActivation", False)))
		):
			self._dispatch(direction or builtIn, generation, foreground, document)

	@staticmethod
	def _pairTap(device, now):
		previous = device.lastTap
		if (
			previous is not None
			and now - previous[0] <= _DOUBLE_TAP_SECONDS
			and device.foreground == previous[1]
			and device.document is previous[2]
		):
			device.lastTap = None
			return "doubletap"
		device.lastTap = (now, device.foreground, device.document)
		return None

	def _dispatch(self, direction, generation, foreground=None, document=None):
		if foreground is None:
			foreground = winUser.getForegroundWindow()
		self._gestures.append((direction, generation, foreground, document))
		if self._dispatching:
			return
		# Accessibility providers can pump window messages during a move.
		# Finish that move before a nested callback changes the rotor or cursor.
		self._dispatching = True
		try:
			while self._gestures:
				direction, generation, foreground, document = self._gestures.popleft()
				if (
					self.handle
					and generation == self._generation
					and foreground == winUser.getForegroundWindow()
					and not isRunningOnSecureDesktop()
					and not isLockScreenModeActive()
					and not any(winUser.getAsyncKeyState(key) & 0x8000 for key in _MODIFIERS)
					and document is getDocument()
				):
					if direction == "doubletap":
						parameters = rawInput.TouchpadParameters.read()
						if parameters is None or parameters.options & rawInput.NATIVE_TAP_FLAGS:
							continue
					if (
						direction.startswith("swipe")
						or direction in _BUILT_IN_TAPS.values()
						or direction in APP_SWITCH_GESTURES.values()
					):
						self._rotorQuietUntil = 0.0
					self._handleGesture(direction)
		finally:
			self._dispatching = False
