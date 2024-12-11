    # -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, tools, SUPERUSER_ID, _, _lt


class Lead(models.Model):
    _inherit = 'crm.lead'

    request_id = fields.Many2one('pengajuan.jasa', string='Request ID')    
    pj_ids = fields.One2many('pengajuan.jasa', 'crm_id', string='List Pengajuan Jasa')    
    
class PresalesActivity(models.Model):
    _inherit = 'crm.presales.activity'

    request_id = fields.Many2one('pengajuan.jasa', string='Request ID')
    
class MaintenanceTeam(models.Model):
    _inherit = 'maintenance.team'

    need_sales_review = fields.Integer(string="Number of Need Review", compute='_compute_sales_review_count')
    
    @api.depends('request_ids.dummy_save')
    def _compute_sales_review_count(self):
        for team in self:            
            data1 = self.env['pengajuan.jasa']._read_group(
                [('maintenance_team_id', '=', team.id), ('sales', '=', self.env.uid), ('stage_pj_id', '=', [10])],
                ['create_date:year', 'stage_pj_id', 'sales', ],
                ['__count']
            )     

            team.need_sales_review = sum(count for (_, _, _, count) in data1)