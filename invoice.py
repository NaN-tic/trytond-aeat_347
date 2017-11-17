# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond import backend
from trytond.model import ModelSQL, ModelView, fields
from trytond.wizard import Wizard, StateView, StateTransition, Button
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, And, Bool
from trytond.transaction import Transaction
from sql.operators import In
from .aeat import OPERATION_KEY

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
    party = fields.Many2One('party.party', 'Party',
        required=True, readonly=True)
    operation_key = fields.Selection(OPERATION_KEY, 'Operation key',
        required=True, readonly=True)
    amount = fields.Numeric('Operation Amount', digits=(16, 2),
        readonly=True)
    invoice = fields.Many2One('account.invoice', 'Invoice', readonly=True)
    party_record = fields.Many2One('aeat.347.report.party', 'Party Record',
        readonly=True)
    party_name = fields.Function(fields.Char('Party Name'), 'get_party_fields')
    party_vat = fields.Function(fields.Char('Party VAT'), 'get_party_fields')
    country_code = fields.Function(fields.Char('Country Code'),
        'get_party_fields')
    province_code = fields.Function(fields.Char('Province Code'),
        'get_party_fields')

    @classmethod
    def get_party_fields(cls, records, names):
        res = {}
        for name in ['party_name', 'party_vat', 'country_code',
                'province_code']:
            res[name] = dict.fromkeys([x.id for x in records], '')
        for record in records:
            party = record.party
            res['party_name'][record.id] = party.name[:39]
            res['party_vat'][record.id] = (party.vat_code[2:] if
                party.vat_code else '')
            res['country_code'][record.id] = (party.vat_code[:2] if
                party.vat_code else '')
            province_code = ''
            address = party.address_get(type='invoice')
            if address and address.zip:
                province_code = address.zip.strip()[:2]
            res['province_code'][record.id] = province_code
        for key in res.keys():
            if key not in names:
                del res[key]
        return res

    @classmethod
    def delete_record(cls, invoices):
        pool = Pool()
        Record = pool.get('aeat.347.record')
        with Transaction().set_user(0, set_context=True):
            Record.delete(Record.search([('invoice', 'in',
                            [i.id for i in invoices])]))


class Invoice:
    __metaclass__ = PoolMeta
    __name__ = 'account.invoice'

    include_347 = fields.Boolean('Include 347')
    aeat347_operation_key = fields.Selection([('', ''), ] + OPERATION_KEY,
        'AEAT 347 Operation Key',
        states={
            'invisible': ~Bool(Eval('include_347')),
            'required': Bool(Eval('include_347')),
            },
        depends=['include_347'])

    @classmethod
    def __register__(cls, module_name):
        pool = Pool()
        Record = pool.get('aeat.347.record')
        TableHandler = backend.get('TableHandler')

        cursor = Transaction().cursor
        table = TableHandler(cursor, cls, module_name)
        table_line = TableHandler(cursor, cls, 'account.invoice.line')
        sql_table = cls.__table__()
        record_table = Record.__table__()

        exist_347 = table.column_exist('include_347')

        super(Invoice, cls).__register__(module_name)

        # Migration: moved 347 check mark from invoice line to invice
        if not exist_347:
            cursor.execute(*record_table.select(record_table.invoice,
                    record_table.operation_key))
            for invoice_id, operation_key in cursor.fetchall():
                cursor.execute(*sql_table.update(
                        columns=[sql_table.include_347,
                            sql_table.aeat347_operation_key],
                        values=[True, operation_key],
                        where=sql_table.id == invoice_id))
            table_line.drop_column('include_347')
            table_line.drop_column('aeat347_operation_key')

    @staticmethod
    def default_include_347():
        return True

    def on_change_party(self):
        super(Invoice, self).on_change_party()
        self.include_347 = self.on_change_with_include_347()
        self.aeat347_operation_key = \
            self.on_change_with_aeat347_operation_key()

    @fields.depends('party')
    def on_change_with_include_347(self, name=None):
        if self.party:
            return self.party.include_347
        return True

    @fields.depends('type', 'aeat347_operation_key', 'include_347')
    def on_change_with_aeat347_operation_key(self):
        if not self.include_347:
            return ''
        if self.aeat347_operation_key:
            return self.aeat347_operation_key
        if self.type:
            return self.get_aeat347_operation_key(self.type)
        else:
            return ''

    @classmethod
    def get_aeat347_operation_key(cls, invoice_type):
        type_ = 'in' if invoice_type[0:2] == 'in' else 'out'
        return 'A' if type_ == 'in' else 'B'

    @classmethod
    def create_aeat347_records(cls, invoices):
        Record = Pool().get('aeat.347.record')
        to_create = {}

        for invoice in invoices:
            if (not invoice.move or invoice.state == 'cancel' or
                    not invoice.include_347):
                continue
            if invoice.aeat347_operation_key:
                operation_key = invoice.aeat347_operation_key
                amount = invoice.total_amount

                if invoice.type in ('out_credit_note', 'in_credit_note'):
                    amount *= -1

                to_create[invoice.id] = {
                    'company': invoice.company.id,
                    'fiscalyear': invoice.move.period.fiscalyear,
                    'month': invoice.invoice_date.month,
                    'party': invoice.party.id,
                    'amount': amount,
                    'operation_key': operation_key,
                    'invoice': invoice.id,
                    }

        Record.delete_record(invoices)
        with Transaction().set_user(0, set_context=True):
            Record.create(to_create.values())

    @classmethod
    def create(cls, vlist):
        for vals in vlist:
            if not vals.get('include_347', True):
                continue
            invoice_type = vals.get('type', 'out_invoice')
            vals['aeat347_operation_key'] = cls.get_aeat347_operation_key(
                invoice_type)
        return super(Invoice, cls).create(vlist)

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

    aeat_347_operation_key = fields.Selection([('none', 'Leave Empty'), ] +
        OPERATION_KEY, 'Operation Key', required=True)
    include_347 = fields.Boolean('Include 347')

    @staticmethod
    def default_aeat_347_operation_key():
        return 'none'


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
        cursor = Transaction().cursor
        invoice_ids = Transaction().context['active_ids']
        invoices = Invoice.browse(invoice_ids)

        value = self.start.aeat_347_operation_key
        include = self.start.include_347
        if value == 'none' or not include:
            value = None

        invoice = Invoice.__table__()
        #Update to allow to modify key for posted invoices
        cursor.execute(*invoice.update(columns=[invoice.aeat347_operation_key,
                    invoice.include_347],
                values=[value, include], where=In(invoice.id, invoice_ids)))

        Invoice.create_aeat347_records(invoices)

        return 'done'
