#!/usr/bin/env python3
"""Tests for navigation drive payload mapping (Pi motor convention)."""

from __future__ import annotations

import unittest

from local_planner import DriveCommand
from drive_payload import drive_to_payload


class DriveToPayloadTests(unittest.TestCase):
    def test_forward_is_negative_y(self) -> None:
        payload = drive_to_payload(DriveCommand(linear=0.22, angular=0.0, phase="roam"))
        self.assertEqual(payload["drive"]["y"], -0.22)
        self.assertEqual(payload["drive"]["x"], 0.0)

    def test_never_commands_backward(self) -> None:
        payload = drive_to_payload(DriveCommand(linear=0.18, angular=0.55, phase="escape"))
        self.assertLessEqual(payload["drive"]["y"], 0.0)

    def test_skid_steer_clamps_turn_while_moving(self) -> None:
        payload = drive_to_payload(DriveCommand(linear=0.18, angular=0.55, phase="escape"))
        forward = -payload["drive"]["y"]
        self.assertLessEqual(abs(payload["drive"]["x"]), forward)

    def test_turn_in_place_allows_full_x(self) -> None:
        payload = drive_to_payload(DriveCommand(linear=0.0, angular=0.5, phase="recovery"))
        self.assertEqual(payload["drive"]["y"], 0.0)
        self.assertEqual(payload["drive"]["x"], 0.5)


if __name__ == "__main__":
    unittest.main()
