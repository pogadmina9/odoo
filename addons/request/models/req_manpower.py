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
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.osv import expression
from odoo.tools.translate import _
from odoo.tools import date_utils, email_split, is_html_empty, groupby, parse_contact_from_email
from odoo.tools.misc import get_lang
from odoo.addons.resource.models.utils import Intervals
from odoo.addons.resource.models.utils import filter_domain_leaf

class MaintenanceTeam(models.Model):
    _inherit = 'maintenance.team'

    mpr_count = fields.Integer(string="Number of Ongoing Request", compute='_compute_mpr_count')
    my_mpr_count = fields.Integer(string="Number of My Request", compute='_compute_mpr_count')
    my_approval_mpr_count = fields.Integer(string="Number of Need My Approval", compute='_compute_mpr_count')
    new_mpr_count = fields.Integer(string="Number of New Request", compute='_compute_mpr_count')
    my_review_candidate_count = fields.Integer(string="Number of Need My Review", compute='_compute_candidate_count')
    today_interview_count = fields.Integer(string="Number of Today Interview", compute='_compute_candidate_count')
    my_interview_confirmation_count = fields.Integer(string="Number of Need My Confirmation", compute='_compute_candidate_count')

   
    def _compute_mpr_count(self):
        for team in self:
            uid = self.env.uid
            employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)], limit=1).id
            data1 = self.env['req.manpower']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('status_id', '!=', [1])],
                ['create_date:year', 'is_rejected', 'location', ],
                ['__count']
            )
            data2 = self.env['req.manpower']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('status_id', '!=', [1]), ('requester', 'in', [employee])],
                ['create_date:year', 'is_rejected', 'location', ],
                ['__count']
            )
            data3 = self.env['req.manpower']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id', '=', [2]), ('pending_approver', 'in', [employee])],
                ['create_date:year', 'is_rejected', 'location', ],
                ['__count']
            )
            data4 = self.env['req.manpower']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id', '=', [3])],
                ['create_date:year', 'is_rejected', 'location', ],
                ['__count']
            )

            team.mpr_count = sum(count for (_, _, _, count) in data1)
            team.my_mpr_count = sum(count for (_, _, _, count) in data2)
            team.my_approval_mpr_count = sum(count for (_, _, _, count) in data3)
            team.new_mpr_count = sum(count for (_, _, _, count) in data4)
            
            
    def _compute_candidate_count(self):
        for team in self:
            uid = self.env.uid
            employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)], limit=1).id
            data1 = self.env['candidate.screening']._read_group(
                [('status_id', '=', 5), ('manpower_request_id.requester', 'in', [employee])],
                ['create_date:year', 'is_rejected', 'age', ],
                ['__count']
            )
            data2 = self.env['candidate.interview']._read_group(
                [('status_id.done', '=', False), ('interviewers', 'in', [employee]), ("start", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("stop", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
                ['create_date:year', 'start:year', 'stop:year', ],
                ['__count']
            )
            data3 = self.env['candidate.interview']._read_group(
                [('status_id.done', '=', False), ('pending_confirm', 'in', [employee])],
                ['create_date:year', 'start:year', 'stop:year', ],
                ['__count']
            )
            
            team.my_review_candidate_count = sum(count for (_, _, _, count) in data1)
            team.today_interview_count = sum(count for (_, _, _, count) in data2)
            team.my_interview_confirmation_count = sum(count for (_, _, _, count) in data3)
            
    def today_interview_list(self):
        return {
            'name': 'Today Interview',
            'type': 'ir.actions.act_window',
            'view_type': 'kanban,calendar,form',
            'view_mode': 'kanban,calendar,form',
            'res_model': 'candidate.interview',
            'domain': [("start", ">=", fields.Datetime.to_string(fields.Datetime.today())), 
                                                    ("stop", "<=", fields.Datetime.to_string(fields.Datetime.today().replace(hour=23, minute=59,second=59)))],
        }
     
class Skill(models.Model):
    _name = 'skill'
    _description = 'Skill'
    _order = 'name asc'

    name = fields.Char('Name', required=True)

class CandidateSkill(models.Model):
    _name = 'candidate.skill'
    _description = 'Candidate Skill'
    _order = 'id'

    name = fields.Many2one('skill', required=True)
    level = fields.Selection([('Basic', 'Basic'), ('Medium', 'Medium'), ('Advanced', 'Advanced')], default='Basic', required=True)
    
    ##Many2one
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade')
    candidate_database_id = fields.Many2one('candidate.database', string='Candidate Database')
    screening_id = fields.Many2one('candidate.screening', string="Latest Screening")    

class WorkingLocation(models.Model):
    _name = 'working.location'
    _description = 'Working Location'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20) 

class CandidateRejectReason(models.Model):
    _name = 'candidate.reject.reason'
    _description = 'Candidate Reject Reason'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)   

class MprReason(models.Model):
    _name = 'mpr.reason'
    _description = 'Manpower Request Reason'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)

class ManpowerStatus(models.Model):
    _name = 'manpower.status'
    _description = 'Manpower Request Status'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    done = fields.Boolean('Request Done')
    
class InterviewStatus(models.Model):
    _name = 'interview.status'
    _description = 'Interview Status'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    done = fields.Boolean('Request Done')

