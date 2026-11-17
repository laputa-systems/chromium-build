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


class NavigationTransport:
    def __init__(self):
        self.messages = []
        self.fixture_ready = False

    def send(self, command):
        if command["method"] == "Page.enable":
            self.messages.extend([
                {"method": "Page.frameNavigated", "params": {"frame": {"id": "F", "loaderId": "old"}}},
                {"method": "Page.domContentEventFired", "params": {"timestamp": 1}},
            ])
        if command["method"] == "Page.setLifecycleEventsEnabled":
            self.messages.append({"method": "Page.lifecycleEvent", "params": {"frameId": "F", "loaderId": "old", "name": "DOMContentLoaded"}})
        result = {"frameId": "F", "loaderId": "new"} if command["method"] == "Page.navigate" else {}
        self.messages.append({"id": command["id"], "result": result})
        if command["method"] == "Page.navigate":
            self.messages.extend([
                {"method": "Page.frameNavigated", "params": {"frame": {"id": "F", "loaderId": "new"}}},
                {"method": "Page.lifecycleEvent", "params": {"frameId": "F", "loaderId": "new", "name": "DOMContentLoaded"}},
            ])

    def receive(self):
        message = self.messages.pop(0)
        if message.get("method") == "Page.lifecycleEvent" and message["params"]["loaderId"] == "new":
            self.fixture_ready = True
        return message


class NavigationTest(unittest.TestCase):
    def test_ignores_previous_document_events_for_the_same_frame(self):
        transport = NavigationTransport()
        helium_test_fast.navigate(CdpConnection(transport), "https://fixture.invalid/")
        self.assertTrue(transport.fixture_ready)


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
