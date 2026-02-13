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
from sql.operators import In


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
    situation = fields.Selection([
            ('1', '1 - Spain but Basque Country and Navarra'),
            ('2', '2 - Basque Country and Navarra'),
            ('3', '3 - Spain, without catastral reference'),
            ('4', '4 - Foreign'),
            ], 'Property Situation', required=True)
    road_type = fields.Selection([
            ('CL', 'Street'),
            ('AV', 'Avenue'),
            ('CR', 'Road'),
            ('PZ', 'Square'),
            ('PS', 'Promenade'),
            ('CM', 'Path'),
            ('PG', 'Industrial Park'),
            ('RD', 'Ring Road'),
            ('TR', 'Cross Street'),
            ('GV', 'Grand Avenue'),
            ('RB', 'Rambla'),
            ('SL', 'Plot'),
            ], 'Road Type')
    street = fields.Char('Street')
    number_type = fields.Selection([
            (None, ''),
            ('NUM', 'Number'),
            ('KM.', 'Kilometer'),
            ('S/N', 'Without number'),
            ], 'Number type')
    number = fields.Char('Number')
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
    municipality_code = fields.Char('Municipality Code', size=5,
        help="Get code from INE")
    province_code = fields.Char('Province Code', size=2)
    zip = fields.Char('Zip', size=5)
    aeat347_party = fields.Boolean('347 Party')
    aeat347_property = fields.Boolean('347 Property',
        states={
            'invisible': ~Eval('aeat347_party', False),
            })

    @staticmethod
    def default_situation():
        return '1'

    @staticmethod
    def default_road_type():
        return 'CL'

    @fields.depends('municipality_code', 'province_code')
    def on_change_municipality_code(self):
        if self.municipality_code and not self.province_code:
            self.province_code = self.municipality_code[:2]

    @fields.depends('product')
    def on_change_product(self):
        try:
            super().on_change_product()
        except AttributeError:
            pass
        if self.product:
            self.aeat347_party = bool(self.product.aeat347_party)
            self.aeat347_property = bool(self.product.aeat347_property)

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