class ReqManpower(models.Model):
    _name = 'req.manpower'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Manpower Request'
    _order = 'id desc'
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['manpower.status'].search([], limit=1)
        
    @api.returns('self')
    def _default_approval_config(self):
        return self.env['req.approval.config'].search([], limit=1)
        
    @api.model
    def _default_requester(self):
        uid = self.env.uid
        return self.sudo().env['hr.employee'].search([('user_id', '=', uid)], limit=1)
        
    @api.returns('self')
    def _default_team_config(self):
        return self.env['req.team.config'].search([], limit=1)
    
    name = fields.Char(string='Name', default=lambda self: _('New Request'))
    display_name = fields.Char(string='Name', default=lambda self: _('New Request'), compute='write_display_name', store=True)
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', default=10)
    config_approval_id = fields.Many2one('req.approval.config', string='Approval Config', default=_default_approval_config)
    status_id = fields.Many2one('manpower.status', string='Status', default=_default_stage, group_expand='_read_group_status_ids', tracking=True)
    requester = fields.Many2one('hr.employee', string='Requester', default=_default_requester, tracking=True)
    requester_job = fields.Many2one('hr.job', string='Job Position', related='requester.job_id')
    requester_department = fields.Many2one('hr.department', string='Department', related='requester.department_id')    
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Optional")
    
    ##Approval
    next_approver = fields.Many2one('hr.employee', string='Next Approver')
    pending_approver = fields.Many2many('hr.employee', string='Pending Approver', readonly=False)
    pending_approver1 = fields.Char('Approver 1')
    pending_approver2 = fields.Char('Approver 2')
    pending_approver3 = fields.Char('Approver 3')
    
    ##Requirement
    job_position = fields.Char(string='Position', tracking=True)
    location = fields.Many2one('working.location', tracking=True)
    salary_by_hc = fields.Boolean('Salary by HC?')
    max_salary = fields.Monetary(string='Max. Salary')
    benefit = fields.Char(string='Benefit')
    last_modified_salary = fields.Many2one('hr.employee')
    direct_upline = fields.Many2one('hr.employee', tracking=True)
    candidate_department = fields.Many2one('hr.department', string='Department', related='direct_upline.department_id')
    employee_type = fields.Selection([('Internship', 'Internship'), ('Contract', 'Contract'), ('Permanent', 'Permanent')], 'Type', default='Contract', required=True)
    deadline = fields.Date(tracking=True)
    quantity = fields.Integer(tracking=True, default="1")
    mpr_reason_id = fields.Many2one('mpr.reason', default=1, tracking=True, required=True)
    other_reason = fields.Char(tracking=True)
    cancel_reason = fields.Char(tracking=True)
    
    ##Qualification
    education = fields.Selection([('SMA/SMK', 'SMA/SMK'), ('D3', 'D3'), ('Sarjana', 'Sarjana'), ('Magister', 'Magister'), ('Doktoral', 'Doktoral')], default='SMA/SMK', required=True, tracking=True)
    age = fields.Integer(tracking=True)
    gender = fields.Selection([('Male', 'Male'), ('Female', 'Female')])
    experience = fields.Selection([('Fresh Graduate', 'Fresh Graduate'), ('1 Year', '1 Year'), ('2 Years', '2 Years'), ('3 Years', '3 Years'), ('4 Years', '4 Years'), ('5 Years', '5 Years'), ('5+ Years', '5+ Years')], required=True, tracking=True)
    marital_status = fields.Selection([('Single', 'Single'), ('Married', 'Married')])
    skill = fields.One2many('candidate.skill', 'manpower_request_id', string='Skill')
    spec_memo = fields.Text('Other Spec.', tracking=True)
    
    ##Analytics
    applicants = fields.Integer('Total Applicants', compute='_compute_total_applicants')
    average_salary = fields.Monetary('Average Expected Salary', compute='_compute_average_salary')
    interview_count = fields.Integer('On Going Interview', compute='_count_analytics')
    screening_count = fields.Integer('On Going Screening', compute='_count_analytics')
    hired_candidate = fields.Many2one('candidate.screening', string='Hired Candidate')
    
    ##TimeTracking
    submit_date = fields.Datetime(string='Submit Date')
    approve_date = fields.Datetime(string='Approve Date')
    confirm_date = fields.Datetime(string='Confirm Date')
    close_date = fields.Datetime(string='Close Date')   

    ##Offering Letter
    ol_screening_id = fields.Many2one('candidate.screening', string='Candidate')
    
    
    ##Parameter
    dummy_save = fields.Boolean('Save')
    eligible_user = fields.Many2many('res.users', string='Eligible User')
    interviewers = fields.Many2many('res.users', 'interviewers_rec_rel', 'mpr_id', 'interviewers_id', string='Interviewers')
    color = fields.Integer(string='Color Index')
    currency_id = fields.Many2one('res.currency', default=11)
    no_salary = fields.Boolean('No Salary')
    seq = fields.Integer('Sequence')
    current_employee = fields.Many2one('hr.employee', compute='_compute_current_employee')
    is_approver = fields.Boolean('Approver', compute='_compute_is_aprover')
    is_mpr_team = fields.Boolean('MPR Team', compute='_compute_mpr_team')
    is_cancelled = fields.Boolean('Cancelled')
    is_rejected = fields.Boolean(string='Rejected')
    is_urgent = fields.Boolean(string='Urgent', store=True)
    url_public = fields.Char(string='URL Public')
    
    ##One2Many
    candidate_screening_ids = fields.One2many('candidate.screening', 'manpower_request_id', string="Candidate")
    candidate_interview_ids = fields.One2many('candidate.interview', 'manpower_request_id', string="Interview")
    manpower_approval_list_ids = fields.One2many('manpower.approval.list', 'manpower_request_id', string="Approval List")
    screening_activity_ids = fields.One2many('screening.activity', 'manpower_request_id', string="Activity")
    job_vacancies_ids = fields.One2many('job.vacancies', 'manpower_request_id', string="Job Vacancies")
    candidate_ol_ids = fields.One2many('candidate.ol', 'manpower_request_id', string="Candidate Offering Letter")
    candidate_onboarding_ids = fields.One2many('candidate.onboarding', 'manpower_request_id', string="Candidate Onboarding")
    
    #Config
    config_team_id = fields.Many2one('req.team.config', string='Approval Config', default=_default_team_config)
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('name', _("New Request")) == _("New Request"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('mpr.seq')

        return super().create(vals_list)
    
    def save(self):
        self.write({'dummy_save': True})
            
    def _reject(self):
        self.write({'status_id': 1, 'is_rejected': 1})
        self.pending_approver = [(5, 0, 0)]
        
    def _approve(self):
        uid = self.env.uid
        current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)]).id
        self.pending_approver = [(3, current_employee)]
        self.compute_next_approver()
        if not self.pending_approver and self.status_id.id == 2:
            self.status_id = 3
            self.approve_date = fields.Datetime.now()
            self.create_new_mpr_email()
        
        else:
            return
            
    def open_cancel(self):
        return {
            'name': 'Cancel Confirmation',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.req_manpower_cancel_form').id,
            'res_model': 'req.manpower',
            'res_id': self.id,
            'target': 'new',
        }  

    def open_reject_form(self):
        return {
            'name': 'Reject Request',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.manpower_approval_list_form').id,
            'res_model': 'manpower.approval.list',
            'context': {'default_manpower_request_id': self.id, 'default_action': 'Reject', 'default_max_salary': self.max_salary},
            'target': 'new',
        }
        
    def open_approve_form(self):
        return {
            'name': 'Reject Request',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.manpower_approval_list_form').id,
            'res_model': 'manpower.approval.list',
            'context': {'default_manpower_request_id': self.id, 'default_max_salary': self.max_salary},
            'target': 'new',
        }
        
    def cancel(self):
        self.write({'is_cancelled': True, 'status_id': 1, 'submit_date': False})
        self.pending_approver = [(5, 0, 0)]
        self.create_cancel_mpr_email()
        
    def confirm(self):
        self.write({'status_id': 4})
    
    def submit(self):
        self.compute_next_approver()
        if self.is_cancelled:
            self._compute_approver()            
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&cids=1&menu_id=488&action=1114&active_id=10&model=req.manpower&view_type=form'}"})
        self.write({'is_rejected': 0, 'is_cancelled': 0, 'cancel_reason': False, 'submit_date': datetime.now()})
        if not self.pending_approver:
            self.write({'status_id': 3, 'approve_date': fields.Datetime.now()})
            self.create_new_mpr_email()
        
        else:
            self.write({'status_id': 2})
            self.create_approval_email()
            
    @api.constrains('max_salary')
    def check_max_salary(self):
        if self.max_salary == 0 and not self.salary_by_hc:
            if self.env.user.has_group('request.group_mpr_admin') or self.env.user.has_group('request.group_mpr_team') or self.env.user.has_group('request.group_manager_bod'):
                raise ValidationError(
                    _("Isi jumlah maximal gaji atau centang 'Salary by HC?' ! \n \n(Bila 'Salary by HC?' dicentang maka HC yang akan menentukan maximal gaji berdasarkan pasaran gaji)"))
        
    @api.model
    def _read_group_status_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        status_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(status_id)
    
    def _count_analytics(self):
        for rec in self:            
            data1 = self.env['candidate.screening'].sudo()._read_group(
                [('is_rejected', '=', 1), ('manpower_request_id.id', '=', rec.id), ('status_id.id', '=', [3,4,5,6,7,8,9])],
                ['create_date:year', 'experience', 'age', ],
                ['__count']
            )
            data2 = self.env['candidate.screening'].sudo()._read_group(
                [('is_rejected', '=', 1), ('manpower_request_id.id', '=', rec.id)],
                ['create_date:year', 'experience', 'age', ],
                ['__count']
            )

            rec.interview_count = sum(count for (_, _, _, count) in data1)    
            rec.screening_count = sum(count for (_, _, _, count) in data2)    
            
    def _compute_average_salary(self):
        for rec in self:
            if rec.candidate_screening_ids:
                records = rec.env['candidate.screening'].search([('expected_salary', '!=', 0), ('manpower_request_id', '=', rec.id)])
                count = len(records)
                total = sum(records.mapped('expected_salary'))
                
                try:
                    rec.average_salary = total // count
                except ZeroDivisionError:
                    rec.average_salary = False
            else:
                rec.average_salary = False
    
    def _compute_total_applicants(self):
        for rec in self:
            if rec.job_vacancies_ids:
                records = rec.env['job.vacancies'].search([('manpower_request_id', '=', rec.id)])
                total = sum(records.mapped('applicants'))
                rec.applicants = total
            else:
                rec.applicants = False
                
    def _compute_mpr_team(self):
        if self.env.user.has_group('request.group_mpr_admin') or self.env.user.has_group('request.group_mpr_team'):
            self.is_mpr_team = True
        
        else:
            self.is_mpr_team = False
            
    def _list_email_approver(self):
        self.ensure_one()
        return "; ".join([e for e in self.pending_approver.mapped("work_email") if e])
        
    def create_approval_email(self):
        mail_template = self.env.ref('request.need_approval_mpr')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_new_mpr_email(self):
        mail_template = self.env.ref('request.new_mpr')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_cancel_mpr_email(self):
        mail_template = self.env.ref('request.cancel_mpr')
        mail_template.send_mail(self.id)
    
    def create_mpr_done_email(self):
        mail_template = self.env.ref('request.done_mpr')
        mail_template.send_mail(self.id)
    
    def write(self, vals):
        res = super(ReqManpower, self).write(vals)
        if 'candidate_screening_ids' in vals:
            if self.candidate_screening_ids and self.status_id.id in [3,4]:
                self.status_id = 5
        if 'job_vacancies_ids' in vals:
            self._update_to_job_vacancies()
        if 'candidate_screening_ids' in vals:
            self._update_current_mpr()
        if 'direct_upline' in vals:
            self._compute_eligible_user()
     
    @api.depends("name")
    def write_display_name(self):
        self.write({'display_name': f"{self.name}{' - '}{self.job_position}"})
        
    def _compute_status(self):
        for rec in self:
            if rec.status_id.id not in [1,2]:
                list_candidate = rec.sudo().env['candidate.screening'].search([('is_rejected', '=', 1), ('manpower_request_id', '=', rec.id)])
                status = list_candidate.mapped("status_id.id")
                ol_candidate = len(rec.sudo().env['candidate.screening'].search([('status_id.id', 'in', [10,11]), ('manpower_request_id', '=', rec.id)]))
                onboarding_candidate = len(rec.sudo().env['candidate.screening'].search([('status_id.id', '=', 11), ('manpower_request_id', '=', rec.id)]))
                done_candidate = len(rec.sudo().env['candidate.screening'].search([('status_id.id', '=', 12), ('manpower_request_id', '=', rec.id)]))
                
                if status:
                    if 12 in status and rec.quantity <= done_candidate:
                        rec.status_id = 9
                        rec.create_mpr_done_email()
                        rec.close_date = fields.Datetime.now()
                    elif 11 in status and rec.quantity <= onboarding_candidate:
                        rec.status_id = 8               
                    elif 10 in status and rec.quantity <= ol_candidate:
                        rec.status_id = 7                    
                    elif 9 in status:
                        rec.status_id = 6
                    elif 8 in status:
                        rec.status_id = 6
                    elif 7 in status:
                        rec.status_id = 6
                    elif 6 in status:
                        rec.status_id = 6
                    elif 5 in status:
                        rec.status_id = 6
                    elif 4 in status:
                        rec.status_id = 6
                    elif 3 in status:
                        rec.status_id = 6
                    elif 2 in status:
                        rec.status_id = 5                    
                    elif 1 in status:
                        rec.status_id = 5
                
                elif rec.job_vacancies_ids and not status:
                    rec.status_id = 4
                    
                else:
                    rec.status_id = 3
            
            else:
                return                     
    
    def _compute_is_aprover(self):
        uid = self.env.uid
        current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)])
        if current_employee in self.pending_approver:
            self.is_approver = True
            
        else:
            self.is_approver = False
    
    def _compute_current_employee(self):
        uid = self.env.uid
        current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)]).id
        self.current_employee = current_employee
    
    @api.onchange('requester')
    def _compute_approver(self):
        for rec in self:
            manager = self.requester.manager_id.id
            gm = self.requester.manager_id.manager_id.id
            director = self.requester.manager_id.manager_id.parent_id.id
            pending_approver = manager + gm + director
            list_bod = rec.sudo().env['hr.employee'].search([('job_id.name', 'ilike', 'Chief')])
            bod = list_bod.mapped("id")
            
            if rec.requester.id in bod:
                rec.pending_approver = False
            
            elif manager in bod:
                rec.pending_approver = [(6, 0, [manager])]
            
            elif gm in bod:
                rec.pending_approver = [(6, 0, [manager,gm])]
            
            else:
                rec.pending_approver = [(6, 0, [manager,gm,director])]
                
                
    def compute_next_approver(self):
        for rec in self:
            uid = self.env.uid
            current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)])
            list_bod = rec.sudo().env['hr.employee'].search([('job_id.name', 'ilike', 'Chief')])
            bod = list_bod.mapped("id")
            
            if rec.status_id.id == 1:
                rec.next_approver = rec.requester.manager_id.id
                
            elif current_employee in bod:
                rec.next_approver = False
                
            else:
                rec.next_approver = rec.next_approver.manager_id.id
                self.create_approval_email()
            
                
    def _update_to_job_vacancies(self):
        if self.status_id.id == 3 and self.job_vacancies_ids:
            self.status_id = 4
            
        elif not self.job_vacancies_ids and not self.candidate_screening_ids:
            self.status_id = 3
            
        else:
            return
    
    def _update_current_mpr(self):
        for rec in self:
            rec.candidate_screening_ids.update_screening_id()
            rec.candidate_screening_ids.retrieve_cv()
    
    @api.onchange('requester', 'direct_upline')
    def _compute_eligible_user(self):
        for rec in self:
            list_bod = rec.sudo().env['hr.employee'].search([('job_id.name', 'ilike', 'Chief')])
            bod = list_bod.mapped("user_id.id")
            requester = rec.requester.user_id.id
            if rec.requester.user_id.id in bod:
                if rec.direct_upline:
                    upline = rec.direct_upline.user_id.id
                    rec.eligible_user = [(6, 0, [requester,upline])]
                else:
                    rec.eligible_user = [(6, 0, [requester])]
                
            
            elif rec.requester:
                manager = self.requester.manager_id.user_id.id
                gm = self.requester.manager_id.manager_id.user_id.id
                director = self.requester.manager_id.manager_id.parent_id.user_id.id
                if rec.direct_upline:
                    upline = rec.direct_upline.user_id.id
                    if manager in bod:
                        rec.eligible_user = [(6, 0, [requester,manager,upline])]
                    elif gm in bod:
                        rec.eligible_user = [(6, 0, [requester,manager,gm,upline])]
                    else:
                        rec.eligible_user = [(6, 0, [requester,manager,gm,director,upline])]
                    
                else:
                    if manager in bod:
                        rec.eligible_user = [(6, 0, [requester,manager])]
                    elif gm in bod:
                        rec.eligible_user = [(6, 0, [requester,manager,gm])]
                    else:
                        rec.eligible_user = [(6, 0, [requester,manager,gm,director])]
            
            else:
                rec.eligible_user = [(6, 0, [requester])]
                
    def temp_write_end(self):
        no_end = self.sudo().env['pengajuan.jasa'].search([('schedule_end', '=', False)])
        for rec in no_end:
            rec.write({'schedule_end': rec.schedule_date})
                
        
    def open_action(self):
        return {
        'name': self.display_name,
        'type': 'ir.actions.act_window',
        'view_mode': 'form',
        'res_model': self._name,
        'res_id': self.id,
        'target': 'current'
        }

        
