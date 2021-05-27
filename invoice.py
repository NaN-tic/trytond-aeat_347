# This file is part aeat_347 module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.model import ModelSQL, ModelView, fields
from trytond.wizard import Wizard, StateView, StateTransition, Button
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from sql.operators import In
from .aeat import OPERATION_KEY
from sql.aggregate import Min
##from trytond.modules.aeat_347.aeat import OPERATION_KEY

__all__ = ['Record', 'Invoice', 'Recalculate347RecordStart',
    'Recalculate347RecordEnd', 'Recalculate347Record', 'Reasign347RecordStart',
    'Reasign347RecordEnd', 'Reasign347Record']


class Record(ModelSQL, ModelView):
    """
    AEAT 347 Record

    Calculated on invoice creation to generate temporal
    data for reports. Aggregated on aeat347 calculation.
    """
    __name__ = 'aeat.347.record'

    company = fields.Many2One('company.company', 'Company', required=True,
        readonly=True)
    fiscalyear = fields.Many2One('account.fiscalyear', 'Fiscal Year',
        required=True, readonly=True)
    month = fields.Integer('Month', readonly=True)
    operation_key = fields.Selection(OPERATION_KEY, 'Operation key',
        required=True, readonly=True)
    amount = fields.Numeric('Operation Amount', digits=(16, 2),
        readonly=True)
    invoice = fields.Many2One('account.invoice', 'Invoice', readonly=True)
    party_record = fields.Many2One('aeat.347.report.party', 'Party Record',
        readonly=True)
    party_tax_identifier = fields.Many2One('party.identifier',
        'Party Tax Identifier')

    @classmethod
    def __register__(cls, module_name):
        cursor = Transaction().connection.cursor()
        table = cls.__table_handler__(module_name)
        sql_table = cls.__table__()
        pool = Pool()
        Invoice = pool.get('account.invoice')
        invoice = Invoice.__table__()
        Party = pool.get('party.party')
        party = Party.__table__()
        Identifier = pool.get('party.identifier')
        identifier = Identifier.__table__()

        exist_tax_identifier = table.column_exist('tax_identifier')
        exist_party = table.column_exist('party')

        if exist_tax_identifier:
            table.drop_column('tax_identifier')

        super(Record, cls).__register__(module_name)

        if exist_tax_identifier or exist_party:
            # Don't use UPDATE FROM because SQLite nor MySQL support it.
            value = identifier.join(invoice,
                condition=invoice.party_tax_identifier == identifier.id).select(
                    identifier.id,
                    where=(identifier.type == 'eu_vat') &
                    (invoice.id == sql_table.invoice))
            cursor.execute(*sql_table.update([sql_table.party_tax_identifier],
                    [value])),

        if exist_tax_identifier:
            # Update empty party_tax_identifier with party tax identifier
            value = identifier.join(party,
                condition=party.id == identifier.party).join(invoice,
                    condition=invoice.party == party.id).select(
                        Min(identifier.id),
                        where=(identifier.type == 'eu_vat') &
                        (invoice.id == sql_table.invoice),
                        group_by=party.id)
            cursor.execute(*sql_table.update([sql_table.party_tax_identifier],
                    [value],
                    where=sql_table.party_tax_identifier == None)),

        if exist_party:
            # Update empty party_tax_identifier with party tax identifier
            value = party.join(identifier,
                condition=identifier.party == party.id).select(
                    Min(identifier.id),
                    where=(identifier.type == 'eu_vat') &
                    (party.id == sql_table.party),
                    group_by=party.id)
            cursor.execute(*sql_table.update([sql_table.party_tax_identifier],
                    [value],
                    where=sql_table.party_tax_identifier == None)),

            table.drop_column('party')

    @classmethod
    def delete_record(cls, invoices):
        with Transaction().set_user(0, set_context=True):
            cls.delete(cls.search([('invoice', 'in',
                            [i.id for i in invoices])]))


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    aeat347_operation_key = fields.Selection(OPERATION_KEY,
        'AEAT 347 Operation Key')

    @classmethod
    def __register__(cls, module_name):
        cursor = Transaction().connection.cursor()
        table = cls.__table_handler__(module_name)
        sql_table = cls.__table__()

        exist_347 = table.column_exist('include_347')
        super(Invoice, cls).__register__(module_name)
        if exist_347:
            table.drop_column('include_347')
        cursor.execute(*sql_table.update(
                columns=[sql_table.aeat347_operation_key],
                values=['empty'],
                where=(sql_table.aeat347_operation_key == '')
                | (sql_table.aeat347_operation_key == 'none')
                | (sql_table.aeat347_operation_key == None)))

    @classmethod
    def __setup__(cls):
        super(Invoice, cls).__setup__()
        cls._check_modify_exclude.add('aeat347_operation_key')

    @fields.depends('type', 'aeat347_operation_key')
    def on_change_with_aeat347_operation_key(self):
        if self.aeat347_operation_key:
            return self.aeat347_operation_key
        if self.type:
            return self.get_aeat347_operation_key(self.type)
        else:
            return None

    @classmethod
    def get_aeat347_operation_key(cls, invoice_type):
        return 'A' if invoice_type == 'in' else 'B'

    def get_aeat347_total_amount(self):
        pool = Pool()
        Currency = pool.get('currency.currency')

        amount = 0
        for tax in self.taxes:
            if not tax.tax:
                continue
            if tax.tax.operation_347 in ('ignore', 'exclude_invoice'):
                continue
            if tax.tax.operation_347 == 'amount_only':
                amount += tax.amount
            elif tax.tax.operation_347 == 'base_amount':
                amount += (tax.base + tax.amount)
        if amount > self.total_amount:
            amount = self.total_amount
        if self.currency != self.company.currency:
            with Transaction().set_context(date=self.currency_date):
                amount = Currency.compute(self.currency, amount,
                    self.company.currency, round=True)
        return amount

    def check_347_taxes(self):
        include = False
        for tax in self.taxes:
            if tax.tax:
                if tax.tax.operation_347 == 'exclude_invoice':
                    return False
                if tax.tax.operation_347 != 'ignore':
                    include = True
        return include

    @fields.depends('type', 'aeat347_operation_key')
    def _on_change_lines_taxes(self):
        super(Invoice, self)._on_change_lines_taxes()
        if not self.check_347_taxes():
            self.aeat347_operation_key = None
        elif not self.aeat347_operation_key:
            self.aeat347_operation_key = self.get_aeat347_operation_key(
                self.type)

    @classmethod
    def create_aeat347_records(cls, invoices):
        pool = Pool()
        Record = pool.get('aeat.347.record')
        Period = pool.get('account.period')

        to_create = {}
        to_update = []
        for invoice in invoices:
            if (not invoice.move or invoice.state == 'cancelled'):
                continue
            if not invoice.check_347_taxes():
                invoice.aeat347_operation_key = None
                to_update.append(invoice)
                continue
            if not invoice.aeat347_operation_key:
                invoice.aeat347_operation_key = \
                    invoice.get_aeat347_operation_key(invoice.type)
                to_update.append(invoice)

            if invoice.aeat347_operation_key:
                operation_key = invoice.aeat347_operation_key
                amount = invoice.get_aeat347_total_amount()

                if invoice.type == 'in':
                    accounting_date = (invoice.accounting_date
                        or invoice.invoice_date)
                    period_id = Period.find(
                        invoice.company.id, date=accounting_date)
                    period = Period(period_id)
                    fiscalyear = period.fiscalyear
                else:
                    fiscalyear = invoice.move.period.fiscalyear

                to_create[invoice.id] = {
                    'company': invoice.company.id,
                    'fiscalyear': fiscalyear,
                    'month': invoice.invoice_date.month,
                    'amount': amount,
                    'operation_key': operation_key,
                    'invoice': invoice.id,
                    'party_tax_identifier': invoice.party_tax_identifier,
                    }

        Record.delete_record(invoices)
        with Transaction().set_context(check_modify_invoice=False):
            cls.save(to_update)
            #cls.save(cls.browse([x.id for x in to_update]))
        with Transaction().set_user(0, set_context=True):
            Record.create(to_create.values())

    @classmethod
    def check_modify(cls, invoices):
        check =Transaction().context.get('check_modify_invoice', True)
        if check:
            super(Invoice, cls).check_modify(invoices)

    @classmethod
    def draft(cls, invoices):
        pool = Pool()
        Record = pool.get('aeat.347.record')
        super(Invoice, cls).draft(invoices)
        Record.delete_record(invoices)

    @classmethod
    def post(cls, invoices):
        super(Invoice, cls).post(invoices)
        cls.create_aeat347_records(invoices)

    @classmethod
    def cancel(cls, invoices):
        pool = Pool()
        Record = pool.get('aeat.347.record')
        super(Invoice, cls).cancel(invoices)
        Record.delete_record(invoices)

    @classmethod
    def create(cls, vlist):
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('aeat347_operation_key'):
                values['aeat347_operation_key'] = cls.get_aeat347_operation_key(
                    values.get('type'))
        return super(Invoice, cls).create(vlist)


