import datetime
import unittest
from decimal import Decimal

from proteus import Model, Wizard
from trytond.modules.account_es.tests.tools import (create_chart, create_tax,
    get_accounts)
from trytond.modules.account.tests.tools import create_fiscalyear
from trytond.modules.account_invoice.tests.tools import (
    create_payment_term, set_fiscalyear_invoice_sequences)
from trytond.modules.company.tests.tools import create_company, get_company
from trytond.modules.currency.tests.tools import get_currency
from trytond.tests.test_tryton import drop_db
from trytond.tests.tools import activate_modules


class Test(unittest.TestCase):

    def setUp(self):
        drop_db()
        super().setUp()

    def tearDown(self):
        drop_db()
        super().tearDown()

    def test(self):

        today = datetime.date.today()

        # Install account_invoice
        activate_modules(['aeat_347', 'account_es', 'asset_invoice',
            'asset_property', 'product', 'account_code_digits'])

        # Create company
        eur = get_currency('EUR')
        _ = create_company(currency=eur)
        company = get_company()

        # Create fiscal year
        fiscalyear = set_fiscalyear_invoice_sequences(
            create_fiscalyear(company))
        fiscalyear.click('create_period')

        # Create chart of accounts
        _ = create_chart(company)
        accounts = get_accounts(company)
        revenue = accounts['revenue']
        expense = accounts['expense']

        # Create tax
        tax = create_tax(Decimal('.10'))
        tax.operation_347 = 'base_amount'
        tax.save()
        tax2 = create_tax(Decimal('.10'))
        tax2.operation_347 = 'base_amount'
        tax2.save()

        # Create party
        Party = Model.get('party.party')
        party1 = Party(name='Party')
        identifier1 = party1.identifiers.new()
        identifier1.type = 'eu_vat'
        identifier1.code = 'ES00000000T'
        party1.save()
        party2 = Party(name='Party 2')
        identifier2 = party2.identifiers.new()
        identifier2.type = 'eu_vat'
        identifier2.code = 'ES00000001R'
        party2.save()
        party3 = Party(name='Party 3')
        identifier3 = party3.identifiers.new()
        identifier3.type = 'eu_vat'
        identifier3.code = 'FR64000063908'
        party3.save()

        # Create account category
        ProductCategory = Model.get('product.category')
        account_category = ProductCategory(name="Account Category")
        account_category.accounting = True
        account_category.account_expense = expense
        account_category.account_revenue = revenue
        account_category.customer_taxes.append(tax)
        account_category.supplier_taxes.append(tax2)
        account_category.save()

        # Create product
        ProductUom = Model.get('product.uom')
        unit, = ProductUom.find([('name', '=', 'Unit')])
        ProductTemplate = Model.get('product.template')
        template = ProductTemplate()
        template.name = 'product'
        template.default_uom = unit
        template.type = 'service'
        template.list_price = Decimal('40')
        template.account_category = account_category
        product, = template.products
        product.cost_price = Decimal('25')
        template.save()
        product, = template.products

        def create_asset_product(name, party, prop):
            template = ProductTemplate()
            template.name = name
            template.default_uom = unit
            template.type = 'assets'
            template.list_price = Decimal('40')
            template.account_category = account_category
            template.save()
            product, = template.products
            product.aeat347_party = party
            product.aeat347_property = prop
            product.save()
            return product

        product_347_full = create_asset_product(
            'product-347-full', True, True)
        product_347_party = create_asset_product(
            'product-347-party', True, False)
        product_347_none = create_asset_product(
            'product-347-none', False, False)

        # Create assets with 347 config from products
        Asset = Model.get('asset')
        asset_full = Asset()
        asset_full.name = 'Asset Full'
        asset_full.product = product_347_full
        asset_full.save()
        self.assertTrue(asset_full.aeat347_party)
        self.assertTrue(asset_full.aeat347_property)

        asset_party = Asset()
        asset_party.name = 'Asset Party'
        asset_party.product = product_347_party
        asset_party.save()
        self.assertTrue(asset_party.aeat347_party)
        self.assertFalse(asset_party.aeat347_property)

        asset_none = Asset()
        asset_none.name = 'Asset None'
        asset_none.product = product_347_none
        asset_none.save()
        self.assertFalse(asset_none.aeat347_party)
        self.assertFalse(asset_none.aeat347_property)

        # Create payment term
        payment_term = create_payment_term()
        payment_term.save()

        # Create out invoice over limit
        Invoice = Model.get('account.invoice')
        invoice = Invoice()
        invoice.party = party1
        invoice.payment_term = payment_term
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal(40)
        line.quantity = 80
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('3200.00'))
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'B')

        # Create out invoice over limit, but changing manually the operation key
        Invoice = Model.get('account.invoice')
        invoice = Invoice()
        invoice.party = party1
        invoice.payment_term = payment_term
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal(40)
        line.quantity = 80
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('3200.00'))
        invoice.aeat347_operation_key = 'empty'
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'empty')

        # Create out invoice not over limit
        invoice = Invoice()
        invoice.party = party2
        invoice.payment_term = payment_term
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal(40)
        line.quantity = 5
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('200.00'))
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'B')

        # Create out invoice over limit and with foreign Tax Identifier
        Invoice = Model.get('account.invoice')
        invoice = Invoice()
        invoice.party = party3
        invoice.payment_term = payment_term
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal(40)
        line.quantity = 80
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('3200.00'))
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'B')

        # Create self out invoice
        Invoice = Model.get('account.invoice')
        invoice = Invoice()
        invoice.party = company.party
        invoice.payment_term = payment_term
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal(40)
        line.quantity = 80
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('3200.00'))
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'empty')

        # Create out credit note
        invoice = Invoice()
        invoice.type = 'out'
        invoice.party = party1
        invoice.payment_term = payment_term
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal(40)
        line.quantity = -2
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('-80.00'))
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'B')

        # Create in invoice
        invoice = Invoice()
        invoice.type = 'in'
        invoice.party = party1
        invoice.aeat347_operation_key = 'A'
        invoice.payment_term = payment_term
        invoice.invoice_date = today
        line = invoice.lines.new()
        line.product = product
        line.quantity = 5
        line.unit_price = Decimal('25')
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('125.00'))
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'A')

        # Create in credit note
        invoice = Invoice()
        invoice.type = 'in'
        invoice.party = party1
        invoice.aeat347_operation_key = 'A'
        invoice.payment_term = payment_term
        invoice.invoice_date = today
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal('25.00')
        line.quantity = -1
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('-25.00'))
        invoice.click('post')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'A')

        # Generate 347 Report
        Report = Model.get('aeat.347.report')
        report = Report()
        report.year = today.year
        report.company_vat = '123456789'
        report.contact_name = 'Guido van Rosum'
        report.contact_phone = '987654321'
        report.representative_vat = '22334455'
        report.click('calculate')
        report.reload()
        self.assertEqual(report.property_count, 0)
        self.assertEqual(report.party_count, 2)
        self.assertEqual(report.party_amount, Decimal('6952.00'))
        self.assertEqual(report.cash_amount, Decimal('0.0'))
        self.assertEqual(report.property_amount, Decimal('0.0'))

        # Reassign 347 lines
        reasign = Wizard('aeat.347.reasign.records', models=[invoice])
        reasign.execute('reasign')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'A')

        # Create out invoice an empty leave
        invoice = Invoice()
        invoice.party = party1
        invoice.payment_term = payment_term
        line = invoice.lines.new()
        line.product = product
        line.unit_price = Decimal(40)
        line.quantity = 80
        self.assertEqual(len(line.taxes), 1)
        self.assertEqual(line.amount, Decimal('3200.00'))
        invoice.click('post')
        reasign = Wizard('aeat.347.reasign.records', models=[invoice])
        reasign.form.aeat347_operation_key = 'empty'
        reasign.execute('reasign')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'empty')