class ManpowerApprovalList(models.Model):
    _name = 'manpower.approval.list'
    _description = 'Manpower Approval List'
    _order = 'id desc'

    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade')    
    name = fields.Many2one('res.users', default=lambda self: self.env.uid)
    action = fields.Selection([('Approve', 'Approve'), ('Reject', 'Reject')], default='Approve')
    approval_date = fields.Datetime(string='Date', default=lambda self: fields.datetime.now())
    max_salary = fields.Monetary(string='Max. Salary')
    currency_id = fields.Many2one('res.currency', default=11)
    note = fields.Char(string='Note')
    
    def action_submit(self):
        if self.action == 'Approve':
            self.manpower_request_id._approve()
        elif self.action == 'Reject':
            self.manpower_request_id._reject()
            self.create_reject_mpr_email()
        return {'type': 'ir.actions.act_window_close'}
    
    @api.constrains('manpower_request_id')
    def check_max_salary(self):
        if self.manpower_request_id.max_salary == 0 and not self.manpower_request_id.salary_by_hc and self.action == 'Approve':
            if self.env.user.has_group('request.group_mpr_admin') or self.env.user.has_group('request.group_mpr_team') or self.env.user.has_group('request.group_manager_bod'):
                raise ValidationError(
                    _("Isi jumlah maximal gaji atau centang 'Salary by HC?' ! \n \n(Bila 'Salary by HC?' dicentang maka HC yang akan menentukan maximal gaji berdasarkan pasaran gaji)"))
                    
    def create_reject_mpr_email(self):
        mail_template = self.env.ref('request.reject_mpr')
        mail_template.send_mail(self.id)
        
class CandidateSource(models.Model):
    _name = 'candidate.source'
    _description = 'Candidate Source'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    
class JobVacancies(models.Model):
    _name = 'job.vacancies'
    _description = 'Job Vacancies'
    _order = 'id'

    name = fields.Many2one('candidate.source', string='Source')
    posting_date = fields.Date('Posting Date', default=lambda self: fields.Date.today())
    url = fields.Char('URL')
    applicants = fields.Integer('Applicants')
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade')
    
class CandidateOl(models.Model):
    _name = 'candidate.ol'
    _description = 'Candidate Offering Letter'
    _order = 'id'
    
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade')
    name = fields.Many2one('candidate.screening', required=True, ondelete='cascade')
    ol_date = fields.Date(string='OL Date', related='name.ol_date', readonly=False)
    company = fields.Many2one('res.partner', string='Company', related='name.company', readonly=False)
    join_date = fields.Date(string='Join Date', related='name.join_date', readonly=False)
    ol_status = fields.Selection([('Internship', 'Internship'), ('Contract', 'Contract'), ('Permanent', 'Permanent')], 'Type', related='name.ol_status', readonly=False)
    ol_duration = fields.Integer('Duration', related='name.ol_duration', readonly=False)
    probation = fields.Boolean('Probation', related='name.probation', readonly=False)
    ol_signed = fields.Date(string='OL Signed', related='name.ol_signed', readonly=False)
    
    # Parameter
    is_rejected = fields.Integer(string='Rejected', default=1)

class OnboardingItem(models.Model):
    _name = 'onboarding.item'
    _description = 'Onboarding Item'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    pic = fields.Many2many('hr.employee', string="PIC")
    sequence = fields.Integer('Sequence', default=20)
    
class OnboardingStatus(models.Model):
    _name = 'onboarding.status'
    _description = 'Onboarding Status'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    done = fields.Boolean('Request Done')
 
class OnboardingCandidate(models.Model):
    _name = 'onboarding.candidate'
    _description = 'Onboarding Candidate'
    _order = 'id'
    
    name = fields.Many2one('onboarding.item', required=True)
        