class Recalculate347RecordStart(ModelView):
    """
    Recalculate AEAT 347 Records Start
    """
    __name__ = "aeat.347.recalculate.records.start"


class Recalculate347RecordEnd(ModelView):
    """
    Recalculate AEAT 347 Records End
    """
    __name__ = "aeat.347.recalculate.records.end"


class Recalculate347Record(Wizard):
    """
    Recalculate AEAT 347 Records
    """
    __name__ = "aeat.347.recalculate.records"
    start = StateView('aeat.347.recalculate.records.start',
        'aeat_347.aeat_347_recalculate_start_view', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Calculate', 'calculate', 'tryton-ok', default=True),
            ])
    calculate = StateTransition()
    done = StateView('aeat.347.recalculate.records.end',
        'aeat_347.aeat_347_recalculate_end_view', [
            Button('Ok', 'end', 'tryton-ok', default=True),
            ])

    def transition_calculate(self):
        Invoice = Pool().get('account.invoice')
        invoices = Invoice.browse(Transaction().context['active_ids'])
        Invoice.create_aeat347_records(invoices)
        return 'done'


class Reasign347RecordStart(ModelView):
    """
    Reasign AEAT 347 Records Start
    """
    __name__ = "aeat.347.reasign.records.start"

    aeat347_operation_key = fields.Selection(OPERATION_KEY, 'Operation Key',
        required=True)

    @staticmethod
    def default_aeat347_operation_key():
        return None


class Reasign347RecordEnd(ModelView):
    """
    Reasign AEAT 347 Records End
    """
    __name__ = "aeat.347.reasign.records.end"


class Reasign347Record(Wizard):
    """
    Reasign AEAT 347 Records
    """
    __name__ = "aeat.347.reasign.records"
    start = StateView('aeat.347.reasign.records.start',
        'aeat_347.aeat_347_reasign_start_view', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Reasign', 'reasign', 'tryton-ok', default=True),
            ])
    reasign = StateTransition()
    done = StateView('aeat.347.reasign.records.end',
        'aeat_347.aeat_347_reasign_end_view', [
            Button('Ok', 'end', 'tryton-ok', default=True),
            ])

    def transition_reasign(self):
        Invoice = Pool().get('account.invoice')
        cursor = Transaction().connection.cursor()
        invoice_ids = Transaction().context['active_ids']
        invoices = Invoice.browse(invoice_ids)

        value = self.start.aeat347_operation_key
        invoice = Invoice.__table__()
        # Update to allow to modify key for posted invoices
        cursor.execute(*invoice.update(columns=[invoice.aeat347_operation_key,],
                values=[value], where=In(invoice.id, invoice_ids)))

        Invoice.create_aeat347_records(invoices)
        return 'done'
