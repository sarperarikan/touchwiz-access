# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Recognize custom touchpad taps, swipes and single-finger drawings."""

from bisect import bisect_left
from dataclasses import dataclass, replace
from itertools import pairwise
from math import dist, hypot, sqrt

from .preferences import SAMPLE_COUNT

MATCH_LIMIT = 0.18
MAX_FINGERS = 5
TAP_SERIES_MS = 350
_TAP_DURATION = 0.28
_TAP_TRAVEL = 0.045
_SWIPE_DISTANCE = 0.075
_TAP_EDGE_DEPTH = 1 / 3


def signature(points):
	if len(points) < 2:
		return None
	lengths = [0.0]
	for previous, current in pairwise(points):
		lengths.append(lengths[-1] + dist(previous, current))
	xs, ys = zip(*points)
	left, top = min(xs), min(ys)
	scale = max(max(xs) - left, max(ys) - top)
	if scale < 0.08 or lengths[-1] < 0.10:
		return None
	samples = []
	for step in range(SAMPLE_COUNT):
		distance = lengths[-1] * step / (SAMPLE_COUNT - 1)
		index = min(len(points) - 1, max(1, bisect_left(lengths, distance)))
		before, after = points[index - 1], points[index]
		span = lengths[index] - lengths[index - 1]
		ratio = (distance - lengths[index - 1]) / span if span else 0
		x = before[0] + (after[0] - before[0]) * ratio
		y = before[1] + (after[1] - before[1]) * ratio
		samples.append(((x - left) / scale, (y - top) / scale))
	return samples


def distance(first, second):
	if isinstance(first, dict) or isinstance(second, dict):
		return 0.0 if first == second else float("inf")
	return sqrt(sum(dist(a, b) ** 2 for a, b in zip(first, second, strict=True)) / SAMPLE_COUNT)


class ReportAssembler:
	"""Join contact slots reported across packets with the same HID scan time."""

	def __init__(self):
		self.pending = None

	def feed(self, frame):
		previous, self.pending = self.pending, None
		if previous and not frame.count and previous.scan == frame.scan:
			frame = replace(
				previous,
				contacts=previous.contacts + frame.contacts,
				anyTip=previous.anyTip or frame.anyTip,
				clicked=previous.clicked or frame.clicked,
				slots=previous.slots + frame.slots,
			)
		if 1 < frame.count <= MAX_FINGERS and frame.slots < frame.count:
			self.pending = frame
			return None
		return frame


@dataclass(frozen=True)
class Sample:
	pattern: dict | list
	drawing: list | None = None
	tapRegion: str | None = None


class Stroke:
	def __init__(self):
		self.points = []
		self.blocked = False
		self.started = None
		self.origin = {}
		self.last = {}
		self.maxTravel = 0.0
		self.maxContacts = 0
		self.edgeTapBlocked = False

	def feed(self, frame, aspect, clicked, now):
		if clicked or frame.count > MAX_FINGERS:
			self.blocked = True
		if not frame.anyTip:
			result = self._finish(now, aspect)
			self.__init__()
			return result
		contacts = frame.contacts or ((frame.contact,) if frame.contact else ())
		self.maxContacts = max(self.maxContacts, frame.count, len(contacts))
		contacts = tuple(contact for contact in contacts if contact.confident)
		if len(contacts) > MAX_FINGERS:
			self.blocked = True
		if self.blocked or not contacts:
			return None
		if self.started is None:
			self.started = now
		current = {contact.id: (contact.x, contact.y * aspect) for contact in contacts}
		if len(current) > len(self.origin):
			self.origin = current
			self.points.clear()
			self.maxTravel = 0.0
		if current.keys() != self.origin.keys():
			if not current.keys() < self.origin.keys():
				self.blocked = True
			return None
		self.last = current
		self.maxTravel = max(self.maxTravel, *(dist(current[key], self.origin[key]) for key in current))
		if len(current) == 1 and len(self.points) < 1024:
			point = next(iter(current.values()))
			if not self.points or point != self.points[-1]:
				self.points.append(point)
		return None

	def _tapRegion(self, aspect):
		topEdge = aspect * _TAP_EDGE_DEPTH
		bottomEdge = aspect - topEdge
		positions = (*self.origin.values(), *self.last.values())
		if all(y <= topEdge for _, y in positions):
			return "top"
		if all(y >= bottomEdge for _, y in positions):
			return "bottom"
		return None

	def _finish(self, now, aspect):
		if self.blocked or self.started is None or not self.last:
			return None
		fingers = len(self.origin)
		if now - self.started <= _TAP_DURATION and self.maxTravel <= _TAP_TRAVEL:
			region = self._tapRegion(aspect) if fingers == self.maxContacts == 2 and not self.edgeTapBlocked else None
			return Sample({"fingers": fingers, "gesture": "tap"}, tapRegion=region)
		drawing = signature(self.points) if fingers == 1 else None
		deltas = [
			(self.last[key][0] - point[0], self.last[key][1] - point[1]) for key, point in self.origin.items()
		]
		dx = sum(x for x, _ in deltas) / fingers
		dy = sum(y for _, y in deltas) / fingers
		axis = 0 if abs(dx) >= abs(dy) else 1
		along, across = (dx, dy) if axis == 0 else (dy, dx)
		straight = (
			drawing is None
			or all(
				abs((dy * (x - self.points[0][0]) - dx * (y - self.points[0][1])) / hypot(dx, dy)) <= 0.045
				for x, y in self.points
			)
			if along
			else False
		)
		if (
			abs(along) >= _SWIPE_DISTANCE
			and abs(across) <= abs(along) * 0.8
			and straight
			and all(delta[axis] * along > 0 and abs(delta[axis]) >= 0.04 for delta in deltas)
		):
			directions = ("swipeleft", "swiperight") if axis == 0 else ("swipeup", "swipedown")
			return Sample({"fingers": fingers, "gesture": directions[along > 0]}, drawing)
		return Sample(drawing) if drawing else None