class CandidateOnboarding(models.Model):
    _name = 'candidate.onboarding'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Candidate Onboarding'
    _order = 'id'
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['onboarding.status'].search([], limit=1)

    name = fields.Many2one('onboarding.item', required=True)
    pic = fields.Many2many('hr.employee', string="PIC", related="name.pic")
    status_id = fields.Many2one('onboarding.status', string='Status', default=_default_stage, group_expand='_read_group_status_ids', tracking=True)
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade', related='screening_id.manpower_request_id', tracking=True)
    screening_id = fields.Many2one('candidate.screening', ondelete='cascade')
    job_position = fields.Char('Position', related='manpower_request_id.job_position')
    deadline = fields.Date('Deadline', help='Will Refer to New Employee Join Date', related='screening_id.join_date')
    company = fields.Many2one('res.partner', 'Company', related='screening_id.company')
    note = fields.Text(tracking=True)
    
    # Parameter
    dummy_save = fields.Boolean('Save')
    color = fields.Integer(string='Color Index')
    current_employee = fields.Many2one('hr.employee', compute='_compute_current_employee')
    url_public = fields.Char(string='URL Public', default='https://portal.performaoptimagroup.com/web#action=1127&model=candidate.onboarding&view_type=kanban&cids=1&menu_id=703')
    
    def save(self):
        self.write({'dummy_save': True})
        
    def write(self, vals):
        res = super(CandidateOnboarding, self).write(vals)
        if 'status_id' in vals:
            self._check_other_onboarding()
    
    @api.model
    def _read_group_status_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        status_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(status_id)
        
    def _compute_current_employee(self):
        uid = self.env.uid
        current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)]).id
        self.current_employee = current_employee
        
    def _check_other_onboarding(self):
        for rec in self:
            other_candidate_onboarding = rec.sudo().env['candidate.onboarding'].search([('status_id.id', 'in', [1,2]), ('screening_id.id', '=', rec.screening_id.id)])
            if not other_candidate_onboarding:
                rec.sudo().screening_id.write({'status_id': 12})
                
class CandidateStatus(models.Model):
    _name = 'candidate.status'
    _description = 'Candidate Status'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    done = fields.Boolean('Request Done')
        
class CandidateScreening(models.Model):
    _name = 'candidate.screening'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Candidate Screening'
    _order = 'id desc'
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['candidate.status'].search([], limit=1)
    
    name = fields.Char('Name', tracking=True)
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade')
    job_position = fields.Char(string='Position', related='manpower_request_id.job_position')
    candidate_database_id = fields.Many2one('candidate.database', string='Candidate Database', domain=[('screening_id', '=', False)])
    candidate_source_id = fields.Many2one('candidate.source', string='Source')
    status_id = fields.Many2one('candidate.status', string='Status', default=_default_stage, group_expand='_read_group_status_ids', tracking=True)
    education = fields.Selection([('SMA/SMK', 'SMA/SMK'), ('D3', 'D3'), ('Sarjana', 'Sarjana'), ('Magister', 'Magister'), ('Doktoral', 'Doktoral')], default='SMA/SMK', required=True, tracking=True)
    dob = fields.Date('Date of Birth')
    age = fields.Integer(store=True, compute='_compute_age', readonly=False)
    gender = fields.Selection([('Male', 'Male'), ('Female', 'Female')], store=True, compute='_existing_database', readonly=False)
    experience = fields.Selection([('Fresh Graduate', 'Fresh Graduate'), ('1 Year', '1 Year'), ('2 Years', '2 Years'), ('3 Years', '3 Years'), ('4 Years', '4 Years'), ('5 Years', '5 Years'), ('5+ Years', '5+ Years')], required=True, tracking=True)
    marital_status = fields.Selection([('Single', 'Single'), ('Married', 'Married')])
    skill = fields.One2many('candidate.skill', 'screening_id', string='Skill')
    expected_salary = fields.Monetary('Expected Salary')
    benefit = fields.Char('Benefit')
    available_date = fields.Date('Available Date', tracking=True)
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Optional")
    
    ##Offering Letter
    ol_date = fields.Date(string='OL Date')
    company = fields.Many2one('res.partner', string='Company')
    join_date = fields.Date(string='Join Date')
    ol_status = fields.Selection([('Internship', 'Internship'), ('Contract', 'Contract'), ('Permanent', 'Permanent')], 'Type', default='Contract')
    ol_duration = fields.Integer('Duration')
    probation = fields.Boolean('Probation')
    ol_signed = fields.Date(string='OL Signed')
    
    ##Reject
    reject_reason = fields.Many2one('candidate.reject.reason', string='Reject Reason', tracking=True)
    other_reject_reason = fields.Text('Reason', tracking=True)
    reject_by = fields.Selection([('Candidate', 'Candidate'), ('Interviewer', 'Interviewer')], tracking=True)
    update_reject_by = fields.Many2one('res.users', string='Update By', tracking=True)
    
    ##Parameter
    dummy_save = fields.Boolean('Save')
    type = fields.Selection([('New', 'New'), ('Existing', 'Existing')], default='New')
    interviewers = fields.Many2many('res.users', 'interviewers_screening_rel', 'screening_id', 'interviewers_id', string='Interviewers')
    is_mpr_team = fields.Boolean('MPR Team', compute='_compute_mpr_team')
    is_action_eligible = fields.Boolean('MPR Team', compute='_compute_action_eligible')
    color = fields.Integer(string='Color Index')
    currency_id = fields.Many2one('res.currency', default=11)
    is_rejected = fields.Integer(string='Rejected', default=1)
    url_public = fields.Char(string='URL Public')
    url_onboarding = fields.Char(string='URL Onboarding', default='https://portal.performaoptimagroup.com/web#action=1127&model=candidate.onboarding&view_type=kanban&cids=1&menu_id=703')
    
    ##One2many
    screening_result_ids = fields.One2many('screening.result', 'screening_id', string="Screening &amp; Interview Results")
    screening_activity_ids = fields.One2many('screening.activity', 'screening_id', string="Activity")
    candidate_interview_ids = fields.One2many('candidate.interview', 'screening_id', string="Interview")
    candidate_ol_ids = fields.One2many('candidate.ol', 'name', string="Candidate Offering Letter")
    candidate_onboarding_ids = fields.One2many('candidate.onboarding', 'screening_id', string="Candidate Onboarding")
    
    def save(self):
        self.write({'dummy_save': True})  
        
    def _update_next_step(self, interview_status_id):
        self.write({'status_id': interview_status_id})  
        
    def open_pre_action_form(self):
        return {
            'name': 'Screening Action',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.pre_action_form').id,
            'res_model': 'candidate.screening',
            'res_id': self.id,
            'target': 'new',
        }    
    
    def open_action_form(self):
        if self.status_id.id in [10]:
            return {
            'name': 'Onboarding Confirmation',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.candidate_screening_onboarding_form').id,
            'res_model': 'candidate.screening',
            'res_id': self.id,
            'target': 'new',
        }
        
        else:
            return {
                'name': 'Select Action',
                'type': 'ir.actions.act_window',
                'view_type': 'form',
                'view_mode': 'form',
                'view_id': self.env.ref('request.candidate_action_form').id,
                'res_model': 'candidate.action',
                'context': {'default_screening_id': self.id},
                'target': 'new',
            }
        
    def open_reject_form(self):
        return {
            'name': 'Reject Reason',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.candidate_screening_reject_form').id,
            'res_model': 'candidate.screening',
            'res_id': self.id,
            'target': 'new',
        }        
    
    def reject(self):
        if not self.reject_by or not self.reject_reason or not self.other_reject_reason:
            raise ValidationError(
                        _("Silahkan isi informasi reject dengan lengkap!"))
        else:
            self.write({'is_rejected': 2, 'update_reject_by': self.env.uid})
            self.candidate_database_id.screening_id = False
            if self.status_id.id == 10:
                ol_rec = self.sudo().env['candidate.ol'].search([('name', '=', self.id)]).id
                self.sudo().manpower_request_id.candidate_ol_ids = [(2, ol_rec, 0)]
            if self.status_id.id not in [1,2,3,4]:
                self.create_reject_email()
        
    def confirm_ol(self):
        if self.ol_date == False or self.company == False or self.join_date == False or self.ol_status == False:
            raise ValidationError(
                    _("Silahkan isi informasi Offering Letter dengan lengkap!"))
                    
        elif self.ol_duration == 0 and self.ol_status not in ['Permanent']:
            raise ValidationError(
                    _("Silahkan isi durasi contract / internship!"))
                    
        else:
            self.write({'status_id': 10})
            self.manpower_request_id.candidate_ol_ids = [(0, 0, {'name': self.id})]
        
    def confirm_onboarding(self):
        if self.ol_signed == False:
            raise ValidationError(
                    _("Silahkan isi tanggal kapan offering letter ditandatangani!"))
        else:
            for rec in self:
                for list in rec.sudo().env['onboarding.item'].search([]):
                    rec.candidate_onboarding_ids = [(0, 0, {'name': list.id, 'screening_id': rec.id})]
                    rec.write({'status_id': 11})
            self.create_onboarding_email()
            
    def write_url_public(self):
        if not self.url_public:
            self.sudo().write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&cids=1&model=candidate.screening&view_type=form&menu_id=488'}"})
        else:
            return
        
    @api.model
    def _read_group_status_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        status_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(status_id)
    
    def write(self, vals):
        res = super(CandidateScreening, self).write(vals)
        if 'status_id' in vals:
            self.write_url_public()
            self.create_candidate_database()
            self.manpower_request_id._compute_status()
        if 'is_rejected' in vals:
            self.manpower_request_id._compute_status()
        if 'candidate_source_id' in vals:
            self.candidate_database_id.candidate_source_id = self.candidate_source_id.id
        if 'name' in vals:
            self.candidate_database_id.name = self.name
        if 'dob' in vals:
            self.candidate_database_id.dob = self.dob
        if 'age' in vals:
            self.candidate_database_id.age = self.age
        if 'gender' in vals:
            self.candidate_database_id.gender = self.gender
        if 'education' in vals:
            self.candidate_database_id.education = self.education
        if 'experience' in vals:
            self.candidate_database_id.experience = self.experience
        if 'marital_status' in vals:
            self.candidate_database_id.marital_status = self.marital_status
        if 'skill' in vals:
            self.candidate_database_id.skill = self.skill
        if 'attachment_ids' in vals:
            if self.attachment_ids and self.is_rejected == 1:
                self.update_cv()
            elif not self.attachment_ids and self.is_rejected == 1:
                self.candidate_database_id.delete_cv()

    def create_candidate_database(self):
        current_id = self.id
        if not self.candidate_database_id and self.status_id.id not in [1]:
            create_database = self.env['candidate.database'].sudo().create({                
                'name': self.name,
                'screening_id': current_id,
                'candidate_source_id': self.candidate_source_id.id,
                'dob': self.dob,
                'age': self.age,
                'gender': self.gender,
                'education': self.education,
                'marital_status': self.marital_status,
                'experience': self.experience,
                'skill': self.skill,
                'attachment_ids': self.attachment_ids
                })
            created_database = self.sudo().env['candidate.database'].search([('name', '=', self.name), ('screening_id', '=', current_id)], limit=1)
            self.candidate_database_id = created_database
        elif self.type in ['Existing'] and self.candidate_database_id:
            self.candidate_database_id.screening_id = self.id
            
    def _compute_action_eligible(self):
        for rec in self:
            search_current_interviewers = self.sudo().env['screening.result'].search([('screening_id', '=', rec.id), ('status_id', '=', rec.status_id.id)])
            current_interviewers = search_current_interviewers.mapped("name.id")
            if self.env.user.has_group('request.group_mpr_admin') or self.env.user.has_group('request.group_mpr_team'):
                self.is_action_eligible = True
            
            elif self.status_id.id == 5 and self.manpower_request_id.create_uid.id == self.env.uid:
                self.is_action_eligible = True
                
            elif self.env.uid in current_interviewers:
                self.is_action_eligible = True
            
            else:
                self.is_action_eligible = False
            
    def _compute_mpr_team(self):
        if self.env.user.has_group('request.group_mpr_admin') or self.env.user.has_group('request.group_mpr_team'):
            self.is_mpr_team = True
        
        else:
            self.is_mpr_team = False
            
    @api.onchange('status_id')
    def _onchange_mpr_team(self):
        if self.env.user.has_group('request.group_mpr_admin') or self.env.user.has_group('request.group_mpr_team'):
            self.is_mpr_team = True
        
        else:
            self.is_mpr_team = False
    
    def open_action(self):
        return {
        'name': self.display_name,
        'type': 'ir.actions.act_window',
        'view_mode': 'form',
        'res_model': self._name,
        'res_id': self.id,
        'target': 'current'
        }
    
    @api.depends('dob')
    def _compute_age(self):
        for record in self:
            start = record.dob
            end = fields.Date.today()
            if record.dob:
                time_interval = Intervals([(start, end, record)])
                delta = sum((i[1] - i[0]).total_seconds() for i in time_interval)
                record.age = delta // 31536000.0
            
            else:
                record.age = False
        
    @api.depends('candidate_database_id')
    def _existing_database(self):
        if self.type in ['Existing']:
            self.name = self.candidate_database_id.name
            self.dob = self.candidate_database_id.dob
            self.age = self.candidate_database_id.age
            self.gender = self.candidate_database_id.gender
            self.education = self.candidate_database_id.education
            self.experience = self.candidate_database_id.experience
            self.marital_status = self.candidate_database_id.marital_status
            self.skill = self.candidate_database_id.skill
            self.candidate_source_id = 5           
    
    def update_screening_id(self):
        for rec in self:
            rec.candidate_database_id.screening_id = rec.id
    
    def update_cv(self):
        for rec in self:
            rec.candidate_database_id.attachment_ids = [(5)]
            for initial in self.attachment_ids:
                initial.copy()
                initial.write({'res_model': 'candidate.database', 'res_id': rec.candidate_database_id.id})
    
    def retrieve_cv(self):
        for rec in self:
            if not rec.attachment_ids and rec.candidate_database_id:
                database = rec.sudo().env['candidate.database'].search([('id', '=', rec.candidate_database_id.id)])
                for initial in rec.candidate_database_id.attachment_ids:
                    initial.copy()
                    initial.write({'res_model': 'candidate.screening', 'res_id': rec.id})
                    
    def _list_email_pic_onboarding(self):
        list_pic = self.sudo().env['candidate.onboarding'].search([('screening_id.id', '=', self.id), ('status_id.id', '=', 1)])
        self.ensure_one()
        return "; ".join([e for e in list_pic.mapped("pic.work_email") if e])
        
    def create_user_review_email(self):
        mail_template = self.env.ref('request.user_review_mpr')
        for rec in self:
            list_attachment = rec.attachment_ids
            cv_candidate = list_attachment.mapped("id")
            result_candidate = rec.screening_result_ids.attachment_ids.mapped("id")
            all_attachment = cv_candidate + result_candidate
            for result in rec.screening_result_ids.attachment_ids:
                result.copy()
        for cv in self.attachment_ids:
            cv.copy()
        mail_template.attachment_ids = [(6, 0, all_attachment)]
        mail_template.send_mail(self.id)
        
    def create_reject_email(self):
        mail_template = self.env.ref('request.candidate_reject_mpr')
        mail_template.send_mail(self.id) 
        
    def create_onboarding_email(self):
        mail_template = self.env.ref('request.reminder_onboarding_mpr')
        mail_template.send_mail(self.id)     
        
    def create_reminder_ol_email(self):
        mail_template = self.env.ref('request.reminder_offering_letter_mpr')
        mail_template.send_mail(self.id)
        
    def _cron_ol_reminder(self):
        list_ol = self.sudo().env['candidate.screening'].search([('status_id.id', '=', 10), ('ol_date', '<', fields.Date.today())])
        for list in list_ol:
            list.create_reminder_ol_email()   
            
    def _cron_onboarding_reminder(self):
        list_no_update = self.sudo().env['candidate.onboarding'].search([('status_id.id', '=', 1)])
        list_onboarding = list_no_update.mapped("screening_id.id")
        list_reminder = self.sudo().env['candidate.screening'].search([('id', 'in', list_onboarding)])
        for list in list_reminder:
            list.create_onboarding_email()
   
    
