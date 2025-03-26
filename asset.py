# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from decimal import Decimal
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction

_ZERO = Decimal(0)

class Asset(metaclass=PoolMeta):
    __name__ = 'asset'

    party = fields.Many2One('party.party', 'Party')
    party_name = fields.Char('Party Name') #, size=40) #Nom del tercer
    party_tax_identifier = fields.Many2One('party.identifier',
        'Party Tax Identifier') # CIF / NIF del tercer
    cadaster_number = fields.Char('Cadaster Reference', size=25) #Número de cadastre
    street = fields.Char('Street') #, size=50) # Carrer
    number_type = fields.Selection([
            ('NUM', 'Number'),
            ('KM.', 'Kilometer'),
            ('S/N', 'Without number'),
            ], 'Number type')
    number = fields.Char('Number') #, size=5) # i número
    province_code = fields.Char('Province Code') #, size=2) # Codi de la província (càlculs per processos)
    municipality_code = fields.Char('Municipality Code') #, size=5) # Codi municipal (càlculs per processos) situation, complement,


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
                cadaster_number = asset.cadaster_number
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
                    'cadaster_number': cadaster_number,
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
