# -*- encoding: utf-8 -*-
###############################################################################
#                                                                             #
#   Copyright (C) 2012 Akretion David BEAL <david.beal@akretion.com>          #
#   Copyright (C) 2012 Akretion Sébastien BEAU <sebastien.beau@akretion.com>  #
#   Copyright (C) 2014 Akretion Chafique DELLI <chafique.delli@akretion.com>  #
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

from openerp.tools.translate import _
from openerp.osv import orm, fields

from openerp.addons.connector.event import on_record_create
from openerp.addons.connector.exception import IDMissingInBackend
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.unit.mapper import (mapping,
                                                  only_create,
                                                  ImportMapper,
                                                  ExportMapper)
from openerp.addons.magentoerpconnect.backend import magento
from openerp.addons.magentoerpconnect.connector import get_environment
from openerp.addons.magentoerpconnect.unit.backend_adapter import (
    GenericAdapter)
from openerp.addons.magentoerpconnect.unit.binder import MagentoModelBinder
from openerp.addons.magentoerpconnect.unit.export_synchronizer import (
    MagentoExporter)
from openerp.addons.magentoerpconnect.unit.import_synchronizer import (
    DelayedBatchImport,
    MagentoImportSynchronizer,
)

_logger = logging.getLogger(__name__)


class MailMessage(orm.Model):
    _inherit = "mail.message"

    _columns = {
        'magento_claim_bind_ids': fields.one2many(
            'magento.claim.comment',
            'openerp_id',
            string="Magento Bindings"),
        'claim_id': fields.many2one(
            'crm.claim',
            string='Claim',
            ondelete='cascade'),
    }


class MagentoClaimComment(orm.Model):
    _name = 'magento.claim.comment'
    _inherit = 'magento.binding'
    _description = 'Magento Claim Comment'
    _inherits = {'mail.message': 'openerp_id'}

    def _get_comments_from_claim(self, cr, uid, ids, context=None):
        comment_obj = self.pool.get('magento.claim.comment')
        return comment_obj.search(cr, uid,
                                  [('magento_claim_id', 'in', ids)],
                                  context=context)

    _columns = {
        'openerp_id': fields.many2one('mail.message',
                                      string='Claim Comment',
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
                'magento.claim.comment': (
                    lambda self, cr, uid, ids, c=None: ids,
                    ['magento_claim_id'],
                    10),
                'magento.crm.claim': (
                    _get_comments_from_claim,
                    ['backend_id'],
                    20),
            },
            readonly=True),
    }

    _sql_constraints = [
        ('magento_uniq', 'unique(backend_id, magento_id)',
         'A Claim Comment with the same ID on Magento already exists.'),
    ]

    def create(self, cr, uid, vals, context=None):
        magento_claim_id = vals['magento_claim_id']
        info = self.pool['magento.crm.claim'].read(cr, uid,
                                                   [magento_claim_id],
                                                   ['openerp_id'],
                                                   context=context)
        claim_id = info[0]['openerp_id'][0]
        vals['claim_id'] = claim_id
        vals['res_id'] = claim_id
        vals['model'] = 'crm.claim'
        if 'subtype_id' in vals.keys() and not vals['subtype_id']:
            vals['subject'] = 'Customer Message'
        return super(MagentoClaimComment, self).create(
            cr, uid, vals, context=context)


