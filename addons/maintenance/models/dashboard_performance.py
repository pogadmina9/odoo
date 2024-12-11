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


class PresalesTeam(models.Model):
    _name = 'presales.team'
    _description = 'Presales Team'
    _order = "sequence, id"

    name = fields.Many2one('res.users', string='Name', required=True)
    sequence = fields.Integer(string='Sequence')
    color = fields.Integer('Color Index')

    project_count_ids = fields.One2many('presales.project.monitoring', string="Project", copy=False,
                                       compute='_compute_project_count')
    project_count = fields.Integer(string='Count of Project', compute='_compute_project_count')
    project_progress = fields.Integer(string='Count of Progress Project', compute='_compute_project_count')
    project_win = fields.Integer(string='Count of Win Project', compute='_compute_project_count')
    project_lose = fields.Integer(string='Count of Lose Project', compute='_compute_project_count')

    @api.depends('name')
    def _compute_project_count(self):
        for team in self:
            team.project_count_ids = self.env['presales.project.monitoring'].search(
                [('presales', '=', team.id), ('archive', '=', False)])
            data1 = self.env['crm.lead']._read_group(
                [('presales', '=', team.id)],
                ['date_deadline:year', 'presales', 'user_id', ],
                ['__count']
            )
            data2 = self.env['crm.lead']._read_group(
                [('presales', '=', team.id), ('stage_id.id', '!=', '4'), ('active', '=', True)],
                ['date_deadline:year', 'presales', 'user_id', ],
                ['__count']
            )
            data3 = self.env['crm.lead']._read_group(
                [('presales', '=', team.id), ('stage_id.id', '=', '4'), ('active', '=', True)],
                ['date_deadline:year', 'presales', 'user_id', ],
                ['__count']
            )
            data4 = self.env['crm.lead']._read_group(
                [('presales', '=', team.id), ('stage_id.id', '=', '13')],
                ['date_deadline:year', 'presales', 'user_id', ],
                ['__count']
            )
            team.project_count = sum(count for (_, _, _, count) in data1)
            team.project_win = sum(count for (_, _, _, count) in data3)
            team.project_lose = sum(count for (_, _, _, count) in data4)
            team.project_progress = team.project_count-team.project_win-team.project_lose
            
    def action_review(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.presales_review_wizard_action')



class PresalesStage(models.Model):
    _name = 'presales.stage'
    _description = 'Presales Stage'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')

class PresalesProjectMonitoring(models.Model):
    _name = 'presales.project.monitoring'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Project'
    _order = "id desc"
    _rec_name = 'no_ppc'
    _track_duration_field = 'stage_id'

    @api.returns('self')
    def _default_stage(self):
        return self.env['presales.stage'].search([], limit=1)

    name = fields.Char('Name', required=True)
    request_date = fields.Datetime('Created Date', tracking=True, default=lambda self: fields.datetime.now())
    archive = fields.Boolean(default=False)
    user = fields.Many2one('res.partner', tracking=True, string='Instansi')
    address = fields.Char(string='Address', tracking=True)
    contact_person = fields.Many2one('res.partner', tracking=True, string='Contact Person')
    phone = fields.Char(string='Phone', tracking=True)
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')
    estimate_amount = fields.Monetary(string="Estimate of Project Amount", store=True, tracking=True)
    deal_amount = fields.Monetary(string="Contract Amount", store=True, tracking=True)
    technical_status = fields.Selection([('lose', 'Lose'), ('win', 'Win')], string='Technical Status', tracking=True)
    lose_reason = fields.Char(string='Lose Reason')
    estimate_date = fields.Datetime(string='Estimate of Project Date')    
    presales = fields.Many2one('presales.team', tracking=True, string='Presales')
    request_id = fields.Many2one('pengajuan.jasa', string='Request ID')
    sales = fields.Many2one('res.partner', tracking=True, string='Sales')
    check_sales = fields.Boolean(default=False, compute='_check_sales')
    no_ppc = fields.Char(string='No Opty / Paket')
    color = fields.Integer('Color Index')
    description = fields.Html('Project Detail')
    stage_id = fields.Many2one('presales.stage', string='Stage', ondelete='restrict', tracking=True,
                               group_expand='_read_group_stage_presales_ids', default=_default_stage, copy=False)

      
    def write_review(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.presales_review_wizard_action')
    
    @api.model
    def _read_group_stage_presales_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_presales_ids = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_presales_ids)
        
    @api.onchange('contact_person')
    def _change_contact_phone_number(self):
        if self.contact_person:
            self.phone = self.contact_person.mobile
        else:
            self.phone = None
            
    @api.depends_context('uid')
    def _check_sales(self):
        if self.env.user == self.sales.user_ids:
            self.check_sales = 'True'

        else:
            self.check_sales = None            
    
        
class PresalesActivity(models.Model):
    _name = 'presales.activity'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Presales Activity'
    _order = "id desc"
        
    name = fields.Many2one('res.users', string='Presales', default=lambda self: self.env.uid, required=True)
    activity = fields.Many2one('activity.type', string='Activity Type')
    date = fields.Datetime(string='Date')
    duration = fields.Float(help="Duration in hours.")
    sales = fields.Many2one('res.users', string='Sales')
    no_ppc = fields.Many2one('presales.project.monitoring', string='No Oppty / Paket')
    user = fields.Many2one('res.partner', tracking=True, string='Instansi', domain="[('is_company', '=', True)]")
    contact_person = fields.Many2one('res.partner', tracking=True, string='Contact Person', domain="['|', ('child_ids', '=', user), ('parent_id', '=', user)]")
    phone = fields.Char(string='Phone', tracking=True)
    job_title = fields.Char(string='Jabatan', tracking=True)
    detail = fields.Char(string='Detail Activity', tracking=True)
    services_req_id = fields.Many2one('maintenance.request', string='ID Pengajuan Jasa')
    
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
    
class ActivityType(models.Model):
    _name = 'activity.type'
    _description = 'Activity Type'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    short_name = fields.Char('Short Name')
    sequence = fields.Integer('Sequence', default=20)
    used_in_jasa = fields.Boolean('Jasa', default=False)
    
class PresalesReview(models.Model):
    _name = 'presales.review'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Presales Review'
    _order = "id desc"

    name = fields.Many2one('presales.team', string='Presales', related='no_ppc.presales')
    create_date = fields.Datetime('Created Date', tracking=True, default=lambda self: fields.datetime.now())
    rating = fields.Selection([('0', 'No Rating'), ('1', 'Very Bad'), ('2', 'Bad'), ('3', 'Average'), ('4', 'Good'), ('5', 'Very Good')], string='Rating', required=True, tracking=True)
    comment = fields.Char('Comment', tracking=True, required=True)
    writer = fields.Many2one('res.users', default=lambda self: self.env.uid, readonly=True)
    no_ppc = fields.Many2one('presales.project.monitoring', string='No Oppty / Paket')
    
    
    def action_submit(self):
        return {'type': 'ir.actions.act_window_close'}
        
class SkillsMatrix(models.Model):
    _name = 'skills.matrix'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Skills'
    _order = "id desc"

    name = fields.Many2one('res.users', string='Name', default=lambda self: self.env.uid, required=True, tracking=True)
    create_date = fields.Datetime('Created Date', tracking=True, default=lambda self: fields.datetime.now())
    brand = fields.Many2one('brand', string='Brand', tracking=True)
    subject = fields.Char('Subject', tracking=True)
    skill = fields.Many2one('skill.category', string='Category', tracking=True)
    type = fields.Selection([('trainig', 'Training'), ('certification', 'Certification')], string='Type', tracking=True)
    issued_date = fields.Date('Issued Date', tracking=True)
    exp_date = fields.Date('Expiration Date', tracking=True)
    
class SkillCategory(models.Model):
    _name = 'skill.category'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Skill Category'
    _order = "sequence, name desc"

    name = fields.Char(string='Name', required=True, tracking=True)
    sub_cat = fields.Many2many('skill.sub.category', string='Sub Category', tracking=True)
    sequence = fields.Integer('Sequence', default=20)

class SkillSubCategory(models.Model):
    _name = 'skill.sub.category'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Skill Sub Category'
    _order = "name asc"

    name = fields.Char(string='Name', required=True, tracking=True)
   




