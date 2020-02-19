# -*- encoding: utf-8 -*-
###############################################################################
#                                                                             #
#   Copyright (C) 2012 Akretion David BEAL <david.beal@akretion.com>          #
#   Copyright (C) 2012 Akretion Sébastien BEAU <sebastien.beau@akretion.com>  #
#   Copyright (C) 2014 Akretion Chafique DELLI <chafique.delli@akretion.com>  #
#   Copyright (C) 2020 Camptocamp SA                                          #
#                                                                             #
#   This program is free software: you can redistribute it and/or modify      #
#   it under the terms of the GNU Affero General Public License as            #
#   published by the Free Software Foundation, either version 3 of the        #
#   License, or (at your option) any later version.                           #
#                                                                             #
#   This program is distributed in the hope that it will be useful,           #
#   but WITHOUT ANY WARRANTY; without even the implied warranty of            #
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the             #
#   GNU Affero General Public License for more details.                       #
#                                                                             #
#   You should have received a copy of the GNU Affero General Public License  #
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.     #
#                                                                             #
###############################################################################

import logging
import xmlrpclib

from openerp.osv import orm, fields

from openerp.addons.connector.exception import IDMissingInBackend
from openerp.addons.connector.unit.mapper import (mapping,
                                                  ImportMapper,
                                                  ExportMapper)
from openerp.addons.magentoerpconnect.unit.binder import MagentoModelBinder
from openerp.addons.magentoerpconnect.unit.backend_adapter import (
    GenericAdapter)
from openerp.addons.magentoerpconnect.unit.import_synchronizer import (
    DelayedBatchImport,
    MagentoImportSynchronizer,
    AddCheckpoint)
from openerp.addons.magentoerpconnect.unit.export_synchronizer import (
    MagentoExporter)
from openerp.addons.magentoerpconnect.backend import magento
from openerp.addons.connector.queue.job import job
from openerp.addons.magentoerpconnect.connector import get_environment
from openerp.addons.connector.event import on_record_write
import openerp.addons.magentoerpconnect.consumer as magentoerpconnect

_logger = logging.getLogger(__name__)


class CrmClaim(orm.Model):
    _inherit = "crm.claim"

    _columns = {
        'magento_bind_ids': fields.one2many(
            'magento.crm.claim', 'openerp_id',
            string="Magento Bindings"),
        'claim_line_ids': fields.one2many('claim.line',
                                          'claim_id',
                                          'Claim Lines'),
        'claim_comment_ids': fields.one2many('mail.message',
                                             'claim_id',
                                             'Claim Comments'),
        'claim_attachment_ids': fields.one2many('ir.attachment',
                                                'claim_id',
                                                'Claim Attachments'),
        'claim_id': fields.integer('Claim ID',
                                   help="'res_id' field in OpenErp"),
    }


class MagentoCrmClaim(orm.Model):
    _name = 'magento.crm.claim'
    _inherit = 'magento.binding'
    _description = 'Magento Claim'
    _inherits = {'crm.claim': 'openerp_id'}

    _columns = {
        'openerp_id': fields.many2one('crm.claim',
                                      string='Claim',
                                      required=True,
                                      ondelete='cascade'),
        'magento_claim_line_ids': fields.one2many('magento.claim.line',
                                                  'magento_claim_id',
                                                  'Magento Claim Lines'),
        'magento_claim_comment_ids': fields.one2many('magento.claim.comment',
                                                     'magento_claim_id',
                                                     'Magento Claim Comments'),
        'magento_claim_attachment_ids': fields.one2many(
            'magento.claim.attachment',
            'magento_claim_id',
            'Magento Claim Attachments'),
        'magento_claim_id': fields.integer('Magento Claim ID',
                                           help="'rma_id' field in Magento"),
    }

    _sql_constraints = [
        ('magento_uniq', 'unique(backend_id, magento_id)',
         'A Claim with the same ID on Magento already exists.'),
    ]


