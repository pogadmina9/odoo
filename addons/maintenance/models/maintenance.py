# -*- coding: utf-8 -*-

import ast
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.osv import expression


class MaintenanceStage(models.Model):
    """ Model for case stages. This models the main stages of a Maintenance Request management flow. """

    _name = 'maintenance.stage'
    _description = 'Maintenance Stage'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')
    approved = fields.Boolean('Request Approved')
    
class MaintenanceEquipmentCategory(models.Model):
    _name = 'maintenance.equipment.category'
    _inherit = ['mail.alias.mixin', 'mail.thread']
    _description = 'Maintenance Equipment Category'

    @api.depends('equipment_ids')
    def _compute_fold(self):
        # fix mutual dependency: 'fold' depends on 'equipment_count', which is
        # computed with a read_group(), which retrieves 'fold'!
        self.fold = False
        for category in self:
            category.fold = False if category.equipment_count else True

    name = fields.Char('Category Name', required=True, translate=True)
    company_id = fields.Many2one('res.company', string='Company',
                                 default=lambda self: self.env.company)
    technician_user_id = fields.Many2one('res.users', 'Responsible', tracking=True, default=lambda self: self.env.uid)
    color = fields.Integer('Color Index')
    note = fields.Html('Comments', translate=True)
    equipment_ids = fields.One2many('maintenance.equipment', 'category_id', string='Equipment', copy=False)
    equipment_count = fields.Integer(string="Equipment Count", compute='_compute_equipment_count')
    maintenance_ids = fields.One2many('maintenance.request', 'category_id', copy=False)
    maintenance_count = fields.Integer(string="Maintenance Count", compute='_compute_maintenance_count')
    maintenance_open_count = fields.Integer(string="Current Maintenance", compute='_compute_maintenance_count')
    alias_id = fields.Many2one(help="Email alias for this equipment category. New emails will automatically "
                                    "create a new equipment under this category.")
    fold = fields.Boolean(string='Folded in Maintenance Pipe', compute='_compute_fold', store=True)

    def _compute_equipment_count(self):
        equipment_data = self.env['maintenance.equipment']._read_group([('category_id', 'in', self.ids)],
                                                                       ['category_id'], ['__count'])
        mapped_data = {category.id: count for category, count in equipment_data}
        for category in self:
            category.equipment_count = mapped_data.get(category.id, 0)

    def _compute_maintenance_count(self):
        maintenance_data = self.env['maintenance.request']._read_group([('category_id', 'in', self.ids)],
                                                                       ['category_id', 'archive'], ['__count'])
        mapped_data = {(category.id, archive): count for category, archive, count in maintenance_data}
        for category in self:
            category.maintenance_open_count = mapped_data.get((category.id, False), 0)
            category.maintenance_count = category.maintenance_open_count + mapped_data.get((category.id, True), 0)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_contains_maintenance_requests(self):
        for category in self:
            if category.equipment_ids or category.maintenance_ids:
                raise UserError(
                    _("You cannot delete an equipment category containing equipment or maintenance requests."))

    def _alias_get_creation_values(self):
        values = super(MaintenanceEquipmentCategory, self)._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('maintenance.request').id
        if self.id:
            values['alias_defaults'] = defaults = ast.literal_eval(self.alias_defaults or "{}")
            defaults['category_id'] = self.id
        return values

class MaintenanceMixin(models.AbstractModel):
    _name = 'maintenance.mixin'
    _check_company_auto = True
    _description = 'Maintenance Maintained Item'

    company_id = fields.Many2one('res.company', string='Company',
                                 default=lambda self: self.env.company)
    effective_date = fields.Date('Effective Date', default=fields.Date.context_today, required=True,
                                 help="This date will be used to compute the Mean Time Between Failure.")
    maintenance_team_id = fields.Many2one('maintenance.team', string='Maintenance Team',
                                          compute='_compute_maintenance_team_id', store=True, readonly=False,
                                          check_company=True)
    technician_user_id = fields.Many2one('res.users', string='Technician', tracking=True)
    maintenance_ids = fields.One2many('maintenance.request')  # needs to be extended in order to specify inverse_name !
    maintenance_count = fields.Integer(compute='_compute_maintenance_count', string="Maintenance Count", store=True)
    maintenance_open_count = fields.Integer(compute='_compute_maintenance_count', string="Current Maintenance",
                                            store=True)
    expected_mtbf = fields.Integer(string='Expected MTBF', help='Expected Mean Time Between Failure')
    mtbf = fields.Integer(compute='_compute_maintenance_request', string='MTBF',
                          help='Mean Time Between Failure, computed based on done corrective maintenances.')
    mttr = fields.Integer(compute='_compute_maintenance_request', string='MTTR', help='Mean Time To Repair')
    estimated_next_failure = fields.Date(compute='_compute_maintenance_request',
                                         string='Estimated time before next failure (in days)',
                                         help='Computed as Latest Failure Date + MTBF')
    latest_failure_date = fields.Date(compute='_compute_maintenance_request', string='Latest Failure Date')

    @api.depends('company_id')
    def _compute_maintenance_team_id(self):
        for record in self:
            if record.maintenance_team_id.company_id and record.maintenance_team_id.company_id.id != record.company_id.id:
                record.maintenance_team_id = False

    @api.depends('effective_date', 'maintenance_ids.stage_id', 'maintenance_ids.close_date',
                 'maintenance_ids.request_date')
    def _compute_maintenance_request(self):
        for record in self:
            maintenance_requests = record.maintenance_ids.filtered(
                lambda mr: mr.maintenance_type == 'corrective' and mr.stage_id.done)
            record.mttr = len(maintenance_requests) and (sum(
                int((request.close_date - request.request_date).days) for request in maintenance_requests) / len(
                maintenance_requests)) or 0
            record.latest_failure_date = max((request.request_date for request in maintenance_requests), default=False)
            record.mtbf = record.latest_failure_date and (
                        record.latest_failure_date - record.effective_date).days / len(maintenance_requests) or 0
            record.estimated_next_failure = record.mtbf and record.latest_failure_date + relativedelta(
                days=record.mtbf) or False

    @api.depends('maintenance_ids.stage_id.done', 'maintenance_ids.archive')
    def _compute_maintenance_count(self):
        for record in self:
            record.maintenance_count = len(record.maintenance_ids)
            record.maintenance_open_count = len(
                record.maintenance_ids.filtered(lambda mr: not mr.stage_id.done and not mr.archive))

