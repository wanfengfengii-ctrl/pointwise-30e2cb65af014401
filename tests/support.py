import unittest

from branchlake import Lake


class LakeCase(unittest.TestCase):
    def setUp(self):
        self.lake = Lake()

    def tearDown(self):
        self.lake.close()
