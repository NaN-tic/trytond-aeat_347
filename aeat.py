# This file is part aeat_347 module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import itertools
import datetime
import unicodedata
import sys
from decimal import Decimal
from retrofix import aeat347
from retrofix.record import Record, write as retrofix_write
from trytond.config import config, parse_uri
from trytond.model import Workflow, ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, Not
from trytond.transaction import Transaction
from trytond.i18n import gettext
from trytond.exceptions import UserError
from sql.functions import Extract

__all__ = ['Report', 'PartyRecord', 'PropertyRecord']

_ZERO = Decimal('0.0')

OPERATION_KEY = [
    (None, ''),
    ('empty', 'Leave Empty'),
    ('A', 'A - Good and service adquisitions above limit (1)'),
    ('B', 'B - Good and service deliveries above limit (1)'),
    ('C', 'C - Money collection on behavlf of third parties above '
        'limit (3)'),
    ('D', 'D - Adquisitions by Public Institutions (...) above '
        'limit (1)'),
    ('E', 'E - Grants and help made by public institutions above limit '
        '(1)'),
    ('F', 'F - Travel Agency Sales'),
    ('G', 'G - Travel Agency Purchases'),
    ]


def remove_accents(unicode_string):
    str_ = str if sys.version_info < (3, 0) else bytes
    unicode_ = str if sys.version_info < (3, 0) else str
    if isinstance(unicode_string, str_):
        unicode_string_bak = unicode_string
        try:
            unicode_string = unicode_string_bak.decode('iso-8859-1')
        except UnicodeDecodeError:
            try:
                unicode_string = unicode_string_bak.decode('utf-8')
            except UnicodeDecodeError:
                return unicode_string_bak

    if not isinstance(unicode_string, unicode_):
        return unicode_string

    unicode_string_nfd = ''.join(
        (c for c in unicodedata.normalize('NFD', unicode_string)
            if (unicodedata.category(c) != 'Mn'
                or c in ('\\u0327', '\\u0303'))
            ))
    # It converts nfd to nfc to allow unicode.decode()
    return unicodedata.normalize('NFC', unicode_string_nfd)


