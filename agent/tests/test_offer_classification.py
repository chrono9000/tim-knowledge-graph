import unittest
from agent.extraction import classify

class OfferClassificationTests(unittest.TestCase):
    def test_original_offer_regression(self):
        self.assertEqual(('statement','recommendation'),classify('I can help assess the Hilltop relay if you need another pair of hands.','user'))
    def test_offer_variants(self):
        for text in ['I could assist with proofreading.','I am happy to help assemble the display.']:
            self.assertEqual('recommendation',classify(text,'user')[1])
    def test_inability_is_not_offer(self):
        self.assertNotEqual('recommendation',classify('I cannot help with the assembly.','user')[1])
