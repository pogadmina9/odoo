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

class StatisticsError(ValueError):
    pass
     
class IrAttachment(models.Model):
    _inherit = 'ir.attachment'
    
    def unlink(self):
        for rec in self:            
            if rec.create_uid.id != rec.env.uid and rec.env.uid != 7:
                raise ValidationError("Sorry, you are not allowed to delete this attachment because you are not the owner of the file.")
            elif rec.res_id == 0:
                return super(IrAttachment, rec).unlink()            
            else:
                model = rec.res_model
                records = self.sudo().env[model].search([('id', '=', rec.res_id)])
                current_datetime = fields.Datetime.now() + timedelta(hours=7)
                for mod in records:
                    mod.message_post(body=f"{rec.name}{' - Attachment is deleted at '}{current_datetime}")
                    return super(IrAttachment, rec).unlink()
        
        
class MaintenanceTeam(models.Model):
    _inherit = 'maintenance.team'

    ongoing_req_po = fields.Integer(string="Number of Ongoing Request", compute='_compute_req_po_count')
    my_request_po = fields.Integer(string="Number of My Request", compute='_compute_req_po_count')

   
    @api.depends('request_ids.dummy_save')
    def _compute_req_po_count(self):
        for team in self:            
            data1 = self.env['req.purchasing']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('status_id', '!=', [1])],
                ['create_date:year', 'status_id', 'sales', ],
                ['__count']
            )
            data2 = self.env['req.purchasing']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('purchasing_id.name', '=', self.env.uid)],
                ['create_date:year', 'status_id', 'sales', ],
                ['__count']
            )            

            team.ongoing_req_po = sum(count for (_, _, _, count) in data1)
            team.my_request_po = sum(count for (_, _, _, count) in data2)


