import unittest

from inventory import discounted_price


class DiscountedPriceTests(unittest.TestCase):
    def test_percentage_and_rounding(self):
        self.assertEqual(discounted_price(199.99, 15), 169.99)
        self.assertEqual(discounted_price(80, 0), 80.00)

    def test_rejects_invalid_inputs(self):
        for price, discount in [(-1, 10), (10, -1), (10, 101)]:
            with self.subTest(price=price, discount=discount):
                with self.assertRaises(ValueError):
                    discounted_price(price, discount)


if __name__ == "__main__":
    unittest.main()
