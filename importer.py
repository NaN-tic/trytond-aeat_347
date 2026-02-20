# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.i18n import gettext
from trytond.modules.importer.tools import ImporterModel, Setup, Cache


class ImporterAsset(ImporterModel):
    'Importer Asset'
    __name__ = 'importer.asset'

    asset_id = fields.Integer('Asset ID')
    company_id = fields.Char('Company ID')
    company_name = fields.Char('Company Name')
    code = fields.Char('Code')
    name = fields.Char('Name')
    active = fields.Boolean('Active')
    type = fields.Char('Type')
    product_code = fields.Char('Product Code')
    product_name = fields.Char('Product Name')
    aeat347_party = fields.Boolean('347 Party')
    aeat347_property = fields.Boolean('347 Property')
    situation = fields.Char('Property Situation')
    road_type = fields.Char('Road Type')
    street = fields.Char('Street')
    number_type = fields.Char('Number type')
    number = fields.Char('Number')
    number_qualifier = fields.Char('Number Qualifier')
    block = fields.Char('Block')
    doorway = fields.Char('Doorway')
    stair = fields.Char('Stair')
    floor = fields.Char('Floor')
    door = fields.Char('Door')
    complement = fields.Char('Complement')
    city = fields.Char('City')
    municipality = fields.Char('Municipality')
    municipality_code = fields.Char('Municipality Code')
    province_code = fields.Char('Province Code')
    zip = fields.Char('Zip')
    land_register = fields.Char('Land Register')

    @classmethod
    def importer_start(cls):
        super().importer_start()
        cache = Setup.get().cache
        cache.companies_id = Cache('company.company', 'id', required=False)
        cache.companies_by_name = Cache('company.company',
            key=lambda x: x.party.name and x.party.name.lower(),
            required=False)
        cache.products_by_code = Cache('product.product', 'code',
            domain=[
                ('code', '!=', None),
                ('code', '!=', ''),
                ('type', 'in', ['assets', 'goods']),
                ],
            required=False)
        cache.products_by_name = Cache('product.product', 'name',
            domain=[
                ('type', 'in', ['assets', 'goods']),
                ],
            required=False)
        cache.assets_by_id = Cache('asset', 'id',
            context={'active_test': False}, required=False)
        cache.assets_by_code = Cache('asset', 'code',
            context={'active_test': False}, required=False)

    @classmethod
    def importer_import(cls, records):
        pool = Pool()
        Asset = pool.get('asset')
        Company = pool.get('company.company')

        setup = Setup.get()
        cache = setup.cache

        to_save = []
        for record in records:
            setup.current_record = record
            company = None
            if 'company_id' in setup.fields and record.company_id:
                company = cache.companies_id.get(record.company_id)
            if (not company and 'company_name' in setup.fields
                    and record.company_name):
                company = cache.companies_by_name.get(record.company_name)
                if not company:
                    record.importer_error(
                        gettext('aeat_347.msg_asset_not_found',
                            asset_id=record.asset_id,
                            asset_code=record.code))
                    continue
            if not company:
                company_id = Transaction().context.get('company')
                if company_id:
                    company = Company(company_id)

            asset = None
            code = (record.code
                if 'code' in setup.fields and record.code else None)
            if 'asset_id' in setup.fields and record.asset_id:
                asset = cache.assets_by_id.get(record.asset_id)
            if not asset and code:
                asset = cache.assets_by_code.get(code)
                if asset and asset.company != company:
                    asset = None

            created = False
            if not asset:
                asset = Asset()
                created = True
                if company:
                    asset.company = company
                else:
                    record.importer_error(
                        gettext('aeat_347.msg_asset_not_found',
                            asset_id=record.asset_id,
                            asset_code=code))
                    continue
                if code:
                    asset.code = code

            product = None
            if 'product_code' in setup.fields and record.product_code:
                product = cache.products_by_code.get(record.product_code)
            if (not product and 'product_name' in setup.fields
                    and record.product_name):
                product = cache.products_by_name.get(record.product_name)
            if product:
                asset.product = product
            elif record.product_code or record.product_name:
                record.importer_error(
                    gettext('importer.msg_asset_product_not_found',
                        product=record.product_code or record.product_name))

            for field in setup.fields:
                if field in ('asset_id', 'code', 'company_id', 'company_name',
                        'product_code', 'product_name'):
                    continue
                if field in cls._fields:
                    setattr(asset, field, getattr(record, field))

            if created and asset.code and company:
                cache.assets_by_code[asset.code] = asset
            to_save.append((asset, record))

        cls.importer_save(to_save)
        return [x[0] for x in to_save]


class Importer(metaclass=PoolMeta):
    __name__ = 'importer'

    @classmethod
    def _get_methods(cls):
        methods = super()._get_methods()
        methods.update({
                'aeat_347_asset': {
                    'string': 'Asset',
                    'model': 'importer.asset',
                    'chunked': True,
                    },
                })
        return methods