@magento
class ClaimCommentAdapter(GenericAdapter):
    _model_name = ['magento.claim.comment']
    _magento_model = 'rma_comment'

    def _call(self, method, arguments):
        try:
            return super(ClaimCommentAdapter, self)._call(method, arguments)
        except xmlrpclib.Fault as err:
            # this is the error in the Magento API
            # when the claim does not exist
            if err.faultCode == 100:
                raise IDMissingInBackend
            else:
                raise

    def search_read(self, filters=None, from_date=None):
        """ Search records according to some criterias
        and returns their information
        """
        backend = self.session.browse(
            'magento.backend', self.backend_record.id)
        if filters is None:
            filters = {}
        if from_date is not None:
            filters = from_date.strftime('%Y/%d/%m %H:%M:%S')
        elif backend:
            filters = backend.import_claims_from_date
        return self._call('%s.list' % self._magento_model,
                          [filters, 1] if filters else [{}, 1])

    def create(self, is_customer, message, created_at, rma_id):
        """ Create a claim comment on the external system """
        return self._call(
            '%s.create' % self._magento_model, [{
                'is_customer': is_customer,
                'message': message,
                'created_at': created_at,
                'rma_id': rma_id
                }]
            )


@magento
class ClaimCommentBatchImport(DelayedBatchImport):
    """ Import the Magento Claim Comments from a date.
    """
    _model_name = ['magento.claim.comment']

    def _import_record(self, record, **kwargs):
        """ Import the record directly """
        # Set priority to 20 to have more chance that it would be executed
        # after the claim import
        return super(ClaimCommentBatchImport, self)._import_record(
            record, max_retries=0, priority=20)

    def run(self, filters=None):
        """ Run the synchronization """
        record_ids = []
        index = 0
        from_date = filters.pop('from_date', None)
        records = self.backend_adapter.search_read(filters, from_date)
        for record in records:
            record_ids.append(int(records[index]['rma_comment_id']))
            index += 1
            record['message'] = record['message'].encode('utf-8')
            self._import_record(record)
        _logger.info('search for magento claim comments from %s returned %s',
                     filters, record_ids)


@magento
class ClaimCommentImport(MagentoImportSynchronizer):
    _model_name = ['magento.claim.comment']

    def run(self, magento_record, force=False):
        """ Run the synchronization

        :param magento_record: the record on Magento
        """

        self.magento_id = int(magento_record['rma_comment_id'])
        self.magento_record = magento_record

        skip = self._must_skip()
        if skip:
            return skip

        binding_id = self.binder.to_openerp(self.magento_id)

        if not force and self._is_uptodate(binding_id):
            return _('Already up-to-date.')
        self._before_import()

        # import the missing linked resources
        self._import_dependencies()

        map_record = self._map_data()

        if binding_id:
            record = self._update_data(map_record)
            self._update(binding_id, record)
        else:
            record = self._create_data(map_record)
            binding_id = self._create(record)

        self.binder.bind(self.magento_id, binding_id)

        self._after_import(binding_id)

    def _import_dependencies(self):
        record = self.magento_record
        claim_binder = self.get_binder_for_model('magento.crm.claim')
        claim_importer = self.get_connector_unit_for_model(
            MagentoImportSynchronizer, 'magento.crm.claim')
        if not claim_binder.to_openerp(record['rma_id']):
            claim_importer.run(record['rma_id'])

    def _create(self, data):
        # we test whether the comment already exists in 'magento.claim.comment'
        # it may have been created during the import dependencies (RMA import)
        openerp_binding_id = False
        if 'magento_id' in data and data.get('magento_id'):
            openerp_binding_id = self.session.search(
                self.model._name, [
                    ('magento_id', '=', data['magento_id']),
                    ('backend_id', '=', self.backend_record.id)
                ])
        if not openerp_binding_id:
            openerp_binding_id = super(ClaimCommentImport, self)._create(data)
        else:
            openerp_binding_id = openerp_binding_id[0]
        return openerp_binding_id


@magento
class ClaimCommentImportMapper(ImportMapper):
    _model_name = 'magento.claim.comment'

    direct = [
        ('message', 'body'),
        ('created_at', 'date')
        ]

    @mapping
    def subtype_id(self, record):
        if record['is_customer'] == '1':
            subtype_id = False
            return {'subtype_id': subtype_id}

    @mapping
    def type(self, record):
        type = 'comment'
        return {'type': type}

    @mapping
    def magento_id(self, record):
        return {'magento_id': record['rma_comment_id']}

    @mapping
    def magento_claim_id(self, record):
        magento_claim_ids = self.session.search(
            'magento.crm.claim',
            [
                ('magento_id', '=', int(record['rma_id'])),
                ('backend_id', '=', self.backend_record.id)
            ])
        if magento_claim_ids:
            claim_id = magento_claim_ids[0]
            return {'magento_claim_id': claim_id}


