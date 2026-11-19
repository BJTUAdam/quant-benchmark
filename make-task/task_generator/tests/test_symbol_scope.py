"""Scope validation is syntactic authorization, never proof that data exists."""
import unittest
from finlab.common import LabError
from finlab.symbol_scope import validate_symbol_selection


class SymbolScopeTests(unittest.TestCase):
    def spec(self,symbols,environment='akquant_futures_v2'):
        return {'environment_id':environment,'bridge_contract':{'symbols':symbols}}

    def test_product_pool_resolves_real_contracts_in_allowed_products(self):
        spec=self.spec(['SC','FU','LU'])
        result=validate_symbol_selection(spec,['SC2509.INE','FU2509.SHF','LU2509.INE'])
        self.assertEqual([r['product'] for r in result],['SC','FU','LU'])

    def test_wrong_exchange_cannot_hide_behind_allowed_product(self):
        with self.assertRaisesRegex(LabError,'FUTURES_EXCHANGE_MISMATCH'):
            validate_symbol_selection(self.spec(['FG']),['FG2509.SHF'])

    def test_pool_does_not_authorize_another_product(self):
        with self.assertRaisesRegex(LabError,'SYMBOL_OUTSIDE_APPROVED_SCOPE'):
            validate_symbol_selection(self.spec(['M']),['Y2509.DCE'])

    def test_exact_contract_does_not_authorize_other_months(self):
        with self.assertRaisesRegex(LabError,'SYMBOL_OUTSIDE_APPROVED_SCOPE'):
            validate_symbol_selection(self.spec(['M2509.DCE']),['M2511.DCE'])

    def test_product_and_continuous_code_are_not_real_tradable_contracts(self):
        for symbol in ('M','M.DCE','M9999.DCE','M8888.DCE','M2513.DCE','M2500.DCE','M9999.DCE.CONTINUOUS','000001.SZ'):
            with self.assertRaises(LabError): validate_symbol_selection(self.spec(['M']),[symbol])

    def test_stock_universe_stays_exact(self):
        spec=self.spec(['000001.SZ'],'akquant_stock_etf_v2')
        self.assertEqual(validate_symbol_selection(spec,['000001.SZ']),[{'symbol':'000001.SZ'}])
        with self.assertRaisesRegex(LabError,'SYMBOL_OUTSIDE_APPROVED_SCOPE'):
            validate_symbol_selection(spec,['600000.SH'])


if __name__=='__main__': unittest.main()
