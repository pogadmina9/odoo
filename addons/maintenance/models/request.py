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


class GeneralAffairRequest(models.Model):
    _name = 'general.affair.request'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'GA Request'
    _order = "id desc"
    _track_duration_field = 'stage_ga_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['stages.general.affair'].search([], limit=1)
    
    name = fields.Char('Request ID', required=True, default=lambda self: _('New Request'))
    create_user = fields.Many2one('res.users', string='Create User', default=lambda self: self.env.uid)
    request_date = fields.Datetime('Request Date', tracking=True, default=lambda self: fields.datetime.now())
    finish_date = fields.Datetime('Finish Date', help="Date the request was finished. ", tracking=True)
    archive = fields.Boolean(default=False)
    color = fields.Integer('Color Index')
    stage_ga_id = fields.Many2one('stages.general.affair', string='Stage', tracking=True, default=_default_stage, group_expand='_read_group_stage_ga_ids')    
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', default=4)
    done = fields.Boolean('Request Done')
    check_group_ga = fields.Boolean(string="Check Group GA", compute='_check_group_ga')
    requester = fields.Many2one('res.users', string='Requester', tracking=True)
    request = fields.Char('Request', tracking=True)
    req_detail = fields.Text('Request Detail', tracking=True)
    assignee = fields.Many2many('res.users', string='Assignee', tracking=True)
    action_plan = fields.Text('Action Plan', tracking=True)
    save = fields.Boolean(default=False)

    
    def dummy_save(self):
        self.write({'save': 1})    
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('name', _("New Request")) == _("New Request"):                
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'general.affair') or _("New")

        return super().create(vals_list)
    
    @api.model
    def _read_group_stage_ga_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_ga_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_ga_id)
    
    @api.depends_context('uid')
    def _check_group_ga(self):
        if self.env.user.has_group('maintenance.group_ga_admin'):
            self.check_group_ga = 'True'

        else:
            self.check_group_ga = None
                      
    