class ScreeningResult(models.Model):
    _name = 'screening.result'
    _description = 'Screening & Interview Results'
    _order = 'id desc'
    _rec_name = 'screening_id'
       
    name = fields.Many2one('res.users', default=lambda self: self.env.uid)
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade')
    candidate_action_id = fields.Many2one('candidate.action', string='Candidate Action')
    screening_id = fields.Many2one('candidate.screening', string="Screening ID", ondelete='cascade')
    candidate_interview_id = fields.Many2one('candidate.interview', string='Interview', ondelete='cascade')    
    status_id = fields.Many2one('candidate.status', string='Status')
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Optional")
    note = fields.Text('Result')
    
    @api.depends('screening_id')
    def _compute_status(self):
        for rec in self:
            if rec.candidate_action_id:
                for attachment in rec.candidate_action_id.attachment_ids:
                    attachment.write({'res_model': 'screening.result', 'res_id': rec.id})
                                    
            else:
                rec.status_id = rec.screening_id.status_id.id            

class CandidateDatabase(models.Model):
    _name = 'candidate.database'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Candidate Database'
    _order = 'id desc'

    name = fields.Char('Name', required=True)
    candidate_source_id = fields.Many2one('candidate.source', string='Source')
    dob = fields.Date('Date of Birth')
    age = fields.Integer('Age')
    gender = fields.Selection([('Male', 'Male'), ('Female', 'Female')])
    education = fields.Selection([('SMA/SMK', 'SMA/SMK'), ('D3', 'D3'), ('Sarjana', 'Sarjana'), ('Magister', 'Magister'), ('Doktoral', 'Doktoral')], default='SMA/SMK', required=True, tracking=True)
    manpower_request_id = fields.Many2one('req.manpower', string='Current Recruitment Process', related='screening_id.manpower_request_id')
    screening_id = fields.Many2one('candidate.screening', string="Current Screening")
    experience = fields.Selection([('Fresh Graduate', 'Fresh Graduate'), ('1 Year', '1 Year'), ('2 Years', '2 Years'), ('3 Years', '3 Years'), ('4 Years', '4 Years'), ('5 Years', '5 Years'), ('5+ Years', '5+ Years')], required=True, tracking=True)
    marital_status = fields.Selection([('Single', 'Single'), ('Married', 'Married')])
    skill = fields.One2many('candidate.skill', 'candidate_database_id', string='Skill')
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Optional")
    
    ##Parameter
    dummy_save = fields.Boolean('Save')
    
    ##One2many
    candidate_screening_ids = fields.One2many('candidate.screening', 'candidate_database_id', string="Recruitment History")
    
    def save(self):
        self.write({'dummy_save': True})
    
    def open_action(self):
        return {
        'name': self.display_name,
        'type': 'ir.actions.act_window',
        'view_mode': 'form',
        'res_model': self._name,
        'res_id': self.id,
        'target': 'current'
        }

    def delete_cv(self):
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
            rec.attachment_ids = [(5, 0, attachment)] 
        
        