class MaintenanceEquipment(models.Model):
    _name = 'maintenance.equipment'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'maintenance.mixin']
    _description = 'Maintenance Equipment'
    _check_company_auto = True

    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'owner_user_id' in init_values and self.owner_user_id:
            return self.env.ref('maintenance.mt_mat_assign')
        return super(MaintenanceEquipment, self)._track_subtype(init_values)

    @api.depends('serial_no')
    def _compute_display_name(self):
        for record in self:
            if record.serial_no:
                record.display_name = record.name + '/' + record.serial_no
            else:
                record.display_name = record.name

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=None, order=None):
        domain = domain or []
        query = None
        if name and operator not in expression.NEGATIVE_TERM_OPERATORS and operator != '=':
            query = self._search([('name', '=', name)] + domain, limit=limit, order=order)
        return query or super()._name_search(name, domain, operator, limit, order)

    name = fields.Char('Equipment Name', required=True, translate=True)
    active = fields.Boolean(default=True)
    owner_user_id = fields.Many2one('res.users', string='Owner', tracking=True)
    category_id = fields.Many2one('maintenance.equipment.category', string='Equipment Category',
                                  tracking=True, group_expand='_read_group_category_ids')
    partner_id = fields.Many2one('res.partner', string='Vendor', check_company=True)
    partner_ref = fields.Char('Vendor Reference')
    location = fields.Char('Location')
    model = fields.Char('Model')
    serial_no = fields.Char('Serial Number', copy=False)
    assign_date = fields.Date('Assigned Date', tracking=True)
    cost = fields.Float('Cost')
    note = fields.Html('Note')
    warranty_date = fields.Date('Warranty Expiration Date')
    color = fields.Integer('Color Index')
    scrap_date = fields.Date('Scrap Date')
    maintenance_ids = fields.One2many('maintenance.request', 'equipment_id')

    @api.onchange('category_id')
    def _onchange_category_id(self):
        self.technician_user_id = self.category_id.technician_user_id

    _sql_constraints = [
        ('serial_no', 'unique(serial_no)', "Another asset already exists with this serial number!"),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        equipments = super().create(vals_list)
        for equipment in equipments:
            if equipment.owner_user_id:
                equipment.message_subscribe(partner_ids=[equipment.owner_user_id.partner_id.id])
        return equipments

    def write(self, vals):
        if vals.get('owner_user_id'):
            self.message_subscribe(partner_ids=self.env['res.users'].browse(vals['owner_user_id']).partner_id.ids)
        return super(MaintenanceEquipment, self).write(vals)

    @api.model
    def _read_group_category_ids(self, categories, domain, order):
        """ Read group customization in order to display all the categories in
            the kanban view, even if they are empty.
        """
        category_ids = categories._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return categories.browse(category_ids)

class MaintenanceRequest(models.Model):
    _name = 'maintenance.request'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin', 'mail.tracking.duration.mixin']
    _description = 'Request'
    _order = "id desc"
    _check_company_auto = True
    _track_duration_field = 'stage_tech_ops_id'

    @api.returns('self')
    def _default_stage(self):
        return self.env['maintenance.stage'].search([], limit=1)       

    def _creation_subtype(self):
        return self.env.ref('maintenance.mt_req_created')

    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'stage_id' in init_values:
            return self.env.ref('maintenance.mt_req_status')
        return super(MaintenanceRequest, self)._track_subtype(init_values)

    def _get_default_team_id(self):
        MT = self.env['maintenance.team']
        team = MT.search([('company_id', '=', self.env.company.id)], limit=1)
        if not team:
            team = MT.search([], limit=1)
        return team.id

    name = fields.Char('Subjects', readonly=True)
    create_user = fields.Many2one('res.users', string='Create User', default=lambda self: self.env.uid)
    issuer = fields.Many2one('res.users', string='Issuer', tracking=True)
    location = fields.Char('Location', tracking=True)
    problem = fields.Char('Problem', tracking=True)
    diagnose = fields.Text('Diagnose / Action Plan', tracking=True)
    warranty = fields.Selection([('iw', 'In Warranty'), ('oow', 'Out of Warranty')], string='Warranty Status',
                                tracking=True)
    service_type = fields.Selection([('carryin', 'Carry In'), ('onsite', 'On Site')], string='Service Type',
                                    tracking=True)
    serial_number = fields.Char(string='Serial Number', tracking=True)
    sales = fields.Many2one('res.users', string='Sales', tracking=True)
    request_type = fields.Selection([('barang', 'Pengajuan Barang'), ('jasa', 'Pengajuan Jasa')], string='Request Type',
                                    tracking=True)                                       
    activity_type = fields.Many2one('activity.type', string='Tipe Jasa', tracking=True)
    phone = fields.Char(string='Phone', tracking=True)
    current_partner = fields.Many2one('res.partner', string='Current Partner')
    approval_user = fields.Many2many('res.partner', string='Approver', tracking=True)
    is_approval_stage = fields.Boolean(string='Approval State', compute='_check_approval_state')    
    is_early_stage = fields.Boolean(string='Early Stage', compute='_check_early_stage')
    is_done = fields.Boolean(string='Done')
    is_req_done = fields.Boolean(string='Done', default=False)
    checker_req_done = fields.Boolean(string='Checker Done', compute='_checker_req_done')    
    jadwal = fields.Datetime(string='Jadwal', tracking=True)
    partner = fields.Many2one('res.partner', string='Instansi', tracking=True)    
    packing = fields.Char(string='Packing List', tracking=True)
    no_ppc = fields.Char(string='No PPC / Paket', tracking=True)
    model = fields.Char(string='Model', tracking=True)
    job = fields.Text(string='Detail Pekerjaan', tracking=True)
    contact = fields.Many2one('res.partner', string='Contact Person', tracking=True)
    address = fields.Char(string='Address', tracking=True)
    bendera = fields.Many2one('bendera', string='Bendera', tracking=True)
    brand = fields.Many2one('brand', string='Brand', tracking=True)
    stage_sc_id = fields.Many2one('stages.sc', string='Stage SC', tracking=True,
                                  group_expand='_read_group_stage_sc_ids')
    stage_tech_ops_id = fields.Many2one('stages.ops.tech', string='Stage Ops and Tech', tracking=True,
                                        group_expand='_read_group_stage_ops_tech_ids')
    stage_tech_ops_pb_id = fields.Many2one('stages.ops.tech.pb', string='Stage Ops and Tech', tracking=True,
                                           group_expand='_read_group_stage_ops_tech_pb_ids')
    stage_finance_id = fields.Many2one('stages.finance', string='Stage Finance', tracking=True,
                                       group_expand='_read_group_stage_finance_ids')
    stage_ga_id = fields.Many2one('stages.general.affair', string='Stage GA', tracking=True,
                                       group_expand='_read_group_stage_ga_ids')
    unit_type = fields.Many2one('unit.type', string='Unit Type', tracking=True)
    packing = fields.Char(string='Packing', tracking=True)
    req_finance_type = fields.Selection([('bs', 'BS'), ('report', 'Report')], string='Request Type', tracking=True)

    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')
    description = fields.Html('Description')
    request_date = fields.Datetime('Request Date', tracking=True, default=lambda self: fields.datetime.now(),
                                   help="Date requested for the maintenance to happen")
    approved_date = fields.Datetime(string='Approved Date')
    owner_user_id = fields.Many2one('res.users', string='Created by User', default=lambda s: s.env.uid)
    category_id = fields.Many2one('maintenance.equipment.category', related='equipment_id.category_id',
                                  string='Category', store=True, readonly=True)
    equipment_id = fields.Many2one('maintenance.equipment', string='Equipment',
                                   ondelete='restrict', index=True, check_company=True)
    user_id = fields.Many2one('res.users', string='Technician', compute='_compute_user_id', store=True, readonly=False,
                              tracking=True)
    assignee = fields.Many2many('res.users', string='Assignee', tracking=True)
    presales = fields.Many2one('res.users', string='Presales', tracking=True)
    teknisi = fields.Many2one('res.users', string='Teknisi', tracking=True)
    stage_id = fields.Many2one('maintenance.stage', string='Stage', ondelete='restrict', tracking=True,
                               group_expand='_read_group_stage_ids', default=_default_stage, copy=False)
    priority = fields.Selection([('0', 'Very Low'), ('1', 'Low'), ('2', 'Normal'), ('3', 'High')], string='Priority')
    color = fields.Integer('Color Index')
    color_picker = fields.Char('Color Picker')
    close_date = fields.Datetime('Finish Date', help="Date the request was finished. ", tracking=True)
    kanban_state = fields.Selection(
        [('normal', 'In Progress'), ('rejected', 'Rejected'), ('done', 'Ready for next stage')],
        string='Kanban State', required=True, default='normal', tracking=True)
    # active = fields.Boolean(default=True, help="Set active to false to hide the maintenance request without deleting it.")
    archive = fields.Boolean(default=False,
                             help="Set archive to true to hide the maintenance request without deleting it.")
    maintenance_type = fields.Selection([('corrective', 'Corrective'), ('preventive', 'Preventive')],
                                        string='Maintenance Type', default="corrective")
    schedule_date = fields.Datetime('Scheduled Date',
                                    help="Date the maintenance team plans the maintenance.  It should not differ much from the Request Date. ",
                                    tracking=True)
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', required=True,
                                          default=_get_default_team_id,
                                          compute='_compute_maintenance_team_id', store=True, readonly=False,
                                          check_company=True, tracking=True)
    duration = fields.Float(help="Duration in hours.")
    done = fields.Boolean(related='stage_id.done')
    approval_list_ids = fields.One2many('approval.list', 'approval_id', string='List Approval')
    pengajuan_barang_ids = fields.One2many('pengajuan.barang', 'pengajuan_barang_id', string='Pengajuan Barang')
    bs_ids = fields.One2many('bs.record', 'bs_id', string='BS Record')
    report_ids = fields.One2many('report.record', 'report_id', string='Report Record')
    presales_activity_ids = fields.One2many('presales.activity', 'services_req_id', string='Presales Activity')
    nomor_bs = fields.Many2one('maintenance.request', string='Nomor BS')
    nomor_report = fields.Many2one('maintenance.request', string='Nomor Report', compute='_compute_nomor_reportbs')
    checker_is_reported = fields.Boolean(string="Checker Is Reported", compute='_checker_is_reported')
    is_reported = fields.Boolean(string="Is Reported", default=False)
    is_report_bs_submit = fields.Boolean(string="Report BS already Submit", default=False)
    amount_total = fields.Monetary(string="Total", store=True, compute='_compute_amounts', tracking=4)
    amount_total_pb = fields.Monetary(string="Total", store=True, compute='_compute_amounts_pb', tracking=4)
    keperluan = fields.Selection([('demo', 'Demo'), ('loan', 'Backup / Peminjaman'), ('inven', 'Inventaris')], string="Keperluan")
    man_power = fields.Selection([('presales', 'Presales'), ('teknisi', 'Teknisi'), ('both', 'Presales & Teknisi')], string="Man Power")
    urgent_action_ids = fields.Many2one('urgent.action', string='Urgent ID', compute='_compute_urgent_action_id')
    urgent_ids = fields.Many2one('urgent.reason', string='Urgent ID', compute='_compute_urgent_id')
    reject_ids = fields.Many2one('reject.reason', string='Reject ID', compute='_compute_reject_id')
    reject_reason = fields.Char(string='Reject Reason', tracking=True)
    urgent_reason = fields.Char(string='Urgent Reason', tracking=True)
    urgent_reject_reason = fields.Char(string='Urgent Reject Reason', tracking=True)
    is_reject = fields.Boolean(string='Rejected')
    is_urgent_approval = fields.Boolean(string='Possible Urgent', tracking=True)
    is_urgent = fields.Boolean(string='Urgent', tracking=True)
    count_unreport_bs = fields.Integer(string='Unreported BS', compute='_compute_unreport_bs')
    check_approved_stage =  fields.Boolean(string="Check Approved Stage")
    check_group_internal = fields.Boolean(string="Check Group Int", compute='_check_group_internal')
    check_group_sc = fields.Boolean(string="Check Group SC", compute='_check_group_sc')
    check_group_tech_ops = fields.Boolean(string="Check Group Tech Ops", compute='_check_group_tech_ops')
    check_group_tech_ops_pb = fields.Boolean(string="Check Group Tech Ops PB", compute='_check_group_tech_ops_pb')
    check_group_ga = fields.Boolean(string="Check Group GA", compute='_check_group_ga')
    check_group_finance = fields.Boolean(string="Check Group Finance", compute='_check_group_finance')
    check_group_product = fields.Boolean(string="Check Group Product", compute='_check_group_product')
    check_approval = fields.Boolean(string="Check Group Approval", compute='_check_group_approval')
    bs_number = fields.Many2one('maintenance.request', string='Nomor BS ')
    dummy_save = fields.Boolean(string='Dummy Save')
    instruction_type = fields.Selection([
        ('pdf', 'PDF'), ('google_slide', 'Google Slide'), ('text', 'Text')],
        string="Instruction", default="text"
    )
    instruction_pdf = fields.Binary('PDF')
    instruction_google_slide = fields.Char('Google Slide',
                                           help="Paste the url of your Google Slide. Make sure the access to the document is public.")
    instruction_text = fields.Html('Text')
    recurring_maintenance = fields.Boolean(string="Recurrent", compute='_compute_recurring_maintenance', store=True,
                                           readonly=False)
    repeat_interval = fields.Integer(string='Repeat Every', default=1)
    repeat_unit = fields.Selection([
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
        ('year', 'Years'),
    ], default='week')
    repeat_type = fields.Selection([
        ('forever', 'Forever'),
        ('until', 'Until'),
    ], default="forever", string="Until")
    repeat_until = fields.Date(string="End Date")
    
    def _check_early_stage(self):
        if self.stage_id.id == 1:
            self.is_early_stage = 'True'
        elif self.stage_sc_id.id == 1:
            self.is_early_stage = 'True'
        elif self.stage_tech_ops_pb_id.id == 1:
            self.is_early_stage = 'True'
        elif self.stage_finance_id.id == 1:
            self.is_early_stage = 'True'
        elif self.stage_tech_ops_id.id == 1:
            self.is_early_stage = 'True'
        else:
            self.is_early_stage = None
            
    def _checker_is_reported(self):
        if not self.is_early_stage:
            self.checker_is_reported = 'True'
            self.nomor_bs.is_reported = 'True'
        else:
            self.checker_is_reported = None
            self.nomor_bs.is_reported = None

    def _checker_req_done(self):
        if self.stage_id.done:
            self.checker_req_done = 'True'
            self.write({'is_req_done': True})
        elif self.stage_sc_id.done:
            self.checker_req_done = 'True'
            self.write({'is_req_done': True})
        elif self.stage_tech_ops_pb_id.done:
            self.checker_req_done = 'True'
            self.write({'is_req_done': True})
        elif self.stage_ga_id.done:
            self.checker_req_done = 'True'
            self.write({'is_req_done': True})
        elif self.stage_finance_id.done:
            self.checker_req_done = 'True'
            self.write({'is_req_done': True})
        elif self.stage_tech_ops_id.done:
            self.checker_req_done = 'True'
            self.write({'is_req_done': True})
        else:
            self.checker_req_done = None
            self.write({'is_req_done': False})
            
    def _check_approval_state(self):
        if self.stage_tech_ops_pb_id.id == 2:
            self.is_approval_stage = 'True'
        elif self.stage_finance_id.id == 2:
            self.is_approval_stage = 'True'
        elif self.stage_tech_ops_id.id == 3:
            self.is_approval_stage = 'True'
        elif self.stage_ga_id.id == 2:
            self.is_approval_stage = 'True'
        else:
            self.is_approval_stage = None
    
    def _do_req_urgent(self, reason):
        self.write({'urgent_reason': reason, 'is_urgent_approval': True, 'color': 3})
            
    def _compute_approved_date(self):
        if self.stage_tech_ops_id.id == 6:
            self.approved_date = fields.Datetime.now()
        else:
            self.approved_date = None

    def archive_equipment_request(self):
        self.write({'archive': True, 'recurring_maintenance': False})

    def reset_equipment_request(self):
        """ Reinsert the maintenance request into the maintenance pipe in the first stage"""
        first_stage_obj = self.env['maintenance.stage'].search([], order="sequence asc", limit=1)
        # self.write({'active': True, 'stage_id': first_stage_obj.id})
        self.write({'archive': False, 'stage_id': first_stage_obj.id})
    
    def cancel_request(self):
        if self.maintenance_team_id.id == 1:
            self.write({'stage_id': 1, 'archive': True})
        elif self.maintenance_team_id.id == 2:
            self.write({'stage_sc_id': 1, 'archive': True})
        elif self.maintenance_team_id.id == 3:
            self.write({'stage_tech_ops_pb_id': 1, 'archive': True})
        elif self.maintenance_team_id.id == 4:
            self.write({'stage_id': 1, 'archive': True})
        elif self.maintenance_team_id.id == 5:
            self.write({'stage_finance_id': 1, 'archive': True})
        else:
            self.write({'stage_tech_ops_id': 1, 'archive': True})
        
    def submit_urgent_reason(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.urgent_reason_action')
        
    def submit_reject_reason(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.reject_reason_action')
        
    def _do_reject(self, reason):
        self.write({'reject_reason': reason, 'is_reject': 1})
        if self.maintenance_team_id.id == 1:
            self.write({'stage_id': 1})
        elif self.maintenance_team_id.id == 2:
            self.write({'stage_sc_id': 1})
        elif self.maintenance_team_id.id == 3:
            self.write({'stage_tech_ops_pb_id': 1})
        elif self.maintenance_team_id.id == 4:
            self.write({'stage_id': 1})
        elif self.maintenance_team_id.id == 5:
            self.write({'stage_finance_id': 1})
        else:
            self.write({'stage_tech_ops_id': 1})
    
    def _do_action_urgent_approve(self, approved_reason):
        self.write({'is_urgent': True, 'is_urgent_approval': None, 'color': 1, 'urgent_reason': approved_reason})
        
    def _do_action_urgent_reject(self, reject_reason):
        self.write({'is_urgent_approval': None, 'color': 0, 'urgent_reject_reason': reject_reason})

    def urgent_action(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.urgent_action_action')
            
    def submit(self):
        self.write({'kanban_state': 'normal', 'stage_id': 5, 'color': 0, 'is_reject': 0, 'archive': False})
        self.write({'reject_reason': None})
        
    def submit_finance(self):
        if self.count_unreport_bs == 1 and not self.archive:       
            self.write({'kanban_state': 'normal', 'stage_finance_id': 2, 'color': 0, 'is_reject': 0, 'archive': False, 'stage_id': None, 'reject_reason': None}) 
        elif self.count_unreport_bs == 0 and self.archive:       
            self.write({'kanban_state': 'normal', 'stage_finance_id': 2, 'color': 0, 'is_reject': 0, 'archive': False, 'stage_id': None, 'reject_reason': None}) 
        else:
            raise UserError(_('Please Submit Your Previous BS Report'))
        
    def submit_finance_report(self):
        self.write({'kanban_state': 'normal', 'stage_finance_id': 2, 'color': 0, 'is_reject': 0, 'archive': False})
        self.write({'stage_id': None})
        self.write({'reject_reason': None})
       
    def submit_tech_ops_pb(self):
        self.write({'kanban_state': 'normal', 'stage_tech_ops_pb_id': 10, 'color': 0, 'is_reject': 0, 'archive': False})
        self.write({'stage_id': None})
        self.write({'reject_reason': None})

    def submit_tech_ops(self):
        self.write({'kanban_state': 'normal', 'stage_tech_ops_id': 3, 'color': 0, 'is_reject': 0, 'archive': False})
        self.write({'stage_id': None})
        self.write({'reject_reason': None})

    def get_dummy_save(self):
        self.write({'dummy_save': 1})

    def save_pb(self):
        if not self.stage_tech_ops_pb_id:
            self.write({'stage_tech_ops_pb_id': 1})

        else:
            self.write({'dummy_save': 1})

    def save_pj(self):
        if not self.stage_tech_ops_id:
            self.write({'stage_tech_ops_id': 1})

        else:
            self.write({'dummy_save': 1})

    def save_sc(self):
        if not self.stage_sc_id:
            self.write({'stage_sc_id': 1})

        else:
            self.write({'dummy_save': 1})

    def save_finance(self):
        if not self.stage_finance_id:
            self.write({'stage_finance_id': 1})

        else:
            self.write({'dummy_save': 1})
            
    def create_report_bs(self):
        return self.env["ir.actions.act_window"]._for_xml_id('maintenance.report_bs_action')            

    @api.depends('dummy_save')
    def _compute_unreport_bs(self):
        for bs in self:            
            data = self.env['maintenance.request']._read_group(
                [('req_finance_type', '=', 'bs'), ("is_reported", "=", False), ("archive", "=", False), ('issuer', '=', self.env.uid)],
                ['schedule_date:year', 'priority', 'kanban_state', ],
                ['__count']
            )
            bs.count_unreport_bs = sum(count for (_, _, _, count) in data)
    
    @api.depends('company_id', 'equipment_id')
    def _compute_maintenance_team_id(self):
        for request in self:
            if request.equipment_id and request.equipment_id.maintenance_team_id:
                request.maintenance_team_id = request.equipment_id.maintenance_team_id.id
            if request.maintenance_team_id.company_id and request.maintenance_team_id.company_id.id != request.company_id.id:
                request.maintenance_team_id = False

    @api.depends('company_id', 'equipment_id')
    def _compute_user_id(self):
        for request in self:
            if request.equipment_id:
                request.user_id = request.equipment_id.technician_user_id or request.equipment_id.category_id.technician_user_id
            if request.user_id and request.company_id.id not in request.user_id.company_ids.ids:
                request.user_id = False

    @api.depends('maintenance_type')
    def _compute_recurring_maintenance(self):
        for request in self:
            if request.maintenance_type != 'preventive':
                request.recurring_maintenance = False

    @api.model_create_multi
    def create(self, vals_list):
        # context: no_log, because subtype already handle this
        maintenance_requests = super().create(vals_list)
        for request in maintenance_requests:
            if request.owner_user_id or request.assignee:
                request._add_followers()
            if request.equipment_id and not request.maintenance_team_id:
                request.maintenance_team_id = request.equipment_id.maintenance_team_id
            if request.close_date and not request.stage_id.done:
                request.close_date = False
            if not request.close_date and request.stage_id.done:
                request.close_date = fields.Datetime.now()
        maintenance_requests.activity_update()
        return maintenance_requests      

    def write(self, vals):
        # Overridden to reset the kanban_state to normal whenever
        # the stage (stage_id) of the Maintenance Request changes.
        if vals and 'kanban_state' not in vals and 'stage_id' in vals:
            vals['kanban_state'] = 'normal'
        if 'stage_id' in vals and self.maintenance_type == 'preventive' and self.recurring_maintenance and self.env[
            'maintenance.stage'].browse(vals['stage_id']).done:
            schedule_date = self.schedule_date or fields.Datetime.now()
            schedule_date += relativedelta(**{f"{self.repeat_unit}s": self.repeat_interval})
            if self.repeat_type == 'forever' or schedule_date.date() <= self.repeat_until:
                self.copy({'schedule_date': schedule_date})
        res = super(MaintenanceRequest, self).write(vals)
        if vals.get('owner_user_id') or vals.get('assignee'):
            self._add_followers()
        if 'stage_id' in vals:
            self.filtered(lambda m: m.stage_id.done and not m.close_date).write({'close_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_id.done).write({'close_date': False})
            self.activity_feedback(['maintenance.mail_act_maintenance_request'])
            self.activity_update()
        if 'stage_tech_ops_id' in vals:
            self.filtered(lambda m: m.stage_tech_ops_id.done and not m.close_date).write({'close_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_tech_ops_id.done).write({'close_date': False})
            self.filtered(lambda m: m.stage_tech_ops_id.approved and not m.approved_date).write({'approved_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_tech_ops_id.approved).write({'approved_date': False})
        if 'stage_tech_ops_pb_id' in vals:
            self.filtered(lambda m: m.stage_tech_ops_pb_id.done and not m.close_date).write({'close_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_tech_ops_pb_id.done).write({'close_date': False})
            self.filtered(lambda m: m.stage_tech_ops_pb_id.approved and not m.approved_date).write({'approved_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_tech_ops_pb_id.approved).write({'approved_date': False})
        if 'stage_finance_id' in vals:
            self.filtered(lambda m: m.stage_finance_id.done and not m.close_date).write({'close_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_finance_id.done).write({'close_date': False})
            self.filtered(lambda m: m.stage_finance_id.approved and not m.approved_date).write({'approved_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_finance_id.approved).write({'approved_date': False})
        if 'stage_sc_id' in vals:
            self.filtered(lambda m: m.stage_sc_id.done and not m.close_date).write({'close_date': fields.Datetime.now()})
            self.filtered(lambda m: not m.stage_sc_id.done).write({'close_date': False})
        if vals.get('user_id') or vals.get('schedule_date'):
            self.activity_update()
        if self._need_new_activity(vals):
            # need to change description of activity also so unlink old and create new activity
            self.activity_unlink(['maintenance.mail_act_maintenance_request'])
            self.activity_update()
        return res

    def _need_new_activity(self, vals):
        return vals.get('equipment_id')

    def _get_activity_note(self):
        self.ensure_one()
        if self.equipment_id:
            return _('Request planned for %s', self.equipment_id._get_html_link())
        return False   

    def activity_update(self):
        """ Update maintenance activities based on current record set state.
        It reschedule, unlink or create maintenance request activities. """
        self.filtered(lambda request: not request.schedule_date).activity_unlink(
            ['maintenance.mail_act_maintenance_request'])
        for request in self.filtered(lambda request: request.schedule_date):
            date_dl = fields.Datetime.from_string(request.schedule_date).date()
            updated = request.activity_reschedule(
                ['maintenance.mail_act_maintenance_request'],
                date_deadline=date_dl,
                new_user_id=request.user_id.id or request.owner_user_id.id or self.env.uid)
            if not updated:
                note = self._get_activity_note()
                request.activity_schedule(
                    'maintenance.mail_act_maintenance_request',
                    fields.Datetime.from_string(request.schedule_date).date(),
                    note=note, user_id=request.user_id.id or request.owner_user_id.id or self.env.uid)

    def _add_followers(self):
        for request in self:
            partner_ids = (request.owner_user_id.partner_id + request.assignee.partner_id).ids
            request.message_subscribe(partner_ids=partner_ids)

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_ids = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_ids)

    @api.model
    def _read_group_stage_sc_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_sc_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_sc_id)

    @api.model
    def _read_group_stage_ops_tech_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_ops_tech_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_ops_tech_id)

    @api.model
    def _read_group_stage_ops_tech_pb_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_ops_tech_pb_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_ops_tech_pb_id)

    @api.model
    def _read_group_stage_finance_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_finance_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_finance_id)
        
    @api.model
    def _read_group_stage_ga_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_ga_id = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_ga_id)
        
    @api.onchange('stage_tech_ops_id')
    def _create_presales_activity(self):
        if self.stage_tech_ops_id.id == 6:
            presales = self.env['presales.activity'].create({
                'services_req_id': self.id,
                'activity': self.activity_type.id,
                'sales': self.sales.id,
                'date': self.jadwal,
                'name': self.presales.id,
                'no_ppc': self.no_ppc,
                'user': self.partner.id
                })
        else:
            self.dummy_save = None

    @api.onchange('contact')
    def _change_partner_name(self):
        if self.contact:
            self.phone = self.contact.phone
        else:
            self.phone = None
            
    @api.onchange('partner')
    def _change_address(self):
        if self.partner:
            self.address = self.partner.contact_address_inline
        else:
            self.address = None
    
    @api.depends('bs_ids.nominal')
    def _compute_amounts(self):
        for request in self:
            bs_records = request.bs_ids.filtered(lambda x: not x.display_type)
            amount_bs = sum(bs_records.mapped('nominal'))

            request.amount_total = amount_bs
            
    def _compute_nomor_reportbs(self):
       for request in self:
            if request.req_finance_type == 'bs':
                result_no_report = self.env['maintenance.request'].search([('nomor_bs.id', '=', self.id)], limit=1)
                request.nomor_report = result_no_report
        
            else:
                request.nomor_report = False
    
    def _compute_reject_id(self):
       for request in self:
            if request.is_reject == True:
                reason_id = self.env['reject.reason'].search([('rejected_record_id.id', '=', self.id)], limit=1, order='id desc')
                request.reject_ids = reason_id
        
            else:
                request.reject_ids = False
    
    def _compute_urgent_id(self):
       for request in self:
                urgent_id = self.env['urgent.reason'].search([('urgent_record_id.id', '=', self.id)], limit=1, order='id desc')
                request.urgent_ids = urgent_id
                
    def _compute_urgent_action_id(self):
       for request in self:
                urgent_action_id = self.env['urgent.action'].search([('request_record_id.id', '=', self.id)], limit=1, order='id desc')
                request.urgent_action_ids = urgent_action_id
              
    @api.depends('pengajuan_barang_ids.harga_sub_total')
    def _compute_amounts_pb(self):
        for request in self:
            pb_records = request.pengajuan_barang_ids
            amount_pb = sum(pb_records.mapped('harga_sub_total'))

            request.amount_total_pb = amount_pb

    @api.depends_context('uid')
    def _check_group_internal(self):
        if self.env.user.has_group('maintenance.group_internal_admin') and self.maintenance_team_id.id == 1:
            self.check_group_internal = 'True'

        else:
            self.check_group_internal = None

    @api.depends_context('uid')
    def _check_group_sc(self):
        if self.env.user.has_group('maintenance.group_sc_admin') and self.maintenance_team_id.id == 2:
            self.check_group_sc = 'True'

        else:
            self.check_group_sc = None

    @api.depends_context('uid')
    def _check_group_tech_ops(self):
        if self.env.user.has_group('maintenance.group_pj_admin') and self.maintenance_team_id.id == 6:
            self.check_group_tech_ops = 'True'

        else:
            self.check_group_tech_ops = None

    def _check_group_tech_ops_pb(self):
        if self.env.user.has_group('maintenance.group_tech_ops_pb_admin') and self.maintenance_team_id.id == 3:
            self.check_group_tech_ops_pb = 'True'

        else:
            self.check_group_tech_ops_pb = None

    @api.depends_context('uid')
    def _check_group_ga(self):
        if self.env.user.has_group('maintenance.group_ga_admin') and self.maintenance_team_id.id == 4:
            self.check_group_ga = 'True'

        else:
            self.check_group_ga = None

    @api.depends_context('uid')
    def _check_group_finance(self):
        if self.env.user.has_group('maintenance.group_finance_admin') and self.maintenance_team_id.id == 5:
            self.check_group_finance = 'True'

        else:
            self.check_group_finance = None
            
    def _check_group_product(self):
        if self.env.user.has_group('maintenance.group_product'):
            self.check_group_product = 'True'

        else:
            self.check_group_product = None

    @api.depends_context('uid')
    def _check_group_approval(self):
        if self.env.user.has_group('maintenance.group_approval_admin') and self.is_approval_stage:
            self.check_approval = 'True'

        else:
            self.check_approval = None
            
    def _check_report_bs_submit(self):
        if self.nomor_report.is_early_stage or not self.nomor_report:
            self.is_report_bs_submit = None
        
        else:
            self.is_report_bs_submit = 'True'

class MaintenanceTeam(models.Model):
    _name = 'maintenance.team'
    _description = 'Maintenance Teams'
    _order = "sequence, id"

    name = fields.Char('Team Name', required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company',
                                 default=lambda self: self.env.company)
    member_ids = fields.Many2many(
        'res.users', 'maintenance_team_users_rel', string="Team Members",
        domain="[('company_ids', 'in', company_id)]")
    color = fields.Integer("Color Index", default=0)
    request_ids = fields.One2many('maintenance.request', 'maintenance_team_id', copy=False)
    equipment_ids = fields.One2many('maintenance.equipment', 'maintenance_team_id', copy=False)
    sequence = fields.Integer(string='Sequence')

    # For the dashboard only
    todo_request_ids = fields.One2many('maintenance.request', string="Requests", copy=False,
                                       compute='_compute_todo_requests')
    todo_request_count = fields.Integer(string="Number of Requests", compute='_compute_todo_requests')
    todo_request_count_date = fields.Integer(string="Number of Requests Scheduled", compute='_compute_todo_requests')
    todo_request_count_high_priority = fields.Integer(string="Number of Requests in High Priority",
                                                      compute='_compute_todo_requests')
    todo_request_count_block = fields.Integer(string="Number of Requests Blocked", compute='_compute_todo_requests')
    todo_request_count_unscheduled = fields.Integer(string="Number of Requests Unscheduled",
                                                    compute='_compute_todo_requests')
    todo_request_created_by_me = fields.Integer(string="Number of Active Request Created By Me", compute='_compute_req_create_by_me')
    todo_request_sales_by_me = fields.Integer(string="Number of Active Request Sales By Me", compute='_compute_req_create_by_me')
    todo_request_sales_create = fields.Integer(string="Number of Active Request", compute='_compute_req_create_by_me')
    todo_request_assign_to_me = fields.Integer(string="Number of Active Request Assign to Me", compute='_compute_req_assign_to_me')
    todo_request_my_project = fields.Integer(string="Number of My Project", compute='_compute_req_create_by_me')
    todo_request_my_approver = fields.Integer(string="Number of Approval Request", compute='_compute_req_create_by_me')
    todo_request_no_bs_report = fields.Integer(string="Number of Unreported BS", compute='_compute_req_create_by_me')
    # Compute GA Fields
    todo_ga_count = fields.Integer(string="Number of Requests", compute='_compute_ga')
    todo_my_ga_count = fields.Integer(string="Number of My Requests", compute='_compute_ga')
    todo_my_assign_ga_count = fields.Integer(string="Number of Active Request Assign to Me", compute='_compute_ga')
    # Compute PJ Fields
    todo_pj_count = fields.Integer(string="Number of Requests", compute='_compute_pj')
    todo_my_pj_count = fields.Integer(string="Number of My Requests", compute='_compute_pj')
    todo_my_assign_pj_count = fields.Integer(string="Number of Active Request Assign to Me", compute='_compute_pj')
    todo_my_project_pj_count = fields.Integer(string="Number of My Project", compute='_compute_pj')
    todo_my_approve_pj_count = fields.Integer(string="Number of Active Request Assign to Me", compute='_compute_pj')
    todo_need_pic_report_count = fields.Integer(string="Number of Request Need PIC Report", compute='_compute_pj')
    
    @api.depends('request_ids.is_done')
    def _compute_todo_requests(self):
        for team in self:
            team.todo_request_ids = self.env['maintenance.request'].search(
                [('maintenance_team_id', '=', team.id), ('is_done', '=', False), ('archive', '=', False)])
            data = self.env['maintenance.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('is_done', '=', False), ('archive', '=', False)],
                ['schedule_date:year', 'priority', 'kanban_state', ],
                ['__count']
            )
            team.todo_request_count = sum(count for (_, _, _, count) in data)
            team.todo_request_count_date = sum(count for (schedule_date, _, _, count) in data if schedule_date)
            team.todo_request_count_high_priority = sum(count for (_, priority, _, count) in data if priority == 3)
            team.todo_request_count_block = sum(
                count for (_, _, kanban_state, count) in data if kanban_state == 'blocked')
            team.todo_request_count_unscheduled = team.todo_request_count - team.todo_request_count_date
            

    @api.depends('request_ids.dummy_save')
    def _compute_req_create_by_me(self):
        for team in self:
            team.todo_request_ids = self.env['maintenance.request'].search(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('issuer', '=', self.env.uid)])
            data1 = self.env['maintenance.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('is_done', '=', False), ('issuer', '=', self.env.uid)],
                ['schedule_date:year', 'priority', 'kanban_state', ],
                ['__count']
            )
            data2 = self.env['maintenance.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('is_done', '=', False), ('sales', '=', self.env.uid)],
                ['schedule_date:year', 'priority', 'kanban_state', ],
                ['__count']
            )
            data3 = self.env['maintenance.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('is_done', '=', False), ('approval_user.user_ids', '=', self.env.uid)],
                ['schedule_date:year', 'priority', 'kanban_state', ],
                ['__count']
            )
            data4 = self.env['maintenance.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('req_finance_type', '=', 'bs'), ("is_reported", "=", False), ("archive", "=", False)],
                ['schedule_date:year', 'priority', 'kanban_state', ],
                ['__count']
            )

            team.todo_request_my_project = sum(count for (_, _, _, count) in data2)
            team.todo_request_my_approver = sum(count for (_, _, _, count) in data3)
            team.todo_request_created_by_me = sum(count for (_, _, _, count) in data1)
            team.todo_request_sales_by_me = sum(count for (_, _, _, count) in data2)
            team.todo_request_no_bs_report = sum(count for (_, _, _, count) in data4)
            team.todo_request_sales_create = team.todo_request_created_by_me + team.todo_request_sales_by_me

    @api.depends('request_ids.stage_id.done')
    def _compute_req_assign_to_me(self):
        for team in self:
            team.todo_request_ids = self.env['maintenance.request'].search(
                [('maintenance_team_id', '=', team.id), ('stage_id.done', '=', False), ('archive', '=', False), ('assignee', '=', self.env.uid)])
            data = self.env['maintenance.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('stage_id.done', '=', False), ('archive', '=', False), ('assignee', '=', self.env.uid)],
                ['schedule_date:year', 'priority', 'kanban_state', ],
                ['__count']
            )

            team.todo_request_assign_to_me = sum(count for (_, _, _, count) in data)
            
    @api.depends('request_ids.dummy_save')
    def _compute_ga(self):
        for team in self:            
            data1 = self.env['general.affair.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False)],
                ['finish_date:year', 'archive', 'stage_ga_id', ],
                ['__count']
            )
            data2 = self.env['general.affair.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('requester', '=', self.env.uid)],
                ['finish_date:year', 'archive', 'stage_ga_id', ],
                ['__count']
            )
            data3 = self.env['general.affair.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('assignee', '=', self.env.uid)],
                ['finish_date:year', 'archive', 'stage_ga_id', ],
                ['__count']
            )

            team.todo_ga_count = sum(count for (_, _, _, count) in data1)
            team.todo_my_ga_count = sum(count for (_, _, _, count) in data2)
            team.todo_my_assign_ga_count = sum(count for (_, _, _, count) in data3)
            
    @api.depends('request_ids.dummy_save')
    def _compute_pj(self):
        for team in self:            
            data1 = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('stage_pj_id', '!=', 7)],
                ['finish_date:year', 'archive', 'stage_pj_id', ],
                ['__count']
            )
            data2 = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('requester', '=', self.env.uid)],
                ['finish_date:year', 'archive', 'stage_pj_id', ],
                ['__count']
            )
            data3a = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('presales.name', '=', self.env.uid)],
                ['finish_date:year', 'archive', 'stage_pj_id', ],
                ['__count']
            )
            data3b = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('teknisi', '=', self.env.uid)],
                ['finish_date:year', 'archive', 'stage_pj_id', ],
                ['__count']
            )
            data4 = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('sales', '=', self.env.uid)],
                ['finish_date:year', 'archive', 'stage_pj_id', ],
                ['__count']
            )            
            data5 = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('approver.user_ids', 'ilike', self.env.uid), ('stage_pj_id', '=', 3)],
                ['finish_date:year', 'archive', 'stage_pj_id', ],
                ['__count']
            )
            data6 = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('archive', '=', False), ('pic_report', '=', False), ('activity_type', 'in', [12,16])],
                ['finish_date:year', 'archive', 'stage_pj_id', ],
                ['__count']
            )
            

            team.todo_pj_count = sum(count for (_, _, _, count) in data1)
            team.todo_my_pj_count = sum(count for (_, _, _, count) in data2)
            data_3a = sum(count for (_, _, _, count) in data3a)
            data_3b = sum(count for (_, _, _, count) in data3b)
            team.todo_my_assign_pj_count = data_3a + data_3b
            team.todo_my_project_pj_count = sum(count for (_, _, _, count) in data4)
            team.todo_my_approve_pj_count = sum(count for (_, _, _, count) in data5)
            team.todo_need_pic_report_count = sum(count for (_, _, _, count) in data6)

    @api.depends('equipment_ids')
    def _compute_equipment(self):
        for team in self:
            team.equipment_count = len(team.equipment_ids)