class Report(metaclass=PoolMeta):
    __name__ = 'aeat.347.report'

    @classmethod
    def get_aeat347_party_records(cls, report):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        Asset = pool.get('asset')

        results = super().get_aeat347_party_records(report)
        if not results:
            return results

        cursor = Transaction().connection.cursor()
        line = InvoiceLine.__table__()
        asset = Asset.__table__()

        invoice_ids = list({r[0] for r in results})
        query = line.join(asset,
            condition=line.invoice_asset == asset.id).select(
                line.invoice,
                where=In(line.invoice, invoice_ids)
                & (asset.aeat347_party == False))
        cursor.execute(*query)
        exclude_invoices = {invoice_id for (invoice_id,) in cursor.fetchall()}
        if not exclude_invoices:
            return results

        return [r for r in results if r[0] not in exclude_invoices]

    @classmethod
    def get_aeat347_property_records(cls, report):
        pool = Pool()
        PartyOperation = pool.get('aeat.347.report.party')
        PartyOperationInvoice = pool.get('aeat.347.report.party-account.invoice')
        Asset = pool.get('asset')
        Invoice = pool.get('account.invoice')
        InvoiceLine = pool.get('account.invoice.line')

        cursor = Transaction().connection.cursor()
        party_op = PartyOperation.__table__()
        party_op_inv = PartyOperationInvoice.__table__()
        invoice = Invoice.__table__()
        line = InvoiceLine.__table__()
        asset = Asset.__table__()

        query = party_op.join(party_op_inv,
            condition=party_op_inv.party_record == party_op.id).join(invoice,
            condition=invoice.id == party_op_inv.invoice).join(
            line, condition=line.invoice == invoice.id).join(
            asset, condition=line.invoice_asset == asset.id).select(
            asset.id, party_op.party_name, party_op.party_vat, line.id,
            where=(party_op.report == report.id) & (asset.aeat347_party == True)
            & (asset.aeat347_property == True) & (asset.land_register != None)
            & (asset.province_code != None) & (asset.municipality_code != None))
        cursor.execute(*query)
        return cursor.fetchall()

    @classmethod
    def calculate(cls, reports):
        pool = Pool()
        PropertyOperation = pool.get('aeat.347.report.property')
        Asset = pool.get('asset')
        InvoiceLine = pool.get('account.invoice.line')

        super().calculate(reports)

        with Transaction().set_user(0):
            PropertyOperation.delete(PropertyOperation.search([
                ('report', 'in', [r.id for r in reports])]))

        def is_decimal(value):
            if not isinstance(value, Decimal):
                return Decimal(value)
            return value

        to_create = []
        for report in reports:
            records = cls.get_aeat347_property_records(report)
            aggregated = {}
            for asset_id, party_name, party_vat, line_id in records:
                key = (asset_id, party_name, party_vat)
                if key not in aggregated:
                    aggregated[key] = {
                        'lines': set(),
                    }
                aggregated[key]['lines'].add(line_id)

            for (asset_id, party_name, party_vat), values in aggregated.items():
                asset = Asset(asset_id)
                if not asset:
                    continue

                amount = sum(t['amount'] + t['base'] for line in values['lines']
                    for t in InvoiceLine(line)._get_taxes().values())
                to_create.append({
                        'amount': amount,
                        'party_name': party_name[:38] if party_name else '',
                        'party_vat': party_vat,
                        'cadaster_number': asset.land_register,
                        'situation': asset.situation,
                        'road_type': asset.road_type,
                        'street': asset.street[:50] if asset.street else '',
                        'number_type': asset.number_type,
                        'number': asset.number.zfill(5),
                        'number_qualifier': asset.number_qualifier,
                        'block': asset.block,
                        'doorway': asset.doorway,
                        'stair': asset.stair,
                        'floor': asset.floor,
                        'door': asset.door,
                        'complement': (asset.complement[:40]
                            if asset.complement else ''),
                        'city': asset.province,
                        'municipality': asset.municipality,
                        'municipality_code': asset.municipality_code,
                        'province_code': asset.province_code,
                        'zip': asset.municipality_code,
                        'report': report.id,
                        'invoice_lines': [('add', list(values['lines']))],
                })

        if to_create:
            with Transaction().set_user(0, set_context=True):
                PropertyOperation.create(to_create)


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    @fields.depends('lines')
    def _on_change_lines_taxes(self):
        super()._on_change_lines_taxes()

        for line in self.lines:
            if not line.invoice_asset:
                continue
            if not line.invoice_asset.aeat347_party:
                self.aeat347_operation_key = 'empty'
                break

    @classmethod
    def create_aeat347_records(cls, invoices):
        pool = Pool()
        PartyAsset = pool.get('asset.party.party')
        cursor = Transaction().connection.cursor()
        invoice_table = cls.__table__()
        super().create_aeat347_records(invoices)

        to_save_assets = []
        to_empty = []
        for invoice in invoices:
            asset_lines = [
                line for line in invoice.lines
                if getattr(line, 'invoice_asset', None)
                ]
            if not asset_lines:
                continue
            has_party = any(
                line.invoice_asset.aeat347_party
                for line in asset_lines
                if line.invoice_asset)
            if not has_party:
                to_empty.append(invoice.id)
            for line in asset_lines:
                if (invoice.type == 'out'
                        and invoice.party_tax_identifier
                        and line.invoice_asset.aeat347_property):
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
                if to_save_assets:
                    PartyAsset.save(to_save_assets)
                    to_save_assets = []

        if to_empty:
            cursor.execute(*invoice_table.update(
                columns=[invoice_table.aeat347_operation_key],
                values=['empty'],
                where=In(invoice_table.id, to_empty)))


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