class ScreeningActivity(models.Model):
    _name = 'screening.activity'
    _description = 'Screening Activity'
    _order = 'id desc'
    _rec_name = 'screening_id'
       
    name = fields.Many2one('res.users', default=lambda self: self.env.uid)
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade', store=True, related='screening_id.manpower_request_id', readonly=False)
    screening_id = fields.Many2one('candidate.screening', string="Screening ID", ondelete='cascade')
    status_id = fields.Many2one('candidate.status', string='Status', store=True, compute='_compute_screening')
    note = fields.Text('Note', required=True)
    
    @api.depends('screening_id')
    def _compute_screening(self):
        for rec in self:
            if rec.screening_id:
                rec.status_id = rec.screening_id.status_id.id
                
class CandidateInterview(models.Model):
    _name = 'candidate.interview'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Candidate Interview'
    _order = 'id desc'
    _rec_name = 'screening_id'    
    _track_duration_field = 'status_id'
    
    @api.model
    def _default_start(self):
        now = fields.Datetime.now()
        return now + (datetime.min - now) % timedelta(minutes=30) + timedelta(hours=24)

    @api.model
    def _default_stop(self):
        now = fields.Datetime.now()
        start = now + (datetime.min - now) % timedelta(minutes=30)
        return start + timedelta(hours=25)
        
    @api.returns('self')
    def _default_stage(self):
        return self.env['interview.status'].search([], limit=1)
        
    @api.model
    def _default_interviewer(self):
        uid = self.env.uid
        return self.sudo().env['hr.employee'].search([('user_id', '=', uid)], limit=1)
       
    status_id = fields.Many2one('interview.status', string="Status", default=_default_stage, group_expand='_read_group_status_ids', tracking=True)
    screening_id = fields.Many2one('candidate.screening', string="Candidate", ondelete='cascade')
    candidate_status_id = fields.Many2one('candidate.status', string='Interview Stage')
    job_position = fields.Char('Position', related='manpower_request_id.job_position')
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade', store=True, related='screening_id.manpower_request_id', readonly=False)
    interview_mode = fields.Selection([('Offline', 'Offline'), ('Online', 'Online')])
    interview_url = fields.Char('Interview URL', tracking=True)
    interviewers = fields.Many2many('hr.employee', string='Interviewers', default=_default_interviewer, tracking=True)
    pending_confirm = fields.Many2many('hr.employee', 'pending_confirm_req_rel', 'interview_id', 'user_id', string='Pending Confirm')
    color = fields.Integer(string='Color Index')
    
    ##Schedule
    start = fields.Datetime(
        'Start Time', required=True, tracking=True, default=_default_start)
    stop = fields.Datetime(
        'Stop Time', required=True, tracking=True, default=_default_stop,
        compute='_compute_stop', readonly=False, store=True)
    duration = fields.Float('Duration', compute='_compute_duration', store=True, readonly=False)
    
    # Parameter
    dummy_save = fields.Boolean('Save')
    current_employee = fields.Many2one('hr.employee', compute='_compute_current_employee')
    is_mpr_team = fields.Boolean('MPR Team', compute='_compute_mpr_team')
    url_public = fields.Char(string='URL Public')
    
    ##One2many
    interview_confirmation_ids = fields.One2many('interview.confirmation', 'candidate_interview_id', string="Confirmation List")
    screening_result_ids = fields.One2many('screening.result', 'candidate_interview_id', string="Interview Result")
    
    def save(self):
        self.write({'dummy_save': True})
        
    def done(self):
        self.write({'status_id': 4})
    
    def _reject(self):
        self.write({'status_id': 1})
        self.pending_confirm = [(5, 0, 0)]
        
    def _confirm(self):
        uid = self.env.uid
        current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)]).id
        self.pending_confirm = [(3, current_employee)]        
        if not self.pending_confirm and self.status_id.id == 1:
            self.status_id = 2
        
        else:
            return
            
    def confirm_all(self):
        for rec in self:
            for list in rec.pending_confirm.mapped("user_id.id"):
                uid_name = self.sudo().env['res.users'].search([('id', '=', self.env.uid)]).name
                rec.interview_confirmation_ids = [(0, 0, {'name': list, 'interview_mode': rec.interview_mode ,'start': rec.start, 'stop': rec.stop, 'note': f"{'Confirm by '}{uid_name}"})]
                rec.write({'status_id': 2, 'pending_confirm': False})   
    
    def candidate_confirm(self):
        for rec in self:
            rec.interview_confirmation_ids = [(0, 0, {'name': self.env.uid, 'interview_mode': rec.interview_mode ,'start': rec.start, 'stop': rec.stop, 'note': 'Candidate confirmed'})]
            rec.write({'status_id': 3})
            rec.create_confirmed_interview_email()
    
    def create_new_interview_email(self):             
        mail_template = self.env.ref('request.mpr_interview_schedule')
        for rec in self:
            list_attachment = rec.screening_id.attachment_ids
            cv_candidate = list_attachment.mapped("id")
            result_candidate = rec.screening_id.screening_result_ids.attachment_ids.mapped("id")
            all_attachment = cv_candidate + result_candidate
            for result in rec.screening_id.screening_result_ids.attachment_ids:
                result.copy()
        for cv in self.screening_id.attachment_ids:
            cv.copy()
        mail_template.attachment_ids = [(6, 0, all_attachment)]
        mail_template.send_mail(self.id)
    
    def create_new_interview_updated_email(self):
        mail_template = self.env.ref('request.mpr_interview_schedule_updated')
        for rec in self:
            list_attachment = rec.screening_id.attachment_ids
            cv_candidate = list_attachment.mapped("id")
            result_candidate = rec.screening_id.screening_result_ids.attachment_ids.mapped("id")
            all_attachment = cv_candidate + result_candidate
            for result in rec.screening_id.screening_result_ids.attachment_ids:
                result.copy()
        for cv in self.screening_id.attachment_ids:
            cv.copy()
        mail_template.attachment_ids = [(6, 0, all_attachment)]
        mail_template.send_mail(self.id)
    
    def create_confirmed_interview_email(self):
        mail_template = self.env.ref('request.mpr_interview_schedule_confirmed')
        mail_template.send_mail(self.id)
    
    def create_reminder_interview_email(self):
        mail_template = self.env.ref('request.mpr_reminder_interview_schedule')
        mail_template.send_mail(self.id)
     
    def create_reminder_interview_result_email(self):
        mail_template = self.env.ref('request.mpr_reminder_interview_result')
        mail_template.send_mail(self.id)
        
    def create_candidate_confirmation_email(self):
        mail_template = self.env.ref('request.mpr_interview_candidate_confirmation')
        mail_template.send_mail(self.id)
    
    def open_interview_confirmation(self):
        return {
            'name': 'Interview Confirmation',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.interview_confirmation_form').id,
            'res_model': 'interview.confirmation',
            'context': {'default_candidate_interview_id': self.id},
            'target': 'new',
        }        
    
    def write(self, vals):
        res = super(CandidateInterview, self).write(vals)
        if 'interviewers' in vals:
            self._add_interviewers_eligible()
            
    def _list_email_pending_confirm(self):
        self.ensure_one()
        return "; ".join([e for e in self.pending_confirm.mapped("work_email") if e])
        
    def _list_email_interviewer(self):
        self.ensure_one()
        return "; ".join([e for e in self.interviewers.mapped("work_email") if e])
        
    def _list_interview_result_reminder(self):
        need_review = self.sudo().env['screening.result'].search([('screening_id', '=', self.screening_id.id), ('note', '=', False), ('status_id', '=', self.candidate_status_id.id)])
        self.ensure_one()
        return "; ".join([e for e in need_review.mapped("name.email") if e])
            
    def _compute_mpr_team(self):
        if self.env.user.has_group('request.group_mpr_admin') or self.env.user.has_group('request.group_mpr_team'):
            self.is_mpr_team = True
        
        else:
            self.is_mpr_team = False
    
    def _compute_current_employee(self):
        uid = self.env.uid
        current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)]).id
        self.current_employee = current_employee
    
    @api.depends('stop', 'start')
    def _compute_duration(self):
        for event in self:
            event.duration = self._get_duration(event.start, event.stop)
            
    @api.depends('start', 'duration')
    def _compute_stop(self):
        duration_field = self._fields['duration']
        self.env.remove_to_compute(duration_field, self)
        for event in self:
            event.stop = event.start and event.start + timedelta(minutes=round((event.duration or 1.0) * 60))
    
    def _get_duration(self, start, stop):
        if not start or not stop:
            return 0
        duration = (stop - start).total_seconds() / 3600
        return round(duration, 2)
        
    @api.model
    def _read_group_status_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        status_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(status_id)
        
    def _auto_pending_confirm(self):
        for rec in self:
            uid = self.env.uid
            current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)]).id
            rec.pending_confirm = [(5, 0, 0)]
            rec.pending_confirm = rec.interviewers
            rec.pending_confirm = [(3, current_employee)]
            if rec.status_id.id not in [1]:
                rec.status_id = 1
            if not rec.pending_confirm and rec.status_id.id == 1:
                rec.status_id = 2
                rec.create_candidate_confirmation_email()
                
    def _auto_new_pending_confirm(self):
        for rec in self:
            list_confirmation1 = rec.sudo().env['interview.confirmation'].search([('candidate_interview_id', '=', rec.id), ('action', '=', 'Cancel For Me'), ('valid', '=', 1)])
            list_confirmation1.sudo().write({'valid': 2})
            conf = rec.sudo().env['interview.confirmation'].search([('candidate_interview_id', '=', rec.id), ('valid', '=', 1), ('action', '!=', 'Cancel For Me')])
            user = conf.mapped("name.employee_id.id")
            rec.pending_confirm = rec.interviewers
            rec.pending_confirm = [(3, user) for user in user]
            if rec.status_id.id not in [1]:
                rec.status_id = 1
            if not rec.pending_confirm and rec.status_id.id == 1:
                rec.status_id = 2                
        
    def _add_interviewers_eligible(self):
        for rec in self:
            if rec.interviewers:
                search_interviewers = rec.sudo().env['candidate.interview'].search([('screening_id', '=', rec.screening_id.id)])
                interviewers = search_interviewers.mapped("interviewers.user_id.id")
                search_all_interviewers = rec.sudo().env['candidate.interview'].search([('manpower_request_id', '=', rec.manpower_request_id.id)])
                all_interviewers = search_all_interviewers.mapped("interviewers.user_id.id")
                rec.sudo().manpower_request_id.interviewers = [(6, 0, all_interviewers)]
                rec.sudo().screening_id.interviewers = [(6, 0, interviewers)]
                
    def _cron_interview_result_reminder(self):
        list_no_result = self.sudo().env['screening.result'].search([('note', '=', False)])
        interview_id = list_no_result.mapped("candidate_interview_id.id")
        list_reminder = self.sudo().env['candidate.interview'].search([("stop", "<", fields.Datetime.to_string(fields.Datetime.today())), ('id', 'in', interview_id)])
        for list in list_reminder:
            list.create_reminder_interview_result_email()            
                