class ApprovalList(models.Model):
    _name = 'approval.list'
    _description = 'Approval List'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']

    name = fields.Many2one('res.users', default=lambda self: self.env.uid)
    is_approved = fields.Boolean(string='Approved')
    approved_date = fields.Datetime(string='Approved Date', default=lambda self: fields.datetime.now())
    note = fields.Char(string='Note')
    match_check_approve = fields.Boolean(string='Match')
    approval_id = fields.Many2one('maintenance.request', string='Approval List')
    approval_pj_id = fields.Many2one('pengajuan.jasa', string='Approval List')
    last_user_approve = fields.Many2one('res.users', string='Last User')
    description = fields.Html('Description')
    check_approval_line = fields.Boolean(string='User is Match')
    test = fields.Boolean(string='Test')

class BSRecord(models.Model):
    _name = 'bs.record'
    _description = 'BS Record'

    start_date = fields.Date(string='Start Date')
    end_date = fields.Date(string='End Date')
    origin_city = fields.Char(string='Origin City')
    destination_city = fields.Char(string='Destination')
    sales = fields.Many2one('res.users', string='Sales')
    no_ppc = fields.Char(string='No Oppty / Paket')
    agenda = fields.Char(string='Agenda')
    name = fields.Many2one('expense.category', string='Category')
    jenis = fields.Many2one('expense.sub.cat', string='Jenis')
    nominal = fields.Monetary(string='Nominal')
    bs_id = fields.Many2one('maintenance.request', string='BS ID')
    currency_id = fields.Many2one('res.currency', related='bs_id.currency_id')
    display_type = fields.Selection(
        selection=[
            ('line_section', "Section"),
            ('line_note', "Note"),
        ],
        default=False)
        
    @api.onchange('jenis')
    def _write_main_cat_id(self):
        if not self.jenis.main_cat_id:
            self.jenis.main_cat_id = self.name.id            

