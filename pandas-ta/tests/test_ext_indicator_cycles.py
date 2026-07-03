from tests.config import get_sample_data

from unittest import TestCase
from pandas import DataFrame


class TestCylesExtension(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = get_sample_data()

    @classmethod
    def tearDownClass(cls):
        del cls.data

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_dsp_ext(self):
        self.data.ta.dsp(append=True)
        self.assertIsInstance(self.data, DataFrame)
        self.assertEqual(self.data.columns[-1], "DSP_14")

    def test_ebsw_ext(self):
        self.data.ta.ebsw(append=True)
        self.assertIsInstance(self.data, DataFrame)
        self.assertEqual(self.data.columns[-1], "EBSW_40_10")
