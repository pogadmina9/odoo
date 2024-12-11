    # -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pytz
import threading
from ast import literal_eval
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from markupsafe import Markup
from psycopg2 import sql

from odoo import api, fields, models, tools, SUPERUSER_ID
from odoo.addons.iap.tools import iap_tools
from odoo.addons.mail.tools import mail_validation
from odoo.addons.phone_validation.tools import phone_validation
from odoo.exceptions import UserError, AccessError
from odoo.osv import expression
from odoo.tools.translate import _
from odoo.tools import date_utils, email_split, is_html_empty, groupby, parse_contact_from_email
from odoo.tools.misc import get_lang
from odoo.addons.resource.models.utils import Intervals

#class Lead(models.Model):
#    _inherit = 'crm.lead'

#    request_id = fields.Many2one('pengajuan.jasa', string='Request ID')    
#    pj_ids = fields.One2many('pengajuan.jasa', 'crm_id', string='List Pengajuan Jasa')
    
class SolutionStatus(models.Model):    
    _name = 'solution.status'
    _description = 'Solution Status'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    approved = fields.Boolean('Approved')
    
class IndustryType(models.Model):    
    _name = 'industry.type'
    _description = 'Industry Type'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20) 

class SolutionsList(models.Model):
    _name = 'solutions.list'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Solutions List'
    _order = "id desc"
    _track_duration_field = 'status'
    
    @api.returns('self')
    def _default_status(self):
        return self.env['solution.status'].search([], limit=1)

    name = fields.Char('Solution Title', tracking=True)
    create_date = fields.Datetime('Created Date', tracking=True, default=lambda self: fields.datetime.now())
    approve_date = fields.Datetime(tracking=True)
    status = fields.Many2one('solution.status', string='Status', tracking=True, default=_default_status)
    created_by = fields.Many2one('res.users', string='Created By', default=lambda self: self.env.uid, required=True, tracking=True)
    industry = fields.Many2many('industry.type', string='Industry', tracking=True)
    description = fields.Html(string='Description')
    reject_reason = fields.Char(string='Reject Reason', tracking=True)
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True)
    file_pdf = fields.Binary(string='File PDF')
    
    #Parameter Fields
    file_name = fields.Char(string='File Name', store=True, compute='_compute_file_name')
    save = fields.Boolean(string='Save')
    kpi_id = fields.Many2one('presales.kpi', string='KPI ID', store=True, compute='_compute_kpi_id')
    url_public = fields.Char(string='URL Public')
    is_rejected = fields.Boolean(string='Rejected', default=False)
    is_cancel = fields.Boolean(string='Cancel', default=False)
    
    def dummy_save(self):
        self.write({'save': 1})
        
    def cancel(self):
        self.write({'is_cancel': 1, 'approve_date': False, 'status': 1})
        
    def submit(self):
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&menu_id=660&action=1097&model=solutions.list&view_type=form'}"})
        self.write({'status': 2, 'reject_reason': False, 'is_rejected': False, 'is_cancel': False})
        self.new_record_mail()
        
    def open_reject(self):
        return {
            'name': 'Reject Reason', # Label
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('dashboard_presales.solution_reject_view_form').id,
            'res_model': 'solutions.list',
            'res_id': self.id,
            'target': 'new',
        }
        
    def approve(self):
        self.write({'status': 3, 'approve_date': fields.Datetime.now()})
        self.new_record_action_mail()
        
    def reject(self):
        self.write({'status': 1, 'is_rejected': 1})
        self.new_record_action_mail()
        
    def old_version(self):
        self.write({'status': 4})

    @api.depends('approve_date')
    def _compute_kpi_id(self):
        if self.approve_date:
            current_month = self.create_date.month
            current_year = self.create_date.year
            presales = self.created_by.name
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
    
    @api.depends('name','created_by')
    def _compute_file_name(self):
        for rec in self:
            rec.write({'file_name': f"{rec.name}{' by '}{rec.created_by.name}"}) 
    
    def new_record_mail(self):
        mail_template = self.env.ref('dashboard_presales.new_solution_issued')
        mail_template.send_mail(self.id)
        
    def new_record_action_mail(self):
        mail_template = self.env.ref('dashboard_presales.new_solution_action')
        mail_template.send_mail(self.id)    

class TrainingStatus(models.Model):    
    _name = 'training.status'
    _description = 'Training Status'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    approved = fields.Boolean('Approved')

class PresalesTraining(models.Model):
    _name = 'presales.training'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Presales Training and Certification'
    _order = "issued_date desc"
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_status(self):
        return self.env['training.status'].search([], limit=1)
    
    name = fields.Many2one('res.users', string='Name', default=lambda self: self.env.uid, required=True, tracking=True)
    create_date = fields.Datetime('Created Date', default=lambda self: fields.datetime.now())
    submit_date = fields.Datetime('Submit Date')
    approved_date = fields.Datetime('Approved Date')
    reject_date = fields.Datetime('Rejected Date')
    status_id = fields.Many2one('training.status', tracking=True, default=_default_status)
    brand = fields.Many2one('brand', string='Brand', tracking=True)
    subject = fields.Char('Name', tracking=True)
    category = fields.Many2one('crm.skill.category', string='Category', tracking=True)
    type = fields.Selection([('training', 'Training'), ('certification', 'Certification')], default='certification', string='Type', tracking=True)
    issued_date = fields.Date('Issued / Training Date', tracking=True)
    exp_date = fields.Date('Expiration Date', tracking=True)
    reject_reason = fields.Char(string='Reject Reason', tracking=True)
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Please Attach Your Certificate / Training Materials Here")
    kpi_id = fields.Many2one('presales.kpi', string='KPI ID', store=True, compute='_compute_kpi_id')
    
    ##Parameter
    is_rejected = fields.Boolean(string='Rejected', default=False)
    is_cancel = fields.Boolean(string='Cancel', default=False)
    url_public = fields.Char(string='URL Public')

    def submit(self):
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&menu_id=660&action=1098&model=presales.training&view_type=form'}"})
        self.write({'status_id': 2, 'submit_date': fields.Datetime.now(),'is_cancel': False, 'is_rejected': False, 'reject_reason': False})
        self.new_record_mail()
        
    def approve(self):
        self.write({'status_id': 3, 'approved_date': fields.Datetime.now()})
        self.new_record_action_mail()
        
    def open_reject(self):
        return {
            'name': 'Reject Reason', # Label
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('dashboard_presales.presales_training_reject_view_form').id,
            'res_model': 'presales.training',
            'res_id': self.id,
            'target': 'new',
        }    
        
    def reject(self):
        self.write({'status_id': 1, 'reject_date': fields.Datetime.now(), 'approved_date': False, 'submit_date': False, 'is_rejected': True})
        self.new_record_action_mail()
        
    def cancel(self):
        self.write({'status_id': 1, 'reject_date': False, 'approved_date': False, 'submit_date': False, 'is_cancel': True})      
        
    @api.depends('status_id','name','approved_date')
    def _compute_kpi_id(self):        
        presales = self.name.name
        if self.status_id.id == 3:  
            current_month = self.issued_date.month
            current_year = self.issued_date.year            
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
    
    def new_record_mail(self):
        mail_template = self.env.ref('dashboard_presales.new_record_issued')
        mail_template.send_mail(self.id)
        
    def new_record_action_mail(self):
        mail_template = self.env.ref('dashboard_presales.new_record_action')
        mail_template.send_mail(self.id)
            
        
     
    
    