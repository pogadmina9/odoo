# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Request to CRM',
    'version': '1.0',
    'category': 'Hidden',
    'depends': ['maintenance', 'crm'],
    'data': [
        'views/crm_req_activity.xml',
    ],
    'auto_install': True,
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}
