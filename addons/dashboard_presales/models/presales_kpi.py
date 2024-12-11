    # -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pytz
import threading
import xlsxwriter

from ast import literal_eval
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from markupsafe import Markup
from psycopg2 import sql
import re

from odoo import api, fields, models, tools, SUPERUSER_ID
from odoo.addons.iap.tools import iap_tools
from odoo.addons.mail.tools import mail_validation
from odoo.addons.phone_validation.tools import phone_validation
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.osv import expression
from odoo.tools.translate import _
from odoo.tools import date_utils, email_split, is_html_empty, groupby, parse_contact_from_email
from odoo.tools.misc import get_lang
from odoo.addons.resource.models.utils import Intervals

    
class CRMPresalesTeam(models.Model):
    _inherit = 'crm.presales.team'

    presales_kpi_ids = fields.One2many('presales.kpi', 'presales', copy=False)
    archive = fields.Boolean(string='Archive', default=False)
    ibt = fields.Boolean(string='IBT', default=False)
    ibb = fields.Boolean(string='IBB', default=False)
    
class Lead(models.Model):
    _inherit = 'crm.lead'

    npkt_date = fields.Datetime(string='Date of NPKT', store=True, compute='_compute_npkt_date')
    total_demo_duration = fields.Float(string='Total Demo Duration', store=True, compute='_compute_duration')
    time_tracking_ids = fields.One2many('crm.time.tracking', 'name', copy=False)
    last_time_tracking_id = fields.Many2one('crm.time.tracking', string='Last Time Tracking')
    won_quarter_id = fields.Many2one('quarter', string='Won Quarter', store=True, compute='_compute_initial_won_quarter', readonly=False)
    kpi_id = fields.Many2one('presales.kpi', string='KPI ID', store=True, compute='_compute_kpi_id', readonly=False)

    @api.onchange('stage_id')
    def _onchange_stage_id(self):
        record_id = self.env['crm.time.tracking'].search([('name', '=', self.no_ppc)], limit=1, order="id desc").id
        end = fields.Datetime.now()
        now = fields.Datetime.now()
        self.time_tracking_ids = [(1, record_id, {'date_end': end})]
        self.time_tracking_ids._compute_time_consumed()
        self.time_tracking_ids = [(0, 0, {'date_start': now, 'stage_id': self.stage_id.id})]
        
    def write_stage_id(self):
        record_id = self.env['crm.time.tracking'].search([('name', '=', self.no_ppc)], limit=1, order="id desc").id
        end = fields.Datetime.now()
        now = fields.Datetime.now()
        self.time_tracking_ids = [(0, 0, {'date_start': now, 'stage_id': self.stage_id.id})]
        self.time_tracking_ids = [(1, record_id, {'date_end': end})]
        self.time_tracking_ids._compute_time_consumed()
        
    @api.depends('time_tracking_ids.time_consumed')
    def _compute_duration(self):
        for request in self:
            demo = request.time_tracking_ids.filtered(lambda x: x.stage_id.id in [7])
            total_demo = sum(demo.mapped('time_consumed'))            

            request.total_demo_duration = total_demo
            
    def check_latest_time_tracking_id(self):
        latest_id = self.env['crm.time.tracking'].search([('name', '=', self.name)], limit=1, order="id desc").id
        self.last_time_tracking_id = latest_id
        
    @api.depends('stage_id')
    def _compute_npkt_date(self):
        for rec in self:
            if rec.stage_id.id == 15:
                rec.npkt_date = fields.Datetime.now()
                
            else:
                rec.npkt_date = False             
        
    @api.depends('date_closed','npkt_date')
    def _compute_initial_won_quarter(self):
        for rec in self:
            if rec.date_closed:
                current_month = rec.date_closed.month
                current_year = rec.date_closed.year
                if 1 <= current_month <= 3:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q1 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 4 <= current_month <= 6:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q2 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 7 <= current_month <= 9:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q3 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 10 <= current_month <= 12:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q4 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
            
            elif rec.npkt_date:
                current_month = rec.npkt_date.month
                current_year = rec.npkt_date.year
                if 1 <= current_month <= 3:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q1 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 4 <= current_month <= 6:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q2 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 7 <= current_month <= 9:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q3 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 10 <= current_month <= 12:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q4 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
            else:
                rec.won_quarter_id = False
    
    @api.model
    def won_quarter(self):
        project_data = self.search([('stage_id', 'in', [4])])
        for rec in project_data:
            if rec.date_closed:
                current_month = rec.date_closed.month
                current_year = rec.date_closed.year
                if 1 <= current_month <= 3:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q1 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 4 <= current_month <= 6:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q2 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 7 <= current_month <= 9:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q3 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
                elif 10 <= current_month <= 12:
                    id = rec.env['quarter'].search([('name', '=', f"{'Q4 '}{current_year}")], limit=1)
                    rec.won_quarter_id = id
                    
            else:
                rec.won_quarter_id = False
                
        for rec in project_data:
            quarter = rec.won_quarter_id.name
            presales = rec.presales_team.name.name
            id = rec.env['presales.kpi'].search([('name', '=', f"{quarter}{' - '}{presales}")], limit=1)
            rec.kpi_id = id
   
            
    @api.depends('won_quarter_id','presales_team','stage_id')
    def _compute_kpi_id(self):
        if self.won_quarter_id and self.stage_id.id == 4:
            quarter = self.won_quarter_id.name
            presales = self.presales_team.name.name           
            id = self.env['presales.kpi'].search([('name', '=', f"{quarter}{' - '}{presales}")], limit=1)
            self.kpi_id = id
                
        else:
            self.kpi_id = False
            
    def compute_initial_won_quarter(self):
        if self.date_closed:
            current_month = self.date_closed.month
            current_year = self.date_closed.year
            if 1 <= current_month <= 3:
                id = self.env['quarter'].search([('name', '=', f"{'Q1 '}{current_year}")], limit=1)
                self.won_quarter_id = id
                
            elif 4 <= current_month <= 6:
                id = self.env['quarter'].search([('name', '=', f"{'Q2 '}{current_year}")], limit=1)
                self.won_quarter_id = id
                
            elif 7 <= current_month <= 9:
                id = self.env['quarter'].search([('name', '=', f"{'Q3 '}{current_year}")], limit=1)
                self.won_quarter_id = id
                
            elif 10 <= current_month <= 12:
                id = self.env['quarter'].search([('name', '=', f"{'Q4 '}{current_year}")], limit=1)
                self.won_quarter_id = id
                
        else:
            self.won_quarter_id = False
        
    @api.model
    def _cron_time_tracking(self):
        date_now = fields.Datetime.now()
        list_request = self.search([])
        for rec in list_request:
            rec.check_latest_time_tracking_id()
        list_time_tracking_id = self.search([]).last_time_tracking_id
        for track in list_time_tracking_id:
            track.write({'date_end': date_now})
            track._compute_time_consumed()
       
