import json
import threading
import unittest

import octri


class StandaloneEventTest(unittest.TestCase):
    def test_capture_event_is_idempotent_and_supports_open_self_hosted_ingest(self) -> None:
        captured = {}
        ready = threading.Event()
        original = octri._urlrequest.urlopen

        class Response:
            def close(self) -> None:
                pass

        def urlopen(request, timeout=0):
            captured["request"] = request
            captured["timeout"] = timeout
            ready.set()
            return Response()

        octri._urlrequest.urlopen = urlopen
        try:
            octri.init("https://monitoring.example.com", None, "project-1")
            octri.capture_event(
                "checkout.completed",
                event_id="event-123",
                tags={"plan": "growth"},
                context={"orderId": "order-1"},
            )
            self.assertTrue(ready.wait(1))
            request = captured["request"]
            body = json.loads(request.data)
            self.assertEqual(request.get_header("Idempotency-key"), "event-123")
            self.assertIsNone(request.get_header("Authorization"))
            self.assertEqual(body["message"], "checkout.completed")
            self.assertEqual(body["environment"], "project-1")
            self.assertEqual(body["tags"], {"octri.origin": "standalone", "plan": "growth"})
            self.assertEqual(captured["timeout"], 5)
        finally:
            octri._urlrequest.urlopen = original

    def test_traceparent_is_trimmed_and_zero_identifiers_are_rejected(self) -> None:
        trace = octri.trace_from_header(
            "  00-4BF92F3577B34DA6A3CE929D0E0E4736-00F067AA0BA902B7-01  "
        )
        self.assertEqual(trace.trace_id, "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(trace.parent_span_id, "00f067aa0ba902b7")

        for header in (
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        ):
            fresh = octri.trace_from_header(header)
            self.assertRegex(fresh.trace_id, r"^[0-9a-f]{32}$")
            self.assertNotEqual(fresh.trace_id, "0" * 32)
            self.assertIsNone(fresh.parent_span_id)

    def test_event_ids_are_non_blank_safe_and_bounded(self) -> None:
        captured = []
        original = octri._post_json

        def post(config, path, payload, idempotency_key):
            captured.append((payload, idempotency_key))

        octri._post_json = post
        try:
            octri.init("https://monitoring.example.com", None, "project-1")
            for event_id in ("  event-123  ", " \t ", "bad\r\nX: true", "x" * 257):
                octri.capture_event("test", event_id=event_id)

            self.assertEqual(captured[0][0]["eventId"], "event-123")
            self.assertEqual(captured[0][1], "event-123")
            for payload, key in captured[1:]:
                self.assertRegex(key, r"^[0-9a-f]{32}$")
                self.assertEqual(payload["eventId"], key)
        finally:
            octri._post_json = original

    def test_invalid_spans_and_unsafe_auth_are_suppressed(self) -> None:
        captured = []
        original_post = octri._post_json

        def post(config, path, payload, idempotency_key):
            captured.append(payload)

        octri._post_json = post
        try:
            octri.init("https://monitoring.example.com", None, "project-1")
            now = "2026-07-13T12:00:00Z"
            octri.capture_span(trace_id="", span_id="span-1", name="test", start_time=now)
            octri.capture_span(trace_id="trace-1", span_id="span-1", name=" ", start_time=now)
            octri.capture_span(
                trace_id="trace-1", span_id="span-1", name="test", start_time="not-a-date"
            )
            octri.capture_span(
                trace_id="0" * 32, span_id="0" * 16, name="test", start_time=now
            )
            self.assertEqual(captured, [])
        finally:
            octri._post_json = original_post

        called = threading.Event()
        original_urlopen = octri._urlrequest.urlopen

        def urlopen(request, timeout=0):
            called.set()
            raise AssertionError("unsafe auth must not be sent")

        octri._urlrequest.urlopen = urlopen
        try:
            octri.init(
                "https://monitoring.example.com", "token\r\nX-Injected: true", "project-1"
            )
            octri.capture_event("suppressed")
            self.assertFalse(called.wait(0.05))
        finally:
            octri._urlrequest.urlopen = original_urlopen

    def test_thread_exhaustion_and_invalid_caller_data_are_best_effort(self) -> None:
        original_thread = octri.threading.Thread

        def exhausted(*args, **kwargs):
            raise RuntimeError("thread limit")

        octri.threading.Thread = exhausted
        try:
            octri.init("https://monitoring.example.com", None, "project-1")
            octri.capture_event("safe")
            octri.capture_event("bad tags", tags=[])  # type: ignore[arg-type]

            class HostileError(Exception):
                def __str__(self) -> str:
                    raise RuntimeError("broken error string")

            octri.capture_error(HostileError())
        finally:
            octri.threading.Thread = original_thread


if __name__ == "__main__":
    unittest.main()
