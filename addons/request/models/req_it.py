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
     

class ReqItStatus(models.Model):
    _name = 'req.it.status'
    _description = 'IT Request Status'
    _order = 'sequence'
    
    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    done = fields.Boolean('Done')
    
class MaintenanceTeam(models.Model):
    _inherit = 'maintenance.team'

    ongoing_req_it = fields.Integer(string="Number of Ongoing Task", compute='_compute_req_it_count')
    my_request_it = fields.Integer(string="Number of My Request", compute='_compute_req_it_count')
    my_assignee_it = fields.Integer(string="Number of My Assignment", compute='_compute_req_it_count')

   
    @api.depends('request_ids.dummy_save')
    def _compute_req_it_count(self):
        for team in self:            
            data1 = self.env['internal.it']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('status_id', '!=', [1])],
                ['create_date:year', 'status_id', 'problem', ],
                ['__count']
            )
            data2 = self.env['internal.it']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('status_id', '!=', [1]), ('requester', '=', self.env.uid)],
                ['create_date:year', 'status_id', 'problem', ],
                ['__count']
            )
            data3 = self.env['internal.it']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('status_id', '!=', [1]), ('assignee', '=', self.env.uid)],
                ['create_date:year', 'status_id', 'problem', ],
                ['__count']
            )

            team.ongoing_req_it = sum(count for (_, _, _, count) in data1)
            team.my_request_it = sum(count for (_, _, _, count) in data2)
            team.my_assignee_it = sum(count for (_, _, _, count) in data3)