class InterviewConfirmation(models.Model):
    _name = 'interview.confirmation'
    _description = 'Interview Confirmation'
    _order = 'id desc'

    candidate_interview_id = fields.Many2one('candidate.interview', string='Interview', ondelete='cascade')    
    name = fields.Many2one('res.users', default=lambda self: self.env.uid)
    action = fields.Selection([('Confirm', 'Confirm'), ('Delegate / Reschedule', 'Delegate / Reschedule'), ('Cancel For Me', 'Cancel For Me')], default='Confirm')
    action_date = fields.Datetime(string='Date', default=lambda self: fields.datetime.now())
    note = fields.Char(string='Note')
    interview_mode = fields.Selection([('Offline', 'Offline'), ('Online', 'Online')])
    start = fields.Datetime('Start Time')
    stop = fields.Datetime('Stop Time')
    
    # Reschedule
    interviewers = fields.Many2many('hr.employee', string='Interviewers')
    
    # Parameter
    valid = fields.Integer(string='Rejected', default=1)
    
    def action_submit(self):
        for rec in self:
            if rec.action == 'Confirm':
                rec.candidate_interview_id._confirm()
                
            elif rec.action == 'Delegate / Reschedule':
                if rec.candidate_interview_id.interview_mode == rec.interview_mode and rec.candidate_interview_id.start == rec.start and rec.candidate_interview_id.stop == rec.stop and rec.candidate_interview_id.interviewers == rec.interviewers:
                    raise ValidationError(
                    _("Anda tidak merubah apapun. Jika tidak ada reschdule jadwal maupun delegasi, silahkan pilih action: 'Confirm' !"))
                
                elif rec.candidate_interview_id.interview_mode != rec.interview_mode or rec.candidate_interview_id.start != rec.start or rec.candidate_interview_id.stop != rec.stop:
                    rec.candidate_interview_id.interviewers = rec.interviewers
                    rec.candidate_interview_id.interview_mode = rec.interview_mode
                    rec.candidate_interview_id.start = rec.start
                    rec.candidate_interview_id.stop = rec.stop
                    rec.candidate_interview_id._auto_pending_confirm()
                    rec.candidate_interview_id.create_new_interview_updated_email()
                    list_confirmation = rec.sudo().env['interview.confirmation'].search([('candidate_interview_id', '=', rec.candidate_interview_id.id), ('id', '!=', rec.id)])
                    list_confirmation.sudo().write({'valid': 2})
                    
                elif rec.candidate_interview_id.interviewers != rec.interviewers:
                    rec.candidate_interview_id.interviewers = rec.interviewers
                    rec.candidate_interview_id.interview_mode = rec.interview_mode
                    rec.candidate_interview_id.start = rec.start
                    rec.candidate_interview_id.stop = rec.stop
                    rec.candidate_interview_id._auto_new_pending_confirm()
                    rec.candidate_interview_id.create_new_interview_email()
                    interviewers = rec.interviewers.mapped("user_id.id")
                    list_confirmation = rec.sudo().env['interview.confirmation'].search([('candidate_interview_id', '=', rec.candidate_interview_id.id), ('name', 'not in', interviewers), ('id', '!=', rec.id)])
                    list_confirmation.sudo().write({'valid': 2})
                    old_review = rec.sudo().env['screening.result'].search([('candidate_interview_id', '=', rec.candidate_interview_id.id)])
                    old = old_review.mapped("id")
                    rec.candidate_interview_id.screening_result_ids = [(2, old) for old in old]
                    for interviewers in rec.interviewers.user_id:
                        rec.candidate_interview_id.screening_result_ids = [(0, 0, {'candidate_interview_id': rec.candidate_interview_id.id, 'name': interviewers.id, 'status_id': self.candidate_interview_id.screening_id.status_id.id, 'screening_id': self.candidate_interview_id.screening_id.id})]
                    
                    
            elif rec.action == 'Cancel For Me':
                if len(rec.interviewers) == 1:
                    raise ValidationError(
                    _("Delegasikan sesi interview ini dengan pilih Action: 'Delegate / Reschedule' !"))
                
                else:
                    list_confirmation = rec.sudo().env['interview.confirmation'].search([('candidate_interview_id', '=', rec.candidate_interview_id.id), ('name', '=', self.env.uid), ('id', '!=', rec.id)])
                    list_confirmation.sudo().write({'valid': 2})
                    current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', self.env.uid)]).id
                    rec.candidate_interview_id.interviewers = [(3, current_employee)]  
                    rec.candidate_interview_id.pending_confirm = [(3, current_employee)]  
                    if not rec.candidate_interview_id.pending_confirm and rec.candidate_interview_id.status_id.id == 1:
                        rec.candidate_interview_id.status_id = 2
                    old_review = rec.sudo().env['screening.result'].search([('candidate_interview_id', '=', rec.candidate_interview_id.id), ('name', '=', self.env.uid)])
                    old = old_review.mapped("id")
                    rec.candidate_interview_id.screening_result_ids = [(2, old) for old in old]
                
            return {'type': 'ir.actions.act_window_close'}
        
    @api.onchange('candidate_interview_id','action')
    def _auto_action(self):
        for rec in self:
            if rec.candidate_interview_id and rec.action:
                rec.start = rec.candidate_interview_id.start
                rec.stop = rec.candidate_interview_id.stop
                rec.interviewers = rec.candidate_interview_id.interviewers
                rec.interview_mode = rec.candidate_interview_id.interview_mode
        
