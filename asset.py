# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from decimal import Decimal
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.i18n import gettext
from trytond.exceptions import UserWarning
from trytond.pyson import Eval

_ZERO = Decimal(0)


class Asset(metaclass=PoolMeta):
    __name__ = 'asset'

    party = fields.Many2One('party.party', 'Party',
        context={
            'company': Eval('company', -1),
            },
        depends={'company'})
    party_name = fields.Char('Party Name')
    party_tax_identifier = fields.Many2One('party.identifier',
        'Party Tax Identifier')
    street = fields.Char('Street')
    number_type = fields.Selection([
            ('NUM', 'Number'),
            ('KM.', 'Kilometer'),
            ('S/N', 'Without number'),
            ], 'Number type')
    number = fields.Char('Number')
    province_code = fields.Char('Province Code')
    municipality_code = fields.Char('Municipality Code')

    @fields.depends('municipality_code', 'province_code')
    def on_change_municipality_code(self):
        if self.municipality_code and not self.province_code:
            self.province_code = self.municipality_code[:2]


class PropertyRecord(metaclass=PoolMeta):
    __name__ = 'aeat.347.report.property'

    records = fields.One2Many('aeat.347.record', 'property_record',
        'AEAT 347 Records', readonly=True)


class Record(metaclass=PoolMeta):
    __name__ = 'aeat.347.record'

    property_record = fields.Many2One('aeat.347.report.property',
        'Property Record', readonly=True)
    asset = fields.Many2One('asset', 'Asset')


class Report(metaclass=PoolMeta):
    __name__ = 'aeat.347.report'

    @classmethod
    def calculate(cls, reports):
        pool = Pool()
        Operation = pool.get('aeat.347.report.property')
        Asset = pool.get('asset')

        super().calculate(reports)

        cursor = Transaction().connection.cursor()

        with Transaction().set_user(0):
            Operation.delete(Operation.search([
                ('report', 'in', [r.id for r in reports])]))

        def is_decimal(value):
            if not isinstance(value, Decimal):
                return Decimal(value)
            return value

        to_create = []
        for report in reports:
            query = """
                SELECT
                    r.asset,
                    sum(amount) as total,
                    %s
                FROM
                    aeat_347_record as r
                WHERE
                    r.year = %s AND
                    r.asset is not null AND
                    r.party_tax_identifier is not null AND
                    r.company = %s
                GROUP BY
                    r.asset
                HAVING
                    sum(amount) > %s
                """ % (cls.aggregate_function(), report.year, report.company.id,
                    report.operation_limit)
            cursor.execute(query)
            result = cursor.fetchall()

            for (asset_id, amount, records) in result:
                asset = Asset(asset_id)
                if not asset:
                    continue

                party_name = ''
                if asset.party_name:
                    party_name = asset.party_name
                party_identifier = ''
                if asset.party_tax_identifier:
                    party_identifier = asset.party_tax_identifier.es_code()
                land_register = asset.land_register
                street = asset.street
                if street:
                    street = street[:50]
                number = asset.number
                provincie_code = asset.province_code
                municipality_code = asset.municipality_code

                to_create.append({
                    'amount': is_decimal(amount),
                    'party_name': party_name[:38],
                    'party_vat': party_identifier,
                    'situation': 1,
                    'cadaster_number': land_register,
                    'street': street,
                    'number': number,
                    'province_code': provincie_code,
                    'municipality_code': municipality_code,
                    'report': report.id,
                    'records': [('add', records)],
                })

        if to_create:
            with Transaction().set_user(0, set_context=True):
                Operation.create(to_create)


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    @classmethod
    def create_aeat347_records(cls, invoices):
        pool = Pool()
        Record = pool.get('aeat.347.record')
        super().create_aeat347_records(invoices)

        to_save = []
        for invoice in invoices:
            for line in invoice.lines:
                if line.invoice_asset:
                    record = Record.search([
                        ('invoice', '=', invoice.id)
                        ], limit=1)
                    if record and record[0].party_tax_identifier:
                        record, = record
                        record.asset = line.invoice_asset
                        to_save.append(record)
        Record.save(to_save)


class InvoiceContract(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    @classmethod
    def create_aeat347_records(cls, invoices):
        pool = Pool()
        ContractConsumption = pool.get('contract.consumption')
        Warning = pool.get('res.user.warning')

        super().create_aeat347_records(invoices)

        for invoice in invoices:
            for line in invoice.lines:
                if (line.origin and
                        isinstance(line.origin, ContractConsumption) and
                        line.origin.contract_line.asset != line.invoice_asset):
                    warning_key = "wrong_asset_%s" % line.id
                    if Warning.check(warning_key):
                        raise UserWarning(warning_key,
                            gettext('aeat_347.msg_wrong_asset'))
