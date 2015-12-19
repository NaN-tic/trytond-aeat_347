#This file is part of aeat_347 module for Tryton.
#The COPYRIGHT file at the top level of this repository contains
#the full copyright notices and license terms.
from sql.functions import Substring
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond import backend
from trytond.transaction import Transaction

__all__ = ['Party']
__metaclass__ = PoolMeta


class Party:
    __name__ = 'party.party'
    include_347 = fields.Boolean('Include on 347', depends=['identifiers'])

    @fields.depends('identifiers', 'include_347')
    def on_change_with_include_347(self, name=None):
        if self.include_347:
            return True
        for identifier in self.identifiers:
            if identifier.code and identifier.code[:2] == 'ES':
                return True
        return False

    @classmethod
    def __register__(cls, module_name):
        TableHandler = backend.get('TableHandler')
        cursor = Transaction().cursor
        table = TableHandler(cursor, cls, module_name)

        created_347 = table.column_exist('include_347')

        super(Party, cls).__register__(module_name)

        #We need to reload table as it may be modified by __register__
        table = TableHandler(cursor, cls, module_name)
        if (not created_347 and table.column_exist('include_347')):
            sql_table = cls.__table__()
            identifier = Pool().get('party.identifier').__table__()
            query = identifier.select(identifier.party, where=Substring(
                    identifier.code, 1, 2) == 'ES')
            cursor.execute(*sql_table.update(
                    columns=[sql_table.include_347], values=[True],
                    where=(sql_table.id.in_(query))))

    @classmethod
    def create(cls, vlist):
        vlist = [x.copy() for x in vlist]
        for vals in vlist:
            if vals.get('vat_country') == 'ES':
                vals['include_347'] = True
        return super(Party, cls).create(vlist)

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        args = []
        for parties, values in zip(actions, actions):
            if values.get('vat_country') == 'ES':
                values['include_347'] = True
            args.extend((parties, values))
        return super(Party, cls).write(*args)