class ClaimLine(orm.Model):
    _inherit = "claim.line"

    _columns = {
        'magento_bind_ids': fields.one2many(
            'magento.claim.line',
            'openerp_id',
            string="Magento Bindings"),
        'claim_id': fields.many2one(
            'crm.claim',
            string='Claim',
            ondelete='cascade'),
        'order_line_id': fields.many2one(
            'sale.order.line',
            'Order Line',
            help="The sale order line related to the returned product"),
        'sequence': fields.integer('Sequence',
                                   help="Gives the sequence "
                                   "of this line when displaying the claim."),
    }


class MagentoClaimLine(orm.Model):
    _name = 'magento.claim.line'
    _inherit = 'magento.binding'
    _description = 'Magento Claim Line'
    _inherits = {'claim.line': 'openerp_id'}

    def _get_lines_from_claim(self, cr, uid, ids, context=None):
        line_obj = self.pool.get('magento.claim.line')
        return line_obj.search(cr, uid,
                               [('magento_claim_id', 'in', ids)],
                               context=context)
    _columns = {
        'openerp_id': fields.many2one('claim.line',
                                      string='Claim Line',
                                      required=True,
                                      ondelete='cascade'),
        'magento_claim_id': fields.many2one('magento.crm.claim',
                                            string='Magento Claim',
                                            ondelete='cascade'),
        'backend_id': fields.related(
            'magento_claim_id',
            'backend_id',
            type='many2one',
            relation='magento.backend',
            string='Magento Backend',
            store={
                'magento.claim.line': (
                    lambda self, cr, uid, ids, c=None: ids,
                    ['magento_claim_id'],
                    10),
                'magento.crm.claim': (
                    _get_lines_from_claim, ['backend_id'], 20),
            },
            readonly=True),
    }

    _sql_constraints = [
        ('magento_uniq', 'unique(backend_id, magento_id)',
         'A Claim Line with the same ID on Magento already exists.'),
    ]

    def create(self, cr, uid, vals, context=None):
        magento_claim_id = vals['magento_claim_id']
        info = self.pool['magento.crm.claim'].read(
            cr, uid, [magento_claim_id], ['openerp_id', 'description'],
            context=context)
        claim_id = info[0]['openerp_id']
        descr = info[0]['description']
        vals['claim_id'] = claim_id[0]
        vals['claim_descr'] = descr
        return super(MagentoClaimLine, self).create(
            cr, uid, vals, context=context)


@magento
class CrmClaimAdapter(GenericAdapter):
    _model_name = ['magento.crm.claim']
    _magento_model = 'rma'

    def _call(self, method, arguments):
        try:
            return super(CrmClaimAdapter, self)._call(method, arguments)
        except xmlrpclib.Fault as err:
            # this is the error in the Magento API
            # when the claim does not exist
            if err.faultCode == 100:
                raise IDMissingInBackend
            else:
                raise

    def search(self, filters=None, from_date=None):
        """ Search records according to some criterias
        and returns a list of ids

        :rtype: list
        """
        if filters is None:
            filters = {}
        if from_date is not None:
            filters = from_date.strftime('%Y/%d/%m %H:%M:%S')
        return [int(row['rma_id']) for row
                in self._call('%s.list' % self._magento_model,
                              [filters] if filters else [{}])]

    def read(self, id, storeview_id=None, attributes=None):
        """ Returns the information of a record

        :rtype: dict
        """
        return self._call('%s.get' % self._magento_model,
                          [int(id), storeview_id, attributes, 'id'])

    def write(self, id, vals):
        """
        Update claim state un Magento.
        """
        return self._call(
            '%s.update' % self._magento_model, [id, vals['state']])


