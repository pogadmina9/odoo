# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Dashboard Presales',
    'description': 'Dashboard for Presales',
    'version': '1.0',
    'author': 'Ricky Raymond',
    'depends': ['mail', 'maintenance', 'crm'],
    'data': [
        'data/solution_status_data.xml',
        'data/mail_template_data.xml',
        'security/ir.model.access.csv',
        'report/report.xml',
        'views/presales_kpi_views.xml',
        'views/presales_skills_views.xml',
    ],
    'installable': True,
    'application': True,    
    'license': 'AGPL-3',
}
