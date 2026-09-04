"""GET /attendance/sheet.csv - HR's monthly sheet as a CSV attachment."""
import csv
import io

from common import attendance, responses
from common.attendance_repository import attendance_roster, query_month
from common.handler import api_handler, require_official


def _safe_cell(value):
    """Prevent profile text from becoming a spreadsheet formula."""
    text = str(value or '')
    if text.startswith(('=', '+', '-', '@')):
        return "'" + text
    return text


@api_handler
def lambda_handler(event, context):
    require_official(event, 'Only HR can download the attendance sheet.')
    now = attendance.utc_now()
    params = event.get('queryStringParameters') or {}
    month = params.get('month') or now.astimezone(attendance.BUSINESS_TIMEZONE).strftime('%Y-%m')
    try:
        attendance.validate_month(month)
    except ValueError as error:
        return responses.bad_request(str(error), {'month': str(error)})

    sheet = attendance.build_sheet(
        month,
        attendance_roster(),
        query_month(month),
        now=now,
    )
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(
        ['Employee ID', 'Employee name', 'Employee role', 'Department'] +
        sheet['days'] +
        ['Present total', 'Work from home total', 'Leave total', 'Absent total']
    )
    for employee in sheet['employees']:
        writer.writerow([
            _safe_cell(employee['employeeId']),
            _safe_cell(employee['employeeName']),
            _safe_cell(employee['employeeRole']),
            _safe_cell(employee['department']),
        ] + [
            attendance.STATUS_LABELS.get(day['status'], '') for day in employee['days']
        ] + [
            employee['totals']['present'],
            employee['totals']['work_from_home'],
            employee['totals']['leave'],
            employee['totals']['absent'],
        ])

    return responses.csv_download(output.getvalue(), 'attendance-' + month + '.csv')
