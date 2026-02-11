# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval


class Product(metaclass=PoolMeta):
    __name__ = 'product.product'

    aeat347_party = fields.Boolean('347 Party')
    aeat347_property = fields.Boolean('347 Property',
        states={
            'invisible': ~Eval('aeat347_party', False),
            })
