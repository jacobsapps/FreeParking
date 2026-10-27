import unittest
from parking.layout_api import reorder_plan


class LayoutContract(unittest.TestCase):
    def test_only_permutes_the_exact_current_tabs(self):
        self.assertEqual(reorder_plan([['a'],['b'],['c']],['a','b','c'],['c','a','b']),[2,0,1])

    def test_split_added_missing_and_duplicate_tabs_are_refused(self):
        for groups,expected,desired in (
            ([['a','b']],['a','b'],['b','a']),
            ([['a'],['extra']],['a'],['a']),
            ([['a']],['a'],['a','b']),
            ([['a'],['a']],['a','a'],['a','a'])):
            with self.assertRaises(ValueError): reorder_plan(groups,expected,desired)
