import json
import queue
import unittest

import octri


class ScrubTest(unittest.TestCase):
    """Every test captures the payload the reporter thread would have posted."""

    def setUp(self) -> None:
        self.posted: "queue.Queue" = queue.Queue()
        self._original = octri._urlrequest.urlopen

        class Response:
            def close(self) -> None:
                pass

        def urlopen(request, timeout=0):
            self.posted.put(json.loads(request.data))
            return Response()

        octri._urlrequest.urlopen = urlopen
        octri.init("https://monitoring.example.com", None, "project-1")

    def tearDown(self) -> None:
        octri._urlrequest.urlopen = self._original
        octri.set_before_send(None)
        octri._extra_scrub_keys.clear()

    def capture(self, message: str, **kwargs) -> dict:
        octri.capture_event(message, **kwargs)
        return self.posted.get(timeout=2)

    # ── Keys ──────────────────────────────────────────────────────────────────

    def test_credential_shaped_keys_are_redacted_however_they_are_spelled(self) -> None:
        body = self.capture(
            "checkout failed",
            context={
                "api_key": "sk_live_1",
                "apiKey": "sk_live_2",
                "X-API-KEY": "sk_live_3",
                "stripe_secret_key": "sk_live_4",
                "Authorization": "Bearer abc",
                "refresh_token": "rt_1",
                "cookie": "sid=1",
                "orderId": "A-1024",
            },
        )

        context = body["context"]
        for key in ("api_key", "apiKey", "X-API-KEY", "stripe_secret_key",
                    "Authorization", "refresh_token", "cookie"):
            self.assertEqual(context[key], "[redacted]", key)
        self.assertEqual(context["orderId"], "A-1024")

    def test_nested_and_list_values_are_redacted_too(self) -> None:
        body = self.capture(
            "upstream rejected the call",
            context={"upstream": {"headers": [{"authorization": "Bearer abc"}]}},
        )

        self.assertEqual(body["context"]["upstream"]["headers"][0]["authorization"], "[redacted]")

    def test_keys_that_merely_contain_a_scrub_word_are_kept(self) -> None:
        body = self.capture("import failed", context={"author": "ada", "wildcard": "*.csv"})

        self.assertEqual(body["context"], {"author": "ada", "wildcard": "*.csv"})

    def test_extra_scrub_fields_are_additive(self) -> None:
        octri.add_scrub_fields("account_number")
        body = self.capture(
            "payout failed",
            context={"accountNumber": "12345678", "orderId": "A-1024"},
        )

        self.assertEqual(body["context"]["accountNumber"], "[redacted]")
        self.assertEqual(body["context"]["orderId"], "A-1024")

    # ── Free text ─────────────────────────────────────────────────────────────

    def test_secrets_that_leaked_into_a_message_are_stripped(self) -> None:
        body = self.capture("401 from billing: Authorization: Bearer sk_live_abc123 rejected")

        self.assertNotIn("sk_live_abc123", body["message"])
        self.assertIn("[redacted]", body["message"])

    def test_a_jwt_in_a_message_is_stripped(self) -> None:
        body = self.capture("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.7Hk2 expired")

        self.assertEqual(body["message"], "token [redacted] expired")

    def test_an_email_in_a_message_is_stripped(self) -> None:
        body = self.capture("no account for ada@example.com")

        self.assertEqual(body["message"], "no account for [redacted]")

    def test_a_card_number_is_stripped_but_an_order_number_is_not(self) -> None:
        body = self.capture("charge 4242 4242 4242 4242 failed for order 1234567890123")

        self.assertNotIn("4242", body["message"])
        self.assertIn("1234567890123", body["message"])

    def test_an_error_message_and_its_stack_are_scrubbed(self) -> None:
        try:
            raise ValueError("mail to ada@example.com bounced")
        except ValueError as error:
            octri.capture_error(error)
        body = self.posted.get(timeout=2)

        self.assertEqual(body["error"]["message"], "mail to [redacted] bounced")
        self.assertNotIn("ada@example.com", json.dumps(body))

    # ── The user field ────────────────────────────────────────────────────────

    # The identity the dashboard keys on is "id", which survives. Direct
    # identifiers under the user are redacted like they are in every generated SDK.
    def test_user_id_survives_but_user_credentials_and_identifiers_do_not(self) -> None:
        body = self.capture(
            "profile update failed",
            user={"id": "u_1", "email": "ada@example.com", "session_token": "st_1", "customerPhone": "+1 555 0100"},
        )

        self.assertEqual(body["user"]["id"], "u_1")
        self.assertEqual(body["user"]["email"], "[redacted]")
        self.assertEqual(body["user"]["session_token"], "[redacted]")
        self.assertEqual(body["user"]["customerPhone"], "[redacted]")

    def test_identifier_words_inside_longer_keys_are_redacted(self) -> None:
        body = self.capture(
            "checkout failed",
            context={
                "billingAddress": {"line1": "1 High St"},
                "shipping_first_name": "Ada",
                "avatarUrl": "https://cdn.example.com/a.png",
                "queryTimeMs": 12,
            },
        )

        self.assertEqual(body["context"]["billingAddress"], "[redacted]")
        self.assertEqual(body["context"]["shipping_first_name"], "[redacted]")
        self.assertEqual(body["context"]["avatarUrl"], "https://cdn.example.com/a.png")
        self.assertEqual(body["context"]["queryTimeMs"], 12)

    # ── before_send ───────────────────────────────────────────────────────────

    def test_before_send_can_edit_a_payload_and_redaction_still_runs_after_it(self) -> None:
        def hook(payload):
            payload["context"] = dict(payload.get("context") or {}, note="call ada@example.com")
            return payload

        octri.set_before_send(hook)
        body = self.capture("build failed", context={"step": "compile"})

        self.assertEqual(body["context"]["step"], "compile")
        self.assertEqual(body["context"]["note"], "call [redacted]")

    def test_before_send_returning_none_drops_the_event(self) -> None:
        octri.set_before_send(lambda payload: None if payload["message"] == "noise" else payload)
        octri.capture_event("noise")
        body = self.capture("signal")

        self.assertEqual(body["message"], "signal")

    # ── Cycles ────────────────────────────────────────────────────────────────

    def test_a_cyclic_context_is_marked_instead_of_losing_the_event(self) -> None:
        circular = {}
        circular["self"] = circular
        body = self.capture("circular", context=circular)

        self.assertEqual(body["context"]["self"], "[circular]")


if __name__ == "__main__":
    unittest.main()