@magento
class MagentoClaimCommentExporter(MagentoExporter):
    """ Export claim comments seller to Magento """
    _model_name = ['magento.claim.comment']

    def _should_import(self):
        return False

    def _create(self, data):
        """ Create the Magento record """
        # special check on data before export
        self._validate_data(data)
        return self.backend_adapter.create(data['is_customer'],
                                           data['message'],
                                           data['created_at'],
                                           data['rma_id'])


@magento
class ClaimCommentExportMapper(ExportMapper):
    _model_name = 'magento.claim.comment'

    @only_create
    @mapping
    def is_customer(self, record):
        return {'is_customer': '0'}

    @only_create
    @mapping
    def created_at(self, record):
        return {'created_at': record.date}

    @only_create
    @mapping
    def message(self, record):
        return {'message': record.body}

    @only_create
    @mapping
    def rma_id(self, record):
        return {'rma_id': record.magento_claim_id.magento_id}


@magento
class MagentoClaimCommentBinder(MagentoModelBinder):

    _model_name = [
        'magento.claim.comment',
        ]

    def bind(self, external_id, binding_id):
        if isinstance(external_id, dict) and external_id.get('rma_comment_id'):
            external_id = external_id['rma_comment_id']
        return super(MagentoClaimCommentBinder, self).bind(
            external_id, binding_id)


@on_record_create(model_names='mail.message')
def comment_create_bindings(session, model_name, record_id, vals):
    """
    Create a ``magento.claim.comment`` record. This record will then
    be exported to Magento.
    """
    if vals['model'] != 'crm.claim':
        return
    comment = session.browse(model_name, record_id)
    subtype_ids = session.search('mail.message.subtype',
                                 [['name', '=', 'Discussions']])
    magento_claim = session.search('magento.crm.claim',
                                   [['openerp_id', '=', comment.res_id]])
    if comment.type == 'comment' \
            and comment.subtype_id.id == subtype_ids[0] \
            and magento_claim:
        claim = session.browse('crm.claim', comment.res_id)
        for magento_claim in claim.magento_bind_ids:
            session.create('magento.claim.comment',
                           {'backend_id': magento_claim.backend_id.id,
                            'openerp_id': comment.id,
                            'magento_claim_id': magento_claim.id})


@on_record_create(model_names='magento.claim.comment')
def delay_export_claim_comment(session, model_name, record_id, vals):
    """
    Delay the job to export the magento claim comment.
    """
    magento_comment = session.browse(model_name, record_id)
    subtype_ids = session.search('mail.message.subtype',
                                 [['name', '=', 'Discussions']])
    if magento_comment.openerp_id.type == 'comment' \
            and magento_comment.openerp_id.subtype_id.id == subtype_ids[0]:
        export_claim_comment.delay(session, model_name, record_id)


@job
def claim_comment_import_batch(session, model_name, backend_id, filters=None):
    """ Prepare a batch import of claim comments from Magento """
    if filters is None:
        filters = {}
    assert 'magento_storeview_id' in filters, \
           'Missing information about Magento Storeview'
    env = get_environment(session, model_name, backend_id)
    importer = env.get_connector_unit(ClaimCommentBatchImport)
    importer.run(filters)


@job
def export_claim_comment(session, model_name, record_id):
    """ Export a claim comment. """
    comment = session.browse(model_name, record_id)
    backend_id = comment.backend_id.id
    env = get_environment(session, model_name, backend_id)
    comment_exporter = env.get_connector_unit(MagentoClaimCommentExporter)
    return comment_exporter.run(record_id)