class Report(Workflow, ModelSQL, ModelView):
    'AEAT 347 Report'
    __name__ = "aeat.347.report"
    company = fields.Many2One('company.company', 'Company', required=True,
        states={
            'readonly': Eval('state') == 'done',
            }, depends=['state'])
    currency = fields.Function(fields.Many2One('currency.currency',
        'Currency'), 'get_currency')
    previous_number = fields.Char('Previous Declaration Number', size=13,
        states={
            'readonly': Eval('state') == 'done',
            }, depends=['state'])
    representative_vat = fields.Char('L.R. VAT number', size=9,
        help='Legal Representative VAT number.', states={
            'readonly': Eval('state') == 'done',
            }, depends=['state'])
    year = fields.Integer("Year", required=True)
    company_vat = fields.Char('VAT number', size=9, states={
            'required': True,
            'readonly': Eval('state') == 'done',
            }, depends=['state'])
    type = fields.Selection([
            ('N', 'Normal'),
            ('C', 'Complementary'),
            ('S', 'Substitutive')
            ], 'Statement Type', required=True, states={
                'readonly': Eval('state') == 'done',
            }, depends=['state'])
    support_type = fields.Selection([
            ('C', 'DVD'),
            ('T', 'Telematics'),
            ], 'Support Type', required=True, states={
                'readonly': Eval('state') == 'done',
            }, depends=['state'])
    calculation_date = fields.DateTime('Calculation Date')
    state = fields.Selection([
            ('draft', 'Draft'),
            ('calculated', 'Calculated'),
            ('done', 'Done'),
            ('cancelled', 'Cancelled')
            ], 'State', readonly=True)
    contact_name = fields.Char('Full Name', size=40, help='The full name must '
        'appear in a correct order: First surname, blank space, Second '
        'surname, blank space, Name')
    contact_phone = fields.Char('Phone', size=9)
    operation_limit = fields.Numeric('Invoiced Limit (1)', digits=(16, 2),
        required=True, help='The declaration will include parties with the '
        'total of operations over this limit')
    received_cash_limit = fields.Numeric('Received Cash Limit (2)',
        digits=(16, 2), required=True, help='The declaration will show the '
        'total of cash operations over this limit')
    on_behalf_third_party_limit = fields.Numeric('On Behalf of Third '
        'Party Limit (3)', digits=(16, 2), required=True,
        help='The declaration will include parties from which we received '
        'payments, on behalf of third parties, over this limit')
    cash_amount = fields.Function(fields.Numeric('Cash Amount (Manual)',
            digits=(16, 2)), 'get_totals')
    party_amount = fields.Function(fields.Numeric(
            'Party Amount', digits=(16, 2)), 'get_totals')
    party_count = fields.Function(fields.Integer('Party Record Count'),
        'get_totals')
    property_amount = fields.Function(fields.Numeric(
            'Property Amount', digits=(16, 2)), 'get_totals')
    property_count = fields.Function(fields.Integer(
            'Property Record Count'), 'get_totals')
    parties = fields.One2Many('aeat.347.report.party', 'report',
        'Party Records', states={
            'readonly': Eval('state') == 'done',
            }, depends=['state'])
    properties = fields.One2Many('aeat.347.report.property', 'report',
        'Property Records', states={
            'readonly': Eval('state') == 'done',
            }, depends=['state'])
    file_ = fields.Binary('File', filename='filename', states={
            'invisible': Eval('state') != 'done',
            })
    filename = fields.Function(fields.Char("File Name"),
        'get_filename')

    @classmethod
    def __setup__(cls):
        super(Report, cls).__setup__()
        cls._order = [
            ('year', 'DESC'),
            ('id', 'DESC'),
            ]
        cls._buttons.update({
                'draft': {
                    'invisible': ~Eval('state').in_(['calculated',
                            'cancelled']),
                    },
                'calculate': {
                    'invisible': ~Eval('state').in_(['draft']),
                    },
                'process': {
                    'invisible': ~Eval('state').in_(['calculated']),
                    },
                'cancel': {
                    'invisible': Eval('state').in_(['cancelled']),
                    },
                })
        cls._transitions |= set((
                ('draft', 'calculated'),
                ('draft', 'cancelled'),
                ('calculated', 'draft'),
                ('calculated', 'done'),
                ('calculated', 'cancelled'),
                ('done', 'cancelled'),
                ('cancelled', 'draft'),
                ))

    @classmethod
    def __register__(cls, module_name):
        pool = Pool()
        FiscalYear = pool.get('account.fiscalyear')

        cursor = Transaction().connection.cursor()
        table = cls.__table_handler__(module_name)
        sql_table = cls.__table__()
        fiscalyear_table = FiscalYear.__table__()

        super().__register__(module_name)

        # migration fiscalyear to year
        if table.column_exist('fiscalyear'):
            query = sql_table.update(columns=[sql_table.year],
                    values=[Extract('YEAR', fiscalyear_table.start_date)],
                    from_=[fiscalyear_table],
                    where=sql_table.fiscalyear == fiscalyear_table.id)
            cursor.execute(*query)
            table.drop_column('fiscalyear')
        if table.column_exist('fiscalyear_code'):
            table.drop_column('fiscalyear_code')

    @staticmethod
    def default_operation_limit():
        return Decimal('3005.06')

    @staticmethod
    def default_on_behalf_third_party_limit():
        return Decimal('300.51')

    @staticmethod
    def default_received_cash_limit():
        return Decimal('6000.00')

    @staticmethod
    def default_type():
        return 'N'

    @staticmethod
    def default_support_type():
        return 'T'

    @staticmethod
    def default_state():
        return 'draft'

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    def get_rec_name(self, name):
        return '%s - %s' % (self.company.rec_name,
            self.year)

    @classmethod
    def search_rec_name(self, name, clause):
        _, operator, value = clause
        if value is not None:
            value = value.replace('%', '').replace('*', '\\*')
            if operator in ('ilike', 'like'):
                operator = '='
            elif operator in ('not ilike', 'not like'):
                operator = '!='
                value = value.replace('%', '').replace('*', '\\*')

        return ['OR',
           ('company.rec_name',) + tuple(clause[1:]),
           ('year', operator, value),
        ]

    def get_currency(self, name):
        return self.company.currency.id

    def get_filename(self, name):
        return 'aeat347-%s.txt' % self.year

    @fields.depends('company')
    def on_change_with_company_vat(self, name=None):
        if self.company:
            tax_identifier = self.company.party.tax_identifier
            return tax_identifier and tax_identifier.es_code() or None

    @classmethod
    def get_totals(cls, reports, names):
        res = {}
        for name in ('party_count', 'property_count'):
            res[name] = dict.fromkeys([x.id for x in reports], 0)
        for name in ('party_amount', 'cash_amount', 'property_amount'):
            res[name] = dict.fromkeys([x.id for x in reports], _ZERO)
        for report in reports:
            res['party_amount'][report.id] = Decimal(sum([x.amount for x in
                    report.parties if x.amount is not None]))
            res['party_count'][report.id] = len(report.parties)
            res['cash_amount'][report.id] = Decimal(sum([x.cash_amount for x in
                    report.parties if x.cash_amount is not None]))
            res['property_amount'][report.id] = Decimal(sum([x.amount for x in
                    report.properties if x.amount is not None]))
            res['property_count'][report.id] = len(report.properties)
        for key in list(res.keys()):
            if key not in names:
                del res[key]
        return res

    @classmethod
    def copy(cls, reports, default=None):
        if default is None:
            default = {}
        default = default.copy()
        if 'parties' not in default:
            default['parties'] = None
        if 'properties' not in default:
            default['properties'] = None
        return super(Report, cls).copy(reports, default=default)

    def pre_validate(self):
        super().pre_validate()
        self.check_year_digits()

    @fields.depends('year')
    def check_year_digits(self):
        if self.year and len(str(self.year)) != 4:
            raise UserError(
                gettext('aeat_347.msg_invalid_year',
                    year=self.year))

    @classmethod
    def validate(cls, reports):
        for report in reports:
            report.check_euro()

    def check_euro(self):
        if self.currency.code != 'EUR':
            raise UserError(gettext('aeat_347.invalid_currency',
                report=self.rec_name))

    @staticmethod
    def aggregate_function():
        uri = parse_uri(config.get('database', 'uri'))
        if uri.scheme == 'postgresql':
            return "array_agg(r.id)"
        return "group_concat(r.id, ',')"

    @classmethod
    @ModelView.button
    @Workflow.transition('calculated')
    def calculate(cls, reports):
        pool = Pool()
        Operation = pool.get('aeat.347.report.party')
        PartyIdentifier = pool.get('party.identifier')

        cursor = Transaction().connection.cursor()

        with Transaction().set_user(0):
            Operation.delete(Operation.search([
                ('report', 'in', [r.id for r in reports])]))

        def is_decimal(value):
            if not isinstance(value, Decimal):
                return Decimal(value)
            return value

        for report in reports:
            query = """
                SELECT
                    r.party_tax_identifier,
                    r.operation_key,
                    sum(case when month <= 3 then amount else 0 end) as first,
                    sum(case when month > 3 and month <= 6
                        then amount else 0 end) as second,
                    sum(case when month > 6 and month <= 9
                        then amount else 0 end) as third,
                    sum(case when month > 9 and month <= 12
                        then amount else 0 end) as fourth,
                    sum(amount) as total,
                    %s
                FROM
                    aeat_347_record as r
                WHERE
                    r.year = %s AND
                    r.party_tax_identifier is not null AND
                    r.company = %s
                GROUP BY
                    r.party_tax_identifier, r.operation_key
                HAVING
                    sum(amount) > %s
                """ % (cls.aggregate_function(), report.year, report.company.id,
                    report.operation_limit)
            cursor.execute(query)
            result = cursor.fetchall()

            to_create = {}
            for (tax_identifier_id, opkey, q1, q2, q3, q4, amount, records
                    ) in result:
                tax_identifier = PartyIdentifier(tax_identifier_id)
                party = tax_identifier.party
                if not party:
                    continue
                code = tax_identifier.es_code()
                country_code = tax_identifier.es_country()
                address = party.address_get(type='invoice')
                if not country_code:
                    if address and address.country:
                        country_code = address.country.code
                province_code = '99'
                if address and address.postal_code and country_code == 'ES':
                    province_code = address.postal_code.strip()[:2]
                    try:
                        int(province_code)
                    except ValueError:
                        province_code = '99'
                records = (records if isinstance(records, (list))
                    else records.split(','))

                key = '%s-%s-%s' % (report.id, tax_identifier_id, opkey)
                if key in to_create:
                    to_create[key]['amount'] += amount
                    to_create[key]['records'] = [('add',
                        to_create[key]['records'][0][1] + records)]
                else:
                    to_create[key] = {
                        'amount': is_decimal(amount),
                        'cash_amount': _ZERO,
                        'party_vat': code or '',
                        'party_name': party.name[:38],
                        'country_code': country_code,
                        'province_code': province_code,
                        'operation_key': opkey,
                        'report': report.id,
                        'community_vat': (tax_identifier.code[2:]
                            if not code else ''),
                        'records': [('add', records)],
                    }

                for f in ['first', 'second', 'third', 'fourth']:
                    qkey = "%s_quarter_amount" % f
                    if qkey not in to_create[key]:
                        to_create[key][qkey] = _ZERO

                    qkey = "%s_quarter_property_amount" % f
                    to_create[key][qkey] = _ZERO

                to_create[key]['first_quarter_amount'] += is_decimal(q1)
                to_create[key]['second_quarter_amount'] += is_decimal(q2)
                to_create[key]['third_quarter_amount'] += is_decimal(q3)
                to_create[key]['fourth_quarter_amount'] += is_decimal(q4)

        with Transaction().set_user(0, set_context=True):
            Operation.create(to_create.values())

        cls.write(reports, {
            'calculation_date': datetime.datetime.now(),
            })

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def process(cls, reports):
        for report in reports:
            report.create_file()

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, reports):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, reports):
        pass

    def auto_sequence(self):
        pool = Pool()
        Report = pool.get('aeat.347.report')
        count = Report.search([
                ('state', '=', 'done'),
                ],
            order=[
                ('year', 'DESC'),
            ], count=True)
        return count + 1

    def create_file(self):
        records = []
        record = Record(aeat347.PRESENTER_HEADER_RECORD)
        record.year = str(self.year)
        record.nif = self.company_vat
        record.presenter_name = self.company.party.name
        record.support_type = self.support_type
        record.contact_phone = self.contact_phone
        record.contact_name = self.contact_name
        record.declaration_number = int('347{}{:0>6}'.format(
            self.year,
            self.auto_sequence()))
        record.previous_declaration_number = self.previous_number
        record.party_count = len(self.parties)
        record.party_amount = self.party_amount
        record.property_count = len(self.properties)
        record.property_amount = self.property_amount or Decimal('0.0')
        record.representative_nif = self.representative_vat
        records.append(record)
        for line in itertools.chain(self.parties, self.properties):
            record = line.get_record()
            record.year = str(self.year)
            record.nif = self.company_vat
            records.append(record)
        try:
            data = retrofix_write(records)
        except AssertionError as e:
            raise UserError(str(e))
        data = remove_accents(data).upper()
        if isinstance(data, str):
            data = data.encode('iso-8859-1')
        self.file_ = self.__class__.file_.cast(data)
        self.save()


