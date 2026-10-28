import copy
import unittest
from app.domain import Conflict, transition, validate


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.h = {'_id':'h', 'name':'Laptop', 'asset_tag':'POOL', 'status':'active',
                  'total':10, 'out':0, 'reserved':0, 'version':1}
        self.aa = []

    def step(self, command, data, checkout=None):
        self.h, self.aa, c = transition(self.h, self.aa, command, data, checkout)
        return c

    def allocation(self, n=5, project='p'):
        self.step('allocate', {'project_id':project,'quantity':n})

    def checkout(self, n=3):
        return self.step('checkout', {'project_id':'p','hardware_id':'h','user_id':'u','quantity':n})

    def test_create_invariants(self):
        validate(self.h, [])

    def test_allocate(self):
        self.allocation()
        self.assertEqual(self.h['reserved'],5)

    def test_multiple_projects(self):
        self.allocation(4)
        self.allocation(6,'p2')
        self.assertEqual(self.h['reserved'],10)

    def test_overallocation(self):
        with self.assertRaises(Conflict):
            self.allocation(11)

    def test_checkout(self):
        self.allocation()
        c=self.checkout()
        self.assertEqual((self.h['out'],c['returned']),(3,0))

    def test_checkout_requires_allocation(self):
        with self.assertRaises(Conflict):
            self.checkout()

    def test_checkout_over_limit(self):
        self.allocation(2)
        with self.assertRaises(Conflict):
            self.checkout(3)

    def test_partial_return(self):
        self.allocation()
        c=self.checkout()
        c=self.step('return',{'project_id':'p','quantity':1},c)
        self.assertEqual((self.h['out'],c['returned']),(2,1))

    def test_full_return(self):
        self.allocation()
        c=self.checkout()
        c=self.step('return',{'project_id':'p','quantity':3},c)
        self.assertEqual((self.h['out'],c['returned']),(0,3))

    def test_over_return(self):
        self.allocation()
        c=self.checkout()
        with self.assertRaises(Conflict):
            self.step('return',{'project_id':'p','quantity':4},c)

    def test_correction_reopens_stock(self):
        self.allocation()
        c=self.checkout()
        c=self.step('return',{'project_id':'p','quantity':2},c)
        c=self.step('correct',{'project_id':'p','returned':1},c)
        self.assertEqual((self.h['out'],c['returned']),(2,1))

    def test_correction_cannot_exceed_allocation(self):
        self.allocation()
        c=self.checkout()
        c=self.step('return',{'project_id':'p','quantity':3},c)
        self.allocation(0)
        with self.assertRaises(Conflict):
            self.step('correct',{'project_id':'p','returned':0},c)

    def test_adjust_increase(self):
        self.step('adjust',{'delta':2})
        self.assertEqual(self.h['total'],12)

    def test_adjust_below_reserved(self):
        self.allocation(8)
        with self.assertRaises(Conflict):
            self.step('adjust',{'delta':-3})

    def test_negative_stock(self):
        with self.assertRaises(Conflict):
            self.step('adjust',{'delta':-11})

    def test_retire(self):
        self.step('retire',{})
        self.assertEqual(self.h['status'],'retired')

    def test_retire_allocated(self):
        self.allocation()
        with self.assertRaises(Conflict):
            self.step('retire',{})

    def test_retired_immutable(self):
        self.step('retire',{})
        with self.assertRaises(Conflict):
            self.step('edit',{'name':'Other'})

    def test_maintenance_requires_return(self):
        self.allocation()
        self.checkout()
        with self.assertRaises(Conflict):
            self.step('edit',{'status':'under_maintenance'})

    def test_maintenance_blocks_checkout(self):
        self.allocation()
        self.step('edit',{'status':'under_maintenance'})
        with self.assertRaises(Conflict):
            self.checkout()

    def test_edit_keys_and_version(self):
        self.step('edit',{'name':'MacBook','asset_tag':'TAG'})
        self.assertEqual((self.h['name_key'],self.h['asset_key'],self.h['version']),('macbook','tag',2))

    def test_transition_does_not_mutate_inputs(self):
        original=copy.deepcopy(self.h)
        transition(self.h,[], 'adjust', {'delta':1})
        self.assertEqual(self.h,original)

    def test_corrupt_allocation_rejected(self):
        self.h['reserved']=5
        with self.assertRaises(Conflict):
            validate(self.h,[])

    def test_stock_limit(self):
        with self.assertRaises(Conflict):
            self.step('adjust',{'delta':1000000})


if __name__ == '__main__':
    unittest.main()