class CandidateAction(models.Model):
    _name = 'candidate.action'
    _description = 'Candidate Action'
    _order = 'id desc'
    _rec_name = 'screening_id'
    
    @api.model
    def _default_start(self):
        now = fields.Datetime.now()
        return now + (datetime.min - now) % timedelta(minutes=30) + timedelta(hours=24)

    @api.model
    def _default_stop(self):
        now = fields.Datetime.now()
        start = now + (datetime.min - now) % timedelta(minutes=30)
        return start + timedelta(hours=25)
        
    @api.model
    def _default_interviewer(self):
        uid = self.env.uid
        return self.sudo().env['hr.employee'].search([('user_id', '=', uid)], limit=1)
    
    @api.model
    def _default_status(self):
        return self.screening_id.status_id.id
        
    @api.model
    def _default_interview_status(self):
        return int(self.screening_id.status_id.id) + 1
    
    updater = fields.Many2one('hr.employee', string='Interviewers', default=_default_interviewer, tracking=True)
    screening_id = fields.Many2one('candidate.screening', string="Candidate", ondelete='cascade')
    manpower_request_id = fields.Many2one('req.manpower', string='Req ID', ondelete='cascade', store=True, related='screening_id.manpower_request_id', readonly=False)
    
    ## For Screening Result
    screening_status_id = fields.Many2one('candidate.status', string='Current Status', default=_default_status, tracking=True)
    interview_done = fields.Selection([('Yes', 'Yes'), ('No', 'No')], default='Yes')
    reschedule_reason = fields.Selection([('Kandidat Berhalangan', 'Kandidat Berhalangan'), ('Interviewer Berhalangan', 'Interviewer Berhalangan')])
    memo = fields.Text('Memo', help="Optional")
    result = fields.Text('Result')
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Optional")
    
    ## For Scheduling Interview
    interview_status_id = fields.Many2one('candidate.status', string='Proceed To', default=_default_interview_status, tracking=True)
    dynamic_domain = fields.Binary(compute='_domain_interview_status')
    interview_mode = fields.Selection([('Offline', 'Offline'), ('Online', 'Online')])
    interview_url = fields.Char('Interview URL')
    interviewers = fields.Many2many('hr.employee', string='Interviewers', default=_default_interviewer, tracking=True)
    
    ##Schedule
    start = fields.Datetime(
        'Start Time', required=True, tracking=True, default=_default_start)
    stop = fields.Datetime(
        'Stop Time', required=True, tracking=True, default=_default_stop,
        compute='_compute_stop', readonly=False, store=True)
    duration = fields.Float('Duration', compute='_compute_duration', store=True, readonly=False)
    
    @api.depends('stop', 'start')
    def _compute_duration(self):
        for event in self:
            event.duration = self._get_duration(event.start, event.stop)
            
    @api.depends('start', 'duration')
    def _compute_stop(self):
        duration_field = self._fields['duration']
        self.env.remove_to_compute(duration_field, self)
        for event in self:
            event.stop = event.start and event.start + timedelta(minutes=round((event.duration or 1.0) * 60))
    
    def _get_duration(self, start, stop):
        if not start or not stop:
            return 0
        duration = (stop - start).total_seconds() / 3600
        return round(duration, 2)
    
    @api.depends('screening_id', 'interview_done')
    def _domain_interview_status(self):
        if self.screening_status_id.id == 6 and self.interview_done == 'Yes':
            self.dynamic_domain = [('id', 'in', [7,9])]
        
        elif self.screening_status_id.id == 7 and self.interview_done == 'Yes':
            self.dynamic_domain = [('id', '=', [8,9])]
            
        elif self.interview_done == 'No':
            current_status = self.screening_status_id.id
            self.dynamic_domain = [('id', '=', [current_status])]
            
        else:
            next_status = int(self.screening_status_id.id) + 1
            self.dynamic_domain = [('id', '=', [next_status])]
            
    @api.onchange('interview_done')
    def _auto_status(self):
        if self.screening_id and self.interview_done == 'Yes':
            self.screening_status_id = self.screening_id.status_id.id
            self.interview_status_id = int(self.screening_status_id.id) + 1
        elif self.screening_id and self.interview_done == 'No':
            self.interview_status_id = self.screening_id.status_id.id
            
    @api.onchange('reschedule_reason', 'memo')
    def _auto_result(self):
        if self.interview_done == 'No':
            self.write({'result': f"{self.reschedule_reason}{' ('}{self.memo}{')'}"})
            
    def _add_interviewers_eligible(self):
        for rec in self:
            if rec.interviewers:
                interviewers = rec.interviewers.mapped("user_id.id")
                rec.sudo().manpower_request_id.interviewers = [(4, interviewers) for interviewers in interviewers]
                rec.sudo().screening_id.interviewers = [(4, interviewers) for interviewers in interviewers]
    
    def submit_action(self):
        for rec in self:
            rec._add_interviewers_eligible()
            if rec.interview_status_id.id in [10]:
                return {
                'name': 'Offering Letter Information',
                'type': 'ir.actions.act_window',
                'view_type': 'form',
                'view_mode': 'form',
                'view_id': self.env.ref('request.candidate_screening_ol_form').id,
                'res_model': 'candidate.screening',
                'res_id': self.screening_id.id,
                'target': 'new',
            }
            
            else:
                previous_result = rec.sudo().env['screening.result'].search([('screening_id', '=', rec.screening_id.id), ('name', '=', self.env.uid), ('status_id', '=', rec.screening_status_id.id), ('note', '=', False)])
                if previous_result:
                    previous_result.sudo().write({'candidate_action_id': rec.id, 'note': rec.result})
                    previous_result._compute_status()
                elif not previous_result:
                    new_result = self.env['screening.result'].sudo().create({                
                    'name': self.env.uid,
                    'screening_id': self.screening_id.id,
                    'manpower_request_id': self.manpower_request_id.id,
                    'status_id': self.screening_status_id.id,
                    'candidate_action_id': self.id,
                    'note': self.result
                    })
                    new_result._compute_status()
                    if rec.interview_status_id.id == 5:
                        mail_template = self.env.ref('request.user_review_mpr')
                        for scr in rec.screening_id:
                            list_attachment = scr.attachment_ids
                            cv_candidate = list_attachment.mapped("id")
                            result_candidate = scr.screening_result_ids.attachment_ids.mapped("id")
                            new_attachment = rec.attachment_ids.mapped("id")
                            all_attachment = cv_candidate + result_candidate + new_attachment
                            for result in scr.screening_result_ids.attachment_ids:
                                result.copy()
                            for cv in scr.attachment_ids:
                                cv.copy()
                            for new in rec.attachment_ids:
                                new.copy()
                        mail_template.attachment_ids = [(6, 0, all_attachment)]
                        mail_template.send_mail(scr.id)
                    
            
            if self.interview_mode:
                interviewers_uid = rec.interviewers.mapped("user_id.id")
                interview = self.env['candidate.interview'].sudo().create({                
                    'interviewers': self.interviewers,
                    'candidate_status_id': self.interview_status_id.id,
                    'interview_mode': self.interview_mode,
                    'start': self.start,
                    'stop': self.stop,
                    'duration': self.duration,
                    'screening_id': self.screening_id.id,
                    'manpower_request_id': self.manpower_request_id.id
                    })
                interview.sudo().write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{interview.id}{'&cids=1&menu_id=706&action=1125&model=candidate.interview&view_type=form'}"})
                interview._auto_pending_confirm()
                if interview.pending_confirm:
                    if rec.interview_status_id.id in [3,6,7,8,9]:
                        if previous_result:
                            mail_template = self.env.ref('request.mpr_interview_schedule')
                            for scr in rec.screening_id:
                                list_attachment = scr.attachment_ids
                                cv_candidate = list_attachment.mapped("id")
                                result_candidate = scr.screening_result_ids.attachment_ids.mapped("id")
                                all_attachment = cv_candidate + result_candidate
                                for result in scr.screening_result_ids.attachment_ids:
                                    result.copy()
                                for cv in scr.attachment_ids:
                                    cv.copy()
                            mail_template.attachment_ids = [(6, 0, all_attachment)]
                            mail_template.send_mail(interview.id)
                        elif not previous_result:
                            mail_template = self.env.ref('request.mpr_interview_schedule')
                            for scr in rec.screening_id:
                                list_attachment = scr.attachment_ids
                                cv_candidate = list_attachment.mapped("id")
                                result_candidate = scr.screening_result_ids.attachment_ids.mapped("id")
                                new_attachment = rec.attachment_ids.mapped("id")
                                all_attachment = cv_candidate + result_candidate + new_attachment
                                for result in scr.screening_result_ids.attachment_ids:
                                    result.copy()
                                for cv in scr.attachment_ids:
                                    cv.copy()
                                for new in rec.attachment_ids:
                                    new.copy()
                            mail_template.attachment_ids = [(6, 0, all_attachment)]
                            mail_template.send_mail(interview.id)
                uid = self.env.uid
                current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)])
                if current_employee in rec.interviewers:
                    interview.interview_confirmation_ids = [(0, 0, {'candidate_interview_id': interview.id, 'start': self.start, 'stop': self.stop, 'note': 'Confirm', 'interview_mode': self.interview_mode})]
                for interviewers in rec.interviewers.user_id:
                    interview.screening_result_ids = [(0, 0, {'candidate_interview_id': interview.id, 'name': interviewers.id, 'status_id': self.interview_status_id.id, 'screening_id': self.screening_id.id})]
            self.screening_id._update_next_step(self.interview_status_id)
            if self.interview_done == 'Yes':
                interview = self.sudo().env['candidate.interview'].search([('candidate_status_id', '=', self.screening_status_id.id), ('screening_id', '=', self.screening_id.id), ('status_id', '!=', [3,4])])
                interview.sudo().write({'status_id': 4})
                

        
        