class PengajuanJasa(models.Model):
    _inherit = 'pengajuan.jasa'
    
    kpi_id = fields.Many2one('presales.kpi', string='KPI ID', store=True, compute='_compute_kpi_id')
    current_presales = fields.Boolean(string='Current Presales', compute='_compute_current_presales')
    is_reviewed = fields.Boolean(string='Reviewed')
    schedule_end = fields.Datetime(tracking=True)
    start_date = fields.Date('Start Date', store=True, tracking=True, compute='_compute_dates', inverse='_inverse_dates')
    stop_date = fields.Date('End Date', store=True, tracking=True, compute='_compute_dates', inverse='_inverse_dates')
    allday = fields.Boolean(string='All Day?')
    
    
    def write_review(self):
        return self.env["ir.actions.act_window"]._for_xml_id('dashboard_presales.review_presales_wizard_action')
        
    def create_copy(self):
        self.copy()
    
    @api.depends('allday', 'schedule_date', 'schedule_end')
    def _compute_dates(self):
        for schedule in self:
            if schedule.allday and schedule.schedule_date and schedule.schedule_end:
                schedule.start_date = schedule.schedule_date.date()
                schedule.stop_date = schedule.schedule_end.date()
            else:
                schedule.start_date = False
                schedule.stop_date = False
                
    def _inverse_dates(self):
        for schedule in self:
            if schedule.allday:
                enddate = fields.Datetime.from_string(schedule.stop_date)
                enddate = enddate + timedelta(hours=10) + timedelta(minutes=30)

                startdate = fields.Datetime.from_string(schedule.start_date)
                startdate = startdate + timedelta(hours=1)

                schedule.write({'schedule_date': startdate,'schedule_end': enddate})
    
    @api.depends('stage_pj_id','presales','schedule_date')
    def _compute_kpi_id(self):
        poc_presentation = self.search([('activity_type', 'in', [16,19])])
        for rec in poc_presentation:
            if rec.stage_pj_id not in [1,3]:
                current_month = rec.schedule_date.month
                current_year = rec.schedule_date.year
                presales = rec.presales.name.name
                if 1 <= current_month <= 3:
                    id = rec.env['presales.kpi'].search([('name', '=', f"{'Q1 '}{current_year}{' - '}{presales}")], limit=1)
                    rec.kpi_id = id
                        
                elif 4 <= current_month <= 6:
                    id = rec.env['presales.kpi'].search([('name', '=', f"{'Q2 '}{current_year}{' - '}{presales}")], limit=1)
                    rec.kpi_id = id
                        
                elif 7 <= current_month <= 9:
                    id = rec.env['presales.kpi'].search([('name', '=', f"{'Q3 '}{current_year}{' - '}{presales}")], limit=1)
                    rec.kpi_id = id
                        
                elif 10 <= current_month <= 12:
                    id = rec.env['presales.kpi'].search([('name', '=', f"{'Q4 '}{current_year}{' - '}{presales}")], limit=1)
                    rec.kpi_id = id
                    
            else:
                rec.kpi_id = False
                
    def _check_ppc_field(self):
        if (re.search('OPT', self.no_ppc, re.IGNORECASE)) or (re.search('DM', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        elif (re.search('NPEK', self.no_ppc, re.IGNORECASE)) or (re.search('PEK', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        elif (re.search('NPPL', self.no_ppc, re.IGNORECASE)) or (re.search('PPL', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        elif (re.search('NPSW', self.no_ppc, re.IGNORECASE)) or (re.search('PSW', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        else:
            self.crm_id = False
            
    def create_project(self):
        if (re.search('OPT', self.no_ppc, re.IGNORECASE)) or (re.search('DM', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        elif (re.search('NPEK', self.no_ppc, re.IGNORECASE)) or (re.search('PEK', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        elif (re.search('NPPL', self.no_ppc, re.IGNORECASE)) or (re.search('PPL', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        elif (re.search('NPSW', self.no_ppc, re.IGNORECASE)) or (re.search('PSW', self.no_ppc, re.IGNORECASE)):
            self._create_crm_lead()
            
        else:
            raise ValidationError("Silahkan isi No PPC/Paket dengan benar!")
            
    def _compute_current_presales(self):
        if self.presales.name.id != self.env.uid:
            self.current_presales = False
            
        else:
            self.current_presales = True
                

class ReviewPresales(models.Model):
    _name = 'review.presales'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Presales Review'
    _order = "id desc"

    name = fields.Many2one('crm.presales.team', string='Presales', related='request_id.presales')
    create_date = fields.Datetime('Created Date', tracking=True, default=lambda self: fields.datetime.now())
    rating = fields.Selection([('0', 'No Rating'), ('1', 'Very Bad'), ('2', 'Bad'), ('3', 'Average'), ('4', 'Good'), ('5', 'Very Good')], string='Rating', required=True, tracking=True)
    comment = fields.Char('Comment', required=True)
    writer = fields.Many2one('res.users', default=lambda self: self.env.uid, readonly=True)
    request_id = fields.Many2one('pengajuan.jasa', string='ID Pengajuan Jasa')
    kpi_id = fields.Many2one('presales.kpi', string='KPI ID', store=True, compute='_compute_kpi_id', readonly=False)
    rating_value = fields.Float(string='Rating Value', store=True, compute='_compute_rating_value', readonly=False)
    
    
    def action_submit(self):
        self.sudo().request_id.is_reviewed = True
        return {'type': 'ir.actions.act_window_close'}
        
    @api.depends('rating')
    def _compute_rating_value(self):
        if self.rating == '0':
            self.rating_value = 0
        elif self.rating == '1':
            self.rating_value = 1
        elif self.rating == '2':
            self.rating_value = 2
        elif self.rating == '3':
            self.rating_value = 3
        elif self.rating == '4':
            self.rating_value = 4
        else:
            self.rating_value = 5
        
        
    @api.depends('create_date')
    def _compute_kpi_id(self):
        if self.create_date:
            current_month = self.create_date.month
            current_year = self.create_date.year
            presales = self.name.name.name
            if 1 <= current_month <= 3:
                id = self.env['presales.kpi'].search([('name', '=', f"{'Q1 '}{current_year}{' - '}{presales}")], limit=1)
                self.kpi_id = id
                
            elif 4 <= current_month <= 6:
                id = self.env['presales.kpi'].search([('name', '=', f"{'Q2 '}{current_year}{' - '}{presales}")], limit=1)
                self.kpi_id = id
                
            elif 7 <= current_month <= 9:
                id = self.env['presales.kpi'].search([('name', '=', f"{'Q3 '}{current_year}{' - '}{presales}")], limit=1)
                self.kpi_id = id
                
            elif 10 <= current_month <= 12:
                id = self.env['presales.kpi'].search([('name', '=', f"{'Q4 '}{current_year}{' - '}{presales}")], limit=1)
                self.kpi_id = id
                
        else:
            self.kpi_id = False  


class Quarter(models.Model):    
    _name = 'quarter'
    _description = 'Quarter'
    _order = 'name desc'

    name = fields.Char('Name', required=True)
    won_quarter_ids = fields.One2many('crm.lead', 'won_quarter_id', string='Won Quarter')
    kpi_parameter_ids = fields.One2many('kpi.parameter', 'quarter_id', string='KPI Parameter')
    presales_kpi_ids = fields.One2many('presales.kpi', 'quarter', string='Presales KPI')
    target_revenue_sales = fields.Monetary(string='Target Revenue Sales')
    target_revenue_presales = fields.Monetary(string='Target Revenue Presales', store=True, compute='_compute_target_revenue')
    target_revenue_point = fields.Float(string='Target Revenue Percentage')
    revenue_kpi_weight = fields.Float(string='Revenue Weight')
    target_revenue_type = fields.Selection([('individual', 'Individual'), ('team', 'Team')], string='Target Type', default='team')
    total_kpi_percentage = fields.Float(string='Total KPI Percentage', store=True, compute='_compute_kpi_percentage')
    target_revenue_sales_ibb = fields.Monetary(string='Target Revenue Sales')
    target_revenue_presales_ibb = fields.Monetary(string='Target Revenue Presales', store=True, compute='_compute_target_revenue_ibb')
    target_revenue_point_ibb = fields.Float(string='Target Revenue Percentage')
    revenue_kpi_weight_ibb = fields.Float(string='Revenue Weight')
    target_revenue_type_ibb = fields.Selection([('individual', 'Individual'), ('team', 'Team')], string='Target Type', default='team')
    
    @api.depends('target_revenue_point','target_revenue_sales')
    def _compute_target_revenue(self):        
        self.target_revenue_presales = self.target_revenue_sales * self.target_revenue_point
        
    @api.depends('target_revenue_point_ibb','target_revenue_sales_ibb')
    def _compute_target_revenue_ibb(self):
        for rec in self:
            rec.target_revenue_presales_ibb = rec.target_revenue_sales_ibb * rec.target_revenue_point_ibb
        
    @api.depends('revenue_kpi_weight','kpi_parameter_ids.weight')
    def _compute_kpi_percentage(self):        
        for rec in self:
            kpi_parameter = sum(rec.kpi_parameter_ids.mapped('weight'))
            rec.total_kpi_percentage = kpi_parameter + rec.revenue_kpi_weight
    
    ##Parameter
    currency_id = fields.Many2one('res.currency', string='Currency', default=11)
    
    @api.model
    def _cron_create_quarter(self):
        current_month = fields.datetime.now().month
        current_year = fields.datetime.now().year
        q = False
        if 1 <= current_month <= 3:
            q = f"{'Q1 '}{current_year}"       
        elif 4 <= current_month <= 6:
            q = f"{'Q2 '}{current_year}"
        elif 7 <= current_month <= 9:
            q = f"{'Q3 '}{current_year}"                
        elif 10 <= current_month <= 12:
            q = f"{'Q4 '}{current_year}"
        check_quarter = self.search([('name', '=', q)], limit=1).id
        if not check_quarter:
            quarter_create = self.sudo().create({                
                    'name': q
                    })
        self._create_kpi()
    
    def _create_kpi(self):
        latest_quarter = self.env['quarter'].search([], limit=1, order="id desc").id
        exist_kpi = self.env['presales.kpi'].search([('quarter', '=', latest_quarter)]).mapped("id")
        presales = self.env['crm.presales.team'].search([('presales_kpi_ids', 'not in', exist_kpi), ('archive', '=', False)])
        presales.presales_kpi_ids = [(0, 0, {'quarter': latest_quarter})]
        
        
class KpiParameter(models.Model):    
    _name = 'kpi.parameter'
    _description = 'KPI Parameter'
    _order = 'sequence asc'
        
    name = fields.Many2one('kpi.name',string='Name', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Sequence')
    target = fields.Integer(string='Target')
    percentage = fields.Float(string='Percentage Point', store=True, compute='_compute_percentage')
    weight = fields.Float(string='KPI Weight')
    quarter_id = fields.Many2one('quarter', string='Quarter', ondelete='cascade')
    
    @api.depends('target','weight')
    def _compute_percentage(self):
        for rec in self:
            if rec.weight and rec.target:
                rec.percentage = rec.weight / rec.target
            else:
                rec.percentage = 0
    
    @api.onchange('name')
    def _onchange_name_to_sequence(self):
        self.write({'sequence': self.name.sequence})
        
class KpiName(models.Model):    
    _name = 'kpi.name'
    _description = 'KPI Name'
    _order = 'id'
    
    name = fields.Char('Name', required=True)        
    sequence = fields.Integer(string='Sequence')        

class PresalesKpi(models.Model):
    _name = 'presales.kpi'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Presales KPI'
    _order = 'quarter,presales asc'
    
    name = fields.Char('KPI Name')
    presales = fields.Many2one('crm.presales.team', string='Presales')
    quarter = fields.Many2one('quarter', string='Quarter')
    total_revenue = fields.Monetary(string='Total Revenue', store=True, compute='_compute_kpi_revenue')
    total_poc_presentation = fields.Integer(string='Total POC & Presentation', store=True, compute='_compute_kpi_count_poc_pre')
    conversion_poc_presentation = fields.Integer(string='Conversion of POC & Presentation', store=True, compute='_compute_kpi_conv_poc_pre')
    total_certification = fields.Integer(string='Training & Certification', store=True, compute='_compute_training_count')
    total_solution = fields.Integer(string='Create New Solution', store=True, compute='_compute_kpi_count_solutions_list')
    sales_feedback = fields.Float(string='Feedback Rating from Sales', store=True, compute='_compute_sales_feedback')
    total_kpi = fields.Float(string='Total KPI', store=True, compute='_compute_total_kpi')
    currency_id = fields.Many2one('res.currency', related='presales.name.company_id.currency_id')
    
    #Individual Revenue
    target_revenue = fields.Monetary(string='Target Revenue')
    revenue_kpi_weight = fields.Float(string='KPI Weight')
    
    #KPI Value
    total_revenue_kpi = fields.Float(string='Revenue KPI', store=True, compute='_compute_total_revenue_kpi')
    total_poc_presentation_kpi = fields.Float(string='KPI B', store=True, compute='_compute_total_poc_presentation_kpi')
    conversion_poc_presentation_kpi = fields.Float(string='KPI c', store=True, compute='_compute_conversion_poc_presentation_kpi')
    total_certification_kpi = fields.Float(string='KPI D', store=True, compute='_compute_total_certification_kpi')
    total_solution_kpi = fields.Float(string='KPI E', store=True, compute='_compute_total_solution_kpi')
    sales_feedback_kpi = fields.Float(string='KPI E', store=True, compute='_compute_sales_feedback_kpi')
    
    ## Record Data
    crm_ids = fields.One2many('crm.lead', 'kpi_id', string='CRM Records')
    pj_ids = fields.One2many('pengajuan.jasa', 'kpi_id', string='PJ Records')
    solutions_list_ids = fields.One2many('solutions.list', 'kpi_id', string='Solutions Created')
    training_ids = fields.One2many('presales.training', 'kpi_id', string='Training &amp; Certification')
    review_presales_ids = fields.One2many('review.presales', 'kpi_id', string='Presales Review')
    
    def test(self):
        self.write({'name': a})
        
    def _get_order_lines_to_report(self):
        return self.crm_ids.filtered(lambda line: line.name)
        
    @api.model_create_multi
    def create(self, vals_list):
        # context: no_log, because subtype already handle this
        presales_kpi = super().create(vals_list)
        for kpi in presales_kpi:
            if not kpi.name:
                kpi.name = f"{kpi.quarter.name}{' - '}{kpi.presales.name.name}"
                
        return presales_kpi
    
    @api.depends('quarter.won_quarter_ids.contract_amount','quarter.target_revenue_type','quarter.target_revenue_type_ibb')
    def _compute_kpi_revenue(self):
        for kpi in self:
            if kpi.presales.ibt:
                if kpi.quarter.target_revenue_type == 'individual':
                    project_records = self.env['crm.lead'].search([('presales_team.id', '=', kpi.presales.id), ('won_quarter_id.id', '=', kpi.quarter.id), ('stage_id', 'in', [4])])
                    amount_revenue = sum(project_records.mapped('contract_amount'))
                    kpi.total_revenue = amount_revenue
                elif kpi.quarter.target_revenue_type == 'team':
                    project_records = self.env['crm.lead'].search([('won_quarter_id.id', '=', kpi.quarter.id), ('stage_id', 'in', [4])])
                    amount_revenue = sum(project_records.mapped('contract_amount'))
                    kpi.total_revenue = amount_revenue
                    
            elif kpi.presales.ibb:
                if kpi.quarter.target_revenue_type_ibb == 'individual':
                    project_records = self.env['crm.lead'].search([('presales_team.id', '=', kpi.presales.id), ('won_quarter_id.id', '=', kpi.quarter.id), ('stage_id', 'in', [4])])
                    amount_revenue = sum(project_records.mapped('contract_amount'))
                    kpi.total_revenue = amount_revenue
                elif kpi.quarter.target_revenue_type_ibb == 'team':
                    project_records = self.env['crm.lead'].search([('won_quarter_id.id', '=', kpi.quarter.id), ('stage_id', 'in', [4]), ('user_id.email', 'ilike', 'primatek')])
                    amount_revenue = sum(project_records.mapped('contract_amount'))
                    kpi.total_revenue = amount_revenue
            
            else:
                kpi.total_revenue = False
                
    @api.depends('pj_ids')
    def _compute_kpi_count_poc_pre(self):
        for kpi in self:
            if kpi.create_date:
                poc_presentation_records = self.env['pengajuan.jasa']._read_group(domain=[('kpi_id.id', '=', kpi.id)], groupby=['no_ppc'])
                poc_presentation = len(poc_presentation_records)
                kpi.total_poc_presentation = poc_presentation

            else:
                return
                
    @api.depends('total_poc_presentation','quarter.kpi_parameter_ids.percentage')
    def _compute_total_poc_presentation_kpi(self):
        for kpi in self:
            parameter = kpi.env['kpi.parameter'].search([('name', 'ilike', 'Total POC'), ('quarter_id.id', '=', kpi.quarter.id)], limit=1, order="id desc").percentage
            kpi.total_poc_presentation_kpi = kpi.total_poc_presentation * parameter
      
    @api.depends('pj_ids.crm_id.stage_id','crm_ids','pj_ids')
    def _compute_kpi_conv_poc_pre(self):
        for kpi in self:
            if kpi.create_date:
                poc_presentation_records = self.env['pengajuan.jasa']._read_group(domain=[('kpi_id', '=', kpi.id)], groupby=['no_ppc'])
                pj_id = kpi.pj_ids.mapped("id")
                won_records = kpi.env['crm.lead'].search([('won_quarter_id.id', '=', kpi.quarter.id), ('pj_ids', 'in', pj_id)])
                poc_presentation_count = len(poc_presentation_records)
                won_count = len(won_records)
                kpi.conversion_poc_presentation =  won_count
                
            else:
                return
                                
    @api.depends('conversion_poc_presentation','quarter.kpi_parameter_ids.percentage')
    def _compute_conversion_poc_presentation_kpi(self):
        for kpi in self:
            parameter = kpi.env['kpi.parameter'].search([('name', 'ilike', 'Conversion of POC'), ('quarter_id.id', '=', kpi.quarter.id)], limit=1, order="id desc").percentage
            kpi.conversion_poc_presentation_kpi = kpi.conversion_poc_presentation * parameter
            
    @api.depends('training_ids')
    def _compute_training_count(self):
        for kpi in self:
            if kpi.create_date:
                training_records = kpi.training_ids.mapped("id")
                training_count = len(training_records)
                kpi.total_certification =  training_count
                
            else:
                return    
    
    @api.depends('total_certification','quarter.kpi_parameter_ids.percentage')
    def _compute_total_certification_kpi(self):
        for kpi in self:
            certification_records = kpi.env['presales.training'].search([('type', '=', 'certification'), ('kpi_id', '=', kpi.id)])
            training_records = kpi.env['presales.training'].search([('type', '=', 'training'), ('status_id', 'in', [3]), ('kpi_id', '=', kpi.id)])
            certification_count = len(certification_records)
            training_count = len(training_records)
            parameter = kpi.env['kpi.parameter'].search([('name', 'ilike', 'Certification'), ('quarter_id.id', '=', kpi.quarter.id)], limit=1, order="id desc").percentage
            kpi.total_certification_kpi = (certification_count * parameter) + (training_count * parameter * 25/100)
     
    @api.depends('solutions_list_ids')
    def _compute_kpi_count_solutions_list(self):
        for kpi in self:
            solutions_list_records = kpi.solutions_list_ids
            count_solutions_list = len(solutions_list_records)
            kpi.total_solution = count_solutions_list   
            
    @api.depends('review_presales_ids')
    def _compute_sales_feedback(self):
        for kpi in self:
            count_review_list = len(kpi.review_presales_ids)
            total_review = sum(kpi.review_presales_ids.mapped('rating_value'))
            try:
                kpi.sales_feedback = total_review / count_review_list
            except ZeroDivisionError:
                kpi.sales_feedback = False
            
            
    @api.depends('total_solution','quarter.kpi_parameter_ids.percentage')
    def _compute_total_solution_kpi(self):
        for kpi in self:
            parameter = kpi.env['kpi.parameter'].search([('name', 'ilike', 'Solution'), ('quarter_id.id', '=', kpi.quarter.id)], limit=1, order="id desc").percentage
            kpi.total_solution_kpi = kpi.total_solution * parameter
  
    @api.depends('sales_feedback','quarter.kpi_parameter_ids.percentage')
    def _compute_sales_feedback_kpi(self):
        for kpi in self:
            parameter = kpi.env['kpi.parameter'].search([('name', 'ilike', 'Rating'), ('quarter_id.id', '=', kpi.quarter.id)], limit=1, order="id desc").percentage
            kpi.sales_feedback_kpi = kpi.sales_feedback * parameter
            
    @api.depends('total_revenue_kpi','total_poc_presentation_kpi','conversion_poc_presentation_kpi','total_certification_kpi','total_solution_kpi','sales_feedback_kpi')
    def _compute_total_kpi(self):
        for rec in self:
            rec.total_kpi = rec.total_revenue_kpi + rec.total_poc_presentation_kpi + rec.conversion_poc_presentation_kpi + rec.total_certification_kpi + rec.total_solution_kpi + rec.sales_feedback_kpi
    
    @api.depends('total_revenue','quarter.target_revenue_presales','quarter.target_revenue_presales_ibb','quarter.target_revenue_type','quarter.target_revenue_type_ibb','quarter.presales_kpi_ids')
    def _compute_total_revenue_kpi(self):
        for kpi in self:
            if kpi.presales.ibb:
                if kpi.quarter.target_revenue_type_ibb == 'team':
                    try:
                        kpi.total_revenue_kpi = (kpi.total_revenue / kpi.quarter.target_revenue_presales_ibb) * kpi.quarter.revenue_kpi_weight_ibb
                    except ZeroDivisionError:
                        kpi.total_revenue_kpi = False
                elif kpi.quarter.target_revenue_type_ibb == 'individual':
                    try:
                        kpi.total_revenue_kpi = (kpi.total_revenue / kpi.target_revenue) * kpi.revenue_kpi_weight
                    except ZeroDivisionError:
                        kpi.total_revenue_kpi = False
                        
            elif kpi.presales.ibt:
                if kpi.quarter.target_revenue_type == 'team':
                    try:
                        kpi.total_revenue_kpi = (kpi.total_revenue / kpi.quarter.target_revenue_presales) * kpi.quarter.revenue_kpi_weight
                    except ZeroDivisionError:
                        kpi.total_revenue_kpi = False
                elif kpi.quarter.target_revenue_type == 'individual':
                    try:
                        kpi.total_revenue_kpi = (kpi.total_revenue / kpi.target_revenue) * kpi.revenue_kpi_weight
                    except ZeroDivisionError:
                        kpi.total_revenue_kpi = False
                        
            else:
                kpi.total_revenue_kpi = False
        
class CRMTimeTracking(models.Model):
    _name = 'crm.time.tracking'
    _description = 'CRM Time Tracking'
    
    name = fields.Many2one('crm.lead', string="CRM ID")
    stage_id = fields.Many2one('crm.stage', string="CRM Stage")
    date_start = fields.Datetime(string="Date Start")
    date_end = fields.Datetime(string="Date End")
    time_consumed = fields.Float('Time Consumed', store=True)
    crm_id = fields.Integer('ID of CRM')
    
    def _compute_time_consumed(self):        
        for record in self:
            start = record.date_start
            end = record.date_end
            time_interval = Intervals([(start, end, record)])
            delta = sum((i[1] - i[0]).total_seconds() for i in time_interval)
            if record.date_end:
                record.time_consumed = delta / 3600.0
            
            else:
                record.time_consumed = False
            
    def _write_date_end(self, stage_id):
        records = self.filtered(lambda m: not m.date_end and m.name is stage_id)
        if records:
            self.date_end = fields.Datetime.now()
            
    @api.model
    def _cron_time_tracking(self):
        date_now = fields.Datetime.now()
        list_request = self.search([('date_end', '=', False)])        
        for rec in list_request:            
            rec.write({'date_end': date_now})
            rec._compute_time_consumed()
            
    @api.onchange('name')
    def _onchange_crm_id(self):
        char_id = self.name.id
        self.write({'crm_id': char_id})