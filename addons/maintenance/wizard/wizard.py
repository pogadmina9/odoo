# -*- coding: utf-8 -*-

import ast
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.osv import expression


class RejectReason(models.Model):
    _name = 'reject.reason'
    _description = 'Reject Reason'
    _order = "id"

    name = fields.Char('Name', translate=True, related='rejected_record_id.name')
    reason = fields.Char('reason', required=True)
    date = fields.Datetime(string='Reject Date')
    rejected_record_id = fields.Many2one('maintenance.request', string='Rejected Record')
    rejected_record_pj_id = fields.Many2one('pengajuan.jasa', string='Rejected Record')
            
    def action_reject(self):
        if self.rejected_record_id:
            self.rejected_record_id._do_reject(self.reason)
        elif self.rejected_record_pj_id:
            self.rejected_record_pj_id._do_reject(self.reason)            
        return {'type': 'ir.actions.act_window_close'}
        
class UrgentReason(models.Model):
    _name = 'urgent.reason'
    _description = 'Urgent Reason'
    _order = "id"

    name = fields.Char('Name', related='urgent_record_id.name')
    reason = fields.Char('Reason', required=True)
    urgent_record_id = fields.Many2one('maintenance.request', string='Reason Record')
    urgent_record_pj_id = fields.Many2one('pengajuan.jasa', string='Reason Record')
    
    def action_submit(self):
        if self.urgent_record_id:
            self.urgent_record_id._do_req_urgent(self.reason)
        elif self.urgent_record_pj_id:
            self.urgent_record_pj_id._do_req_urgent(self.reason)
        return {'type': 'ir.actions.act_window_close'}
        
class UrgentAction(models.Model):
    _name = 'urgent.action'
    _description = 'Urgent Action'
    _order = "id"

    name = fields.Char('Name', related='request_record_id.name')
    action = fields.Selection([('approve', 'Approve'), ('reject', 'Reject')], string='Action', default=False)
    approved_reason = fields.Char('Urgent Reason')
    reject_reason = fields.Char('Reject Reason')
    request_record_id = fields.Many2one('maintenance.request', string='Reason Record')
    request_record_pj_id = fields.Many2one('pengajuan.jasa', string='Reason Record')
    
    def action_submit(self):
        if self.action == 'approve' and self.request_record_id:
            self.request_record_id._do_action_urgent_approve(self.approved_reason)            
        elif self.action == 'reject' and self.request_record_id:
            self.request_record_id._do_action_urgent_reject(self.reject_reason)
        elif self.action == 'approve' and self.request_record_pj_id:
            self.request_record_pj_id._do_action_urgent_approve(self.approved_reason)            
        elif self.action == 'reject' and self.request_record_pj_id:
            self.request_record_pj_id._do_action_urgent_reject(self.reject_reason)
            
    @api.onchange('action')
    def _auto_reason(self):
        if self.request_record_id:
            self.approved_reason = self.request_record_id.urgent_reason
        elif self.request_record_pj_id:
            self.approved_reason = self.request_record_pj_id.urgent_reason
            
            
class CancelReason(models.Model):
    _name = 'cancel.reason'
    _description = 'Cancel Reason'
    _order = "id"

    name = fields.Char('Name', related='cancel_record_id.name')
    reason = fields.Char('Reason', required=True)
    cancel_record_id = fields.Many2one('maintenance.request', string='Reason Record')
    cancel_record_pj_id = fields.Many2one('pengajuan.jasa', string='Reason Record')
    
    def action_submit(self):
        if self.cancel_record_id:
            self.cancel_record_id._do_cancel(self.reason)
        elif self.cancel_record_pj_id:
            self.cancel_record_pj_id._do_cancel(self.reason)
        return {'type': 'ir.actions.act_window_close'}        