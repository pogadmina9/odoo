# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models, tools
from datetime import date, datetime, timedelta
import pytz


class PresalesKpiReport(models.Model):
    _name = 'report.dashboard_presales.report_name'
    _inherit = 'report.report_xlsx.abstract'

    def generate_xlsx_report(self, workbook, data, partners):
        for obj in partners:
            report_name = obj.presales.name.name
            # One sheet by partner
            sheet = workbook.add_worksheet(report_name[:31])
            data = obj._get_order_lines_to_report()
            bold = workbook.add_format({'bold': True})
            bold_right = workbook.add_format({'bold': True, 'align': 'right', 'border': 1})
            bold_center = workbook.add_format({'bold': True, 'align': 'center', 'border': 1})
            left = workbook.add_format({'align': 'left', 'border': 1})
            left.set_align('vcenter')
            percentage = workbook.add_format({'num_format': '0%', 'align': 'center', 'border': 1})
            percentage.set_align('center')
            percentage.set_align('vcenter')
            idr = workbook.add_format({'num_format': '_-[$Rp-en-ID]* #,##0_-;-[$Rp-en-ID]* #,##0_-;_-[$Rp-en-ID]* "-"_-;_-@_-', 'border': 1})
            idr.set_align('center')
            idr.set_align('vcenter')
            datetime = workbook.add_format({'num_format': '[$-id-ID]dd mmmm yyyy hh:mm;@', 'border': 1})
            datetime.set_align('center')
            datetime.set_align('vcenter')
            date = workbook.add_format({'num_format': '[$-id-ID]dd mmmm yyyy;@', 'border': 1})
            date.set_align('center')
            date.set_align('vcenter')
            number = workbook.add_format({'num_format': '0', 'border': 1})
            number.set_align('center')
            number.set_align('vcenter')
            number_2 = workbook.add_format({'num_format': '0.00', 'border': 1, 'align': 'center'})
            center = workbook.add_format({'align': 'center', 'border': 1})
            cell_format = workbook.add_format()
            cell_format.set_align('center')
            cell_format.set_align('vcenter')
            bold_ylw = workbook.add_format({'align': 'center', 'border': 1, 'bg_color': 'yellow', 'bold': True})
            percentage_ylw = workbook.add_format({'align': 'center', 'border': 1, 'bg_color': 'yellow', 'bold': True, 'num_format': '0%'})
            border_ylw = workbook.add_format({'align': 'center', 'border': 1, 'bg_color': 'yellow'})
            border = workbook.add_format({'border': 1})
            border.set_align('center')
            border.set_align('vcenter')
            border_wrap = workbook.add_format({'border': 1, 'text_wrap': True})
            border_wrap.set_align('center')
            border_wrap.set_align('vcenter')
            sheet.set_column(0, 0, 29, cell_format)
            sheet.set_column(1, 1, 25, cell_format)
            sheet.set_column(2, 2, 23, cell_format)
            sheet.set_column(3, 3, 25, cell_format)
            sheet.set_column(4, 4, 25, cell_format)
            sheet.set_column(5, 5, 20, cell_format)
            sheet.set_column(6, 6, 25, cell_format)
            sheet.write(0, 0, obj.name, bold)            
            sheet.write(1, 0, '')
            format_1 = workbook.add_format({'bold': True, 'align': 'center', 'border': 1})
            sheet.merge_range('A3:D3', 'KPI Report', bold_ylw)
            sheet.write(3, 0, 'Parameter', border_ylw)
            sheet.write(3, 1, 'Target', border_ylw)
            sheet.write(3, 2, 'Achievement', border_ylw)
            sheet.write(3, 3, 'KPI Percentage', border_ylw)
            sheet.write(4, 0, 'Team Revenue', border)
            sheet.write(4, 1, obj.quarter.target_revenue_presales, idr)
            sheet.write(4, 2, obj.total_revenue, idr)            
            sheet.write(4, 3, obj.total_revenue_kpi, percentage)
            row = 5
            if self.env['kpi.parameter'].search([('name', 'ilike', 'Total POC'), ('quarter_id.id', '=', obj.quarter.id)]):
                sheet.write(row, 2, obj.total_poc_presentation, border)            
                sheet.write(row, 3, obj.total_poc_presentation_kpi, percentage)
                row +=1
            if self.env['kpi.parameter'].search([('name', 'ilike', 'Conversion of POC'), ('quarter_id.id', '=', obj.quarter.id)]):
                sheet.write(row, 2, obj.conversion_poc_presentation, border)            
                sheet.write(row, 3, obj.conversion_poc_presentation_kpi, percentage)
                row +=1
            if self.env['kpi.parameter'].search([('name', 'ilike', 'Certification'), ('quarter_id.id', '=', obj.quarter.id)]):
                sheet.write(row, 2, obj.total_certification, border)            
                sheet.write(row, 3, obj.total_certification_kpi, percentage)
                row +=1
            if self.env['kpi.parameter'].search([('name', 'ilike', 'Rating'), ('quarter_id.id', '=', obj.quarter.id)]):
                sheet.write(row, 2, obj.sales_feedback, number_2)            
                sheet.write(row, 3, obj.sales_feedback_kpi, percentage)
                row +=1
            if self.env['kpi.parameter'].search([('name', 'ilike', 'Solution'), ('quarter_id.id', '=', obj.quarter.id)]):
                sheet.write(row, 2, obj.total_solution, border)            
                sheet.write(row, 3, obj.total_solution_kpi, percentage)
                row +=1
            sheet.write(row, 2, 'Total', bold_ylw)
            sheet.write(row, 3, obj.total_kpi, percentage_ylw)
            row = 5            
            if len(obj.quarter.kpi_parameter_ids) > 0:
                sheet.write(row, 0, obj.quarter.kpi_parameter_ids[0].name.name, border)
                sheet.write(row, 1, obj.quarter.kpi_parameter_ids[0].target, border)
                for rec in obj.quarter.kpi_parameter_ids[1:]:
                    row +=1
                    sheet.write(row, 0, rec.name.name, border)
                    sheet.write(row, 1, rec.target, border)
            row +=1
            sheet.write(row, 0, '')
            row +=1
            sheet.write(row, 0, '')
            row +=1
            sheet.write(row, 0, 'Project Won', bold)
            row +=1
            sheet.write(row, 0, 'Nomor PPC', border_ylw)
            sheet.write(row, 1, 'Contract Amount', border_ylw)
            sheet.write(row, 2, 'Sales', border_ylw)
            sheet.write(row, 3, 'Customer', border_ylw)
            row +=1
            if len(obj.crm_ids) > 0:
                sheet.write(row, 0, obj.crm_ids[0].no_ppc, border)
                sheet.write(row, 1, obj.crm_ids[0].contract_amount, idr)
                sheet.write(row, 2, obj.crm_ids[0].user_id.name, border)
                sheet.write(row, 3, obj.crm_ids[0].partner_id.name, border_wrap)
                for rec in obj.crm_ids[1:]:
                    row +=1
                    sheet.write(row, 0, rec.no_ppc, border)
                    sheet.write(row, 1, rec.contract_amount, idr)
                    sheet.write(row, 2, rec.user_id.name, border)
                    sheet.write(row, 3, rec.partner_id.name, border_wrap)
            row +=1
            sheet.write(row, 0, '')
            row +=1
            sheet.write(row, 0, 'Demo / Presentation Conducted', bold)
            row +=1
            sheet.write(row, 0, 'Request ID', border_ylw)
            sheet.write(row, 1, 'Schedule Date', border_ylw)
            sheet.write(row, 2, 'Activity Type', border_ylw)
            sheet.write(row, 3, 'Customer', border_ylw)
            sheet.write(row, 4, 'Sales', border_ylw)
            sheet.write(row, 5, 'No PPC', border_ylw)
            sheet.write(row, 6, 'Job Details', border_ylw)
            user_tz = self.env.user.tz or 'UTC'  # Get user's timezone or use UTC as default
            tz = pytz.timezone(user_tz)
            if len(obj.pj_ids) > 0:
                row +=1
                sheet.write(row, 0, obj.pj_ids[0].name, border)
                sheet.write(row, 1, obj.pj_ids[0].schedule_date + timedelta(hours=7), datetime)
                sheet.write(row, 2, obj.pj_ids[0].activity_type.name, border)
                sheet.write(row, 3, obj.pj_ids[0].partner.name, border_wrap)
                sheet.write(row, 4, obj.pj_ids[0].sales.name, border)
                sheet.write(row, 5, obj.pj_ids[0].no_ppc, border)
                sheet.write(row, 6, obj.pj_ids[0].job_description, left)
                for rec in obj.pj_ids[1:]:
                    row +=1
                    sheet.write(row, 0, rec.name, border)
                    sheet.write(row, 1, rec.schedule_date + timedelta(hours=7), datetime)
                    sheet.write(row, 2, rec.activity_type.name, border)
                    sheet.write(row, 3, rec.partner.name, border_wrap)
                    sheet.write(row, 4, rec.sales.name, border)
                    sheet.write(row, 5, rec.no_ppc, border)
                    sheet.write(row, 6, rec.job_description, left)
            row +=1
            sheet.write(row, 0, '')
            row +=1
            sheet.write(row, 0, 'Training & Certification', bold)
            row +=1
            sheet.write(row, 0, 'Name', border_ylw)
            sheet.write(row, 1, 'Type', border_ylw)
            sheet.write(row, 2, 'Brand', border_ylw)
            sheet.write(row, 3, 'Category', border_ylw)
            sheet.write(row, 4, 'Issued Date', border_ylw)
            sheet.write(row, 5, 'Expiration Date', border_ylw)
            if len(obj.training_ids) > 0:
                row +=1
                sheet.write(row, 0, obj.training_ids[0].subject, border)
                sheet.write(row, 1, obj.training_ids[0].type)
                sheet.write(row, 2, obj.training_ids[0].brand.name, border)
                sheet.write(row, 3, obj.training_ids[0].category.name, border)
                sheet.write(row, 4, obj.training_ids[0].issued_date, date)
                sheet.write(row, 5, obj.training_ids[0].exp_date, date)
                for rec in obj.training_ids[1:]:
                    row +=1
                    sheet.write(row, 0, rec.subject, border)
                    sheet.write(row, 1, rec.type, border)
                    sheet.write(row, 2, rec.brand.name, border)
                    sheet.write(row, 3, rec.category.name, border)
                    sheet.write(row, 4, rec.issued_date, date)
                    sheet.write(row, 5, rec.exp_date, date)
            row +=1
            sheet.write(row, 0, '')
            row +=1
            sheet.write(row, 0, 'Solution Created', bold)
            row +=1
            sheet.write(row, 0, 'Title', border_ylw)
            sheet.write(row, 1, 'Created Date', border_ylw)
            sheet.write(row, 2, 'Industry', border_ylw)
            if len(obj.solutions_list_ids) > 0:
                row +=1
                sheet.write(row, 0, obj.solutions_list_ids[0].name, border)
                sheet.write(row, 1, obj.solutions_list_ids[0].create_date + timedelta(hours=7), datetime)
                sheet.write(row, 2,  ', '.join(obj.solutions_list_ids[0].industry.mapped('name')), left)
                for rec in obj.solutions_list_ids[1:]:
                    row +=1
                    sheet.write(row, 0, rec.name, border)
                    sheet.write(row, 1, rec.create_date + timedelta(hours=7), datetime)
                    sheet.write(row, 2,  ', '.join(obj.solutions_list_ids.industry.mapped('name')), left)
            row +=1
            sheet.write(row, 0, '')
            row +=1
            sheet.write(row, 0, 'Sales Feedback Review', bold)
            row +=1
            sheet.write(row, 0, 'Name', border_ylw)
            sheet.write(row, 1, 'Create Date', border_ylw)
            sheet.write(row, 2, 'Request ID', border_ylw)
            sheet.write(row, 3, 'Rating', border_ylw)
            sheet.write(row, 4, 'Comment', border_ylw)
            if len(obj.review_presales_ids) > 0:
                row +=1
                sheet.write(row, 0, obj.review_presales_ids[0].writer.name, border)
                sheet.write(row, 1, obj.review_presales_ids[0].create_date, datetime)
                sheet.write(row, 2, obj.review_presales_ids[0].request_id.name, border)
                sheet.write(row, 3, int(obj.review_presales_ids[0].rating), number)
                sheet.write(row, 4, obj.review_presales_ids[0].comment, border)
                for rec in obj.review_presales_ids[1:]:
                    row +=1
                    sheet.write(row, 0, rec.writer.name, border)
                    sheet.write(row, 1, rec.create_date + timedelta(hours=7), datetime)
                    sheet.write(row, 2, rec.request_id.name, border)
                    sheet.write(row, 3, int(rec.rating), number)
                    sheet.write(row, 4, rec.comment, border)
                
            
                
    
