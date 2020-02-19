# -*- coding: utf-8 -*-
# Copyright 2020 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from openerp.osv import orm, fields

from openerp.addons.connector.unit.mapper import (mapping, ImportMapper)
from openerp.addons.magentoerpconnect.unit.backend_adapter import (
    GenericAdapter)
from openerp.addons.magentoerpconnect.unit.binder import MagentoModelBinder
from openerp.addons.magentoerpconnect.unit.import_synchronizer import (
    DirectBatchImport,
    MagentoImportSynchronizer,
)
from openerp.addons.magentoerpconnect.backend import magento2000


class MagentoCrmClaimReason(orm.Model):
    _name = 'magento.crm.claim.reason'
    _inherit = 'magento.binding'
    _description = 'Magento Return Reason'

    _columns = {
        'name': fields.char(u"Name", size=64),
    }

    _sql_constraints = [
        ('magento_uniq', 'unique(backend_id, magento_id)',
         'A Claim Reason with the same ID on Magento already exists.'),
    ]


@magento2000
class CrmClaimReasonAdapter(GenericAdapter):
    _model_name = 'magento.crm.claim.reason'
    _magento2_model = 'returnsAttributeMetadata'


@magento2000
class CrmClaimReasonBatchImporter(DirectBatchImport):
    _model_name = 'magento.crm.claim.reason'

    def run(self, filters=None):
        """Overridden to not use 'search' to get claim reasons to import
        but simply parse the result of the API endpoint.
        """
        metadata = self.backend_adapter._call(
            self.backend_adapter._magento2_model)
        reasons_data = {}
        for data in metadata:
            if data["attribute_code"] != "reason":
                continue
            reasons_data = data
        for reason_data in reasons_data.get("options", []):
            if not reason_data["value"]:
                continue
            importer = self.get_connector_unit_for_model(
                MagentoImportSynchronizer)
            importer.run(reason_data["value"], data=reason_data)


@magento2000
class CrmClaimReasonImporter(MagentoImportSynchronizer):
    _model_name = 'magento.crm.claim.reason'


@magento2000
class CrmClaimReasonImportMapper(ImportMapper):
    _model_name = 'magento.crm.claim.reason'

    direct = [("label", "name")]

    @mapping
    def backend_id(self, record):
        return {'backend_id': self.backend_record.id}


@magento2000
class MagentoCrmClaimReasonBinder(MagentoModelBinder):
    _model_name = "magento.crm.claim.reason"