class ReportRecord(models.Model):
    _name = 'report.record'
    _description = 'Report Record'

    start_date = fields.Date(string='Start Date')
    end_date = fields.Date(string='End Date')
    sales = fields.Many2one('res.users', string='Sales')
    no_ppc = fields.Char(string='No Oppty / Paket')
    name = fields.Many2one('expense.category', string='Kategori')
    jenis = fields.Many2one('bs.jenis', string='Jenis')
    vendor = fields.Char(string='Vendor')
    description = fields.Char(string='Description')
    nominal_bs = fields.Monetary(string='Uang BS')
    nominal_pribadi = fields.Monetary(string='Uang Pribadi')
    nominal_total = fields.Monetary(string='Total Nominal', compute='_compute_nominal_total')
    report_id = fields.Many2one('maintenance.request', string='Report ID')
    currency_id = fields.Many2one('res.currency', related='report_id.currency_id')
    display_type = fields.Selection(
        selection=[
            ('line_section', "Section"),
            ('line_note', "Note"),
        ],
        default=False)

    @api.depends('nominal_bs', 'nominal_pribadi')
    def _compute_nominal_total(self):
        for rec in self:
            rec.nominal_total = - rec.nominal_bs + rec.nominal_pribadi

class PengajuanBarang(models.Model):
    _name = 'pengajuan.barang'
    _description = 'Pengajuan Barang'

    name = fields.Char(string='Nama Barang / Pekerjaan')
    vendor = fields.Char(string='Vendor')
    qty = fields.Float(string='Quantity')
    satuan = fields.Char(string='Satuan')
    harga_satuan = fields.Monetary(string='Harga Satuan')
    no_po = fields.Char(string='No PO')
    harga_sub_total = fields.Monetary(string='Sub Total', compute='_compute_sub_total')
    pengajuan_barang_id = fields.Many2one('maintenance.request', string='Pengajuan barang ID')
    currency_id = fields.Many2one('res.currency', related='pengajuan_barang_id.currency_id')

    @api.depends('qty', 'harga_satuan')
    def _compute_sub_total(self):
        for rec in self:
            rec.harga_sub_total = rec.harga_satuan * rec.qty

