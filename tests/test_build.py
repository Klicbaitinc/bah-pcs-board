import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402

SITE = ROOT / "site"
PUBLIC = "https://klicbaitinc.github.io/bah-pcs-board"


class BuildSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("SITE_URL", None)
        os.environ.pop("BASE_PATH", None)
        build_site.main()

    def read(self, *parts):
        return (SITE.joinpath(*parts)).read_text()

    def test_home_and_chrome(self):
        html = self.read("index.html")
        self.assertIn('href="/bah-pcs-board/static/style.css"', html)
        self.assertIn('src="/bah-pcs-board/static/app.js"', html)
        self.assertIn(f'href="{PUBLIC}/"', html)
        self.assertIn("not investment advice", html)
        self.assertIn("No ratings", html)
        self.assertIn("Penske link not connected", html)
        self.assertIn("U-Haul link not connected", html)
        self.assertIn("Raisin link not connected", html)
        self.assertNotIn("penske.com", html.lower())
        self.assertNotIn("uhaul.com", html.lower())
        self.assertIn("Email list not open", html)

    def test_zip_san_diego(self):
        html = self.read("zip", "92101", "index.html")
        self.assertIn("ZIP 92101", html)
        self.assertIn("San Diego, CA", html)
        self.assertIn("CA038", html)
        self.assertIn("$3,975", html)
        self.assertIn("$3,147", html)
        self.assertIn("With dependents", html)
        self.assertIn("Without dependents", html)
        self.assertIn(f'href="{PUBLIC}/zip/92101/"', html)

    def test_zip_bragg_and_unrated(self):
        bragg = self.read("zip", "28310", "index.html")
        self.assertIn("$1,806", bragg)
        self.assertIn("$1,527", bragg)
        self.assertIn("Fort Bragg/Pope, NC", bragg)
        unrated = self.read("zip", "00601", "index.html")
        self.assertIn("XX499", unrated)
        self.assertIn("no row", unrated)
        self.assertNotIn("$1,806", unrated)

    def test_tx270_uses_current_file(self):
        html = self.read("mha", "tx270", "index.html")
        self.assertIn("$1,872", html)
        self.assertIn("marked old", html)
        self.assertNotIn("$1,554", html)

    def test_duty_station_and_alias(self):
        page = self.read("duty-station", "fort-bragg-pope-nc", "index.html")
        self.assertIn("NC182", page)
        self.assertIn(f'href="{PUBLIC}/duty-station/fort-bragg-pope-nc/"', page)
        alias = self.read("duty-station", "fort-bragg-nc", "index.html")
        self.assertIn("Fort Bragg, NC", alias)
        self.assertIn(f'href="{PUBLIC}/duty-station/fort-bragg-pope-nc/"', alias)
        self.assertIn('content="noindex, follow"', alias)

    def test_county_group(self):
        html = self.read("mha", "zz510", "index.html")
        self.assertIn("County Cost Group 510", html)
        self.assertIn("county cost group", html)

    def test_pcs_disclosure_and_crawl_files(self):
        pcs = self.read("pcs", "index.html")
        self.assertIn("PCS move cost checklist", pcs)
        self.assertIn("not a quote", pcs.lower())
        self.assertIn("Penske", pcs)
        self.assertIn("U-Haul", pcs)
        self.assertIn("Raisin", pcs)
        self.assertIn("not investment advice", pcs.lower())
        self.assertNotIn("best mover", pcs.lower())
        disclosure = self.read("disclosure", "index.html")
        self.assertIn("FlexOffers", disclosure)
        self.assertIn("not investment advice", disclosure)
        robots = self.read("robots.txt")
        self.assertIn(f"Sitemap: {PUBLIC}/sitemap.xml", robots)
        sitemap = self.read("sitemap.xml")
        self.assertIn(f"<loc>{PUBLIC}/</loc>", sitemap)
        self.assertIn(f"<loc>{PUBLIC}/zip/92101/</loc>", sitemap)
        self.assertIn(f"<loc>{PUBLIC}/mha/ca038/</loc>", sitemap)
        self.assertIn(f"<loc>{PUBLIC}/duty-station/san-diego-ca/</loc>", sitemap)
        self.assertIn(f"<loc>{PUBLIC}/pcs/</loc>", sitemap)
        self.assertNotIn("/duty-station/fort-bragg-nc/", sitemap)
        self.assertTrue((SITE / ".nojekyll").exists())
        self.assertTrue((SITE / "static" / "search.json").exists())

    def test_zip_page_count(self):
        count = sum(1 for path in (SITE / "zip").iterdir() if path.is_dir())
        self.assertEqual(count, 40959)


if __name__ == "__main__":
    unittest.main()
