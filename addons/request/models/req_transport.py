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
     
class TransportStatus(models.Model):

    _name = 'transport.status'
    _description = 'Transport Status'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')

class ReqTransport(models.Model):
    _name = 'req.transport'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Request Transport'
    _order = 'id desc'
    _track_duration_field = 'status_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['transport.status'].search([], limit=1)
        
    @api.returns('self')
    def _default_approval_config(self):
        return self.env['req.approval.config'].search([], limit=1)
        
    @api.model
    def _default_start(self):
        now = fields.Datetime.now()
        return now + (datetime.min - now) % timedelta(minutes=30)

    @api.model
    def _default_stop(self):
        now = fields.Datetime.now()
        start = now + (datetime.min - now) % timedelta(minutes=30)
        return start + timedelta(hours=1)
        
    @api.model
    def _default_requester(self):
        uid = self.env.uid
        return self.sudo().env['hr.employee'].search([('user_id', '=', uid)], limit=1)
    
    name = fields.Char(string='Name', default=lambda self: _('New Request'))
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', default=10)
    config_approval_id = fields.Many2one('req.approval.config', string='Approval Config', default=_default_approval_config)
    status_id = fields.Many2one('transport.status', string='Status', default=_default_stage, tracking=True)
    request_type = fields.Selection([('ops', 'Operational Car'), ('pickup', 'Pickup'), ('delivery', 'Delivery'), ('txn', 'Pickup & Delivery')], 'Type', default='ops', required=True)
    requester = fields.Many2one('hr.employee', string='Requester', default=_default_requester, tracking=True)
    requester_job = fields.Many2one('hr.job', string='Job Position', related='requester.job_id')
    requester_department = fields.Many2one('hr.department', string='Department', related='requester.department_id')    
    destination = fields.Char(tracking=True)
    agenda = fields.Char(tracking=True)
    need_driver = fields.Selection([('no', 'No'), ('yes', 'Yes')], default='no')
    pending_approver = fields.Many2many('hr.employee', string='Pending Approver', related='config_approval_id.approver_ops_car', readonly=False)
    other_passenger = fields.Many2many('hr.employee', 'other_passenger_rel', 'trans_id', 'other_passenger_id', string='Other Passenger')
    attachment_ids = fields.One2many('ir.attachment', 'res_id', string="Attachments", copy=True, help="Optional")
    
    ##TimeTracking
    submit_date = fields.Datetime(string='Submit Date')
    approve_date = fields.Datetime(string='Approve Date')
    confirm_date = fields.Datetime(string='Confirm Date')
    close_date = fields.Datetime(string='Close Date')
    
    ##Car
    driver = fields.Many2one('hr.employee', string='Driver')
    car = fields.Many2one('fleet.vehicle', string='Car', tracking=True)
    license_plate = fields.Char(string='License Plate', related='car.license_plate')
    transmission = fields.Selection([('manual', 'Manual'), ('automatic', 'Automatic')], 'Transmission', related='car.transmission')
    fuel_type = fields.Selection(FUEL_TYPES, 'Fuel Type', related='car.fuel_type')
    seats = fields.Integer('Seats Number', help='Jumlah Kursi Pada Mobil', related='car.seats')
    
    ##Schedule
    start = fields.Datetime(
        'Start', required=True, tracking=True, default=_default_start)
    stop = fields.Datetime(
        'Stop', required=True, tracking=True, default=_default_stop,
        compute='_compute_stop', readonly=False, store=True)
    allday = fields.Boolean('All Day', default=False)
    start_date = fields.Date(
        'Start Date', store=True, tracking=True,
        compute='_compute_dates', inverse='_inverse_dates')
    stop_date = fields.Date(
        'End Date', store=True, tracking=True,
        compute='_compute_dates', inverse='_inverse_dates')
    duration = fields.Float('Duration', compute='_compute_duration', store=True, readonly=False)
    
    ##Parameter
    dummy_save = fields.Boolean('Save')
    is_approver = fields.Boolean('Approver', compute='_compute_is_aprover')
    is_cancelled = fields.Boolean('Cancelled')
    is_rejected = fields.Boolean(string='Rejected')
    is_urgent = fields.Boolean(string='Urgent', store=True)
    url_public = fields.Char(string='URL Public')
    
    ##One2Many
    approval_list_ids = fields.One2many('transport.approval.list', 'transport_request_id', string="Approval List")
    
    def save(self):
        self.write({'dummy_save': True})
            
    def _reject(self):
        self.write({'status_id': 1, 'is_rejected': 1})
        self.pending_approver = [(5, 0, 0)]
        
    def _approve(self):
        self.write({'status_id': 3, 'approve_date': datetime.now()})
        self.pending_approver = [(5, 0, 0)]
        
    def cancel(self):
        self.write({'is_cancelled': True, 'status_id': 1})
        self.pending_approver = [(5, 0, 0)]
        
    def confirm(self):
        self.write({'status_id': 4})
    
    def submit(self):
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.id}{'&menu_id=487&cids=1&action=1073&active_id=8&model=req.task&view_type=form'}"})
        self.write({'is_rejected': 0,'is_cancelled': 0, 'submit_date': datetime.now(), 'status_id': 2})
        
    def _compute_is_aprover(self):
        uid = self.env.uid
        current_employee = self.sudo().env['hr.employee'].search([('user_id', '=', uid)])
        if current_employee in self.pending_approver:
            self.is_approver = True
            
        else:
            self.is_approver = False
    
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('request_type', _("ops")) == _("ops"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('transport.ops.seq')
            elif vals.get('request_type', _("pickup")) == _("pickup"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('transport.ops.pick')
            elif vals.get('request_type', _("delivery")) == _("delivery"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('transport.ops.dlv')
            elif vals.get('request_type', _("txn")) == _("txn"):                
                vals['name'] = self.env['ir.sequence'].next_by_code('transport.ops.txn')

        return super().create(vals_list)
    
    @api.depends('allday', 'start', 'stop')
    def _compute_dates(self):
        for meeting in self:
            if meeting.allday and meeting.start and meeting.stop:
                meeting.start_date = meeting.start.date()
                meeting.stop_date = meeting.stop.date()
            else:
                meeting.start_date = False
                meeting.stop_date = False

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
            if event.allday:
                event.stop -= timedelta(seconds=1)

    @api.onchange('start_date', 'stop_date')
    def _onchange_date(self):
        for event in self:
            if event.stop_date and event.start_date:
                event.with_context(is_calendar_event_new=True).write({
                    'start': fields.Datetime.from_string(event.start_date).replace(hour=1),
                    'stop': fields.Datetime.from_string(event.stop_date).replace(hour=10,minute=30),
                })

    def _inverse_dates(self):
        for meeting in self:
            if meeting.allday:
                enddate = fields.Datetime.from_string(meeting.stop_date)
                enddate = enddate.replace(hour=10,minute=30)

                startdate = fields.Datetime.from_string(meeting.start_date)
                startdate = startdate.replace(hour=1)

                meeting.write({
                    'start': startdate.replace(tzinfo=None),
                    'stop': enddate.replace(tzinfo=None)
                })
                
                
    def _get_duration(self, start, stop):
        if not start or not stop:
            return 0
        duration = (stop - start).total_seconds() / 3600
        return round(duration, 2)
        
class TransportApprovalList(models.Model):
    _name = 'transport.approval.list'
    _description = 'TXN Approval List'
    _order = 'id desc'

    transport_request_id = fields.Many2one('req.transport', string='Req ID', ondelete='cascade')    
    name = fields.Many2one('res.users', default=lambda self: self.env.uid)
    action = fields.Selection([('approve', 'Approve'), ('reject', 'Reject')], default='approve')
    approval_date = fields.Datetime(string='Date', default=lambda self: fields.datetime.now())
    note = fields.Char(string='Note')
    
    def action_submit(self):
        if self.action == 'approve':
            self.transport_request_id._approve()
        elif self.action == 'reject':
            self.transport_request_id._reject()
        return {'type': 'ir.actions.act_window_close'}