class BSJenis(models.Model):
    _name = 'bs.jenis'
    _description = 'BS Jenis'
    _order = "id desc"

    name = fields.Char(string='Name')
    sequence = fields.Integer(string='Sequence')

class ExpenseCategory(models.Model):
    _name = 'expense.category'
    _description = 'Expense Category'
    _order = "sequence, id desc"

    name = fields.Char(string='Name')
    sub_category = fields.Many2many('expense.sub.cat', string='Jenis')
    sequence = fields.Integer(string='Sequence')
    
    @api.onchange('sub_category')
    def _write_main_cat_id(self):
        if not self.sub_category.main_cat_id:
            self.sub_category.main_cat_id = self.id

class ExpenseSubCategory(models.Model):
    _name = 'expense.sub.cat'
    _description = 'Expense Sub Category'
    _order = "sequence, id desc"

    name = fields.Char(string='Name')
    main_cat_id = fields.Many2one('expense.category', string='Main Category')
    sequence = fields.Integer(string='Sequence')

class Brand(models.Model):
    _name = 'brand'
    _description = 'Brand'
    _order = "sequence, id"

    name = fields.Char(string='Brand')
    sequence = fields.Integer(string='Sequence')
    
class Type(models.Model):
    _name = 'unit.type'
    _description = 'Unit Type'
    _order = "sequence, id"

    name = fields.Char(string='Unit Type')
    sequence = fields.Integer(string='Sequence')

class Bendera(models.Model):
    _name = 'bendera'
    _description = 'Bendera'
    _order = "sequence, id"

    name = fields.Char(string='Bendera')
    full_name = fields.Char(string='Nama')
    sequence = fields.Integer(string='Sequence')

class StagesFinance(models.Model):
    _name = 'stages.finance'
    _description = 'Stages Finance'
    _order = "sequence, id"

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')
    approved = fields.Boolean('Request Approved')

class StagesSC(models.Model):
    _name = 'stages.sc'
    _description = 'Stages SC'
    _order = "sequence, id"

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')
    approved = fields.Boolean('Request Approved')

class StagesOpsTech(models.Model):
    _name = 'stages.ops.tech'
    _description = 'Stages Services'
    _order = "sequence, id"

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')
    approved = fields.Boolean('Request Approved')

class StagesOpsTechPB(models.Model):
    _name = 'stages.ops.tech.pb'
    _description = 'Stage Penganjuan Barang'
    _order = "sequence, id"

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')
    approved = fields.Boolean('Request Approved')

class StagesGeneralAffair(models.Model):
    _name = 'stages.general.affair'
    _description = 'Stages General Affair'
    _order = "sequence, id"

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')
    approved = fields.Boolean('Request Approved')   