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
     


class ReqTaskStatus(models.Model):
    _name = 'req.task.status'
    _description = 'Task Request Status'
    _order = 'sequence'
    
    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    done = fields.Boolean('Done')
    todo = fields.Boolean('Todo Status')
    ga = fields.Boolean('General Affair')
    internal_it = fields.Boolean('Internal IT')
    fleet = fields.Boolean('Peminjaman Kendaraan')
    
class TaskType(models.Model):
    _name = 'task.type'
    _description = 'Task Type'
    _order = 'sequence'
    
    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)

class MaintenanceTeam(models.Model):
    _inherit = 'maintenance.team'

    ongoing_task = fields.Integer(string="Number of Ongoing Task", compute='_compute_task_count')
    my_request = fields.Integer(string="Number of My Request")

   
    @api.depends('request_ids.dummy_save')
    def _compute_task_count(self):
        for team in self:            
            data1 = self.env['req.task']._read_group(
                [('maintenance_team_id', '=', team.id), ('status_id.done', '=', False), ('status_id', '!=', [1])],
                ['create_date:year', 'priority', 'urgent_note', ],
                ['__count']
            )    

            team.ongoing_task = sum(count for (_, _, _, count) in data1)
    
    def create_todo(self):        
        return {
            'name': 'Create Todo', # Label
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.todo_form').id,
            'res_model': 'req.task',
        }
   