class PurchasingTeam(models.Model):
    _name = 'purchasing.team'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Purchasing Team'
    _order = 'name asc'
    
    name = fields.Many2one('res.users', string='Name')
    job_id = fields.Many2one('hr.job', string='Position', related='name.employee_id.job_id')
    archived = fields.Boolean(string='Archived', default=False)
    controller = fields.Boolean(string='Controller', default=False)
    po_line_ids = fields.One2many('purchasing.line', 'purchasing_id', string="List PO Line")
    po_line_controller_ids = fields.One2many('purchasing.line', 'checker', string="List PO Line")
    req_purchasing_ids = fields.One2many('req.purchasing', 'purchasing_id', string="List PO")
    req_purchasing_controller_ids = fields.One2many('req.purchasing', 'checker', string="List PO")
    
    ##ForDashboard
    time_filter_selection = fields.Selection([('all_time', 'All Time'), ('today', 'Today'), ('yesterday', 'Yesterday'), ('this_week', 'This Week'), ('last_week', 'Last Week'), ('this_month', 'This Month'), ('custom', 'Custom')], default='all_time', required=True)
    time_filter_date_start = fields.Date(string="Date Range", Store=False)
    time_filter_date_end = fields.Date(string="Date End", Store=False)
    
    ##Purchasing
    average_po_time = fields.Integer(string='Average Creating PO Time', store=True, compute='_compute_po')
    average_po_time_duration = fields.Char(string='Average Creating PO Time')
    count_po = fields.Integer(string='PO Submitted', store=True, compute='_compute_po')
    count_po_error = fields.Integer(string='Error PO', store=True, compute='_compute_po')
    error_rate_po = fields.Float(string='Error Rate PO', store=True, compute='_compute_po')
    count_po_line = fields.Integer(string='PO Item Submitted', store=True, compute='_compute_po')
    count_po_line_error = fields.Integer(string='Error Item', store=True, compute='_compute_po')
    error_rate_item = fields.Float(string='Error Rate Item', store=True, compute='_compute_po')
    kb_count_po = fields.Integer(string='PO Submitted', compute='_compute_kb_po')
    kb_count_po_line = fields.Integer(string='PO Item Submitted', compute='_compute_kb_po')
    
    ##Controller
    currency_id = fields.Many2one('res.currency', related='name.company_id.currency_id')
    sum_total_gap = fields.Monetary(string='Total Saved', store=True, compute='_compute_controller_po')
    count_po_controller = fields.Integer(string='PO Checked', store=True, compute='_compute_controller_po')
    count_po_line_controller = fields.Integer(string='PO Item Checked', store=True, compute='_compute_controller_po')
    average_checking_time = fields.Integer(string='Average Checking Time', store=True, compute='_compute_controller_po')
    average_checking_time_duration = fields.Char(string='Average Checking Time')
    kb_sum_total_gap = fields.Monetary(string='Total Saved', compute='_compute_controller_kb_po')
    kb_count_po_controller = fields.Integer(string='PO Checked', compute='_compute_controller_kb_po')
    kb_count_po_line_controller = fields.Integer(string='PO Item Checked', compute='_compute_controller_kb_po')
    
    
    def open_details(self):
        return {
            'name': 'Detail Reports',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.purchasing_team_form').id,
            'res_model': 'purchasing.team',
            'res_id': self.id,
        }
        
    def po_submit_today_list(self):
        return {
            'name': 'PO Submit Today',
            'type': 'ir.actions.act_window',
            'view_type': 'tree',
            'view_mode': 'tree',
            'view_id': self.env.ref('request.req_purchasing_tree').id,
            'res_model': 'req.purchasing',
            'context': {'search_default_purchasing_id': self.id},
            'domain': [("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
        }
        
    def po_item_submit_today_list(self):
        return {
            'name': 'PO Item Submit Today',
            'type': 'ir.actions.act_window',
            'view_type': 'tree',
            'view_mode': 'tree',
            'view_id': self.env.ref('request.req_purchasing_line_tree').id,
            'res_model': 'purchasing.line',
            'context': {'search_default_purchasing_id': self.id},
            'domain': [("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
        }
    
    def generate_statistics(self):
        if self.time_filter_selection == 'all_time':
            self._compute_po()
        elif self.time_filter_selection == 'this_month':
            self._compute_this_month()
        elif self.time_filter_selection == 'last_week':
            self._compute_last_week()
        elif self.time_filter_selection == 'this_week':
            self._compute_this_week()
        elif self.time_filter_selection == 'yesterday':
            self._compute_yesterday()
        elif self.time_filter_selection == 'today':
            self._compute_today()
        elif self.time_filter_selection == 'custom':
            self._compute_custom()
            
    def generate_statistics_controller(self):
        if self.time_filter_selection == 'all_time':
            self._compute_po()
            self._compute_controller_po()
        elif self.time_filter_selection == 'this_month':
            self._compute_this_month()
            self._compute_this_month_controller()
        elif self.time_filter_selection == 'last_week':
            self._compute_last_week()
            self._compute_last_week_controller()
        elif self.time_filter_selection == 'this_week':
            self._compute_this_week()
            self._compute_this_week_controller()
        elif self.time_filter_selection == 'yesterday':
            self._compute_yesterday()
            self._compute_yesterday_controller()
        elif self.time_filter_selection == 'today':
            self._compute_today()
            self._compute_today_controller()
        elif self.time_filter_selection == 'custom':
            self._compute_custom()
            self._compute_custom_controller()
    
    def _compute_kb_po(self):
        for team in self:            
            data1 = team.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = team.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            
            team.kb_count_po_line = sum(count for (_, _, _, count) in data1)
            team.kb_count_po = sum(count for (_, _, _, count) in data2)  

    def _cron_reset_kb(self):
        for rec in self.search([]):
            rec.kb_count_po_line = 0
            rec.kb_count_po = 0
            rec.kb_count_po_line_controller = 0
            rec.kb_count_po_controller = 0
            rec.kb_sum_total_gap = 0         
    
    @api.depends('po_line_ids', 'req_purchasing_ids.submit_date')
    def _compute_po(self):
        for team in self:            
            data1 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('name.status_id', '!=', [1])],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('reject_reason_id', '!=', False), ('name.status_id', '!=', [1])],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data3 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '!=', [1])],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data4 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '=', [5])],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            
            team.count_po_line = sum(count for (_, _, _, count) in data1)
            team.count_po_line_error = sum(count for (_, _, _, count) in data2)
            team.count_po = sum(count for (_, _, _, count) in data3)
            team.count_po_error = sum(count for (_, _, _, count) in data4)
            team.time_filter_selection = 'all_time'
            
            if team.count_po_line_error == 0 or team.count_po_line == 0:
                team.error_rate_item = 0
                team.write({'average_po_time_duration': ""})
                
            if team.count_po_error == 0 or team.count_po == 0:
                team.error_rate_po = 0
                    
            try:
                team.error_rate_item = float(team.count_po_line_error) / float(team.count_po_line)
                team.error_rate_po = float(team.count_po_error) / float(team.count_po)
            except ZeroDivisionError:
                return 0
            
            po_records = self.env['purchasing.line'].search([('purchasing_id', '=', team.id), ('name.status_id', '!=', [1]), ('active', '=', True)])
            total_po_duration = sum(po_records.mapped('approved_to_submit_duration_value'))
            
            if total_po_duration == 0 or team.count_po_line == 0:
                team.write({'average_po_time_duration': ""})
            
            else:
                try:
                    team.average_po_time = total_po_duration // team.count_po_line
                except ZeroDivisionError:
                    return 0
                    
                if team.average_po_time > 0:
                    if team.average_po_time < 60:
                        value = int(team.average_po_time)
                        team.write({'average_po_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_po_time < 1440:
                        value = int(team.average_po_time // 60.0)
                        team.write({'average_po_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_po_time > 1440:
                        value = int(team.average_po_time // 1440.0)
                        team.write({'average_po_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_po_time_duration = False
    
    def _compute_this_month(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(today, 'month')), ('submit_date', '<=', date_utils.end_of(today, 'month'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('reject_reason_id', '!=', False), ('submit_date', '>=', date_utils.start_of(today, 'month')), ('submit_date', '<=', date_utils.end_of(today, 'month'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data3 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(today, 'month')), ('submit_date', '<=', date_utils.end_of(today, 'month'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data4 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '=', [5]), ('submit_date', '>=', date_utils.start_of(today, 'month')), ('submit_date', '<=', date_utils.end_of(today, 'month'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )                     
                
            team.count_po_line = sum(count for (_, _, _, count) in data1)
            team.count_po_line_error = sum(count for (_, _, _, count) in data2)
            team.count_po = sum(count for (_, _, _, count) in data3)
            team.count_po_error = sum(count for (_, _, _, count) in data4)
                
            if team.count_po_line_error == 0 or team.count_po_line == 0:
                team.error_rate_item = 0
                team.write({'average_po_time_duration': ""})
                
            if team.count_po_error == 0 or team.count_po == 0:
                team.error_rate_po = 0
                
            try:
                team.error_rate_item = float(team.count_po_line_error) / float(team.count_po_line)
                team.error_rate_po = float(team.count_po_error) / float(team.count_po)
            except ZeroDivisionError:
                return 0
            
            po_records = self.env['purchasing.line'].search([('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(today, 'month')), ('submit_date', '<=', date_utils.end_of(today, 'month'))])
            total_po_duration = sum(po_records.mapped('approved_to_submit_duration_value'))
            
            if total_po_duration == 0 or team.count_po_line == 0:
                team.write({'average_po_time_duration': ""})
            
            else:
                try:
                    team.average_po_time = total_po_duration // team.count_po_line
                except ZeroDivisionError:
                    return 0
                    
                if team.average_po_time > 0:
                    if team.average_po_time < 60:
                        value = int(team.average_po_time)
                        team.write({'average_po_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_po_time < 1440:
                        value = int(team.average_po_time // 60.0)
                        team.write({'average_po_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_po_time > 1440:
                        value = int(team.average_po_time // 1440.0)
                        team.write({'average_po_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_po_time_duration = False
                
    def _compute_last_week(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(last_week, 'week')), ('submit_date', '<=', date_utils.end_of(last_week, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('reject_reason_id', '!=', False), ('submit_date', '>=', date_utils.start_of(last_week, 'week')), ('submit_date', '<=', date_utils.end_of(last_week, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data3 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(last_week, 'week')), ('submit_date', '<=', date_utils.end_of(last_week, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data4 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '=', [5]), ('submit_date', '>=', date_utils.start_of(last_week, 'week')), ('submit_date', '<=', date_utils.end_of(last_week, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )                     
                
            team.count_po_line = sum(count for (_, _, _, count) in data1)
            team.count_po_line_error = sum(count for (_, _, _, count) in data2)
            team.count_po = sum(count for (_, _, _, count) in data3)
            team.count_po_error = sum(count for (_, _, _, count) in data4)
            
            if team.count_po_line_error == 0 or team.count_po_line == 0:
                team.error_rate_item = 0
                team.write({'average_po_time_duration': ""})
                
            if team.count_po_error == 0 or team.count_po == 0:
                team.error_rate_po = 0
                
            try:
                team.error_rate_item = float(team.count_po_line_error) / float(team.count_po_line)
                team.error_rate_po = float(team.count_po_error) / float(team.count_po)
            except ZeroDivisionError:
                return 0
            
            po_records = self.env['purchasing.line'].search([('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(last_week, 'week')), ('submit_date', '<=', date_utils.end_of(last_week, 'week'))])
            total_po_duration = sum(po_records.mapped('approved_to_submit_duration_value'))
            
            if total_po_duration == 0 or team.count_po_line == 0:
                team.write({'average_po_time_duration': ""})
            
            else:
                try:
                    team.average_po_time = total_po_duration // team.count_po_line
                except ZeroDivisionError:
                    return 0
                    
                if team.average_po_time > 0:
                    if team.average_po_time < 60:
                        value = int(team.average_po_time)
                        team.write({'average_po_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_po_time < 1440:
                        value = int(team.average_po_time // 60.0)
                        team.write({'average_po_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_po_time > 1440:
                        value = int(team.average_po_time // 1440.0)
                        team.write({'average_po_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_po_time_duration = False
            
    def _compute_this_week(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(today, 'week')), ('submit_date', '<=', date_utils.end_of(today, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('reject_reason_id', '!=', False), ('submit_date', '>=', date_utils.start_of(today, 'week')), ('submit_date', '<=', date_utils.end_of(today, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data3 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(today, 'week')), ('submit_date', '<=', date_utils.end_of(today, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data4 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '=', [5]), ('submit_date', '>=', date_utils.start_of(today, 'week')), ('submit_date', '<=', date_utils.end_of(today, 'week'))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )                     
                
            team.count_po_line = sum(count for (_, _, _, count) in data1)
            team.count_po_line_error = sum(count for (_, _, _, count) in data2)
            team.count_po = sum(count for (_, _, _, count) in data3)
            team.count_po_error = sum(count for (_, _, _, count) in data4)
            
            if team.count_po_line_error == 0 or team.count_po_line == 0:
                team.error_rate_item = 0
                team.write({'average_po_time_duration': ""})
                
            if team.count_po_error == 0 or team.count_po == 0:
                team.error_rate_po = 0
                
            try:
                team.error_rate_item = float(team.count_po_line_error) / float(team.count_po_line)
                team.error_rate_po = float(team.count_po_error) / float(team.count_po)
            except ZeroDivisionError:
                return 0
            
            po_records = self.env['purchasing.line'].search([('purchasing_id', '=', team.id), ('active', '=', True), ('submit_date', '>=', date_utils.start_of(today, 'week')), ('submit_date', '<=', date_utils.end_of(today, 'week'))])
            total_po_duration = sum(po_records.mapped('approved_to_submit_duration_value'))
            
            if total_po_duration == 0 or team.count_po_line == 0:
                team.write({'average_po_time_duration': ""})
            
            else:
                try:
                    team.average_po_time = total_po_duration // team.count_po_line
                except ZeroDivisionError:
                    return 0
                    
                if team.average_po_time > 0:
                    if team.average_po_time < 60:
                        value = int(team.average_po_time)
                        team.write({'average_po_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_po_time < 1440:
                        value = int(team.average_po_time // 60.0)
                        team.write({'average_po_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_po_time > 1440:
                        value = int(team.average_po_time // 1440.0)
                        team.write({'average_po_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_po_time_duration = False
            
    def _compute_yesterday(self):
        for team in self:
            today = fields.Date.today()
            yesterday = fields.Datetime.today() + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('reject_reason_id', '!=', False), ("submit_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data3 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data4 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '=', [5]), ("submit_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )                     
                
            team.count_po_line = sum(count for (_, _, _, count) in data1)
            team.count_po_line_error = sum(count for (_, _, _, count) in data2)
            team.count_po = sum(count for (_, _, _, count) in data3)
            team.count_po_error = sum(count for (_, _, _, count) in data4)
            
            if team.count_po_line_error == 0 or team.count_po_line == 0:
                team.error_rate_item = 0
                team.write({'average_po_time_duration': ""})
                
            if team.count_po_error == 0 or team.count_po == 0:
                team.error_rate_po = 0
                
            try:
                team.error_rate_item = float(team.count_po_line_error) / float(team.count_po_line)
                team.error_rate_po = float(team.count_po_error) / float(team.count_po)
            except ZeroDivisionError:
                return 0
            
            po_records = self.env['purchasing.line'].search([('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))])
            total_po_duration = sum(po_records.mapped('approved_to_submit_duration_value'))
            
            if team.count_po_line == 0:
                team.write({'average_po_time_duration': ""})
            
            else:
                try:
                    team.average_po_time = total_po_duration // team.count_po_line
                except ZeroDivisionError:
                    return 0
                    
                if team.average_po_time > 0:
                    if team.average_po_time < 60:
                        value = int(team.average_po_time)
                        team.write({'average_po_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_po_time < 1440:
                        value = int(team.average_po_time // 60.0)
                        team.write({'average_po_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_po_time > 1440:
                        value = int(team.average_po_time // 1440.0)
                        team.write({'average_po_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_po_time_duration = False
            
    def _compute_today(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('reject_reason_id', '!=', False), ("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                        ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data3 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                        ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data4 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '=', [5]), ("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                        ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )                     
                
            team.count_po_line = sum(count for (_, _, _, count) in data1)
            team.count_po_line_error = sum(count for (_, _, _, count) in data2)
            team.count_po = sum(count for (_, _, _, count) in data3)
            team.count_po_error = sum(count for (_, _, _, count) in data4)
            
            if team.count_po_line_error == 0 or team.count_po_line == 0:
                team.error_rate_item = 0
                team.write({'average_po_time_duration': ""})
                
            if team.count_po_error == 0 or team.count_po == 0:
                team.error_rate_po = 0
                
            try:
                team.error_rate_item = float(team.count_po_line_error) / float(team.count_po_line)
                team.error_rate_po = float(team.count_po_error) / float(team.count_po)
            except ZeroDivisionError:
                return 0

            po_records = self.env['purchasing.line'].search([('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                        ("submit_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))])
            total_po_duration = sum(po_records.mapped('approved_to_submit_duration_value'))
            
            if total_po_duration == 0 or team.count_po_line == 0:
                team.write({'average_po_time_duration': ""})
            
            else:
                try:
                    team.average_po_time = total_po_duration // team.count_po_line
                except ZeroDivisionError:
                    return 0
                    
                if team.average_po_time > 0:
                    if team.average_po_time < 60:
                        value = int(team.average_po_time)
                        team.write({'average_po_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_po_time < 1440:
                        value = int(team.average_po_time // 60.0)
                        team.write({'average_po_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_po_time > 1440:
                        value = int(team.average_po_time // 1440.0)
                        team.write({'average_po_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_po_time_duration = False
            
    def _compute_custom(self):
        for team in self:
            data1 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", self.time_filter_date_start), ("submit_date", "<=", self.time_filter_date_end)],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['purchasing.line']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('reject_reason_id', '!=', False), ("submit_date", ">=", self.time_filter_date_start), ("submit_date", "<=", self.time_filter_date_end)],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data3 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", self.time_filter_date_start), ("submit_date", "<=", self.time_filter_date_end)],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data4 = self.env['req.purchasing']._read_group(
                [('purchasing_id', '=', team.id), ('active', '=', True), ('status_id', '=', [5]), ("submit_date", ">=", self.time_filter_date_start), ("submit_date", "<=", self.time_filter_date_end)],
                ['submit_date:year', 'sales', 'checker', ],
                ['__count']
            )                     
                
            team.count_po_line = sum(count for (_, _, _, count) in data1)
            team.count_po_line_error = sum(count for (_, _, _, count) in data2)
            team.count_po = sum(count for (_, _, _, count) in data3)
            team.count_po_error = sum(count for (_, _, _, count) in data4)
            
            if team.count_po_line_error == 0 or team.count_po_line == 0:
                team.error_rate_item = 0
                team.write({'average_po_time_duration': ""})
                
            if team.count_po_error == 0 or team.count_po == 0:
                team.error_rate_po = 0
                
            try:
                team.error_rate_item = float(team.count_po_line_error) / float(team.count_po_line)
                team.error_rate_po = float(team.count_po_error) / float(team.count_po)
            except ZeroDivisionError:
                return 0
            
            po_records = self.env['purchasing.line'].search([('purchasing_id', '=', team.id), ('active', '=', True), ("submit_date", ">=", self.time_filter_date_start), ("submit_date", "<=", self.time_filter_date_end)])
            total_po_duration = sum(po_records.mapped('approved_to_submit_duration_value'))
            
            if total_po_duration == 0 or team.count_po_line == 0:
                team.write({'average_po_time_duration': ""})
            
            else:
                try:
                    team.average_po_time = total_po_duration // team.count_po_line
                except ZeroDivisionError:
                    return 0
                    
                if team.average_po_time > 0:
                    if team.average_po_time < 60:
                        value = int(team.average_po_time)
                        team.write({'average_po_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_po_time < 1440:
                        value = int(team.average_po_time // 60.0)
                        team.write({'average_po_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_po_time > 1440:
                        value = int(team.average_po_time // 1440.0)
                        team.write({'average_po_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_po_time_duration = False
    
    def _compute_controller_kb_po(self):
        for team in self:            
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            
            team.kb_count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.kb_count_po_controller = sum(count for (_, _, _, count) in data2)
            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))])
            team.kb_sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            
    @api.depends('po_line_ids', 'req_purchasing_controller_ids.closed_date')
    def _compute_controller_po(self):
        for team in self:            
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('name.status_id', '=', [4,5])],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('status_id', '=', [4,5])],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            
            
            team.count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.count_po_controller = sum(count for (_, _, _, count) in data2)
            
            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ('name.status_id', '=', [4,5])])
            total_checking_duration = sum(checked_records.mapped('submit_to_checked_duration_value'))
            team.sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            if total_checking_duration == 0 or team.count_po_line_controller == 0:
                team.write({'average_checking_time_duration': ""})
                
            else:
                try:
                    team.average_checking_time = total_checking_duration // team.count_po_line_controller
                except ZeroDivisionError:
                    return 0
                    
                if team.average_checking_time > 0:
                    if team.average_checking_time < 60:
                        value = int(team.average_checking_time)
                        team.write({'average_checking_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_checking_time < 1440:
                        value = int(team.average_checking_time // 60.0)
                        team.write({'average_checking_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_checking_time > 1440:
                        value = int(team.average_checking_time // 1440.0)
                        team.write({'average_checking_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_checking_time_duration = False
            
    def _compute_this_month_controller(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(today, 'month')), ('closed_date', '<=', date_utils.end_of(today, 'month'))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )            
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(today, 'month')), ('closed_date', '<=', date_utils.end_of(today, 'month'))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            
            team.count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.count_po_controller = sum(count for (_, _, _, count) in data2)
            
            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(today, 'month')), ('closed_date', '<=', date_utils.end_of(today, 'month'))])
            total_checking_duration = sum(checked_records.mapped('submit_to_checked_duration_value'))
            team.sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            if total_checking_duration == 0 or team.count_po_line_controller == 0:
                team.write({'average_checking_time_duration': ""})
            
            else:
                try:
                    team.average_checking_time = total_checking_duration // team.count_po_line_controller
                except ZeroDivisionError:
                    return 0
                    
                if team.average_checking_time > 0:
                    if team.average_checking_time < 60:
                        value = int(team.average_checking_time)
                        team.write({'average_checking_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_checking_time < 1440:
                        value = int(team.average_checking_time // 60.0)
                        team.write({'average_checking_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_checking_time > 1440:
                        value = int(team.average_checking_time // 1440.0)
                        team.write({'average_checking_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_checking_time_duration = False
                
    def _compute_last_week_controller(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(last_week, 'week')), ('closed_date', '<=', date_utils.end_of(last_week, 'week'))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(last_week, 'week')), ('closed_date', '<=', date_utils.end_of(last_week, 'week'))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )              
                
            team.count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.count_po_controller = sum(count for (_, _, _, count) in data2)
            
            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(last_week, 'week')), ('closed_date', '<=', date_utils.end_of(last_week, 'week'))])
            total_checking_duration = sum(checked_records.mapped('submit_to_checked_duration_value'))
            team.sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            if total_checking_duration == 0 or team.count_po_line_controller == 0:
                team.write({'average_checking_time_duration': ""})
            
            else:
                try:
                    team.average_checking_time = total_checking_duration // team.count_po_line_controller
                except ZeroDivisionError:
                    return 0
                    
                if team.average_checking_time > 0:
                    if team.average_checking_time < 60:
                        value = int(team.average_checking_time)
                        team.write({'average_checking_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_checking_time < 1440:
                        value = int(team.average_checking_time // 60.0)
                        team.write({'average_checking_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_checking_time > 1440:
                        value = int(team.average_checking_time // 1440.0)
                        team.write({'average_checking_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_checking_time_duration = False
            
            
    def _compute_this_week_controller(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(today, 'week')), ('closed_date', '<=', date_utils.end_of(today, 'week'))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )            
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(today, 'week')), ('closed_date', '<=', date_utils.end_of(today, 'week'))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
                
            team.count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.count_po_controller = sum(count for (_, _, _, count) in data2)
            
            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ('closed_date', '>=', date_utils.start_of(today, 'week')), ('closed_date', '<=', date_utils.end_of(today, 'week'))])
            total_checking_duration = sum(checked_records.mapped('submit_to_checked_duration_value'))
            team.sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            if total_checking_duration == 0 or team.count_po_line_controller == 0:
                team.write({'average_checking_time_duration': ""})
            
            else:
                try:
                    team.average_checking_time = total_checking_duration // team.count_po_line_controller
                except ZeroDivisionError:
                    return 0
                    
                if team.average_checking_time > 0:
                    if team.average_checking_time < 60:
                        value = int(team.average_checking_time)
                        team.write({'average_checking_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_checking_time < 1440:
                        value = int(team.average_checking_time // 60.0)
                        team.write({'average_checking_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_checking_time > 1440:
                        value = int(team.average_checking_time // 1440.0)
                        team.write({'average_checking_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_checking_time_duration = False
            
    def _compute_yesterday_controller(self):
        for team in self:
            today = fields.Date.today()
            yesterday = fields.Datetime.today() + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )            
                
            team.count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.count_po_controller = sum(count for (_, _, _, count) in data2)
            
            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(yesterday)), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(yesterday.replace(hour=23, minute=59,second=59)))])
            total_checking_duration = sum(checked_records.mapped('submit_to_checked_duration_value'))
            team.sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            if total_checking_duration == 0 or team.count_po_line_controller == 0:
                team.write({'average_checking_time_duration': ""})
            
            else:
                try:
                    team.average_checking_time = total_checking_duration // team.count_po_line_controller
                except ZeroDivisionError:
                    return 0
                    
                if team.average_checking_time > 0:
                    if team.average_checking_time < 60:
                        value = int(team.average_checking_time)
                        team.write({'average_checking_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_checking_time < 1440:
                        value = int(team.average_checking_time // 60.0)
                        team.write({'average_checking_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_checking_time > 1440:
                        value = int(team.average_checking_time // 1440.0)
                        team.write({'average_checking_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_checking_time_duration = False
            
    def _compute_today_controller(self):
        for team in self:
            today = fields.Date.today()
            yesterday = today + relativedelta(days=-1)
            last_week = today + relativedelta(weeks=-1)
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                        ("closed_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
             
            team.count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.count_po_controller = sum(count for (_, _, _, count) in data2)
            
            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("closed_date", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))])
            total_checking_duration = sum(checked_records.mapped('submit_to_checked_duration_value'))
            team.sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            if total_checking_duration == 0 or team.count_po_line_controller == 0:
                team.write({'average_checking_time_duration': ""})
            
            else:
                try:
                    team.average_checking_time = total_checking_duration // team.count_po_line_controller
                except ZeroDivisionError:
                    return 0
                    
                if team.average_checking_time > 0:
                    if team.average_checking_time < 60:
                        value = int(team.average_checking_time)
                        team.write({'average_checking_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_checking_time < 1440:
                        value = int(team.average_checking_time // 60.0)
                        team.write({'average_checking_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_checking_time > 1440:
                        value = int(team.average_checking_time // 1440.0)
                        team.write({'average_checking_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_checking_time_duration = False
            
    def _compute_custom_controller(self):
        for team in self:
            data1 = self.env['purchasing.line']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", self.time_filter_date_start), ("closed_date", "<=", self.time_filter_date_end)],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )
            data2 = self.env['req.purchasing']._read_group(
                [('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", self.time_filter_date_start), ("closed_date", "<=", self.time_filter_date_end)],
                ['closed_date:year', 'sales', 'checker', ],
                ['__count']
            )                
                
            team.count_po_line_controller = sum(count for (_, _, _, count) in data1)
            team.count_po_controller = sum(count for (_, _, _, count) in data2)

            checked_records = self.env['purchasing.line'].search([('checker', '=', team.id), ('active', '=', True), ("closed_date", ">=", self.time_filter_date_start), ("closed_date", "<=", self.time_filter_date_end)])
            total_checking_duration = sum(checked_records.mapped('submit_to_checked_duration_value'))
            team.sum_total_gap = sum(checked_records.mapped('sub_total_gap'))
            
            if total_checking_duration == 0 or team.count_po_line_controller == 0:
                team.write({'average_checking_time_duration': ""})
            
            else:
                try:
                    team.average_checking_time = total_checking_duration // team.count_po_line_controller
                except ZeroDivisionError:
                    return 0
                    
                if team.average_checking_time > 0:
                    if team.average_checking_time < 60:
                        value = int(team.average_checking_time)
                        team.write({'average_checking_time_duration': f"{value}{' Minutes'}"})
                        
                    elif team.average_checking_time < 1440:
                        value = int(team.average_checking_time // 60.0)
                        team.write({'average_checking_time_duration': f"{value}{' Hours'}"}) 
                    
                    elif team.average_checking_time > 1440:
                        value = int(team.average_checking_time // 1440.0)
                        team.write({'average_checking_time_duration': f"{value}{' Days'}"})
                                      
                else:
                    team.average_checking_time_duration = False
            
         
class ReqPurchasing(models.Model):
    _name = 'req.purchasing'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Checking Purchase Order'
    _order = 'id desc'
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['req.purchasing.status'].search([], limit=1)
        
    @api.returns('self')
    def _default_purchasing(self):
        return self.env['purchasing.team'].search([('name', '=', self.env.uid)], limit=1)
    
    name = fields.Char('Request ID', default=lambda self: _('New Request'))
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', default=7)
    status_id = fields.Many2one('req.purchasing.status', string="Req Status", tracking=True, default=_default_stage, group_expand='_read_group_status_ids')
    submit_date = fields.Datetime(string='Submit Date')
    req_po_date = fields.Datetime(string='Req PO on PPC')
    req_po_approve_date = fields.Datetime(string='Req PO Approved on PPC')
    receive_date = fields.Datetime(string='Receive Date')
    closed_date = fields.Datetime(string='Closed Date')
    purchasing_id = fields.Many2one('purchasing.team', string='Purchasing', default=_default_purchasing)
    sales = fields.Many2one('res.users', string='Sales', tracking=True)
    po_approved_ppc = fields.Datetime(string='PO Approved PPC')
    checker = fields.Many2one('purchasing.team', string='Checker', domain="[('controller', '=', True)]")
    nomor_po = fields.Char('Nomor PO', tracking=True)
    vendor = fields.Many2one('res.partner', string='Vendor', domain="[('category_id.name', 'ilike', 'Vendor')]", tracking=True)
    nomor_paket = fields.Char('Nomor Paket', tracking=True)
    grand_total_gap = fields.Monetary(string='Total Gap', store=True, compute='_compute_total_gap')
    reject_reason_id = fields.Many2many('purchase.reject.reason', string='Reject Reason', readonly=False, store=True, compute='_compute_reject_reasons')
    rev_number = fields.Integer('Revision Number', default=0)
    purchase_line_ids = fields.One2many('purchasing.line', 'name', string='Purchase Order Line', copy=True, auto_join=True)
    progress_note = fields.Char(tracking=True)
    urgent_note = fields.Char(tracking=True)
    purchasing_note = fields.Text(tracking=True)
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True)
    parent_id = fields.Many2one('req.purchasing', string="Parent ID")
    history_rec = fields.Many2many('req.purchasing',  'history_rec_rel', 'req_id', 'purchase_id', string="History Record")
    
    ## KPI
    total_checking_hours = fields.Float(string="Total Hours of Checking")
    ## Parameter Fields
    active = fields.Boolean('Active', default='True')
    dummy_save = fields.Boolean('Save')
    pti = fields.Boolean('PTI', store=True, readonly=False, compute='_compute_pti')
    currency_id = fields.Many2one('res.currency', related='checker.name.company_id.currency_id')
    readonly = fields.Boolean('Readonly', compute='_compute_readonly')
    is_cancelled = fields.Boolean('Cancelled')
    is_urgent = fields.Boolean(string='Urgent')
    color = fields.Integer('Color Index')
    url_local = fields.Char(string='URL Local')
    url_public = fields.Char(string='URL Public')
    
    def save(self):
        self.write({'dummy_save': True})
    
    def cancel(self):
        self.write({'is_cancelled': True, 'status_id': 1})
        
    def submit(self):
        self.write({'url_local': f"{'http://192.168.10.13:8069/web#id='}{self.id}{'&menu_id=487&cids=1&action=1073&active_id=8&model=req.task&view_type=form'}"})
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&menu_id=487&cids=1&action=1073&active_id=8&model=req.task&view_type=form'}"})
        self.write({'status_id': 2, 'submit_date': datetime.now(), 'is_cancelled': 0})
        self.create_vendor()
        self.create_new_mail()
        if not (re.search('REV', self.name, re.IGNORECASE)):
            self.write({'parent_id': self.id})
            self._search_similar_rec()
        
        
    def receive(self):
        checker = self.env['purchasing.team'].search([('name', '=', self.env.uid)], limit=1)
        self.write({'status_id': 3, 'receive_date': datetime.now(), 'checker': checker})
    
    def approve(self):
        self.write({'status_id': 4, 'closed_date': datetime.now()})
        self.create_approved_mail()
        
    def reject(self):
        self.write({'status_id': 5, 'closed_date': datetime.now()})
        self.copy()
        self.parent_id._search_similar_rec()
        self.history_rec = self.parent_id.history_rec
        self.create_rejected_mail()
        similar_rec = self.search([('name', 'ilike', self.name)], limit=1)        
        for rec in similar_rec:
            rec.history_rec = rec.parent_id.history_rec
            
    def create_vendor(self):
        if not self.vendor.company_type == "company" or self.vendor.category_id.id not in [4]:
            self.vendor.sudo().write({'company_type': "company", 'category_id': [4]})
            
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('name', _("New Request")) == _("New Request"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('req.purchasing.seq')

        return super().create(vals_list)
    
    @api.model
    def _read_group_status_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        status_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(status_id)
        
    @api.depends('purchase_line_ids.reject_reason_id')
    def _compute_reject_reasons(self):
        for rec in self:
            reason = self.purchase_line_ids.mapped('reject_reason_id.id')
            self.reject_reason_id = [(6, 0, reason)]  
            
    @api.depends('purchasing_id')
    def _compute_pti(self):
        for rec in self:
            list_pti = rec.sudo().env['res.users'].search([('email', 'ilike', 'primatek')])
            pti = list_pti.mapped("id")
            if rec.purchasing_id.name.id in pti:
                rec.write({'pti': True})
            
            else:
                rec.write({'pti': False})
            
    @api.depends('purchase_line_ids.sub_total_gap')
    def _compute_total_gap(self):
        for rec in self:
            total_gap = sum(rec.purchase_line_ids.mapped('sub_total_gap'))
            rec.grand_total_gap = total_gap
                
    @api.returns('self', lambda value: value.id)
    def copy(self, default=None):
        if default is None:
            default = {}
        if not default.get('name'):
            default['name'] = "REV-" + self.name
            default['status_id'] = 1
            default['submit_date'] = False
            default['receive_date'] = False
            default['closed_date'] = False        
            default['rev_number'] = self.rev_number + 1
            default['parent_id'] = self.parent_id.id                       
        return super(ReqPurchasing, self).copy(default)

    def create_new_mail(self):
        mail_template = self.env.ref('request.new_po_checking_req')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_rejected_mail(self):
        mail_template = self.env.ref('request.po_checking_rejected')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_approved_mail(self):
        mail_template = self.env.ref('request.po_checking_approved')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
          
    def _compute_readonly(self):
        if self.env.user.has_group('request.group_purchase_controller') and self.env.user == self.checker.name:
            self.readonly = False
        
        else:
            self.readonly = True
            
    def _search_similar_rec(self):
        similar_rec = self.search([('name', 'ilike', self.name)])
        list = similar_rec.mapped("id")
        self.history_rec = [(6, 0, list)]
        
class PurchaseLine(models.Model):
    _name = 'purchasing.line'
    _description = 'Purchasing Line'
    _order = 'id'
    
    name = fields.Many2one('req.purchasing', string="Req ID", ondelete='cascade')
    req_po_date = fields.Datetime(string='Req PO on PPC', store=True, compute='_compute_req_po', readonly=False,)
    req_po_approve_date = fields.Datetime(string='Req PO Approved on PPC', store=True, compute='_compute_approve_req_po', readonly=False,)
    submit_date = fields.Datetime(string='Submit Date', related='name.submit_date', store=True)
    receive_date = fields.Datetime(string='Receive Date', related='name.receive_date', store=True)
    closed_date = fields.Datetime(string='Closed Date', related='name.closed_date', store=True)
    vendor = fields.Many2one('res.partner', string='Vendor', related='name.vendor', store=True)
    purchasing_id = fields.Many2one('purchasing.team', string='Purchasing', related='name.purchasing_id', store=True)
    sales = fields.Many2one('res.users', string='Sales', related='name.sales', store=True)
    nomor_po = fields.Char('Nomor PO', related='name.nomor_po', store=True)
    nomor_paket = fields.Char('Nomor Paket', related='name.nomor_paket', store=True)
    product = fields.Char('Product')
    checker = fields.Many2one('purchasing.team', string='Checker', related='name.checker', store=True)
    quantity = fields.Integer('Quantity')
    hpp_product = fields.Monetary(string='HPP Product')
    hpp_controller = fields.Monetary(string='HPP Controller')
    gap = fields.Monetary(string='Gap', store=True, compute='_compute_gap')
    sub_total_gap = fields.Monetary(string='Sub Total', store=True, compute='_compute_sub_total_gap')
    reject_reason_id = fields.Many2many('purchase.reject.reason', string='Reject Reason')
    note = fields.Char('Note')
    
    ## Time Tracking Fields
    req_po_approved_duration = fields.Char(string='Request PO to Approved')
    approved_to_submit_duration = fields.Char(string='PO Approved to Submit Checking')
    submit_to_checked_duration = fields.Char(string='Submit Checking to Checked')
    req_po_approved_duration_value = fields.Float(string='Request PO to Approved', store=True, compute='_compute_req_po_approved_duration_value')
    approved_to_submit_duration_value = fields.Float(string='PO Approved to Submit Checking', store=True, compute='_compute_approved_to_submit_duration_value')
    submit_to_checked_duration_value = fields.Float(string='Receive Checking to Checked', store=True, compute='_compute_submit_to_checked_duration_value')
    
    ## Parameter Fields
    active = fields.Boolean('Active', default='True', related='name.active')
    currency_id = fields.Many2one('res.currency', related='checker.name.company_id.currency_id', store=True)
    
    @api.depends('hpp_product','hpp_controller')
    def _compute_gap(self):
        for rec in self:
            rec.gap = rec.hpp_product - rec.hpp_controller
        
    @api.depends('quantity','gap')
    def _compute_sub_total_gap(self):
        for rec in self:
            rec.sub_total_gap = rec.gap * rec.quantity
            
    @api.depends('name.req_po_date')
    def _compute_req_po(self):
        self.write({'req_po_date': self.name.req_po_date})
        
    @api.depends('name.req_po_approve_date')
    def _compute_approve_req_po(self):
        self.write({'req_po_approve_date': self.name.req_po_approve_date})
    
    @api.depends('req_po_date','req_po_approve_date')
    def _compute_req_po_approved_duration_value(self):
        for record in self:
            if record.req_po_date and record.req_po_approve_date:
                req = record.req_po_date
                approved = record.req_po_approve_date
                time_interval = Intervals([(req, approved, record)])
                delta = sum((i[1] - i[0]).total_seconds() for i in time_interval)
                delta_m = delta // 60.0
                
                if delta <= 3600:
                    record.req_po_approved_duration_value = delta_m
                    value = int(record.req_po_approved_duration_value)
                    record.write({'req_po_approved_duration': f"{value}{' Minutes'}"})
                    
                elif delta <= 86400:
                    record.req_po_approved_duration_value = delta_m
                    value = int(record.req_po_approved_duration_value // 60.0)
                    record.write({'req_po_approved_duration': f"{value}{' Hours'}"}) 
                
                elif delta > 86400:
                    record.req_po_approved_duration_value = delta_m
                    value = int(record.req_po_approved_duration_value // 1440.0)
                    record.write({'req_po_approved_duration': f"{value}{' Days'}"})
                              
            else:
                return
    
    @api.depends('req_po_approve_date','submit_date')    
    def _compute_approved_to_submit_duration_value(self):
        list_records = self.search([('req_po_approve_date', '!=', False), ('active', '=', True), ('submit_date', '!=', False)])
        for record in list_records:
            approved = record.req_po_approve_date
            submit = record.submit_date
            time_interval = Intervals([(approved, submit, record)])
            delta = sum((i[1] - i[0]).total_seconds() for i in time_interval)
            delta_m = delta // 60.0
            
            if delta < 3600:
                record.approved_to_submit_duration_value = delta_m
                value = int(record.approved_to_submit_duration_value)
                record.write({'approved_to_submit_duration': f"{value}{' Minutes'}"})
                
            elif delta < 86400:
                record.approved_to_submit_duration_value = delta_m
                value = int(record.approved_to_submit_duration_value // 60.0)
                record.write({'approved_to_submit_duration': f"{value}{' Hours'}"}) 
            
            elif delta > 86400:
                record.approved_to_submit_duration_value = delta_m
                value = int(record.approved_to_submit_duration_value // 1440.0)
                record.write({'approved_to_submit_duration': f"{value}{' Days'}"})
                              
            else:
                return   
                
    @api.depends('receive_date','closed_date')            
    def _compute_submit_to_checked_duration_value(self):
        list_records = self.search([('receive_date', '!=', False), ('active', '=', True), ('closed_date', '!=', False)])
        for record in list_records:
            req = record.receive_date
            approved = record.closed_date
            time_interval = Intervals([(req, approved, record)])
            delta = sum((i[1] - i[0]).total_seconds() for i in time_interval)
            delta_m = delta // 60.0
            
            if delta < 3600:
                record.submit_to_checked_duration_value = delta_m
                value = int(record.submit_to_checked_duration_value)
                record.write({'submit_to_checked_duration': f"{value}{' Minutes'}"})
            
            elif delta < 86400:
                record.submit_to_checked_duration_value = delta_m
                value = int(record.submit_to_checked_duration_value // 60.0)
                record.write({'submit_to_checked_duration': f"{value}{' Hours'}"}) 
            
            elif delta > 86400:
                record.submit_to_checked_duration_value = delta_m
                value = int(record.submit_to_checked_duration_value // 1440.0)
                record.write({'submit_to_checked_duration': f"{value}{' Days'}"})
                              
            else:
                return
    

class ReqPurchasingStatus(models.Model):
    _name = 'req.purchasing.status'
    _description = 'Request Purchasing Status'
    _order = 'id'
    
    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    done = fields.Boolean('Done')
    
class PurchaseRejectReason(models.Model):
    _name = 'purchase.reject.reason'
    _description = 'Purchase Reject Reason'
    _order = 'id'
    
    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)