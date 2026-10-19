import json, os, sys, tempfile, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "updater"))
sys.path.insert(0, HERE)
import hub, make_fixtures


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        make_fixtures.make(cls.dir)
        with open(os.path.join(cls.dir, "feeds.json")) as f:
            cfg = json.load(f)
        cls.now = int(time.time())
        cls.health, cls.items = hub.collect(cfg["feeds"], cls.dir, cls.now)
        cls.stories = [hub.make_story(c, cfg["outlet_weights"], cls.now) for c in hub.group(cls.items)]

    def status(self, name):
        return next(h["status"] for h in self.health if h["name"] == name)

    def test_broken_empty_missing_feeds_are_flagged(self):
        self.assertEqual(self.status("Broken Feed"), "error")
        self.assertEqual(self.status("Empty Feed"), "empty")
        self.assertEqual(self.status("Missing Feed"), "error")
        self.assertEqual(self.status("Demo Gazette"), "ok")

    def test_old_and_off_topic_items_are_dropped(self):
        titles = " ".join(i["title"] for i in self.items)
        self.assertNotIn("last week", titles)
        self.assertNotIn("Tokyo", titles)

    def test_same_url_with_tracking_param_counts_once(self):
        n = sum(1 for i in self.items if "third-quarter profit" in i["title"] and i["source"] == "Demo Gazette")
        self.assertEqual(n, 1)

    def test_same_event_is_one_story_with_four_outlets(self):
        s = next(s for s in self.stories if "Northgate" in s["lead"]["title"] and "profit" in s["lead"]["title"].lower())
        self.assertEqual(s["n"], 4)
        self.assertEqual(len(s["more"]), 3)
        self.assertEqual(s["lead"]["source"], "Sample Business Daily")   # tied top weight, published first

    def test_different_event_same_company_stays_separate(self):
        div = [s for s in self.stories if "dividend" in s["lead"]["title"]]
        self.assertEqual(len(div), 1)
        self.assertEqual(div[0]["n"], 1)

    def test_towers_and_rates_group(self):
        self.assertEqual(next(s for s in self.stories if "towers" in s["lead"]["title"])["n"], 3)
        self.assertEqual(next(s for s in self.stories if "base rate" in s["lead"]["title"])["n"], 2)

    def test_aggregator_outlet_name_is_used_and_suffix_stripped(self):
        it = next(i for i in self.items if i["url"].endswith("aaa111"))
        self.assertEqual(it["source"], "Placeholder Post")
        self.assertFalse(it["title"].endswith("Placeholder Post"))

    def test_final_checks_pass_on_clean_data(self):
        self.assertEqual(hub.check_stories(self.stories), [])

    def test_checks_catch_duplicate_link(self):
        bad = [dict(s) for s in self.stories[:2]]
        bad[1] = dict(bad[1], lead=bad[0]["lead"], id="zzz")
        self.assertTrue(any("duplicate link" in e for e in hub.check_stories(bad)))

    def test_snippets_never_reach_output(self):
        hub.summarize(self.stories, {}, None, "m")
        page = hub.render(self.stories, self.health, self.now, True)
        self.assertNotIn("context for", page)


class CopyGuard(unittest.TestCase):
    T = ["Northgate Bank posts 18% rise in third-quarter profit"]

    def test_rejects_copied_run(self):
        self.assertIn("copies", hub.problem("Northgate Bank posts 18% rise in profit for the quarter", self.T, []))

    def test_rejects_near_copy(self):
        self.assertIsNotNone(hub.problem("Northgate Bank profit rises 18% third quarter", self.T, []))

    def test_rejects_dash_and_stock_words(self):
        self.assertIsNotNone(hub.problem("The lender earned more — a pivotal result.", self.T, []))

    def test_accepts_original_wording(self):
        ok = "The Dubai lender earned about a fifth more than a year ago, helped by more loans to customers."
        self.assertIsNone(hub.problem(ok, self.T, []))


class Helpers(unittest.TestCase):
    def test_canon_strips_tracking(self):
        self.assertEqual(hub.canon("https://A.com/x/?utm_source=a&id=3#frag"), "https://a.com/x?id=3")


if __name__ == "__main__":
    unittest.main()