class PartyRecord(ModelSQL, ModelView):
    """
    AEAT 347 Party Record
    """
    __name__ = 'aeat.347.report.party'
    _rec_name = "party_vat"

    company = fields.Many2One('company.company', 'Company', required=True)
    report = fields.Many2One('aeat.347.report', 'AEAT 347 Report',
        ondelete='CASCADE')
    party_name = fields.Char('Party Name', size=40)
    party_vat = fields.Char('VAT', size=9)
    representative_vat = fields.Char('L.R. VAT number', size=9,
        help='Legal Representative VAT number')
    community_vat = fields.Char('Community VAT number', size=17,
        help='VAT number for professionals established in other state member '
            'without national VAT')
    province_code = fields.Char('Province Code', size=2)
    country_code = fields.Char('Country Code', size=2)
    operation_key = fields.Selection(OPERATION_KEY, 'Operation Key')
    amount = fields.Numeric('Operations Amount', digits=(16, 2))
    insurance = fields.Boolean('Insurance Operation', help='Only for '
        'insurance companies. Set to identify insurance operations aside from '
        'the rest.')
    business_premises_rent = fields.Boolean('Bussiness Premises Rent',
        help='Set to identify premises rent operations aside from the rest. '
        'You\'ll need to fill in the premises info only when you are the one '
        'that receives the money.')
    cash_vat_operation = fields.Boolean('Cash VAT Operation',
        help='Only for cash basis operations. Set to identify cash basis '
            'operations aside from the rest.')
    cash_vat_criteria = fields.Numeric('Cash VAT Criteria', digits=(16, 2),
        help='Annual amount of transactions accrued under cash VAT criteria.',
        states={
            'invisible': Not(Bool(Eval('cash_vat_operation'))),
            })
    tax_person_operation = fields.Boolean('Taxable Person Operation',
        help='Only for taxable person operations. Set to identify taxable '
            'person operations aside from the rest.')
    related_goods_operation = fields.Boolean('Related Goods Operation',
        help='Only for related goods operations. Set to identify related '
            'goods operations aside from the rest.')
    cash_amount = fields.Numeric('Cash Amount Received', digits=(16, 2))
    property_amount = fields.Numeric('VAT Liable Property Amount',
        digits=(16, 2))
    fiscalyear_code_cash_operation = fields.Integer(
        'Fiscal Year Cash Operation')
    first_quarter_amount = fields.Numeric('First Quarter Amount',
        digits=(16, 2))
    first_quarter_property_amount = fields.Numeric('First '
        'Quarter Property Amount', digits=(16, 2))
    second_quarter_amount = fields.Numeric('Second Quarter Amount',
        digits=(16, 2))
    second_quarter_property_amount = fields.Numeric('Second '
        'Quarter Property Amount', digits=(16, 2))
    third_quarter_amount = fields.Numeric('Third Quarter Amount',
        digits=(16, 2))
    third_quarter_property_amount = fields.Numeric('Third '
        'Quarter Property Amount', digits=(16, 2))
    fourth_quarter_amount = fields.Numeric('Fourth Quarter Amount',
        digits=(16, 2))
    fourth_quarter_property_amount = fields.Numeric('Fourth '
        'Quarter Property Amount', digits=(16, 2))
    records = fields.One2Many('aeat.347.record', 'party_record',
        'AEAT 347 Records', readonly=True)

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @staticmethod
    def default_amount():
        return Decimal('0.0')

    @staticmethod
    def default_cash_amount():
        return Decimal('0.0')

    @staticmethod
    def default_first_quarter_amount():
        return Decimal('0.0')

    @staticmethod
    def default_second_quarter_amount():
        return Decimal('0.0')

    @staticmethod
    def default_third_quarter_amount():
        return Decimal('0.0')

    @staticmethod
    def default_fourth_quarter_amount():
        return Decimal('0.0')

    @staticmethod
    def default_first_quarter_property_amount():
        return Decimal('0.0')

    @staticmethod
    def default_second_quarter_property_amount():
        return Decimal('0.0')

    @staticmethod
    def default_third_quarter_property_amount():
        return Decimal('0.0')

    @staticmethod
    def default_fourth_quarter_property_amount():
        return Decimal('0.0')

    def get_record(self):
        record = Record(aeat347.PARTY_RECORD)
        record.party_nif = self.party_vat
        record.community_vat = self.community_vat or ''
        record.representative_nif = self.representative_vat or ''
        record.party_name = remove_accents(self.party_name)
        record.province_code = self.province_code
        if self.country_code == 'ES':
            record.country_code = ''
        else:
            record.country_code = self.country_code
        record.operation_key = self.operation_key
        record.amount = self.amount
        record.insurance = self.insurance
        record.business_premises_rent = self.business_premises_rent
        record.cash_amount = self.cash_amount or _ZERO
        record.vat_liable_property_amount = self.property_amount \
            or Decimal('0.0')
        record.fiscalyear_cash_operation = str(
            self.fiscalyear_code_cash_operation or '')
        record.first_quarter_amount = (self.first_quarter_amount or
            Decimal('0.0'))
        record.first_quarter_property_amount = (
            self.first_quarter_property_amount or Decimal('0.0'))
        record.second_quarter_amount = (self.second_quarter_amount or
            Decimal('0.0'))
        record.second_quarter_property_amount = (
            self.second_quarter_property_amount or Decimal('0.0'))
        record.third_quarter_amount = (self.third_quarter_amount or
            Decimal('0.0'))
        record.third_quarter_property_amount = (
            self.third_quarter_property_amount or Decimal('0.0'))
        record.fourth_quarter_amount = (self.fourth_quarter_amount or
            Decimal('0.0'))
        record.fourth_quarter_property_amount = (
            self.fourth_quarter_property_amount or Decimal('0.0'))
        record.cash_vat_operation = self.cash_vat_operation
        record.cash_vat_criteria = (self.cash_vat_criteria or Decimal('0.0')
            if self.cash_vat_operation else Decimal('0.0'))
        record.tax_person_operation = self.tax_person_operation
        record.related_goods_operation = self.related_goods_operation
        return record


