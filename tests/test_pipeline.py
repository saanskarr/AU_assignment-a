import unittest
from src.validators import (
    clean_header,
    parse_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_event_timestamp
)


class TestValidators(unittest.TestCase):

    def test_phones(self):
        # All 6 valid phone styles
        self.assertEqual(parse_phone("9876543210"), "+919876543210")
        self.assertEqual(parse_phone("+91 98765 43210"), "+919876543210")
        self.assertEqual(parse_phone("09876543210"), "+919876543210")
        self.assertEqual(parse_phone("91-98765-43210"), "+919876543210")
        self.assertEqual(parse_phone("(+91) 9876543210"), "+919876543210")
        self.assertEqual(parse_phone("+919876543210"), "+919876543210")

        # Bad numbers
        self.assertIsNone(parse_phone("987654321"))          # too short
        self.assertIsNone(parse_phone("5876543210"))         # starts with 5
        self.assertIsNone(parse_phone("022-2500-1234"))      # landline
        self.assertIsNone(parse_phone("N/A"))
        self.assertIsNone(parse_phone("+1 415 555 0134"))

    def test_dates(self):
        self.assertEqual(parse_date("2026-12-01"), "2026-12-01")
        self.assertEqual(parse_date("01/12/2026"), "2026-12-01")
        self.assertEqual(parse_date("01-12-2026"), "2026-12-01")
        self.assertEqual(parse_date("01-Dec-2026"), "2026-12-01")
        self.assertEqual(parse_date("01-dec-2026"), "2026-12-01")

        # Invalid dates
        self.assertIsNone(parse_date("31/02/2026"))
        self.assertIsNone(parse_date("2026-13-01"))
        self.assertIsNone(parse_date("TBD"))

    def test_decimals(self):
        self.assertEqual(parse_decimal("12500"), "12500.00")
        self.assertEqual(parse_decimal("₹12,500"), "12500.00")
        self.assertEqual(parse_decimal("Rs. 12500"), "12500.00")
        self.assertEqual(parse_decimal("INR 12500.5"), "12500.50")

        self.assertIsNone(parse_decimal("-10"))
        self.assertIsNone(parse_decimal("twelve thousand"))

    def test_headers_and_enums(self):
        self.assertEqual(clean_header("  Insured__Name "), "insured name")
        self.assertEqual(parse_enum(" HI ", ["en", "hi", "ta"]), "hi")
        self.assertIsNone(parse_enum("Hindi", ["en", "hi", "ta"]))

    def test_timestamps(self):
        iso, dt = parse_event_timestamp("2026-10-01T19:00:00Z")
        self.assertEqual(iso, "2026-10-02T00:30:00+05:30")
        self.assertEqual(dt, "2026-10-02")


if __name__ == "__main__":
    unittest.main()