class PengajuanJasa(models.Model):
    _name = 'pengajuan.jasa'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Pengajuan Jasa'
    _order = "id desc"
    _track_duration_field = 'stage_pj_id'
    
    @api.returns('self')
    def _default_stage(self):
        return self.env['stages.ops.tech'].search([], limit=1)
    
    name = fields.Char('Request ID', default=lambda self: _('New Request'))
    seq = fields.Char('Sequence ID')
    create_user = fields.Many2one('res.users', string='Create User', default=lambda self: self.env.uid)
    request_date = fields.Datetime('Request Date', tracking=True, default=lambda self: fields.datetime.now())
    finish_date = fields.Datetime('Finish Date', help="Date the request was finished. ", tracking=True)
    approved_date = fields.Datetime(string='Approved Date', tracking=True)
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', default=6)
    stage_pj_id = fields.Many2one('stages.ops.tech', string='Stage', tracking=True, default=_default_stage, group_expand='_read_group_stage_pj_ids')
    requester = fields.Many2one('res.users', string='Requester', tracking=True, domain="[('employee_ids', '!=', False)]")
    reject_reason = fields.Char(string='Reject Reason', tracking=True)
    urgent_reason = fields.Char(string='Urgent Reason', tracking=True)
    cancel_reason = fields.Char(string='Cancel Reason', tracking=True)
    urgent_reject_reason = fields.Char(string='Urgent Reject Reason', tracking=True)
    activity_type = fields.Many2one('crm.activity.type', string='Activity', domain="[('used_in_jasa', '=', True)]", tracking=True)
    pic_report = fields.Many2one('crm.presales.team', string='Report PIC', tracking=True)
    man_power = fields.Selection([('presales', 'Presales'), ('teknisi', 'Teknisi'), ('both', 'Presales & Teknisi')], string="Man Power", tracking=True)
    approver = fields.Many2many('res.partner', string='Pending Approver', tracking=True, domain="[('user_ids.employee_ids', '!=', False)]") 
    sales = fields.Many2one('res.users', string='Sales', tracking=True, domain="[('employee_ids', '!=', False)]")
    bendera = fields.Many2one('bendera', string='Bendera', tracking=True)
    no_ppc = fields.Char(string='No PPC / Paket', tracking=True)
    partner = fields.Many2one('res.partner', string='Instansi', tracking=True, domain="[('is_company', '=', True)]")
    contact = fields.Many2one('res.partner', string='Contact Person', tracking=True, domain="[('commercial_partner_id', '=', partner), ('is_company', '=', False)]")
    address = fields.Text(string='Address', tracking=True)
    phone = fields.Char(string='Phone', tracking=True)
    presales = fields.Many2one('crm.presales.team', string='Presales', tracking=True)
    pic_teknisi = fields.Many2one('res.users', string='PIC Teknisi', tracking=True, domain="[('employee_ids', '!=', False)]")
    other_teknisi = fields.Boolean('Tambah Man Power')
    teknisi = fields.Many2many('res.users', string='Teknisi Lain', tracking=True, domain="[('employee_ids', '!=', False)]", help='Man Power Tambahan')
    schedule_date = fields.Datetime('Scheduled Date', tracking=True, help='Jadwal Tiba di Lokasi')
    job_description = fields.Text('Job Description', tracking=True)
    approval_list_ids = fields.One2many('approval.list', 'approval_pj_id', string='List Approval')
    crm_id = fields.Many2one('crm.lead', string='No PPC / Paket')
    approver_email = fields.Char(string='Approver Email')
    teknisi_email = fields.Char(string='Teknisi Email')
    url_local = fields.Char(string='URL Local')
    url_public = fields.Char(string='URL Public')
    user_type = fields.Selection([('new_user', 'New User'), ('existing_user', 'Existing User')], string="User Type", default='existing_user')        
    new_instansi = fields.Char(string='New Instansi')
    new_street = fields.Char(string='Address')
    new_city = fields.Char(string='City')
    new_state = fields.Char(string='State')
    new_zip = fields.Char(string='Zip')
    new_country = fields.Char(string='Country')
    new_cp = fields.Char(string='New Contact Person')
    new_phone = fields.Char(string='Phone Number')
    new_job_position = fields.Char(string='Job Position')
    
    
    # Parameter fields
    archive = fields.Boolean(default=False)
    is_contact_created = fields.Boolean(default=False)
    is_rejected = fields.Integer('Rejected')
    is_urgent = fields.Integer('Urgent')
    is_possible_urgent = fields.Integer('Possible Urgent')
    color = fields.Integer('Color Index')
    done = fields.Boolean('Request Done')
    check_group_pj = fields.Boolean(string="Check Group PJ", compute='_check_group_pj')    
    check_group_approver = fields.Boolean(string="Check Group Approver", compute='_check_group_approver')
    check_approver_button = fields.Boolean(string="Check Approver Button", compute='_check_approver_button')
    check_stage = fields.Boolean(default=False)
    save = fields.Boolean(default=False)
    compute_field = fields.Boolean('Compute', compute='_auto')
    pj_id = fields.Integer('PJ ID', compute='_auto_pj_id')
    readonly = fields.Boolean(compute='_check_readonly')
    upline_check = fields.Boolean(default=False, tracking=True)
    sales_check = fields.Boolean(default=False, tracking=True)
    current_user = fields.Many2one('res.users', compute='_compute_current_user')
    
    def dummy_save(self):
        self.write({'save': 1})
    
    def do_approve(self):
        now = fields.Datetime.now()
        self_id = self.env.uid
        approver_id = self.env['res.partner'].search([('user_ids', '=', self_id)], limit=1).id
        if self.check_group_approver:
            presales_project = self.env['approval.list'].create({                
                'name': self.env.uid,
                'approval_pj_id': self.id,
                'approved_date': now                
                })
            self.approver = [(3, approver_id)]
            self._check_assignment_after_approve()
        
        
    def _check_assignment_after_approve(self):
        if self.man_power in ['presales'] and self.presales:
           self.write({'stage_pj_id': 6})
           self._assign_presales_crm()
        elif self.man_power in ['teknisi'] and self.pic_teknisi:
           self.write({'stage_pj_id': 6})
        elif self.man_power in ['both'] and self.pic_teknisi and self.presales:
           self.write({'stage_pj_id': 6})
           self._assign_presales_crm()
        else:
            return

    def do_submit(self):
        self.write({'stage_pj_id': 3, 'is_rejected': False, 'archive': False, 'urgent_reject_reason': False, 'check_stage': True, 'cancel_reason': False})
        self.write({'url_local': f"{'http://192.168.10.13:8069/web#id='}{self.pj_id}{'&menu_id=487&cids=1&action=978&active_id=6&model=pengajuan.jasa&view_type=form'}"})
        self.write({'url_public': f"{'https://portal.performaoptimagroup.com/web#id='}{self.pj_id}{'&menu_id=487&cids=1&action=978&active_id=6&model=pengajuan.jasa&view_type=form'}"})
        self._create_new_partner()
        self._auto_fill_partner()
        self._check_ppc_field()
        self._search_crm_id()
        self._compute_mail_list()
        self.create_new_approval_mail()
        
    def create_new_approval_mail(self):
        mail_template = self.env.ref('maintenance.new_req_pj')
        mail_template.send_mail(self.id)
        
    def _do_cancel(self, reason):
        self.write({'stage_pj_id': 1, 'archive': True, 'color': 0, 'is_possible_urgent': False, 'is_urgent': False, 'check_stage': False, 'cancel_reason': reason, 'approver_email': False})
    
    def _do_req_urgent(self, reason):
        self.write({'is_possible_urgent': 1, 'color': 3, 'urgent_reason': reason})
        
    def do_submit_report(self):
        self.write({'stage_pj_id': 10})
    
    def submit_urgent_reason(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.urgent_reason_pj_action')
        
    def urgent_action(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.urgent_pj_action')
        
    def _do_action_urgent_approve(self, approved_reason):
        self.write({'is_urgent': True, 'is_possible_urgent': None, 'color': 1, 'urgent_reason': approved_reason})
        
    def _do_action_urgent_reject(self, reject_reason):
        self.write({'is_possible_urgent': None, 'color': 0, 'urgent_reject_reason': reject_reason})
    
    def _do_reject(self, reason):
        self.write({'stage_pj_id': 1, 'is_rejected': True, 'reject_reason': reason, 'is_possible_urgent': False, 'is_urgent': False, 'color': 0})                    
    
    def submit_reject_reason(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.reject_reason_pj_action')
        
    def submit_cancel_reason(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.cancel_reason_pj_action')
        
    @api.returns('self', lambda value: value.id)
    def copy(self, default=None):
        act = self.activity_type.short_name
        seq = self.env['ir.sequence'].next_by_code('pj.sequence')
        if default is None:
            default = {}
        if not default.get('name'):
            default['user_type'] = "existing_user"
            default['seq'] = seq
            default['name'] = f"{'PJ-'}{act}{seq}"
            default['stage_pj_id'] = 1
            default['request_date'] = fields.Datetime.now()
            default['approved_date'] = False
            default['finish_date'] = False          
            default['start_date'] = False        
            default['man_power'] = False        
            default['presales'] = False        
            default['pic_teknisi'] = False        
            default['teknisi'] = False              
            default['is_reviewed'] = False              
            default['is_rejected'] = False              
            default['is_urgent'] = False              
            default['is_possible_urgent'] = False              
            default['archive'] = False              
            default['upline_check'] = False              
            default['sales_check'] = False              
            default['reject_reason'] = False              
            default['urgent_reject_reason'] = False              
            default['urgent_reason'] = False              
            default['pic_report'] = False              
            default['approver_email'] = False              
        return super(PengajuanJasa, self).copy(default)
    
    def write(self, vals):
        res = super(PengajuanJasa, self).write(vals)
        if'stage_pj_id' in vals:
            self.filtered(lambda m: m.stage_pj_id.done and not m.finish_date).write({'finish_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_pj_id.done).write({'finish_date': False})
            self.filtered(lambda m: m.stage_pj_id.approved and not m.approved_date).write({'approved_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_pj_id.approved).write({'approved_date': False})
            
        if 'approver' in vals:
            if not self.approver and self.stage_pj_id.id == 3:
                self.stage_pj_id = 4
        if 'sales_check' in vals:
            if self.sales_check:
                self.stage_pj_id = 7              
        if 'activity_type' in vals:
            if self.stage_pj_id.id == 1:
                self._name_change()
        if 'pic_teknisi' in vals:
            self.filtered(lambda m: m.man_power in ['teknisi'] and m.pic_teknisi and m.stage_pj_id.id in [4]).write({'stage_pj_id': 6})
            self.filtered(lambda m: m.man_power in ['both'] and m.pic_teknisi and m.presales and m.stage_pj_id.id in [4]).write({'stage_pj_id': 6})
        if 'presales' in vals:
            self.filtered(lambda m: m.man_power in ['presales'] and m.presales and m.stage_pj_id.id in [4]).write({'stage_pj_id': 6})
            self.filtered(lambda m: m.man_power in ['both'] and m.pic_teknisi and m.presales and m.stage_pj_id.id in [4]).write({'stage_pj_id': 6})
        return res
        
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:            
            if vals.get('name', _("New Request")) == _("New Request"):                
                vals['seq'] = self.env['ir.sequence'].next_by_code('pj.sequence')

        return super().create(vals_list)
    
    def _auto(self):
        act = self.activity_type.short_name
        seq = self.seq
        if self.name == _('New Request'):
            self.compute_field = 'True'
            self.write({'name': f"{'PJ-'}{act}{seq}"})
        elif self.presales and not self.env['crm.presales.activity'].search([('no_ppc', '=', self.no_ppc), ('date', '=', self.schedule_date), ('request_id', '=', self.id)], limit=1).id:
            self.compute_field = 'True'
            self._assign_presales_crm()
            self._create_presales_activity_directly()
        else:
            self.compute_field = False
            
    def _check_readonly(self):
        if self.env.user.has_group('maintenance.group_pj_admin'):
            self.readonly = False
        elif self.stage_pj_id.id == 1:
            self.readonly = False
        else:
            self.readonly = 'True'
            
    def _name_change(self):
        act = self.activity_type.short_name
        seq = self.seq
        self.write({'name': f"{'PJ-'}{act}{seq}"})
    
    @api.onchange('teknisi')
    def _fill_teknisi_mail(self):
        self.teknisi_email = self.mapped("teknisi.name")
    
    def _list_email_teknisi(self):
        self.ensure_one()
        return ",".join([e for e in self.teknisi.mapped("email") if e])
        
    def _list_email_approver(self):
        self.ensure_one()
        return ",".join([e for e in self.approver.mapped("email") if e])
    
    def _create_crm_lead(self):
        self._search_crm_id()
        if not self.env['crm.lead'].search([('no_ppc', '=', self.no_ppc)]):
            presales_project = self.env['crm.lead'].sudo().create({                
                'name': 'New Project',
                'request_id': self.id,
                'user_id': self.sales.id,
                'no_ppc': self.no_ppc,
                'presales_team': self.presales.id or self.pic_report.id,
                'partner_id': self.partner.id,
                'partner_address': self.address,
                'contact_person': self.contact.id,
                'cp_phone': self.contact.phone
                })
            self.crm_id = self.env['crm.lead'].search([('no_ppc', '=', self.no_ppc)], limit=1).id
        elif not self.crm_id.presales_team:
            self.crm_id.presales_team = self.presales
        else:
            self.save = None
    
    def _assign_presales_crm(self):
        if self.crm_id and not self.crm_id.presales_team:
            self.crm_id.presales_team = self.presales
        
        else:
            return                
    
    @api.onchange('man_power')
    def _auto_select_approver(self):
        if self.man_power in ['presales']:
            self.approver = [(6, 0, [27])]
        
        elif self.man_power in ['teknisi']:
            self.approver = [(6, 0, [36])]
        
        elif self.man_power in ['both']:
            self.approver = [(6, 0, [36,27])]
    
    @api.onchange('stage_pj_id')
    def _create_presales_activity(self):
        no_ppc = self.env['crm.lead'].search([('no_ppc', '=', self.no_ppc)], limit=1).id
        if self.stage_pj_id.id == 6 and self.presales:
            presales_project = self.env['crm.presales.activity'].sudo().create({                
                'name': self.presales.name.id,
                'request_id': self.pj_id,
                'activity': self.activity_type.id,
                'date': self.schedule_date,
                'sales': self.sales.id,
                'no_ppc': no_ppc,
                'user': self.partner.id,
                'contact_person': self.contact.id,
                'job_title': self.contact.function,
                'phone': self.contact.phone,
                'detail': self.job_description
                })            
        else:
            self.save = None
               
    def _create_presales_activity_directly(self):
        no_ppc = self.env['crm.lead'].search([('no_ppc', '=', self.no_ppc)], limit=1).id
        presales_project = self.env['crm.presales.activity'].sudo().create({                
            'name': self.presales.name.id,
            'request_id': self.pj_id,
            'activity': self.activity_type.id,
            'date': self.schedule_date,
            'sales': self.sales.id,
            'no_ppc': no_ppc,
            'user': self.partner.id,
            'contact_person': self.contact.id,
            'job_title': self.contact.function,
            'phone': self.contact.phone,
            'detail': self.job_description
             })            
        
    
    def _create_new_partner(self):
        state = self.env['res.country.state'].search([('name', '=', self.new_state)], limit=1).id
        country = self.env['res.country'].search([('name', '=', self.new_country)], limit=1).id
        if self.user_type in ['new_user'] and not self.is_contact_created:
            create_instansi = self.env['res.partner'].create({                
                'is_company': True,
                'name': self.new_instansi,
                'street': self.new_street,
                'city': self.new_city,
                'state_id': state,
                'zip': self.new_zip,
                'country_id': country,
                'user_id': self.sales.id
                })
            create_cp = self.env['res.partner'].create({                
                    'is_company': False,
                    'parent_id': self.env['res.partner'].search([('name', '=', self.new_instansi)], limit=1).id,
                    'name': self.new_cp,
                    'function': self.new_job_position,
                    'phone': self.new_phone,
                    'mobile': self.new_phone
                    })
            self.write({'is_contact_created': True})
        else:
            self.save = None
            
    def _auto_fill_partner(self):
        partner_id = self.env['res.partner'].search([('name', '=', self.new_instansi)], limit=1).id
        contact_id = self.env['res.partner'].search([('name', '=', self.new_cp), ('commercial_company_name', '=', self.new_instansi)], limit=1).id
        phone_num = self.env['res.partner'].search([('name', '=', self.new_cp), ('commercial_company_name', '=', self.new_instansi)], limit=1).phone
        full_address = self.env['res.partner'].search([('name', '=', self.new_instansi)], limit=1).contact_address_inline
        if self.user_type in ['new_user']: 
            self.write({'partner': partner_id, 'contact': contact_id, 'address': full_address, 'phone': phone_num})
                    
    @api.onchange('partner')
    def _auto_address(self):
        self.write({'address': self.partner.contact_address_inline})
        if not self.sales:
            self.write({'sales': self.partner.user_id})
        
    @api.onchange('contact')
    def _auto_phone(self):
        if not self.contact.phone:       
            self.write({'phone': self.contact.mobile})
        else:
            self.write({'phone': self.contact.phone})
    
    @api.onchange('stage_pj_id')
    def _auto_check_stage(self):
        if self.stage_pj_id.id != 1:       
            self.write({'check_stage': True})
        else:
            self.write({'check_stage': False})
    
    @api.onchange('activity_type')
    def _auto_remove_pic_report(self):
        if not self.activity_type.need_presales_report:       
            self.write({'pic_report': False})
        else:
            self.write({'save': False})

    @api.onchange('stage_pj_id')
    def _auto_mapping_approver_email(self):
        if self.stage_pj_id.id == 3:       
            self._compute_mail_list()
        else:
            self.approver_email == False
        
    @api.model
    def _read_group_stage_pj_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_pj_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_pj_id)
    
    @api.depends_context('uid')
    def _check_group_pj(self):
        if self.env.user.has_group('maintenance.group_pj_admin'):
            self.check_group_pj = 'True'

        else:
            self.check_group_pj = None
            
    @api.depends_context('uid')
    def _check_group_approver(self):
        if self.env.user.has_group('maintenance.group_approval_admin'):
            self.check_group_approver = 'True'

        else:
            self.check_group_approver = None
            
    def _check_approver_button(self):        
        if self.current_user in self.approver.user_ids and self.check_group_approver:
            self.check_approver_button = 'True'        
        else:
            self.check_approver_button = None
             
    def _auto_pj_id(self):
        if not self.pj_id:
            self.pj_id = self.id            

        else:
            return            
           
    def _compute_current_user(self):
        self.current_user = self.env.uid
        
    def _compute_mail_list(self):
        self.approver_email = self.mapped("approver.user_ids.email_formatted")
               
    def _search_crm_id(self):
        crm_id = self.env['crm.lead'].search([('no_ppc', '=', self.no_ppc)], limit=1).id
        if not self.crm_id:
            self.crm_id = crm_id
         
        else:
            return
           
    @api.model
    def _cron_stage(self):
        date_now = fields.Datetime.now()
        list_ticket = self.search([('schedule_date', '<', date_now), ('stage_pj_id', '=', 6)])        
        for rec in list_ticket:
            rec.write({'stage_pj_id': 9})           
