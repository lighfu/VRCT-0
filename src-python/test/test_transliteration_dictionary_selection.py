import os
import tempfile
import unittest

from models.transliteration.transliteration_transliterator import Transliterator


class DictionarySelectionTests(unittest.TestCase):
    def test_uses_core_dictionary_by_default(self) -> None:
        transliterator = Transliterator()
        self.assertEqual(transliterator.dict_type, "core")
        result = transliterator.analyze("東京に行きます", use_macron=False)
        readings = "".join(part.get("hira", "") for part in result)
        self.assertIn("とうきょう", readings)

    def test_falls_back_to_core_when_full_dictionary_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = os.path.join(directory, "system_full.dic")
            with open(broken, "wb") as f:
                f.write(b"not a sudachi dictionary")
            transliterator = Transliterator(dict_path=broken)
        self.assertEqual(transliterator.dict_type, "core")
        self.assertTrue(transliterator.analyze("東京", use_macron=False))

    def test_falls_back_to_core_when_full_dictionary_is_missing(self) -> None:
        transliterator = Transliterator(dict_path=os.path.join("no", "such", "system_full.dic"))
        self.assertEqual(transliterator.dict_type, "core")


if __name__ == "__main__":
    unittest.main()
