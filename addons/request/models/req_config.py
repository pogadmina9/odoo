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
from odoo.addons.fleet.models.fleet_vehicle_model import FUEL_TYPES
     
class ReqApprovalConfig(models.Model):
    _name = 'req.approval.config'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Request Approval Configuration'
    _order = 'id desc'
    
    name = fields.Char(string='Name')
    approver_ops_car = fields.Many2many('hr.employee', 'approver_ops_car_rel', 'conf_id', 'approver_ops_car_id', string='Approver')
    mail_ops_car = fields.Many2many('hr.employee', 'mail_ops_car_rel', 'conf_id', 'mail_ops_car_id', string='CC Email To')
    
class ReqTeamConfig(models.Model):
    _name = 'req.team.config'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Request Team Configuration'
    _order = 'id desc'
    
    name = fields.Char(string='Name')
    ##Transport
    admin_transport = fields.Many2many('res.users', 'admin_transport_rel', 'conf_id', 'admin_transport_id', string='Administrator')
    transport_team = fields.Many2many('hr.employee', 'transport_team_rel', 'conf_id', 'transport_team_id', string='Team')
    ##InternalIT
    admin_internal_it = fields.Many2many('res.users', 'admin_internal_it_rel', 'conf_id', 'admin_internal_it_id', string='Administrator')
    internal_it_team = fields.Many2many('hr.employee', 'internal_it_team_rel', 'conf_id', 'internal_it_team_id', string='Team')
    ##MPR
    admin_mpr = fields.Many2many('res.users', 'admin_mpr_rel', 'conf_id', 'admin_mpr_id', string='Administrator')
    mpr_team = fields.Many2many('hr.employee', 'mpr_team_rel', 'conf_id', 'mpr_team_id', string='Team')
    
    def write(self, vals):
        res = super(ReqTeamConfig, self).write(vals)
        if 'admin_mpr' in vals:
            self.update_mpr_admin()
        if 'mpr_team' in vals:
            self.update_mpr_team()
        if 'admin_internal_it' in vals:
            self.update_internal_it_admin()
        if 'internal_it_team' in vals:
            self.update_internal_it_team()
            
    def update_mpr_admin(self):
        for rec in self:
            search_group = rec.sudo().env['res.groups'].search([('name', 'ilike', 'MPR Admin')])
            for group in search_group:
                    group.sudo().write({'users': False})
                    group.sudo().write({'users': rec.admin_mpr})
  
    def update_mpr_team(self):
        for rec in self:
            search_group = rec.sudo().env['res.groups'].search([('name', 'ilike', 'MPR Team')])
            mpr_team = rec.mpr_team.mapped("id")
            search_team = rec.sudo().env['res.users'].search([('employee_id', 'in', mpr_team)])
            for group in search_group:
                    group.sudo().write({'users': False})
                    group.sudo().write({'users': search_team})
                    
    def update_internal_it_admin(self):
        for rec in self:
            search_group = rec.sudo().env['res.groups'].search([('name', 'ilike', 'Internal IT Admin')])
            for group in search_group:
                    group.sudo().write({'users': False})
                    group.sudo().write({'users': rec.admin_internal_it})
  
    def update_internal_it_team(self):
        for rec in self:
            search_group = rec.sudo().env['res.groups'].search([('name', 'ilike', 'Internal IT User')])
            internal_it_team = rec.internal_it_team.mapped("id")
            search_team = rec.sudo().env['res.users'].search([('employee_id', 'in', internal_it_team)])
            for group in search_group:
                    group.sudo().write({'users': False})
                    group.sudo().write({'users': search_team})
