import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import parse_bah  # noqa: E402

ZIP_PATH = ROOT / "data" / "raw" / "BAH-ASCII-2026.zip"
OFFICIAL_SHA = "5446fed831e125c23cc6a407dcde4cae541f108e90ac852aaadeec0aa11e515f"


class ParseBahTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = ZIP_PATH.read_bytes()
        cls.data = parse_bah.parse_bah_zip(cls.blob)

    def test_official_checksum(self):
        self.assertEqual(hashlib.sha256(self.blob).hexdigest(), OFFICIAL_SHA)

    def test_shape(self):
        self.assertEqual(self.data.year, 2026)
        self.assertEqual(len(self.data.mhas), 338)
        self.assertEqual(len(self.data.zip_to_mha), 40959)
        self.assertEqual(self.data.changed_vs_old, ["TX270"])
        self.assertTrue(self.data.senior_collapsed)
        self.assertEqual(self.data.members["with_dependents"], "bahw26.txt")
        self.assertEqual(self.data.members["without_dependents"], "bahwo26.txt")

    def test_known_rates(self):
        san = self.data.mhas["CA038"]
        self.assertEqual(san.name, "San Diego, CA")
        self.assertEqual(san.with_rates["E-5"], 3975)
        self.assertEqual(san.without_rates["E-5"], 3147)
        self.assertEqual(san.with_rates["O-3"], 4518)
        self.assertEqual(self.data.zip_to_mha["92101"], "CA038")

        bragg = self.data.mhas["NC182"]
        self.assertEqual(bragg.name, "Fort Bragg/Pope, NC")
        self.assertEqual(bragg.with_rates["E-5"], 1806)
        self.assertEqual(bragg.without_rates["E-5"], 1527)
        self.assertEqual(self.data.zip_to_mha["28310"], "NC182")

        self.assertEqual(self.data.mhas["TX270"].name, "Abilene/Dyess AFB, TX")
        self.assertEqual(self.data.mhas["TX270"].with_rates["E-5"], 1872)
        self.assertTrue(self.data.mhas["TX270"].changed_vs_old)

    def test_names(self):
        self.assertEqual(self.data.mhas["MD133"].name, "Fort G. G. Meade, MD")
        self.assertEqual(self.data.mhas["LA326"].name, "St. Mary and Terrebonne, LA")
        self.assertEqual(self.data.mhas["MO161"].name, "St. Louis, MO")
        self.assertEqual(self.data.mhas["ZZ510"].name, "County Cost Group 510")
        self.assertEqual(self.data.mhas["ZZ510"].kind, "county")

    def test_senior_grades_match(self):
        for mha in self.data.mhas.values():
            for table in (mha.with_rates, mha.without_rates):
                self.assertEqual(table["O-7"], table["O-8"])
                self.assertEqual(table["O-7"], table["O-10"])

    def test_unrated_zip(self):
        self.assertEqual(self.data.zip_to_mha["00601"], "XX499")
        self.assertNotIn("XX499", self.data.mhas)

    def test_old_members_are_not_the_table(self):
        # The old TX270 with-dependents E-5 was 1554. The current file is 1872.
        self.assertNotEqual(self.data.mhas["TX270"].with_rates["E-5"], 1554)


if __name__ == "__main__":
    unittest.main()
