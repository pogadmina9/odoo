    # -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pytz
import threading
import re
from dateutil import relativedelta
from ast import literal_eval
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from markupsafe import Markup
from psycopg2 import sql

from odoo import api, fields, models, tools, SUPERUSER_ID, _, _lt
from odoo.addons.iap.tools import iap_tools
from odoo.addons.mail.tools import mail_validation
from odoo.fields import Command
from odoo.addons.phone_validation.tools import phone_validation
from odoo.exceptions import UserError, AccessError
from odoo.osv import expression
from odoo.tools.translate import _
from odoo.tools import date_utils, email_split, is_html_empty, groupby, parse_contact_from_email
from odoo.tools.misc import get_lang
from odoo.addons.resource.models.utils import Intervals
from odoo.addons.resource.models.utils import filter_domain_leaf
     

class ReqTask(models.Model):
    _name = 'req.internal.it'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Internal IT'
    _order = 'id desc'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['req.task.status'].search([], limit=1)
    
    name = fields.Many2one('req.task')
    requester = fields.Many2one('res.users', related='name.requester')
    location = fields.Char(tracking=True)
    problem = fields.Char(tracking=True)
    assignee = fields.Many2one('res.users')
    action = fields.Text(string='Action')
    
class ReqTransportationTXN(models.Model):
    _name = 'req.transportation.txn'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Request Transportation TXN'
    _order = 'id desc'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['req.task.status'].search([], limit=1)
    
    name = fields.Many2one('req.task')
    requester = fields.Many2one('res.users', related='name.requester')
    job_position = fields.Char(related='requester.employee_id.job_id.name')
    tujuan = fields.Char(tracking=True)   
    schedule = fields.Datetime(string='Schedule')