@magento
class CrmClaimBatchImport(DelayedBatchImport):
    """ Import the Magento Claims.

    For every claim in the list, a delayed job is created.
    Import from a date
    """
    _model_name = ['magento.crm.claim']

    def _import_record(self, record_id, **kwargs):
        """ Import the record directly """
        return super(CrmClaimBatchImport, self)._import_record(
            record_id, max_retries=0, priority=5)

    def run(self, filters=None):
        """ Run the synchronization """
        from_date = filters.pop('from_date', None)
        record_ids = self.backend_adapter.search(filters, from_date)
        _logger.info('search for magento claims %s returned %s',
                     filters, record_ids)
        for record_id in record_ids:
            self._import_record(record_id)


@magento
class CrmClaimImport(MagentoImportSynchronizer):
    _model_name = ['magento.crm.claim']

    def _import_dependencies(self):
        record = self.magento_record
        order_binder = self.get_binder_for_model('magento.sale.order')
        order_importer = self.get_connector_unit_for_model(
            MagentoImportSynchronizer, 'magento.sale.order')
        if order_binder.to_openerp(record['order_increment_id']) is None:
            order_importer.run(record['order_increment_id'])

    def _create(self, data):
        openerp_binding_id = super(CrmClaimImport, self)._create(data)
        return openerp_binding_id

    def _must_skip(self):
        if self._get_binding_id():
            _logger.debug('Claim %s already exist, skip import'
                          % self.magento_id)
            return True
        return False


@magento
class CrmClaimImportMapper(ImportMapper):
    _model_name = 'magento.crm.claim'

    direct = [('subject', 'name'),
              ('description', 'description'),
              ('created_at', 'date'),
              ]

    children = [
        ('items', 'magento_claim_line_ids', 'magento.claim.line'),
        ('comments', 'magento_claim_comment_ids', 'magento.claim.comment'),
        ('attachments',
         'magento_claim_attachment_ids',
         'magento.claim.attachment'),
    ]

    def _map_child(self, map_record, from_attr, to_attr, model_name):
        if from_attr in map_record.source:
            return super(CrmClaimImportMapper, self)._map_child(
                map_record, from_attr, to_attr, model_name)

    @mapping
    def partner_id(self, record):
        partner_ids = self.session.search(
            'magento.res.partner',
            [['magento_id', '=', record['customer_id']]])
        partner = self.session.browse('magento.res.partner', partner_ids[0])
        partner_id = partner.openerp_id.id
        address = self.session.browse('res.partner', partner_id)
        return {'partner_id': partner_id,
                'email_from': address.email,
                'partner_phone': address.phone}

    @mapping
    def ref(self, record):
        order_ids = self.session.search(
            'sale.order',
            [['name', '=', record['order_increment_id']]])
        if order_ids:
            ref = 'sale.order,' + str(order_ids[0])
            invoice_ids = self.session.search('account.invoice', [
                ('sale_ids', 'in', order_ids),
                ('type', '=', 'out_invoice'),
            ])
            order = self.session.browse('sale.order', order_ids[0])
            return {
                'ref': ref,
                'invoice_id': invoice_ids and invoice_ids[0],
                'order_id': order_ids[0],
                'delivery_address_id': order.partner_shipping_id.id}

    @mapping
    def state(self, record):
        sess = self.session
        model_data_obj = sess.pool['ir.model.data']
        __, stage_id = model_data_obj.get_object_reference(
                    sess.cr, sess.uid, 'crm_claim', 'stage_claim1')
        return {'stage_id': stage_id}

    @mapping
    def backend_id(self, record):
        return {'backend_id': self.backend_record.id}

    @mapping
    def number(self, record):
            return {'number': record['rma_id']}


