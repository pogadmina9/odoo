# -*- coding: utf-8 -*-

import logging
import ast
from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta
from datetime import date


from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.addons.resource.models.utils import Intervals
from odoo.tools import format_datetime
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.tools.float_utils import float_is_zero
from odoo.tools import format_duration


class CRMPresalesTeam(models.Model):
    _name = 'crm.presales.team'
    _description = 'Presales Team'
    _order = "sequence, id"

    name = fields.Many2one('res.users', string='Name', required=True)
    sequence = fields.Integer(string='Sequence')
    color = fields.Integer('Color Index')

    project_count_ids = fields.One2many('crm.lead', string="Project", copy=False,
                                       compute='_compute_project_count')
    project_count = fields.Integer(string='Count of Project', compute='_compute_project_count')
    project_progress = fields.Integer(string='Count of Progress Project', compute='_compute_project_count')
    project_win = fields.Integer(string='Count of Win Project', compute='_compute_project_count')
    project_lose = fields.Integer(string='Count of Lose Project', compute='_compute_project_count')

    @api.depends('name')
    def _compute_project_count(self):
        for team in self:
            team.project_count_ids = self.env['crm.lead'].search(
                [('presales_team', '=', team.id), ('active', '=', True)])
            data1 = self.env['crm.lead']._read_group(
                [('presales_team', '=', team.id)],
                ['date_deadline:year', 'presales_team', 'user_id', ],
                ['__count']
            )
            data2 = self.env['crm.lead']._read_group(
                [('presales_team', '=', team.id), ('stage_id.id', '!=', '4'), ('active', '=', True)],
                ['date_deadline:year', 'presales_team', 'user_id', ],
                ['__count']
            )
            data3 = self.env['crm.lead']._read_group(
                [('presales_team', '=', team.id), ('stage_id.id', '=', '4'), ('active', '=', True)],
                ['date_deadline:year', 'presales_team', 'user_id', ],
                ['__count']
            )
            data4 = self.env['crm.lead']._read_group(
                [('presales_team', '=', team.id), ('stage_id.id', '=', '13')],
                ['date_deadline:year', 'presales_team', 'user_id', ],
                ['__count']
            )
            team.project_count = sum(count for (_, _, _, count) in data1)
            team.project_win = sum(count for (_, _, _, count) in data3)
            team.project_lose = sum(count for (_, _, _, count) in data4)
            team.project_progress = team.project_count-team.project_win-team.project_lose
            
    #def action_review(self):
        #return self.env["ir.actions.act_window"]._for_xml_id('maintenance.presales_review_wizard_action')

     
class CRMPresalesActivity(models.Model):
    _name = 'crm.presales.activity'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Presales Activity'
    _order = "date desc"
        
    name = fields.Many2one('res.users', string='Presales', default=lambda self: self.env.uid, required=True)
    activity = fields.Many2one('crm.activity.type', string='Activity Type')
    date = fields.Datetime(string='Date')
    duration = fields.Float(help="Duration in hours.")
    sales = fields.Many2one('res.users', string='Sales')
    no_ppc = fields.Many2one('crm.lead', string='No Oppty / Paket')
    user = fields.Many2one('res.partner', tracking=True, string='Instansi', domain="[('is_company', '=', True)]")
    contact_person = fields.Many2one('res.partner', tracking=True, string='Contact Person', domain="['|', ('child_ids', '=', user), ('parent_id', '=', user)]")
    phone = fields.Char(string='Phone', tracking=True)
    job_title = fields.Char(string='Jabatan', tracking=True)
    detail = fields.Char(string='Detail Activity', tracking=True)
    
    @api.onchange('no_ppc')
    def _auto_fill_with_ppc(self):
        if self.no_ppc:
            self.sales = self.no_ppc.user_id.id
            self.user = self.no_ppc.partner_id.id
            self.contact_person = self.no_ppc.contact_person.id
            self.phone = self.no_ppc.contact_person.phone
            self.job_title = self.no_ppc.contact_person.function
        else:
            self.sales = None    
            self.user = None    
            self.contact_person = None    
    
    @api.onchange('contact_person')
    def _change_contact_phone_number(self):
        if self.contact_person:
            self.phone = self.contact_person.phone
        else:
            self.phone = None
            
    @api.onchange('contact_person')
    def _change_contact_title(self):
        if self.contact_person:
            self.job_title = self.contact_person.function
        else:
            self.job_title = None
    
class CRMActivityType(models.Model):
    _name = 'crm.activity.type'
    _description = 'Activity Type'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    short_name = fields.Char('Short Name')
    sequence = fields.Integer('Sequence', default=20)
    used_in_jasa = fields.Boolean('Jasa', default=False)
    need_presales_report = fields.Boolean('Need Report', default=False)
    
class CRMPresalesReview(models.Model):
    _name = 'crm.presales.review'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Presales Review'
    _order = "id desc"

    name = fields.Many2one('crm.presales.team', string='Presales', related='no_ppc.presales_team')
    create_date = fields.Datetime('Created Date', tracking=True, default=lambda self: fields.datetime.now())
    rating = fields.Selection([('0', 'No Rating'), ('1', 'Very Bad'), ('2', 'Bad'), ('3', 'Average'), ('4', 'Good'), ('5', 'Very Good')], string='Rating', required=True, tracking=True)
    comment = fields.Char('Comment', tracking=True, required=True)
    writer = fields.Many2one('res.users', default=lambda self: self.env.uid, readonly=True)
    no_ppc = fields.Many2one('crm.lead', string='No Oppty / Paket')
    
    
    def action_submit(self):
        return {'type': 'ir.actions.act_window_close'}
        
class CRMSkillsMatrix(models.Model):
    _name = 'crm.skills.matrix'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Skills'
    _order = "id desc"

    name = fields.Many2one('res.users', string='Name', default=lambda self: self.env.uid, required=True, tracking=True)
    create_date = fields.Datetime('Created Date', tracking=True, default=lambda self: fields.datetime.now())
    brand = fields.Many2one('brand', string='Brand', tracking=True)
    subject = fields.Char('Subject', tracking=True)
    skill = fields.Many2one('crm.skill.category', string='Category', tracking=True)
    type = fields.Selection([('training', 'Training'), ('certification', 'Certification')], string='Type', tracking=True)
    issued_date = fields.Date('Issued Date', tracking=True)
    exp_date = fields.Date('Expiration Date', tracking=True)
    
class CRMSkillCategory(models.Model):
    _name = 'crm.skill.category'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Skill Category'
    _order = "sequence, name desc"

    name = fields.Char(string='Name', required=True, tracking=True)
    sub_cat = fields.Many2many('crm.skill.sub.category', string='Sub Category', tracking=True)
    sequence = fields.Integer('Sequence', default=20)

class CRMSkillSubCategory(models.Model):
    _name = 'crm.skill.sub.category'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Skill Sub Category'
    _order = "name asc"

    name = fields.Char(string='Name', required=True, tracking=True)
   




