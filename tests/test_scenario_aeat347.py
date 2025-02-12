import datetime
import unittest
from decimal import Decimal

from proteus import Model, Wizard
from trytond.modules.account.tests.tools import (create_chart,
                                                 create_fiscalyear, create_tax,
                                                 get_accounts)
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
        activate_modules(['aeat_347', 'account_es'])

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

        # Create payment term
        payment_term = create_payment_term()
        payment_term.save()

        # Create out invoice over limit
        Record = Model.get('aeat.347.record')
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
        rec1, = Record.find([('invoice', '=', invoice.id)])
        self.assertEqual(rec1.party_tax_identifier.code, identifier1.code)
        self.assertEqual(rec1.month, today.month)
        self.assertEqual(rec1.operation_key, 'B')
        self.assertEqual(rec1.amount, Decimal('3520.00'))

        # Create out invoice over limit, but changing manually the operation key
        Record = Model.get('aeat.347.record')
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
        self.assertEqual(Record.find([('invoice', '=', invoice.id)]), [])

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
        rec1, = Record.find([('invoice', '=', invoice.id)])
        self.assertEqual(rec1.party_tax_identifier.code, identifier2.code)
        self.assertEqual(rec1.month, today.month)
        self.assertEqual(rec1.operation_key, 'B')
        self.assertEqual(rec1.amount, Decimal('220.00'))

        # Create out invoice over limit and with foreign Tax Identifier
        Record = Model.get('aeat.347.record')
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
        rec1, = Record.find([('invoice', '=', invoice.id)])
        self.assertEqual(rec1.party_tax_identifier.code, identifier3.code)
        self.assertEqual(rec1.month, today.month)
        self.assertEqual(rec1.operation_key, 'B')
        self.assertEqual(rec1.amount, Decimal('3520.00'))

        # Create self out invoice
        Record = Model.get('aeat.347.record')
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
        self.assertEqual(Record.find([('invoice', '=', invoice.id)]), [])

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
        rec1, = Record.find([('invoice', '=', invoice.id)])
        self.assertEqual(rec1.party_tax_identifier.code, identifier1.code)
        self.assertEqual(rec1.month, today.month)
        self.assertEqual(rec1.operation_key, 'B')
        self.assertEqual(rec1.amount, Decimal('-88.00'))

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
        rec1, = Record.find([('invoice', '=', invoice.id)])
        self.assertEqual(rec1.party_tax_identifier.code, identifier1.code)
        self.assertEqual(rec1.month, today.month)
        self.assertEqual(rec1.operation_key, 'A')
        self.assertEqual(rec1.amount, Decimal('137.50'))

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
        rec1, = Record.find([('invoice', '=', invoice.id)])
        self.assertEqual(rec1.party_tax_identifier.code, identifier1.code)
        self.assertEqual(rec1.month, today.month)
        self.assertEqual(rec1.operation_key, 'A')
        self.assertEqual(rec1.amount, Decimal('-27.50'))

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
        rec1, = Record.find([('invoice', '=', invoice.id)])
        self.assertEqual(rec1.party_tax_identifier.code, identifier1.code)
        reasign = Wizard('aeat.347.reasign.records', models=[invoice])
        reasign.form.aeat347_operation_key = 'empty'
        reasign.execute('reasign')
        invoice.reload()
        self.assertEqual(invoice.aeat347_operation_key, 'empty')
        self.assertEqual(Record.find([('invoice', '=', invoice.id)]), [])
