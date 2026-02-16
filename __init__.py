# This file is part aeat_347 module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import aeat
from . import invoice
from . import tax
from . import asset
from . import product


def register():
    Pool.register(
        aeat.Report,
        aeat.PartyRecord,
        aeat.PropertyRecord,
        aeat.PartyRecordInvoice,
        aeat.PropertyRecordInvoiceLine,
        invoice.Invoice,
        invoice.InvoiceLine,
        invoice.Reasign347Start,
        invoice.Reasign347End,
        product.Product,
        tax.TaxTemplate,
        tax.Tax,
        module='aeat_347', type_='model')
    Pool.register(
        asset.Asset,
        asset.Report,
        asset.Party,
        asset.AssetParty,
        module='aeat_347', type_='model', depends=['asset', 'party',
            'asset_property'])
    Pool.register(
        asset.Invoice,
        module='aeat_347', type_='model', depends=['asset', 'party',
            'asset_invoice', 'asset_property'])
    Pool.register(
        asset.InvoiceContract,
        module='aeat_347', type_='model', depends=['asset', 'party',
            'asset_invoice', 'asset_property', 'contract'])
    Pool.register(
        invoice.Reasign347,
        module='aeat_347', type_='wizard')