class PropertyRecord(ModelSQL, ModelView):
    """
    AEAT 347 Property Record
    """
    __name__ = 'aeat.347.report.property'
    _rec_name = "cadaster_number"

    company = fields.Many2One('company.company', 'Company', required=True)
    report = fields.Many2One('aeat.347.report', 'AEAT 347 Report',
        ondelete='CASCADE')
    party_vat = fields.Char('VAT number', size=9)
    representative_vat = fields.Char('L.R. VAT number', size=9,
        help='Legal Representative VAT number')
    party_name = fields.Char('Party Name', size=40)
    amount = fields.Numeric('Amount', digits=(16, 2))
    situation = fields.Selection([
            ('1', '1 - Spain but Basque Country and Navarra'),
            ('2', '2 - Basque Country and Navarra'),
            ('3', '3 - Spain, without catastral reference'),
            ('4', '4 - Foreign'),
            ], 'Property Situation', required=True)
    cadaster_number = fields.Char('Cadaster Reference', size=25)
    road_type = fields.Char('Road Type', size=5)
    street = fields.Char('Street', size=50)
    number_type = fields.Selection([
            ('NUM', 'Number'),
            ('KM.', 'Kilometer'),
            ('S/N', 'Without number'),
            ], 'Number type')
    number = fields.Char('Number', size=5)
    number_qualifier = fields.Selection([
            ('BIS', 'Bis'),
            ('MOD', 'Mod'),
            ('DUP', 'Dup'),
            ('ANT', 'Ant'),
            ], 'Number Qualifier')
    block = fields.Char('Block', size=3)
    doorway = fields.Char('Doorway', size=3)
    stair = fields.Char('Stair', size=3)
    floor = fields.Char('Floor', size=3)
    door = fields.Char('Door', size=3)
    complement = fields.Char('Complement', size=40,
        help='Complement (urbanization, industrial park...)')
    city = fields.Char('City', size=30)
    municipality = fields.Char('Municipality', size=30)
    municipality_code = fields.Char('Municipality Code', size=5)
    province_code = fields.Char('Province Code', size=2)
    zip = fields.Char('Zip', size=5)

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    def get_record(self):
        record = Record(aeat347.PROPERTY_RECORD)
        record.party_nif = self.party_vat
        record.representative_nif = self.representative_vat
        record.party_name = self.party_name
        record.amount = self.amount
        record.situation = self.situation
        record.cadaster_number = self.cadaster_number
        record.road_type = self.road_type
        record.street = self.street
        record.number_type = self.number_type
        record.number = self.number
        record.number_qualifier = self.number_qualifier
        record.block = self.block
        record.doorway = self.doorway
        record.stair = self.stair
        record.floor = self.floor
        record.door = self.door
        record.complement = self.complement
        record.city = self.city
        record.municipality = self.municipality
        record.municipality_code = self.municipality_code
        record.province_code = self.province_code
        record.zip = self.zip
        return record
