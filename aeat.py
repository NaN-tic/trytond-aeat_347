# This file is part aeat_347 module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import itertools
import datetime
import unicodedata
import sys
from collections import defaultdict
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
from sql.functions import Extract, Substring
from sql.conditionals import Coalesce, Case
from sql.operators import Exists
from sql import Literal
from sql.aggregate import Sum, Min

_ZERO = Decimal(0)

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
            })
    currency = fields.Function(fields.Many2One('currency.currency',
        'Currency'), 'get_currency')
    previous_number = fields.Char('Previous Declaration Number', size=13,
        states={
            'readonly': Eval('state') == 'done',
            })
    representative_vat = fields.Char('L.R. VAT number', size=9,
        help='Legal Representative VAT number.', states={
            'readonly': Eval('state') == 'done',
            })
    year = fields.Integer("Year", required=True)
    company_vat = fields.Char('VAT number', size=9, states={
            'required': True,
            'readonly': Eval('state') == 'done',
            })
    type = fields.Selection([
            ('N', 'Normal'),
            ('C', 'Complementary'),
            ('S', 'Substitutive')
            ], 'Statement Type', required=True, states={
                'readonly': Eval('state') == 'done',
            })
    support_type = fields.Selection([
            ('C', 'DVD'),
            ('T', 'Telematics'),
            ], 'Support Type', required=True, states={
                'readonly': Eval('state') == 'done',
            })
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
            })
    properties = fields.One2Many('aeat.347.report.property', 'report',
        'Property Records', states={
            'readonly': Eval('state') == 'done',
            })
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
        PartyRecord = pool.get('aeat.347.report.party')
        PropertyRecord = pool.get('aeat.347.report.property')

        cursor = Transaction().connection.cursor()
        table = cls.__table_handler__(module_name)
        party_table = PartyRecord.__table_handler__(module_name)
        property_table = PropertyRecord.__table_handler__(module_name)
        sql_table = cls.__table__()
        party_sql_table = PartyRecord.__table__()
        property_sql_table = PropertyRecord.__table__()
        fiscalyear_sql_table = FiscalYear.__table__()

        # Cleanup 2025 draft/calculated reports before migration
        report_ids = []
        if (party_table.column_exist('records')
                or property_table.column_exist('records')):
            report_ids_query = sql_table.select(
                sql_table.id,
                where=(sql_table.year == 2025)
                & (sql_table.state.in_(['draft', 'calculated'])))
            cursor.execute(*report_ids_query)
            report_ids = [r[0] for r in cursor.fetchall()]
            if report_ids:
                cursor.execute(*party_sql_table.delete(
                    where=party_sql_table.report.in_(report_ids)))
                cursor.execute(*property_sql_table.delete(
                    where=property_sql_table.report.in_(report_ids)))

        super().__register__(module_name)

        # migration fiscalyear to year
        if table.column_exist('fiscalyear'):
            query = sql_table.update(columns=[sql_table.year],
                    values=[Extract('YEAR', fiscalyear_sql_table.start_date)],
                    from_=[fiscalyear_sql_table],
                    where=sql_table.fiscalyear == fiscalyear_sql_table.id)
            cursor.execute(*query)
            table.drop_column('fiscalyear')
        if table.column_exist('fiscalyear_code'):
            table.drop_column('fiscalyear_code')

        # Recalculate 2025 draft/calculated reports with new structure
        if report_ids:
            with Transaction().set_user(0, set_context=True):
                reports = cls.browse(report_ids)
                cls.calculate(reports)
            party_table.drop_column('records')
            property_table.drop_column('records')

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
            # year is numeric field. Not suport % operators or .
            value = value.replace('.', '').replace('%', '').replace('*', '\\*')

            if operator in ('ilike', 'like'):
                operator = '='
            elif operator in ('not ilike', 'not like'):
                operator = '!='
                value = value.replace('%', '').replace('*', '\\*')

            try:
                value = int(value)
            except ValueError:
                value = None

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
    def get_aeat347_party_records(cls, report):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        InvoiceTax = pool.get('account.invoice.tax')
        Tax = pool.get('account.tax')
        PartyIdentifier = pool.get('party.identifier')
        Address = pool.get('party.address')
        Country = pool.get('country.country')

        cursor = Transaction().connection.cursor()
        invoice = Invoice.__table__()
        invoice_tax = InvoiceTax.__table__()
        tax = Tax.__table__()
        identifier = PartyIdentifier.__table__()
        address = Address.__table__()
        country = Country.__table__()

        date = Coalesce(invoice.accounting_date, invoice.invoice_date)
        address_min = address.select(
            Min(address.id),
            where=(address.party == invoice.party))
        country_expr = Case(
            (identifier.type == 'eu_vat', Substring(identifier.code, 1, 2)),
            (identifier.type.in_([
                'es_cif', 'es_dni', 'es_nie', 'es_nif', 'es_vat']), 'ES'),
            else_=None)
        country_code_expr = Case(
            (country_expr == None, country.code),
            else_=country_expr)
        province_code_expr = Case(
            ((country_code_expr == 'ES') & (address.postal_code != None),
                Substring(address.postal_code, 1, 2)),
            else_='99')
        code_expr = Case(
            ((identifier.type == 'eu_vat')
                & (Substring(identifier.code, 1, 2) == 'ES'),
                Substring(identifier.code, 3)),
            else_=identifier.code)
        amount_expr = Case(
            (tax.operation_347 == 'amount_only', invoice_tax.amount),
            (tax.operation_347 == 'base_amount',
                invoice_tax.base + invoice_tax.amount),
            else_=Literal(0))
        amount_sum = Sum(amount_expr)
        amount = amount_sum

        tax_include = invoice_tax.join(tax,
            condition=invoice_tax.tax == tax.id).select(
                Literal(1),
                where=(invoice_tax.invoice == invoice.id)
                & (tax.operation_347 != 'ignore'))
        tax_exclude = invoice_tax.join(tax,
            condition=invoice_tax.tax == tax.id).select(
                Literal(1),
                where=(invoice_tax.invoice == invoice.id)
                & (tax.operation_347 == 'exclude_invoice'))

        condition = (
            (invoice.company == report.company.id)
            & (invoice.state.in_(['posted', 'paid']))
            & (Extract('YEAR', date) == report.year)
            & (invoice.party_tax_identifier != None)
            & (invoice.aeat347_operation_key != 'empty')
            )
        condition &= ~Exists(tax_exclude) & Exists(tax_include)

        query = invoice.join(invoice_tax,
            condition=invoice_tax.invoice == invoice.id).join(tax,
                condition=invoice_tax.tax == tax.id).join(identifier,
                    condition=invoice.party_tax_identifier == identifier.id
                ).join(address, type_='LEFT',
                    condition=address.id == address_min).join(
                    country, type_='LEFT',
                    condition=address.country == country.id).select(
                        invoice.id, invoice.party, Extract('MONTH', date),
                        invoice.aeat347_operation_key,
                        code_expr, country_code_expr, province_code_expr,
                        amount,
                        where=condition,
                        group_by=[invoice.id, date, invoice.party,
                            invoice.aeat347_operation_key,
                            code_expr, country_code_expr, province_code_expr])
        cursor.execute(*query)
        return cursor.fetchall()

    @classmethod
    @ModelView.button
    @Workflow.transition('calculated')
    def calculate(cls, reports):
        pool = Pool()
        Operation = pool.get('aeat.347.report.party')
        Party = pool.get('party.party')

        with Transaction().set_user(0):
            Operation.delete(Operation.search([
                ('report', 'in', [r.id for r in reports])]))

        def is_decimal(value):
            if not isinstance(value, Decimal):
                return Decimal(value)
            return value

        to_create = []
        for report in reports:
            aggregated = defaultdict(lambda: {
                    'amount': _ZERO,
                    'first_quarter_amount': _ZERO,
                    'second_quarter_amount': _ZERO,
                    'third_quarter_amount': _ZERO,
                    'fourth_quarter_amount': _ZERO,
                    })
            details = {}
            records = cls.get_aeat347_party_records(report)

            for (invoice_id, party_id, month, opkey, code, country_code,
                    province_code, amount) in records:
                key = (code, opkey)
                if key not in details:
                    details[key] = {
                        'party': party_id,
                        'country_code': country_code,
                        'province_code': province_code,
                        'invoices': [invoice_id],
                        }
                else:
                    details[key]['invoices'].append(invoice_id)

                amount = is_decimal(amount)
                aggregated[key]['amount'] += amount
                if month <= 3:
                    aggregated[key]['first_quarter_amount'] += amount
                elif month <= 6:
                    aggregated[key]['second_quarter_amount'] += amount
                elif month <= 9:
                    aggregated[key]['third_quarter_amount'] += amount
                else:
                    aggregated[key]['fourth_quarter_amount'] += amount

            for (code, opkey), totals in aggregated.items():
                if totals['amount'] <= report.operation_limit:
                    continue
                detail = details.get((code, opkey))
                if not detail:
                    continue
                party = Party(detail['party'])
                to_create.append({
                        'amount': totals['amount'],
                        'cash_amount': _ZERO,
                        'party_vat': (code
                            if detail['country_code'] == 'ES' else ''),
                        'party_name': party.name[:38],
                        'country_code': detail['country_code'],
                        'province_code': detail['province_code'],
                        'operation_key': opkey,
                        'report': report.id,
                        'community_vat': (code
                            if detail['country_code'] != 'ES' else ''),
                        'first_quarter_amount': totals['first_quarter_amount'],
                        'second_quarter_amount': totals[
                            'second_quarter_amount'],
                        'third_quarter_amount': totals['third_quarter_amount'],
                        'fourth_quarter_amount': totals[
                            'fourth_quarter_amount'],
                        'first_quarter_property_amount': _ZERO,
                        'second_quarter_property_amount': _ZERO,
                        'third_quarter_property_amount': _ZERO,
                        'fourth_quarter_property_amount': _ZERO,
                        'invoices': [('add', detail['invoices'])],
                        })

        if to_create:
            with Transaction().set_user(0, set_context=True):
                Operation.create(to_create)

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
        record.property_amount = self.property_amount or Decimal(0)
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
            data = data.encode('iso-8859-1', errors='ignore')
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
    bdns_call_number = fields.Char('BDNS call number', size=6,
        states={
            'readonly': Eval('operation_key') != 'E',
            })
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
    invoices = fields.Many2Many(
        'aeat.347.report.party-account.invoice', 'party_record', 'invoice',
        'Invoices', readonly=True)

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @staticmethod
    def default_amount():
        return Decimal(0)

    @staticmethod
    def default_cash_amount():
        return Decimal(0)

    @staticmethod
    def default_first_quarter_amount():
        return Decimal(0)

    @staticmethod
    def default_second_quarter_amount():
        return Decimal(0)

    @staticmethod
    def default_third_quarter_amount():
        return Decimal(0)

    @staticmethod
    def default_fourth_quarter_amount():
        return Decimal(0)

    @staticmethod
    def default_first_quarter_property_amount():
        return Decimal(0)

    @staticmethod
    def default_second_quarter_property_amount():
        return Decimal(0)

    @staticmethod
    def default_third_quarter_property_amount():
        return Decimal(0)

    @staticmethod
    def default_fourth_quarter_property_amount():
        return Decimal(0)

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
        record.bdns_call_number = self.bdns_call_number or ''
        record.operation_key = self.operation_key
        record.amount = self.amount
        record.insurance = self.insurance
        record.business_premises_rent = self.business_premises_rent
        record.cash_amount = self.cash_amount or _ZERO
        record.vat_liable_property_amount = self.property_amount \
            or Decimal(0)
        record.fiscalyear_cash_operation = str(
            self.fiscalyear_code_cash_operation or '')
        record.first_quarter_amount = (self.first_quarter_amount or
            Decimal(0))
        record.first_quarter_property_amount = (
            self.first_quarter_property_amount or Decimal(0))
        record.second_quarter_amount = (self.second_quarter_amount or
            Decimal(0))
        record.second_quarter_property_amount = (
            self.second_quarter_property_amount or Decimal(0))
        record.third_quarter_amount = (self.third_quarter_amount or
            Decimal(0))
        record.third_quarter_property_amount = (
            self.third_quarter_property_amount or Decimal(0))
        record.fourth_quarter_amount = (self.fourth_quarter_amount or
            Decimal(0))
        record.fourth_quarter_property_amount = (
            self.fourth_quarter_property_amount or Decimal(0))
        record.cash_vat_operation = self.cash_vat_operation
        record.cash_vat_criteria = (self.cash_vat_criteria or Decimal(0)
            if self.cash_vat_operation else Decimal(0))
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
            (None, ''),
            ('NUM', 'Number'),
            ('KM.', 'Kilometer'),
            ('S/N', 'Without number'),
            ], 'Number type')
    number = fields.Char('Number', size=5)
    number_qualifier = fields.Selection([
            (None, ''),
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
    invoice_lines = fields.Many2Many(
        'aeat.347.report.property-account.invoice.line', 'property_record',
        'line', 'Invoices', readonly=True)

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


class PartyRecordInvoice(ModelSQL):
    'AEAT 347 Party Record - Invoice'
    __name__ = 'aeat.347.report.party-account.invoice'

    party_record = fields.Many2One('aeat.347.report.party', 'Party Record',
        required=True, ondelete='CASCADE')
    invoice = fields.Many2One('account.invoice', 'Invoice',
        required=True, ondelete='CASCADE')


class PropertyRecordInvoiceLine(ModelSQL):
    'AEAT 347 Property Record - Invoice Line'
    __name__ = 'aeat.347.report.property-account.invoice.line'

    property_record = fields.Many2One('aeat.347.report.property',
        'Property Record', required=True, ondelete='CASCADE')
    line = fields.Many2One('account.invoice.line', 'Invoice Line',
        required=True, ondelete='CASCADE')
