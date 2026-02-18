# This file is part aeat_347 module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.model import ModelView, fields
from trytond.wizard import Wizard, StateView, StateTransition, Button
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from sql.operators import In
from .aeat import OPERATION_KEY


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
                    | (sql_table.aeat347_operation_key == 'none')))

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

    def check_347_taxes(self):
        include = False
        for tax in self.taxes:
            if tax.tax:
                if tax.tax.operation_347 == 'exclude_invoice':
                    return False
                if tax.tax.operation_347 != 'ignore':
                    include = True
        return include

    @fields.depends('taxes', 'type', 'aeat347_operation_key', 'party',
        'company', methods=['check_347_taxes', 'get_aeat347_operation_key'])
    def _on_change_lines_taxes(self):
        super()._on_change_lines_taxes()
        if ((self.taxes and not self.check_347_taxes())
                or (self.company and self.party == self.company.party)):
            self.aeat347_operation_key = 'empty'
        elif not self.aeat347_operation_key:
            self.aeat347_operation_key = self.get_aeat347_operation_key(
                self.type)

    @classmethod
    def check_aeat347_operation_key(cls, invoices):
        to_update = []
        for invoice in invoices:
            if (not invoice.move or invoice.state == 'cancelled'):
                continue
            if invoice.aeat347_operation_key == 'empty':
                continue
            if (not invoice.check_347_taxes()
                    or invoice.party == invoice.company.party):
                invoice.aeat347_operation_key = 'empty'
                to_update.append(invoice)
                continue
            if not invoice.aeat347_operation_key:
                invoice.aeat347_operation_key = \
                    invoice.get_aeat347_operation_key(invoice.type)
                to_update.append(invoice)
        with Transaction().set_context(check_modify_invoice=False):
            cls.save(to_update)

    @classmethod
    def check_modify(cls, invoices):
        check = Transaction().context.get('check_modify_invoice', True)
        if check:
            super(Invoice, cls).check_modify(invoices)

    @classmethod
    def draft(cls, invoices):
        super(Invoice, cls).draft(invoices)

    @classmethod
    def post(cls, invoices):
        super(Invoice, cls).post(invoices)
        cls.check_aeat347_operation_key(invoices)

    @classmethod
    def cancel(cls, invoices):
        super(Invoice, cls).cancel(invoices)

    @classmethod
    def create(cls, vlist):
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('aeat347_operation_key'):
                values['aeat347_operation_key'] = (
                    cls.get_aeat347_operation_key(values.get('type')))
        return super(Invoice, cls).create(vlist)


class InvoiceLine(metaclass=PoolMeta):
    __name__ = 'account.invoice.line'

    invoice_date = fields.Function(fields.Date('Invoice Date'),
        'get_invoice_date')

    @fields.depends('invoice', '_parent_invoice.invoice_date')
    def get_invoice_date(self, name=None):
        if self.invoice:
            return self.invoice.invoice_date


class Reasign347Start(ModelView):
    """
    Reasign AEAT 347 Start
    """
    __name__ = "aeat.347.reasign.start"

    aeat347_operation_key = fields.Selection(OPERATION_KEY, 'Operation Key',
        required=True)

    @staticmethod
    def default_aeat347_operation_key():
        return None


class Reasign347End(ModelView):
    """
    Reasign AEAT 347 End
    """
    __name__ = "aeat.347.reasign.end"


class Reasign347(Wizard):
    """
    Reasign AEAT 347
    """
    __name__ = "aeat.347.reasign"
    start = StateView('aeat.347.reasign.start',
        'aeat_347.aeat_347_reasign_start_view', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Reasign', 'reasign', 'tryton-ok', default=True),
            ])
    reasign = StateTransition()
    done = StateView('aeat.347.reasign.end',
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
        cursor.execute(*invoice.update(
                columns=[invoice.aeat347_operation_key,],
                values=[value], where=In(invoice.id, invoice_ids)))

        Invoice.check_aeat347_operation_key(invoices)
        return 'done'
