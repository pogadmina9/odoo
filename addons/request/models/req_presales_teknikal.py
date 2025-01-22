    # -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pytz
import threading
import re
from ast import literal_eval
from collections import OrderedDict, defaultdict
from dateutil.relativedelta import relativedelta
from odoo.tools import date_utils
from datetime import date, datetime, timedelta
from markupsafe import Markup
from psycopg2 import sql

from odoo import api, fields, models, tools, SUPERUSER_ID, _, _lt
from odoo.addons.iap.tools import iap_tools
from odoo.addons.mail.tools import mail_validation
from odoo.fields import Command
from odoo.addons.phone_validation.tools import phone_validation
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.osv import expression
from odoo.tools.translate import _
from odoo.tools import date_utils, email_split, is_html_empty, groupby, parse_contact_from_email
from odoo.tools.misc import get_lang
from odoo.addons.resource.models.utils import Intervals
from odoo.addons.resource.models.utils import filter_domain_leaf
        
        
class PengajuanJasa(models.Model):
    _inherit = 'pengajuan.jasa'
    
    report_date = fields.Datetime('Report Date', tracking=True)
    close_date = fields.Datetime('Close Date', tracking=True)
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments")

    def teknikal_schedule_today(self):
        return {
            'name': 'Jadwal Teknisi Hari Ini',
            'type': 'ir.actions.act_window',
            'view_type': 'tree',
            'view_mode': 'tree',
            'view_id': self.env.ref('request.req_teknikal_schedule_tree').id,
            'res_model': 'pengajuan.jasa',
            'context': {'search_default_approved': 1, 'search_default_need_technical': 1},
            'domain': [("schedule_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59))), 
                                                    ("schedule_end", ">=", fields.Datetime.to_string(fields.Datetime.today()))],
        }
        
    def teknikal_schedule_tommorow(self):
        tommorow = fields.Datetime.today() + relativedelta(days=1)
        return {
            'name': 'Jadwal Teknisi Besok',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'tree',
            'view_id': self.env.ref('request.req_teknikal_schedule_tree').id,
            'res_model': 'pengajuan.jasa',
            'context': {'search_default_approved': 1, 'search_default_need_technical': 1},
            'domain': [("schedule_date", "<=", fields.Datetime.to_string(tommorow.replace(hour=23, minute=59,second=59))), 
                                                    ("schedule_end", ">=", fields.Datetime.to_string(tommorow))],
        }