class InternalIT(models.Model):
    _name = 'internal.it'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Internal IT Request'
    _order = 'write_date desc'
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['req.it.status'].search([], limit=1)
        
    @api.returns('self')
    def _default_team_config(self):
        return self.env['req.team.config'].search([], limit=1)
    
    name = fields.Char(string='Request ID', default=lambda self: _('New Request'))
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', default=1)
    status_changed_date = fields.Datetime(string='Status Changed Date')
    submit_date = fields.Datetime(string='Submit Date')
    received_date = fields.Datetime(string='Receive Date')
    finish_date = fields.Datetime(string='Finish Date')
    closed_date = fields.Datetime(string='Closed Date')
    status_id = fields.Many2one('req.it.status', default=_default_stage, tracking=True, group_expand='_read_group_status_ids')
    requester = fields.Many2one('res.users', default=lambda self: self.env.uid, domain="[('employee_ids', '!=', False)]")
    location = fields.Char(tracking=True)
    problem = fields.Text(tracking=True)
    reopen_reason = fields.Char(string='Reopen Reason')
    assignee = fields.Many2many('res.users', tracking=True)
    list_team = fields.Many2many('res.users', 'list_team_rel', 'req_it_id', 'list_team_id', compute='_get_team')
    action = fields.Text(string='Action', tracking=True)
    url_public = fields.Char(string='URL Public')
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Optional")
    
    ##Parameter fields
    dummy_save = fields.Boolean('Save')
    initial_readonly = fields.Boolean('Readonly',store=True, compute='_compute_initial_readonly')
    readonly = fields.Boolean('Readonly', compute='_compute_readonly')
    is_it_admin = fields.Boolean('IT Admin')
    is_cancelled = fields.Boolean('Cancelled')
    is_rejected = fields.Boolean(string='Rejected')
    
    #ForDashboard
    submit_to_finish_duration = fields.Char(string='Submit to Finish Duration')
    submit_to_finish_duration_value = fields.Float(string='Submit to Finish Duration Value', store=True, compute='_compute_submit_to_finish_duration_value')
    
    #Config
    config_team_id = fields.Many2one('req.team.config', string='Approval Config', default=_default_team_config)
    
    def save(self):
        self.write({'dummy_save': True})
        
    def cancel(self):
        self.write({'is_cancelled': True, 'status_id': 1})
       
    def reject(self):
        self.write({'is_rejected': True, 'status_id': 1})
        
    def submit(self):
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&menu_id=487&cids=1&action=1073&active_id=8&model=req.task&view_type=form'}"})
        self.write({'is_rejected': 0, 'is_cancelled': 0, 'submit_date': datetime.now(), 'status_id': 2})
        
    def finish(self):
        self.write({'finish_date': datetime.now(), 'status_id': 5})
    
    def reopen(self):
        self.write({'finish_date': False, 'status_id': 3})
    
    @api.model
    def _read_group_status_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        status_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(status_id)
        
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('name', _("New Request")) == _("New Request"):
                vals['name'] = self.env['ir.sequence'].next_by_code('internal.it.seq')

        return super().create(vals_list)
        
    def write(self, vals):
        res = super(InternalIT, self).write(vals)
        if 'status_id' in vals:
            self.filtered(lambda m: m.status_id.id in [1]).write({'submit_date': False, 'received_date': False, 'finish_date': False, 'closed_date': False, 'submit_to_finish_duration': False})
            self.filtered(lambda m: m.status_id.id in [2] and m.received_date).write({'received_date': False, 'finish_date': False, 'closed_date': False, 'submit_to_finish_duration': False})
            self.filtered(lambda m: m.status_id.id in [2] and not m.submit_date).write({'submit_date': fields.Datetime.now()})
            self.filtered(lambda m: m.status_id.id in [3] and not m.received_date).write({'received_date': fields.Datetime.now()})
            self.filtered(lambda m: m.status_id.id in [3] and m.finish_date).write({'finish_date': False, 'closed_date': False, 'submit_to_finish_duration': False}) 
            self.filtered(lambda m: m.status_id.id in [4] and m.finish_date).write({'finish_date': False, 'closed_date': False, 'submit_to_finish_duration': False})
            self.filtered(lambda m: m.status_id.id in [5] and m.closed_date).write({'closed_date': False})
            self.filtered(lambda m: m.status_id.done and not m.finish_date).write({'finish_date': fields.Datetime.now()})
            self.filtered(lambda m: m.status_id.id in [6] and not m.closed_date).write({'closed_date': fields.Datetime.now()})
        return res
    
    @api.depends('requester')
    def _compute_initial_readonly(self):
        if self.env.user.has_group('request.group_internal_it_admin') or self.env.user.has_group('request.group_internal_it_user'):
            self.initial_readonly = False
        
        else:
            self.initial_readonly = True      
            
    def _compute_readonly(self):
        if self.env.user.has_group('request.group_internal_it_admin') or self.env.user.has_group('request.group_internal_it_user'):
            self.readonly = False
        
        else:
            self.readonly = True
            
    @api.depends('finish_date')    
    def _compute_submit_to_finish_duration_value(self):
        list_records = self.search([('finish_date', '!=', False), ('submit_date', '!=', False), ('submit_to_finish_duration', '=', False)])
        for record in list_records:
            finish = record.finish_date
            submit = record.submit_date
            time_interval = Intervals([(submit, finish, record)])
            delta = sum((i[1] - i[0]).total_seconds() for i in time_interval)
            delta_minutes = int(delta // 60.0)
            
            if delta_minutes < 60:
                record.submit_to_finish_duration_value = delta_minutes
                record.write({'submit_to_finish_duration': f"{delta_minutes}{' Minutes'}"})
                
            elif delta_minutes < 1440:
                record.submit_to_finish_duration_value = delta_minutes
                value = int(record.submit_to_finish_duration_value // 60.0)
                record.write({'submit_to_finish_duration': f"{value}{' Hours'}"}) 
            
            elif delta_minutes > 1440:
                record.submit_to_finish_duration_value = delta_minutes
                value = int(record.submit_to_finish_duration_value // 1440.0)
                record.write({'submit_to_finish_duration': f"{value}{' Days'}"})
                
    @api.depends('config_team_id.internal_it_team','config_team_id.admin_internal_it')
    def _get_team(self):
        for rec in self:
            if rec.config_team_id.internal_it_team or rec.config_team_id.admin_internal_it:
                team = rec.config_team_id.internal_it_team.mapped("user_id.id")
                admin = rec.config_team_id.admin_internal_it.mapped("id")
                rec.list_team = (team) + (admin)
            else:
                rec.list_team = False
