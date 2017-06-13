# -*- coding: utf-8 -*-
# © 2013 Guewen Baconnier,Camptocamp SA,Akretion
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{'name': 'Magento Connector',
 'version': '10.0.1.0.0',
 'category': 'Connector',
 'depends': ['account',
             'product',
             'delivery',
             'sale_stock',
             'connector_ecommerce',
             # 'product_multi_category',
             ],
 'external_dependencies': {
     'python': ['magento'],
 },
 'author': "Camptocamp,Akretion,Sodexis,Odoo Community Association (OCA)",
 'license': 'AGPL-3',
 'website': 'http://www.odoo-magento-connector.com',
 'images': ['images/magento_backend.png',
            'images/jobs.png',
            'images/product_binding.png',
            'images/invoice_binding.png',
            'images/magentoerpconnect.png',
            ],
 'data': ['data/magentoerpconnect_data.xml',
          # 'security/ir.model.access.csv',
          # 'views/setting_view.xml',
          'views/magento_model_view.xml',
          'views/product_view.xml',
          'views/product_category_view.xml',
          'views/partner_view.xml',
          # 'views/sale_view.xml',
          # 'views/invoice_view.xml',
          'views/magentoerpconnect_menu.xml',
          # 'views/delivery_view.xml',
          # 'views/stock_view.xml',
          # 'views/account_payment_mode_view.xml',
          ],
 'installable': True,
 'application': True,
 }
