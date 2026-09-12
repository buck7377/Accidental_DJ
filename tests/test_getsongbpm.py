"""GetSongBPM client tests against a mocked HTTP layer (no API key needed)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from accidental_dj import getsongbpm
from accidental_dj.getsongbpm import GetSongBPMClient, GetSongBPMError


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def client_with(responses):
    """Client whose session returns the queued responses in order."""
    client = GetSongBPMClient("test-key", delay=0)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        result = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    client.session.get = fake_get
    return client, calls


SEARCH_HIT = {"search": [{
    "id": "99", "title": "Jolene", "tempo": "110", "key_of": "Am",
    "time_sig": "4/4", "artist": {"name": "Dolly Parton"},
}]}


class TestLookup(unittest.TestCase):
    def test_requires_an_api_key(self):
        with self.assertRaises(GetSongBPMError) as ctx:
            GetSongBPMClient("")
        self.assertIn("getsongbpm.com/api", str(ctx.exception))

    def test_successful_lookup(self):
        client, calls = client_with([FakeResponse(SEARCH_HIT)])
        result = client.lookup("Jolene - 2015 Remaster", "Dolly Parton feat. Someone")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.tempo, 110.0)
        self.assertEqual(result.camelot, "8A")
        self.assertEqual(result.time_sig, "4/4")
        # The query is built from the cleaned title and the primary artist only.
        self.assertEqual(calls[0][1]["lookup"], "song:Jolene artist:Dolly Parton")
        self.assertEqual(calls[0][1]["api_key"], "test-key")

    def test_band_names_containing_commas_reach_the_api_intact(self):
        # sync stores Spotify's artists[0], so a comma inside one artist name
        # is part of the name and must not be truncated.
        client, calls = client_with([FakeResponse({"search": []})])
        client.lookup("September", "Earth, Wind & Fire")
        self.assertEqual(calls[0][1]["lookup"], "song:September artist:Earth, Wind & Fire")

    def test_falls_back_to_song_endpoint_when_search_lacks_analysis(self):
        thin = {"search": [{"id": "99", "title": "Jolene", "artist": {"name": "Dolly Parton"}}]}
        detail = {"song": {"id": "99", "title": "Jolene", "tempo": "110",
                           "key_of": "Am", "time_sig": "4/4",
                           "artist": {"name": "Dolly Parton"}}}
        client, calls = client_with([FakeResponse(thin), FakeResponse(detail)])
        result = client.lookup("Jolene", "Dolly Parton")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.tempo, 110.0)
        self.assertEqual(result.camelot, "8A")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1][0].endswith("/song/"))
        self.assertEqual(calls[1][1]["id"], "99")

    def test_no_result_is_a_cacheable_miss(self):
        client, _ = client_with([FakeResponse({"search": {"error": "no result"}})])
        result = client.lookup("Nonexistent Song", "Nobody")
        self.assertEqual(result.status, "no_match")
        self.assertEqual(result.detail, "no result")

    def test_empty_search_list_is_a_miss(self):
        client, _ = client_with([FakeResponse({"search": []})])
        self.assertEqual(client.lookup("X", "Y").status, "no_match")

    def test_rejects_a_confidently_wrong_match(self):
        wrong = {"search": [{"id": "1", "title": "Completely Different Song",
                             "tempo": "90", "key_of": "C",
                             "artist": {"name": "Another Band Entirely"}}]}
        client, _ = client_with([FakeResponse(wrong)])
        result = client.lookup("Jolene", "Dolly Parton")
        self.assertEqual(result.status, "no_match")
        self.assertIn("no confident match", result.detail)

    def test_match_with_no_tempo_or_key_on_record(self):
        bare = {"search": [{"id": "7", "title": "Jolene", "artist": {"name": "Dolly Parton"}}]}
        client, _ = client_with([FakeResponse(bare), FakeResponse({"song": {"id": "7"}})])
        result = client.lookup("Jolene", "Dolly Parton")
        self.assertEqual(result.status, "no_match")
        self.assertIn("no tempo or key", result.detail)

    def test_bad_api_key_raises_instead_of_caching(self):
        client, _ = client_with([FakeResponse(None, status_code=403)])
        with self.assertRaises(GetSongBPMError) as ctx:
            client.lookup("Jolene", "Dolly Parton")
        self.assertIn("GETSONGBPM_API_KEY", str(ctx.exception))

    def test_network_failure_is_recorded_as_an_error(self):
        client, calls = client_with([requests.ConnectionError("boom")])
        result = client.lookup("Jolene", "Dolly Parton")
        self.assertEqual(result.status, "error")
        self.assertIn("network error", result.detail)
        self.assertEqual(len(calls), 3)  # retried before giving up

    def test_retries_then_succeeds_after_a_429(self):
        responses = [FakeResponse(None, status_code=429), FakeResponse(SEARCH_HIT)]
        client, calls = client_with(responses)
        with mock.patch.object(getsongbpm.time, "sleep"):
            result = client.lookup("Jolene", "Dolly Parton")
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(calls), 2)

    def test_empty_title_after_cleaning_is_not_queried(self):
        client, calls = client_with([FakeResponse(SEARCH_HIT)])
        result = client.lookup("(Live)", "")
        self.assertEqual(result.status, "no_match")
        self.assertEqual(len(calls), 0)


class TestRateLimiter(unittest.TestCase):
    def test_waits_between_requests(self):
        limiter = getsongbpm.RateLimiter(delay=1.3)
        with mock.patch.object(getsongbpm.time, "sleep") as sleep, \
                mock.patch.object(getsongbpm.time, "monotonic", side_effect=[0, 0.3, 0.3]):
            limiter.wait()   # first call does not wait
            limiter.wait()   # 0.3s elapsed, so it must sleep the remaining 1.0s
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args[0][0], 1.0, places=6)

    def test_default_delay_is_one_point_three_seconds(self):
        self.assertEqual(getsongbpm.DEFAULT_DELAY, 1.3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