class ReqTask(models.Model):
    _name = 'req.task'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Task Request'
    _order = 'id desc'
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['req.task.status'].search([], limit=1)
    
    name = fields.Char('Request ID', default=lambda self: _('New Request'))
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', default=8)
    task_type = fields.Many2one('task.type', string="Task Type", default=1)
    status_id = fields.Many2one('req.task.status', string="Status", tracking=True, default=_default_stage, group_expand='_read_group_status_ids')
    type = fields.Selection([('personal', 'Personal Todo'), ('task', 'Task')], default='task')
    submit_date = fields.Datetime(string='Submit Date')
    approved_date = fields.Datetime(string='Approved Date')
    receive_date = fields.Datetime(string='Receive Date')
    finish_date = fields.Datetime(string='Finish Date')
    closed_date = fields.Datetime(string='Closed Date')
    requester = fields.Many2one('res.users', string='Requester', default=lambda self: self.env.uid, tracking=True)
    request_department = fields.Many2one('hr.department', string='Requester Department')
    requester_upline = fields.Many2many('hr.employee', 'requester_upline_rel', 'task_id', 'req_upline_id', string='Requester Upline')
    pic = fields.Many2one('res.users', string='Assignee', tracking=True, domain="[('employee_ids', '!=', False)]")
    team = fields.Many2many('res.users', string='Team', tracking=True, domain="[('employee_ids', '!=', False)]")
    pic_department = fields.Many2one('hr.department', string='Assignee Department')
    pic_upline = fields.Many2many('hr.employee', 'pic_upline_rel', 'task_id', 'pic_upline_id', string='Assignee Upline')
    pending_approver = fields.Many2many('hr.employee', string='Pending Approver')
    pending_approver1 = fields.Char(string='Pending Approver 1')
    pending_approver2 = fields.Char(string='Pending Approver 2')
    req_detail = fields.Text(string='Request Detail')
    notes = fields.Text(string='Progress Notes', tracking=True)
    priority = fields.Selection([('0', 'Low'), ('1', 'Normal'), ('2', 'Medium'), ('3', 'High')], default='1', tracking=True)
    urgent_note = fields.Char(tracking=True)
    deadline = fields.Datetime(string='Deadline', tracking=True)
    reopen_reason = fields.Char(string='Reopen Reason', tracking=True)
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True)
    
    ## Record Data
    approval_list_ids = fields.One2many('task.approval.list', 'task_request_id', string="Approval List")
    task_delegate_list_ids = fields.One2many('task.delegate.list', 'task_request_id', string="Delegation List")
    ## Parameter Fields
    dummy_save = fields.Boolean('Save')
    readonly = fields.Boolean('Readonly')
    latest_delegate_id = fields.Many2one('task.delegate.list', store=True, compute='_compute_latest_delegate')
    latest_approval_id = fields.Many2one('task.approval.list', store=True, compute='_compute_latest_approval')
    current_user = fields.Many2one('res.users', compute='_compute_current_user')
    is_cancelled = fields.Boolean('Cancelled')
    is_approver = fields.Boolean('Approver', compute='_compute_approver')
    is_requester_upline = fields.Boolean('Requester Upline', compute='_compute_requester_upline')
    is_pic_upline = fields.Boolean('PIC Upline', compute='_compute_pic_upline')
    is_pic_upline_delegation = fields.Boolean('PIC Upline Delegation', compute='_compute_pic_upline_delegation')
    is_rejected = fields.Boolean(string='Rejected')
    is_urgent = fields.Boolean(string='Urgent', store=True, compute='_compute_urgent')
    is_overdue = fields.Selection([('1', 'No'), ('2', 'Yes')], string="Is Overdue", default='1')
    overdue_value = fields.Integer(string='Overdue Value', default='1')
    overdue_info = fields.Char(string='Overdue Info')
    is_delegate_submit = fields.Boolean(string='Delegate Submit', compute='_compute_delegate_submit')
    add_team = fields.Boolean(string='Add Team')
    color = fields.Integer('Color Index')
    url_local = fields.Char(string='URL Local')
    url_public = fields.Char(string='URL Public')
    pending_approver_mail = fields.Char(string='Pending Approver Mail', store=True, compute='_compute_approver_mail')
    requester_upline_mail = fields.Char(string='Requester Upline Mail', store=True, compute='_compute_requester_upline_mail')
    pic_upline_mail = fields.Char(string='PIC Upline Mail', store=True, compute='_compute_pic_upline_mail')
    is_new_mail = fields.Boolean(string='New Mail', default=False)
    
    @api.constrains('deadline')
    def check_deadline(self):
        now = fields.Datetime.now()
        if self.deadline:
            if self.deadline < now:
                raise ValidationError(
                    _("Deadline tidak bisa diisi dengan waktu lampau!"))
    
    def save(self):
        self.write({'dummy_save': True})        
        
    def cancel(self):
        self.write({'is_cancelled': True, 'status_id': 1, 'is_new_mail': 0})
        self.pending_approver = [(5, 0, 0)]
    
    def submit(self):
        self.write({'url_local': f"{'http://192.168.10.13:8069/web#id='}{self.id}{'&menu_id=487&cids=1&action=1073&active_id=8&model=req.task&view_type=form'}"})
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&menu_id=487&cids=1&action=1073&active_id=8&model=req.task&view_type=form'}"})
        self._check_department()
        self.write({'is_rejected': 0,'is_cancelled': 0, 'submit_date': datetime.now()})
        
    def accept(self):
        self.write({'status_id': 4, 'receive_date': datetime.now()})
        
    def _reject(self):
        self.write({'status_id': 1, 'is_rejected': 1, 'is_new_mail': 0})
        self.pending_approver = [(5, 0, 0)]
        self.create_rejected_task_mail()
        
    def _approve(self):
        self.write({'status_id': 3, 'approved_date': datetime.now()})
        self.pending_approver = [(5, 0, 0)]
        self.create_new_task_mail()
        
    def finish(self):
        self.write({'status_id': 6, 'finish_date': datetime.now(), 'reopen_reason': False})
        if self.type == 'task':
            self.create_finished_task_mail()

    def reopen(self):
        self.write({'status_id': 4, 'finish_date': False})
        self.create_reopen_task_mail()

    def closed(self):
        self.write({'status_id': 7, 'closed_date': datetime.now()})
        if self.type == 'task':
            self.create_closed_task_mail()
    
    def _submit_delegate(self):
        self.write({'status_id': 5, 'is_new_mail': 0})
        self.create_req_delegate_task_mail()
        
    def _delegation_approved(self, new_pic, new_pic_department, new_pic_upline):
        self.write({'status_id': 3, 'pic': new_pic, 'pic_department': new_pic_department, 'approved_date': datetime.now(), 'is_new_mail': 1})
        self.pending_approver = [(5, 0, 0)]
        self.sudo().pic_upline = new_pic_upline
        self.create_new_task_mail()
        
    def _list_email_pending_approver(self):
        self.ensure_one()
        return "; ".join([e for e in self.pending_approver.mapped("work_email") if e])
    
    def _list_email_assignee_upline(self):
        self.ensure_one()
        return "; ".join([e for e in self.pic_upline.mapped("work_email") if e])
    
    def _list_email_requester_upline(self):
        self.ensure_one()
        return "; ".join([e for e in self.requester_upline.mapped("work_email") if e])
        
    def _compute_current_user(self):
        self.current_user = self.env.uid
    
    def _compute_approver(self):
        if self.current_user.employee_id in self.pending_approver:
            self.is_approver = True
        
        else:
            self.is_approver = False   
            
    def _compute_pic_upline(self):
        if self.current_user.employee_id in self.pic_upline:
            self.is_pic_upline = True
        
        else:
            self.is_pic_upline = False    
            
    def _compute_pic_upline_delegation(self):
        if self.current_user.employee_id in self.pic_upline and self.latest_delegate_id.status_id.id == 1:
            self.is_pic_upline_delegation = True
        
        else:
            self.is_pic_upline_delegation = False    
            
    def _compute_requester_upline(self):
        if self.current_user.employee_id in self.requester_upline:
            self.is_requester_upline = True
        
        else:
            self.is_requester_upline = False  
            
    def _compute_delegate_submit(self):
        if self.latest_delegate_id.status_id.id == 2:
            self.is_delegate_submit = True
        
        else:
            self.is_delegate_submit = False  
        
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('type', _("task")) == _("task"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('req.task.seq')
            elif vals.get('type', _("personal")) == _("personal"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('todo.seq')

        return super().create(vals_list)
    
    @api.model
    def _read_group_status_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        status_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(status_id)
        
    def write(self, vals):
        res = super(ReqTask, self).write(vals)
        if vals.get('deadline'):
            self._cron_overdue()

    
    @api.onchange('pic')
    def _auto_pic_department(self):
        manager1 = self.pic.employee_id.manager_id.id
        manager1_name = self.pic.employee_id.manager_id.display_name
        manager2 = self.pic.employee_id.manager_id.parent_id.id
        manager2_name = self.pic.employee_id.manager_id.parent_id.display_name
        manager3 = self.pic.employee_id.manager_id.parent_id.parent_id.id
        manager3_name = self.pic.employee_id.manager_id.parent_id.parent_id.display_name
        manager4 = self.pic.employee_id.parent_id.id
        manager4_name = self.pic.employee_id.parent_id.display_name
        manager5 = self.pic.employee_id.parent_id.parent_id.id
        manager5_name = self.pic.employee_id.parent_id.parent_id.display_name
        manager6 = self.pic.employee_id.parent_id.parent_id.parent_id.id
        manager6_name = self.pic.employee_id.parent_id.parent_id.parent_id.display_name
        self.sudo().write({'pic_department': self.pic.department_id.id})
        if not self.pic.employee_id.gm_id and not self.pic.employee_id.manager_id and self.pic.employee_id.id == self.pic.employee_id.department_id.manager_id.id:
            self.sudo().pic_upline = [(6, 0, [manager4,manager5,manager6])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif self.pic.employee_id.manager_id.id != self.pic.employee_id.parent_id.id:
            self.sudo().pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif not self.pic.employee_id.gm_id and not self.pic.employee_id.manager_id and self.pic.employee_id.id != self.pic.employee_id.department_id.manager_id.id:
            self.sudo().pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif self.pic.employee_id.gm_id and not self.pic.employee_id.manager_id and self.pic.employee_id.id != self.pic.employee_id.department_id.manager_id.id:
            self.sudo().pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif not self.pic.employee_id.gm_id and self.pic.employee_id.manager_id and self.pic.employee_id.id != self.pic.employee_id.department_id.manager_id.id:
            self.sudo().pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif self.pic.employee_id.id != self.pic.employee_id.department_id.gm_id.id:
            self.sudo().pic_upline = [(6, 0, [manager1,manager2])]
            self.sudo().write({'pending_approver1': manager1_name,'pending_approver2': manager2_name})
                    
        elif self.pic.employee_id.id == self.pic.employee_id.department_id.gm_id.id:
            self.sudo().pic_upline = [(6, 0, [manager1,manager2,manager3])]
            self.sudo().write({'pending_approver1': manager1_name,'pending_approver2': manager2_name})
        
        
    @api.onchange('requester')
    def _auto_requester_department(self):
        manager1 = self.requester.employee_id.manager_id.id
        manager2 = self.requester.employee_id.manager_id.parent_id.id
        manager3 = self.requester.employee_id.manager_id.parent_id.parent_id.id
        manager4 = self.requester.employee_id.parent_id.id
        manager5 = self.requester.employee_id.parent_id.parent_id.id
        manager6 = self.requester.employee_id.parent_id.parent_id.parent_id.id
        manager6_name = self.requester.employee_id.parent_id.parent_id.parent_id.display_name
        self.sudo().write({'request_department': self.requester.department_id.id})
        if not self.requester.employee_id.gm_id and not self.requester.employee_id.manager_id and self.requester.employee_id.id == self.requester.employee_id.department_id.manager_id.id:
            self.sudo().requester_upline = [(6, 0, [manager4,manager5,manager6])]
            
        elif self.requester.employee_id.manager_id.id != self.requester.employee_id.parent_id.id:
            self.sudo().requester_upline = [(6, 0, [manager4,manager5])]
            
        elif not self.requester.employee_id.gm_id and not self.requester.employee_id.manager_id and self.requester.employee_id.id != self.requester.employee_id.department_id.manager_id.id:
            self.sudo().requester_upline = [(6, 0, [manager4,manager5])]
            
        elif self.requester.employee_id.gm_id and not self.requester.employee_id.manager_id and self.requester.employee_id.id != self.requester.employee_id.department_id.manager_id.id:
            self.sudo().requester_upline = [(6, 0, [manager4,manager5])]
                        
        elif not self.requester.employee_id.gm_id and self.requester.employee_id.manager_id and self.requester.employee_id.id != self.requester.employee_id.department_id.manager_id.id:
            self.sudo().requester_upline = [(6, 0, [manager4,manager5])]
                                
        elif self.requester.employee_id.id != self.requester.employee_id.department_id.gm_id.id:
            self.sudo().requester_upline = [(6, 0, [manager1,manager2])]
                                
        elif self.requester.employee_id.id == self.requester.employee_id.department_id.gm_id.id:
            self.sudo().requester_upline = [(6, 0, [manager1,manager2,manager3])]
                    
    def _check_department(self):
        for rec in self:
            list_manager = rec.sudo().env['hr.employee'].search([('job_id.name', 'ilike', 'Manager')])
            manager = list_manager.mapped("id")
            if self.type == 'personal':
                self.write({'status_id': 4})
                
            elif self.task_type.name == 'Reminder':
                self.write({'status_id': 4})
                self.create_new_task_mail()
            
            elif self.pic.employee_id.id in manager:
                self.write({'status_id': 3})
                self.create_new_task_mail()
            
            elif self.pic.employee_id.id == self.pic.employee_id.department_id.manager_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                
            elif self.pic.employee_id.id == self.pic.employee_id.department_id.gm_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                                
            elif self.pic.employee_id.gm_id and self.requester.employee_id.id == self.pic.employee_id.gm_id.parent_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                
            elif self.pic.employee_id.gm_id and self.requester.employee_id.id == self.pic.employee_id.gm_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                
            elif self.pic.employee_id.manager_id and self.requester.employee_id.id == self.pic.employee_id.manager_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
            
            elif self.pic.employee_id.manager_id and self.requester.employee_id.id == self.pic.employee_id.manager_id.parent_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                
            elif self.requester.employee_id.id == self.pic.employee_id.parent_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                
            elif self.pic.employee_id.id == self.requester.employee_id.parent_id.id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                
            elif not self.pic.employee_id.gm_id and not self.pic.employee_id.manager_id and not self.pic.employee_id.parent_id:
                self.write({'status_id': 3})
                self.create_new_task_mail()
                
            else:                
                 self.write({'status_id': 2})
                 self.sudo().pending_approver = self.pic_upline
                 self.create_approval_new_task_mail()
            
    @api.depends('task_delegate_list_ids')
    def _compute_latest_delegate(self):
        for rec in self:
            if rec.task_delegate_list_ids:
                latest_delegation = rec.env['task.delegate.list'].search([('task_request_id.id', '=', rec.id)], limit=1, order="id desc").id
                self.latest_delegate_id = latest_delegation
            else:
                rec.latest_delegate_id = False    
               
    @api.depends('approval_list_ids')
    def _compute_latest_approval(self):
        for rec in self:
            if rec.approval_list_ids:
                latest_approval = rec.env['task.approval.list'].search([('task_request_id.id', '=', rec.id)], limit=1, order="id desc").id
                self.latest_approval_id = latest_approval
            else:
                rec.latest_approval_id = False               
            
    @api.depends('priority')
    def _compute_urgent(self):
        for rec in self:
            if rec.priority == '3':                
                rec.is_urgent = True
                rec.write({'color': 1})
            elif rec.priority == '2':
                rec.is_urgent = False
                rec.write({'color': 3})
            else:
                rec.is_urgent = False               
             
    @api.depends('pending_approver')
    def _compute_approver_mail(self):
        for rec in self:
            rec.pending_approver_mail = self.mapped("pending_approver.user_id.email_formatted")
            
    @api.depends('requester_upline')
    def _compute_requester_upline_mail(self):
        for rec in self:
            rec.requester_upline_mail = self.mapped("requester_upline.user_id.email_formatted")
            
    @api.depends('pic_upline')
    def _compute_pic_upline_mail(self):
        for rec in self:
            rec.pic_upline_mail = self.mapped("pic_upline.user_id.email_formatted")               
            
    def open_delegation(self):
        latest_delegation = self.env['task.delegate.list'].search([('task_request_id.id', '=', self.id)], limit=1, order="id desc").id
        return {
            'name': 'Delegation Details', # Label
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'task.delegate.list',
            'res_id': latest_delegation,
            'target': 'new',
        }
        
    def open_reopen_form(self):
        return {
            'name': 'Reopen Reason', # Lable
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.req_task_reopen_view_form').id,
            'res_model': 'req.task',
            'res_id': self.id,
            'target': 'new',
        }
        
    @api.model
    def _cron_overdue(self):
        list_request_deadline = self.search([('deadline', '!=', False), ('status_id.id', '!=', [1,6,7])])
        list_request_nodeadline = self.search([('deadline', '=', False), ('is_overdue', '=', '2')])
        for record in list_request_deadline:
            deadline = record.deadline
            date_now = fields.Datetime.now()
            time_interval = Intervals([(deadline, date_now, record)])
            delta = sum((i[1] - i[0]).total_seconds() for i in time_interval)
            if delta < 1:
                record.overdue_value = 1
                record.write({'is_overdue': '1'})            
                
            elif delta < 86400:
                record.overdue_value = delta
                value = int(record.overdue_value // 3600.0)
                record.write({'overdue_info': f"{value}{' Hours Overdue'}", 'is_overdue': '2'}) 
            
            elif delta > 86400:
                record.overdue_value = delta
                value = int(record.overdue_value // 86400.0)
                record.write({'overdue_info': f"{value}{' Days Overdue'}", 'is_overdue': '2'})
                              
            else:
                return
                
        for record in list_request_nodeadline:
            record.write({'is_overdue': '1', 'overdue_value': 1})
    
    def create_cancel_task_mail(self):
        mail_template = self.env.ref('request.cancel_req_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
    
    def create_new_task_mail(self):
        mail_template = self.env.ref('request.new_req_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_approval_new_task_mail(self):
        mail_template = self.env.ref('request.new_req_approval_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_rejected_task_mail(self):
        mail_template = self.env.ref('request.rejected_req_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_req_delegate_task_mail(self):
        mail_template = self.env.ref('request.req_task_delegation')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_rejected_delegate_task_mail(self):
        mail_template = self.env.ref('request.rejected_req_task_delegation')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_finished_task_mail(self):
        mail_template = self.env.ref('request.finished_req_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_reopen_task_mail(self):
        mail_template = self.env.ref('request.reopened_req_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_closed_task_mail(self):
        mail_template = self.env.ref('request.closed_req_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
        
    def create_notification_task_mail(self):
        mail_template = self.env.ref('request.notification_req_task')
        for rec in self:
            list_attachment = rec.attachment_ids
            attachment = list_attachment.mapped("id")
        for initial in self.attachment_ids:
            initial.copy()
        mail_template.attachment_ids = [(6, 0, attachment)]
        mail_template.send_mail(self.id)
 
class TaskApprovalList(models.Model):
    _name = 'task.approval.list'
    _description = 'Task Approval List'
    _order = 'id desc'

    task_request_id = fields.Many2one('req.task', string='Task ID', ondelete='cascade')    
    name = fields.Many2one('res.users', default=lambda self: self.env.uid)
    action = fields.Selection([('approve', 'Approve'), ('reject', 'Reject')], default='approve')
    approval_date = fields.Datetime(string='Date', default=lambda self: fields.datetime.now())
    note = fields.Char(string='Note')
    
    def action_submit(self):
        if self.action == 'approve':
            self.task_request_id._approve()
        elif self.action == 'reject':
            self.task_request_id._reject()
        return {'type': 'ir.actions.act_window_close'}
        
class DelegateList(models.Model):
    _name = 'task.delegate.list'  
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']    
    _description = 'Task Delegate List'
    _order = 'id desc'
    _track_duration_field = 'status_id'

    @api.returns('self')
    def _default_stage(self):
        return self.env['delegate.task.status'].search([], limit=1)
    
    name = fields.Char('Delegation ID', default=lambda self: _('New Request'))
    task_request_id = fields.Many2one('req.task', string='Task ID', ondelete='cascade')
    status_id = fields.Many2one('delegate.task.status', string="Status", default=_default_stage, tracking=True)
    request_date = fields.Datetime(string='Request Date', default=lambda self: fields.datetime.now())
    old_pic = fields.Many2one('res.users', string='Old Assignee')
    old_pic_department = fields.Many2one('hr.department', string='Old Assignee Department')
    new_pic = fields.Many2one('res.users', string='New Assignee', tracking=True, domain="[('employee_ids', '!=', False)]")  
    new_pic_department = fields.Many2one('hr.department', string='New Assignee Department')
    new_pic_upline = fields.Many2many('hr.employee', string='New Assignee Upline')
    pending_approver1 = fields.Char(string='Pending Approver 1')
    pending_approver2 = fields.Char(string='Pending Approver 2')
    delegate_reason = fields.Char(string='Delegate Reason', tracking=True)
    closed_date = fields.Datetime(string='Action Date')
    note = fields.Char(string='Note')
    
    ## Parameter Fields
    dummy_save = fields.Boolean('Save')
    
    def save(self):
        self.write({'dummy_save': True})
    
    def reject(self):
        self.write({'status_id': 4, 'closed_date': datetime.now()})
        self.task_request_id.write({'status_id': 4})
        self.task_request_id.pending_approver = [(5, 0, 0)]
        self.task_request_id.create_rejected_delegate_task_mail()

    def create_delegate(self):
        for rec in self:
            list_manager = rec.sudo().env['hr.employee'].search([('job_id.name', 'ilike', 'Manager')])
            manager = list_manager.mapped("id")
            if self.create_uid.employee_id.id == self.new_pic.employee_id.department_id.manager_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.create_uid.employee_id.id == self.new_pic.employee_id.department_id.gm_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.create_uid.employee_id.id == self.new_pic.employee_id.parent_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.create_uid.employee_id.id == self.new_pic.employee_id.manager_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.new_pic.employee_id.id == self.old_pic.employee_id.department_id.manager_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.new_pic.employee_id.id == self.old_pic.employee_id.department_id.gm_id.parent_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.new_pic.employee_id.id == self.old_pic.employee_id.department_id.gm_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.new_pic.employee_id.id == self.old_pic.employee_id.parent_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            elif self.new_pic.employee_id.id == self.old_pic.employee_id.manager_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.sudo().accept_delegation()
                
            else:
                self.sudo().task_request_id._submit_delegate()
                return {'type': 'ir.actions.act_window_close'}
        
    def approve(self):
        for rec in self:
            list_manager = rec.sudo().env['hr.employee'].search([('job_id.name', 'ilike', 'Manager')])
            manager = list_manager.mapped("id")            
            if self.new_pic.employee_id.id in manager:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
            
            elif self.new_pic.employee_id.id == self.new_pic.employee_id.department_id.manager_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
                
            elif self.new_pic.employee_id.id == self.new_pic.employee_id.department_id.gm_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
                                
            elif self.new_pic.employee_id.gm_id and self.env.uid == self.new_pic.employee_id.gm_id.parent_id.user_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
                
            elif self.new_pic.employee_id.gm_id and self.env.uid == self.new_pic.employee_id.gm_id.user_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
                
            elif self.new_pic.employee_id.manager_id and self.env.uid == self.new_pic.employee_id.manager_id.user_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
            
            elif self.new_pic.employee_id.manager_id and self.env.uid == self.new_pic.employee_id.manager_id.parent_id.user_id.id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
                                                
            elif not self.new_pic.employee_id.gm_id and not self.new_pic.employee_id.manager_id and not self.new_pic.employee_id.parent_id:
                self.write({'status_id': 3, 'closed_date': datetime.now()})
                self.accept_delegation()
                
            else:
                self.write({'status_id': 2})
                self.sudo().task_request_id.pending_approver = self.new_pic_upline
                self.sudo().task_request_id.pending_approver1 = self.pending_approver1                
                self.sudo().task_request_id.pending_approver2 = self.pending_approver2                
                self.task_request_id.create_req_delegate_task_mail()
            
    def accept_delegation(self):        
        self.write({'status_id': 3, 'closed_date': datetime.now()})        
        self.task_request_id._delegation_approved(self.new_pic, self.new_pic_department, self.new_pic_upline)
        self.sudo().task_request_id.pending_approver1 = self.pending_approver1                
        self.sudo().task_request_id.pending_approver2 = self.pending_approver2        
        return {'type': 'ir.actions.act_window_close'}   
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('name', _("New Request")) == _("New Request"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('delegate.task.seq')

        return super().create(vals_list)

    @api.onchange('new_pic')
    def _auto_new_pic_department(self):                        
        manager1 = self.new_pic.employee_id.manager_id.id
        manager1_name = self.new_pic.employee_id.manager_id.display_name
        manager2 = self.new_pic.employee_id.manager_id.parent_id.id
        manager2_name = self.new_pic.employee_id.manager_id.parent_id.display_name
        manager3 = self.new_pic.employee_id.manager_id.parent_id.parent_id.id
        manager3_name = self.new_pic.employee_id.manager_id.parent_id.parent_id.display_name
        manager4 = self.new_pic.employee_id.parent_id.id
        manager4_name = self.new_pic.employee_id.parent_id.display_name
        manager5 = self.new_pic.employee_id.parent_id.parent_id.id
        manager5_name = self.new_pic.employee_id.parent_id.parent_id.display_name
        manager6 = self.new_pic.employee_id.parent_id.parent_id.parent_id.id
        manager6_name = self.new_pic.employee_id.parent_id.parent_id.parent_id.display_name
        self.sudo().write({'new_pic_department': self.new_pic.department_id.id})
        if not self.new_pic.employee_id.gm_id and not self.new_pic.employee_id.manager_id and self.new_pic.employee_id.id == self.new_pic.employee_id.department_id.manager_id.id:
            self.sudo().new_pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif self.new_pic.employee_id.parent_id.id != self.new_pic.employee_id.manager_id.id:
            self.sudo().new_pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
        
        elif not self.new_pic.employee_id.gm_id and not self.new_pic.employee_id.manager_id and self.new_pic.employee_id.id != self.new_pic.employee_id.department_id.manager_id.id:
            self.sudo().new_pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif self.new_pic.employee_id.gm_id and not self.new_pic.employee_id.manager_id and self.new_pic.employee_id.id != self.new_pic.employee_id.department_id.manager_id.id:
            self.sudo().new_pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif not self.new_pic.employee_id.gm_id and self.new_pic.employee_id.manager_id and self.new_pic.employee_id.id != self.new_pic.employee_id.department_id.manager_id.id:
            self.sudo().new_pic_upline = [(6, 0, [manager4,manager5])]
            self.sudo().write({'pending_approver1': manager4_name,'pending_approver2': manager5_name})
            
        elif self.new_pic.employee_id.id != self.new_pic.employee_id.department_id.gm_id.id:
            self.sudo().new_pic_upline = [(6, 0, [manager1,manager2])]
            self.sudo().write({'pending_approver1': manager1_name,'pending_approver2': manager2_name})
                    
        elif self.new_pic.employee_id.id == self.new_pic.employee_id.department_id.gm_id.id:
            self.sudo().new_pic_upline = [(6, 0, [manager1,manager2,manager3])]
            self.sudo().write({'pending_approver1': manager1_name,'pending_approver2': manager2_name})

    def open_reject(self):
        latest_delegation = self.env['task.delegate.list'].search([('id', '=', self.id)], limit=1, order="id desc").id
        return {
            'name': 'Delegation Details', # Lable
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('request.task_delegate_reject_view_form').id,
            'res_model': 'task.delegate.list',
            'res_id': latest_delegation,
            'target': 'new',
        }
    
class DelegateTaskStatus(models.Model):
    _name = 'delegate.task.status'
    _description = 'Task Request Status'
    _order = 'id'
    
    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Kanban')
    done = fields.Boolean('Done')