@magento
class ClaimLineImportMapper(ImportMapper):
    _model_name = 'magento.claim.line'

    direct = [('qty', 'product_returned_quantity'), ]

    @mapping
    def name(self, record):
        order_line_ids = self.session.search(
            'magento.sale.order.line',
            [['magento_id', '=', record['order_item_id']]])
        order_line = self.session.browse(
            'magento.sale.order.line', order_line_ids[0])
        order_name = order_line.magento_order_id.magento_id
        return {'name': order_name}

    @mapping
    def order_line_id(self, record):
        order_line_ids = self.session.search(
            'magento.sale.order.line',
            [['magento_id', '=', record['order_item_id']]])
        order_line = self.session.browse(
            'magento.sale.order.line', order_line_ids[0])
        order_line_id = order_line.openerp_id.id
        return {'order_line_id': order_line_id}

    @mapping
    def product_id(self, record):
        order_line_ids = self.session.search(
            'magento.sale.order.line',
            [['magento_id', '=', record['order_item_id']]])
        order_line = self.session.browse(
            'magento.sale.order.line', order_line_ids[0])
        product_id = order_line.openerp_id.product_id.id
        return {'product_id': product_id}

    @mapping
    def claim_origine(self, record):
        claim_origine = 'other'
        return {'claim_origine': claim_origine}

    @mapping
    def magento_id(self, record):
        return {'magento_id': record['rma_item_id']}

    @mapping
    def invoice_line_id(self, record):
        order_line_ids = self.session.search(
            'magento.sale.order.line',
            [['magento_id', '=', record['order_item_id']]])
        order_line = self.session.browse(
            'magento.sale.order.line', order_line_ids[0])
        product_id = order_line.openerp_id.product_id.id
        invoice_ids = self.session.search('account.invoice', [
            ('sale_ids', 'in', [order_line.order_id.id]),
            ('type', '=', 'out_invoice'),
            ])
        if not invoice_ids:
            return {}
        line_ids = self.session.search(
            'account.invoice.line',
            [['invoice_id', 'in', invoice_ids],
             ['product_id', '=', product_id]])
        return {'invoice_line_id': line_ids and line_ids[0]}

    @mapping
    def location_dest_id(self, record):
        order_line_ids = self.session.search(
            'magento.sale.order.line',
            [['magento_id', '=', record['order_item_id']]])
        order_line = self.session.browse(
            'magento.sale.order.line', order_line_ids[0])
        product_id = order_line.openerp_id.product_id.id
        warehouse_id = self.session.pool['crm.claim']._get_default_warehouse(
            self.session.cr, self.session.uid, self.session.context)
        loc_id = self.session.pool['claim.line'].get_destination_location(
            self.session.cr,
            self.session.uid,
            product_id,
            warehouse_id,
            self.session.context)
        return {'location_dest_id': loc_id}


@job
def crm_claim_import_batch(session, model_name, backend_id, filters=None):
    """ Prepare a batch import of claims from Magento """
    if filters is None:
        filters = {}
    assert 'magento_storeview_id' in filters, \
           'Missing information about Magento Storeview'
    env = get_environment(session, model_name, backend_id)
    importer = env.get_connector_unit(CrmClaimBatchImport)
    importer.run(filters)


@on_record_write(model_names='crm.claim')
def delay_export_all_bindings(session, model_name, record_id, vals):
    if 'stage_id' in vals:
        magentoerpconnect.delay_export_all_bindings(
            session, model_name, record_id, vals=vals)


@magento
class MagentoCrmClaimExporter(MagentoExporter):
    """ Export claim state to Magento. """
    _model_name = ['magento.crm.claim']

    def _should_import(self):
        return False


@magento
class MagentoCrmClaimExportMapper(ExportMapper):
    _model_name = 'magento.crm.claim'

    @mapping
    def stage_id(self, record):
        state = record.stage_id.name
        return {'state': state}


@magento
class MagentoClaimBinder(MagentoModelBinder):

    _model_name = [
        'magento.crm.claim',
        'magento.claim.line',
        ]


@magento
class CrmClaimAddCheckpoint(AddCheckpoint):

    _model_name = [
        'magento.crm.claim',
        'magento.claim.line',
        'magento.claim.comment',
        'magento.claim.attachment'
        ]
