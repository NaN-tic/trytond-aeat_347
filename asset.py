# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
import datetime
from decimal import Decimal
from trytond.model import fields, ModelSQL, ModelView
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.i18n import gettext
from trytond.exceptions import UserWarning
from trytond.pyson import Eval

_ZERO = Decimal(0)


class Asset(metaclass=PoolMeta):
    __name__ = 'asset'

    parties = fields.One2Many('asset.party.party', 'asset', 'Parties')
    party = fields.Function(fields.Many2One('party.party', 'Party',
        context={
            'company': Eval('company', -1),
            },
        depends={'company'}), 'get_party_fields')
    party_name = fields.Function(fields.Char('Party Name'), 'get_party_fields')
    party_tax_identifier = fields.Function(fields.Many2One('party.identifier',
        'Party Tax Identifier'), 'get_party_fields')
    street = fields.Char('Street')
    number_type = fields.Selection([
            (None, ''),
            ('NUM', 'Number'),
            ('KM.', 'Kilometer'),
            ('S/N', 'Without number'),
            ], 'Number type')
    number = fields.Char('Number')
    province_code = fields.Char('Province Code')
    municipality_code = fields.Char('Municipality Code')
    municipality = fields.Char('Municipality', size=30)
    province = fields.Char('Province', size=30)
    door = fields.Char('Door', size=3)
    floor = fields.Char('Floor', size=3)
    stair = fields.Char('Stair', size=3)

    @fields.depends('municipality_code', 'province_code')
    def on_change_municipality_code(self):
        if self.municipality_code and not self.province_code:
            self.province_code = self.municipality_code[:2]

    @classmethod
    def get_party_fields(cls, assets, names):
        result = {}
        for name in ['party', 'party_name', 'party_tax_identifier']:
            result[name] = {}

        for asset in assets:
            if not asset.parties:
                for name in ['party', 'party_name', 'party_tax_identifier']:
                    result[name][asset.id] = None
                continue

            party = None
            non_end_date_parties = []
            end_date_bigger_today = []
            for party in asset.parties:
                if not party.end_date:
                    non_end_date_parties.append(party)
                elif party.end_date > datetime.date.today():
                    end_date_bigger_today.append(party)

            # We use the first party we found with a end date bigger than
            # today of without end date. Dont having any end date have more
            # priority than having an end date
            if end_date_bigger_today:
                party = end_date_bigger_today[0]
            if non_end_date_parties == 1:
                party = non_end_date_parties[0]

            if not party:
                for name in ['party', 'party_name', 'party_tax_identifier']:
                    result[name][asset.id] = None
                continue

            for name in ['party', 'party_name', 'party_tax_identifier']:
                match name:
                    case 'party':
                        result[name][asset.id] = party.party.id
                    case 'party_name':
                        result[name][asset.id] = party.party.name
                    case 'party_tax_identifier':
                        if party.party.tax_identifier:
                            result[name][asset.id] = party.party.tax_identifier.id
                        else:
                            result[name][asset.id] = None
        return result


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
        PartyIdentifier = pool.get('party.identifier')

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
                    r.party_tax_identifier,
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
                    r.asset,
                    r.party_tax_identifier
                HAVING
                    sum(amount) > %s
                """ % (cls.aggregate_function(), report.year, report.company.id,
                    report.operation_limit)
            cursor.execute(query)
            result = cursor.fetchall()

            for (asset_id, party_tax_identifier, amount, records) in result:
                asset = Asset(asset_id)
                party_tax = PartyIdentifier(party_tax_identifier)
                if not asset:
                    continue

                party_name = ''
                party_identifier = ''
                if party_tax:
                    party_name = party_tax.party.name
                    party_identifier = party_tax.es_code()
                elif asset.party_tax_identifier:
                    party_name = asset.party_tax_identifier.party.name
                    party_identifier = asset.party_tax_identifier.es_code()
                land_register = asset.land_register
                street = asset.street
                if street:
                    street = street[:50]
                number = asset.number
                provincie_code = asset.province_code
                municipality_code = asset.municipality_code
                municipality = asset.municipality
                door = asset.door
                floor = asset.floor
                stair = asset.stair
                province = asset.province

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
                    'zip': municipality_code,
                    'municipality': municipality,
                    'door': door,
                    'floor': floor,
                    'stair': stair,
                    'city': province,
                    'report': report.id,
                    'records': [('add', records)],
                })

        if to_create:
            with Transaction().set_user(0, set_context=True):
                Operation.create(to_create)

class Party(metaclass=PoolMeta):
    __name__ = 'party.party'

    assets = fields.One2Many('asset.party.party', 'party', 'Assets')


class AssetParty(ModelSQL, ModelView):
    'Asset Party'
    __name__ = 'asset.party.party'

    asset = fields.Many2One('asset', 'Asset', required=True,
        ondelete='CASCADE')
    party = fields.Many2One('party.party', 'Party', required=True,
        ondelete='RESTRICT')
    start_date = fields.Date('Start Date')
    end_date = fields.Date('End Date')


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    @classmethod
    def create_aeat347_records(cls, invoices):
        pool = Pool()
        Record = pool.get('aeat.347.record')
        PartyAsset = pool.get('asset.party.party')
        super().create_aeat347_records(invoices)

        to_save = []
        to_save_assets = []
        for invoice in invoices:
            for line in invoice.lines:
                if line.invoice_asset:
                    record = Record.search([
                        ('invoice', '=', invoice.id)
                        ], limit=1)
                    if record and record[0].party_tax_identifier:
                        if invoice.type == 'out':
                            create_asset_party = False
                            if line.invoice_asset.parties:
                                party_asset = PartyAsset.search([
                                    ('party', '=', invoice.party.id),
                                    ('asset', '=', line.invoice_asset.id),
                                    ['OR',
                                        ('end_date', '=', None),
                                        ('end_date', '>=', invoice.invoice_date),],
                                ])

                                if not party_asset:
                                    last_asset_party = line.invoice_asset.parties[-1]
                                    last_asset_party.end_date = (
                                        invoice.invoice_date - datetime.timedelta(
                                            days=1))
                                    to_save_assets.append(last_asset_party)
                                    create_asset_party = True
                            else:
                                create_asset_party = True

                            if create_asset_party:
                                new_asset_party = AssetParty()
                                new_asset_party.party = invoice.party
                                new_asset_party.asset = line.invoice_asset
                                new_asset_party.start_date = invoice.invoice_date
                                to_save_assets.append(new_asset_party)

                        record, = record
                        record.asset = line.invoice_asset
                        to_save.append(record)
                    # We need to save the party asset in each line, otherwise,
                    # when we search the party assets the one we created in the
                    # same transaction will no appear
                    if to_save_assets:
                        PartyAsset.save(to_save_assets)
                        to_save_assets = []
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
