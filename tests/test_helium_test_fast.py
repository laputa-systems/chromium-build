"""The headless load check counts every crashed target and recognises SIGSYS exits."""

import unittest

import helium_test_fast
from browser_test_support import CdpConnection


def connection_with(events):
    connection = CdpConnection(None)
    connection.events = events
    return connection


def crashed(error_code):
    return {"method": "Target.targetCrashed", "params": {"targetId": "T", "status": "crashed", "errorCode": error_code}}


class CrashedTargetsTest(unittest.TestCase):
    def test_sigsys_wait_status_is_recognised_with_or_without_core_dump(self):
        connection = connection_with([crashed(31), crashed(31 | 0x80)])
        self.assertEqual([crash["sigsys"] for crash in helium_test_fast.crashed_targets(connection)], [True, True])

    def test_other_deaths_are_crashes_but_not_sigsys(self):
        connection = connection_with([crashed(9), crashed(11 | 0x80), crashed(0)])
        crashes = helium_test_fast.crashed_targets(connection)
        self.assertEqual(len(crashes), 3)
        self.assertFalse(any(crash["sigsys"] for crash in crashes))

    def test_crash_events_are_consumed_and_others_kept(self):
        other = {"method": "Target.targetCreated", "params": {}}
        connection = connection_with([other, crashed(31)])
        self.assertEqual(len(helium_test_fast.crashed_targets(connection)), 1)
        self.assertEqual(connection.events, [other])
        self.assertEqual(helium_test_fast.crashed_targets(connection), [])


if __name__ == "__main__":
    unittest.main()
