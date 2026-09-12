"""Unit tests for the parts that decide what ends up on the page."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from accidental_dj import db
from accidental_dj.camelot import compatible_codes, keys_compatible, to_camelot
from accidental_dj.matching import (Candidate, build_pairs, ratio_label,
                                    tempo_match)
from accidental_dj.report import payload, render
from accidental_dj.spotify import flatten
from accidental_dj.textnorm import clean_title, primary_artist


class TestCamelot(unittest.TestCase):
    def test_known_keys(self):
        cases = {
            "Am": "8A", "A minor": "8A", "C": "8B", "F#m": "11A", "Gbm": "11A",
            "Db": "3B", "C#": "3B", "Bb": "6B", "Bbm": "3A", "B": "1B",
            "Bm": "10A", "Abm": "1A", "E major": "12B", "Ebm": "2A",
        }
        for raw, expected in cases.items():
            self.assertEqual(to_camelot(raw), expected, raw)

    def test_unreadable_keys(self):
        for raw in ("", None, "???", "H", "xyz"):
            self.assertIsNone(to_camelot(raw))

    def test_compatibility_rules(self):
        self.assertTrue(keys_compatible("8A", "8A"))    # same code
        self.assertTrue(keys_compatible("8A", "8B"))    # relative major/minor
        self.assertTrue(keys_compatible("8A", "9A"))    # one step up
        self.assertTrue(keys_compatible("8A", "7A"))    # one step down
        self.assertFalse(keys_compatible("8A", "10A"))  # two steps
        self.assertFalse(keys_compatible("8A", "9B"))   # diagonal
        self.assertFalse(keys_compatible("8A", None))

    def test_wraparound_between_12_and_1(self):
        self.assertTrue(keys_compatible("12A", "1A"))
        self.assertTrue(keys_compatible("1B", "12B"))
        self.assertIn("1A", compatible_codes("12A"))
        self.assertIn("12B", compatible_codes("1B"))


class TestTitles(unittest.TestCase):
    def test_strips_noise(self):
        cases = {
            "Dreams - 2004 Remaster": "Dreams",
            "Africa - Remastered 2015": "Africa",
            "Hurt (Live at Folsom)": "Hurt",
            "Mr. Brightside (Radio Edit)": "Mr. Brightside",
            "Sicko Mode (feat. Drake)": "Sicko Mode",
            "Jolene (1974)": "Jolene",
            "One More Time (Radio Edit) [Remastered]": "One More Time",
            "Gimme Shelter (Remastered 2009) - Mono Version": "Gimme Shelter",
        }
        for raw, expected in cases.items():
            self.assertEqual(clean_title(raw), expected, raw)

    def test_keeps_meaningful_parentheses(self):
        self.assertEqual(clean_title("Levels - Skrillex Remix"), "Levels - Skrillex Remix")
        self.assertEqual(clean_title("Blinding Lights"), "Blinding Lights")

    def test_primary_artist_keeps_band_names_intact(self):
        self.assertEqual(primary_artist("Simon & Garfunkel"), "Simon & Garfunkel")
        self.assertEqual(primary_artist("Earth, Wind & Fire"), "Earth, Wind & Fire")
        self.assertEqual(primary_artist("AC/DC"), "AC/DC")
        self.assertEqual(primary_artist("Calvin Harris feat. Rihanna"), "Calvin Harris")


class TestTempo(unittest.TestCase):
    def test_direct_match_within_tolerance(self):
        drift, ratio = tempo_match(128, 130, 3.0)
        self.assertEqual(ratio, 1.0)
        self.assertLess(drift, 2.0)

    def test_out_of_tolerance(self):
        self.assertIsNone(tempo_match(128, 100, 3.0))

    def test_half_and_double_time(self):
        self.assertEqual(tempo_match(128, 64, 3.0), (0.0, 2.0))    # B at half tempo
        self.assertEqual(tempo_match(85, 170, 3.0), (0.0, 0.5))    # B at double tempo
        self.assertEqual(ratio_label(2.0), "half-time")
        self.assertEqual(ratio_label(0.5), "double-time")
        self.assertEqual(ratio_label(1.0), "")

    def test_half_double_can_be_disabled(self):
        self.assertIsNone(tempo_match(128, 64, 3.0, allow_half_double=False))

    def test_direct_beats_half_double_on_a_tie(self):
        drift, ratio = tempo_match(120, 120, 5.0)
        self.assertEqual((drift, ratio), (0.0, 1.0))


class TestPairs(unittest.TestCase):
    def candidates(self):
        return [
            Candidate(0, "a", 128.0, "8A", "one"),
            Candidate(1, "b", 129.0, "8B", "two"),
            Candidate(2, "c", 64.5, "9A", "three"),
            Candidate(3, "d", 128.5, "8A", "one"),   # same artist as 0
            Candidate(4, "e", 128.0, "3B", "four"),  # incompatible key
        ]

    def test_excludes_same_artist_by_default(self):
        pairs = build_pairs(self.candidates(), tolerance=3.0)
        self.assertNotIn({0, 3}, [{p.a, p.b} for p in pairs])

    def test_allows_same_artist_with_flag(self):
        pairs = build_pairs(self.candidates(), tolerance=3.0, allow_same_artist=True)
        self.assertIn({0, 3}, [{p.a, p.b} for p in pairs])

    def test_never_pairs_incompatible_keys(self):
        pairs = build_pairs(self.candidates(), tolerance=3.0, allow_same_artist=True)
        self.assertFalse(any(4 in (p.a, p.b) for p in pairs))

    def test_ranked_by_drift_then_same_key(self):
        pairs = build_pairs(self.candidates(), tolerance=6.0, allow_same_artist=True)
        drifts = [p.drift for p in pairs]
        self.assertEqual(drifts, sorted(drifts))
        tied = [p for p in pairs if abs(p.drift - pairs[0].drift) < 1e-9]
        if len(tied) > 1:
            same_key_flags = [p.same_key for p in tied]
            self.assertEqual(same_key_flags, sorted(same_key_flags, reverse=True))

    def test_cap_limits_appearances(self):
        pairs = build_pairs(self.candidates(), tolerance=6.0, max_per_track=1,
                            allow_same_artist=True)
        counts = {}
        for pair in pairs:
            counts[pair.a] = counts.get(pair.a, 0) + 1
            counts[pair.b] = counts.get(pair.b, 0) + 1
        self.assertTrue(all(count <= 1 for count in counts.values()), counts)

    def test_cap_of_zero_means_uncapped(self):
        capped = build_pairs(self.candidates(), tolerance=6.0, max_per_track=1,
                             allow_same_artist=True)
        uncapped = build_pairs(self.candidates(), tolerance=6.0, max_per_track=0,
                               allow_same_artist=True)
        self.assertGreater(len(uncapped), len(capped))

    def test_each_pair_appears_once(self):
        pairs = build_pairs(self.candidates(), tolerance=6.0, allow_same_artist=True)
        keys = [tuple(sorted((p.a, p.b))) for p in pairs]
        self.assertEqual(len(keys), len(set(keys)))


class TestSpotifyFlatten(unittest.TestCase):
    def test_flatten(self):
        row = flatten({
            "added_at": "2021-05-01T00:00:00Z",
            "track": {
                "id": "abc", "name": "Jolene",
                "artists": [{"name": "Dolly Parton"}, {"name": "Someone"}],
                "album": {"name": "Jolene", "release_date": "1974-02-04"},
                "duration_ms": 162000, "popularity": 71,
                "external_ids": {"isrc": "USSM17400001"},
            },
        })
        self.assertEqual(row["primary_artist"], "Dolly Parton")
        self.assertEqual(row["artist"], "Dolly Parton, Someone")
        self.assertEqual(row["release_year"], 1974)
        self.assertEqual(row["isrc"], "USSM17400001")

    def test_skips_local_files(self):
        self.assertIsNone(flatten({"track": {"id": None, "is_local": True}}))

    def test_handles_year_only_release_date(self):
        row = flatten({"track": {"id": "x", "name": "T", "artists": [{"name": "A"}],
                                 "album": {"release_date": "1968"}}})
        self.assertEqual(row["release_year"], 1968)


class TestDatabase(unittest.TestCase):
    def test_misses_are_cached_and_retryable(self):
        conn = db.connect(":memory:")
        db.upsert_track(conn, dict(id="1", title="T", artist="A", primary_artist="A",
                                   album=None, release_year=None, duration_ms=None,
                                   popularity=None, isrc=None, added_at=None,
                                   synced_at=db.now()))
        self.assertEqual(len(db.pending_tracks(conn)), 1)
        db.record_audio(conn, "1", "no_match", detail="no result")
        self.assertEqual(len(db.pending_tracks(conn)), 0)            # skipped on re-run
        self.assertEqual(len(db.pending_tracks(conn, retry_misses=True)), 1)
        db.record_audio(conn, "1", "ok", tempo=128.0, key_raw="Am", camelot="8A")
        self.assertEqual(len(db.pending_tracks(conn, retry_misses=True)), 0)
        row = conn.execute("SELECT attempts, status FROM audio").fetchone()
        self.assertEqual((row["attempts"], row["status"]), (2, "ok"))


class TestReport(unittest.TestCase):
    def test_renders_self_contained_page(self):
        rows = [
            {"index": 0, "id": "a", "title": "Jolene", "artist": "Dolly Parton",
             "album": "Jolene", "release_year": 1974, "duration_ms": 162000,
             "popularity": 78, "bpm": 110.0, "camelot": "8A", "time_sig": "4/4"},
            {"index": 1, "id": "b", "title": "</script> Attack", "artist": "Trap Guy",
             "album": None, "release_year": 2017, "duration_ms": 180000,
             "popularity": 60, "bpm": 111.0, "camelot": "8A", "time_sig": "4/4"},
        ]
        pairs = build_pairs([
            Candidate(0, "a", 110.0, "8A", "dolly parton"),
            Candidate(1, "b", 111.0, "8A", "trap guy"),
        ], tolerance=3.0)
        data = payload(rows, pairs, tolerance=3.0, max_per_track=6,
                       allow_same_artist=False, include_half_double=True,
                       enriched_count=2)
        self.assertEqual(len(data["pairs"]), 1)

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.html")
            render(data, out)
            with open(out, encoding="utf-8") as handle:
                html = handle.read()
        # No external resources, and the required attribution is present.
        self.assertNotIn("<script src", html)
        self.assertIn("getsongbpm.com", html)
        # A title containing </script> must not break out of the JSON island.
        self.assertNotIn("</script> Attack", html)
        island = html.split('<script id="payload" type="application/json">')[1]
        island = island.split("</script>")[0]
        self.assertEqual(json.loads(island)["tracks"][1]["t"], "</script> Attack")


if __name__ == "__main__":
    unittest.main(verbosity=2)
