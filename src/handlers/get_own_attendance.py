"""GET /attendance/me - the signed-in employee's monthly register."""
from common import attendance, responses
from common.accounts import ROLE_EMPLOYEE
from common.attendance_repository import query_employee_month
from common.handler import Forbidden, api_handler, caller_employee_id, require_role
from common.repository import find_record


@api_handler
def lambda_handler(event, context):
    if require_role(event) != ROLE_EMPLOYEE:
        raise Forbidden('This route shows an employee their own attendance.')

    employee_id = caller_employee_id(event)
    if not employee_id:
        raise Forbidden('This request carried no usable employee identity.')

    profile, _ = find_record(employee_id)
    if profile is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    now = attendance.utc_now()
    params = event.get('queryStringParameters') or {}
    month = params.get('month') or now.astimezone(attendance.BUSINESS_TIMEZONE).strftime('%Y-%m')
    try:
        attendance.validate_month(month)
    except ValueError as error:
        return responses.bad_request(str(error), {'month': str(error)})

    records = query_employee_month(employee_id, month)
    sheet = attendance.build_sheet(month, [profile], records, now=now)
    return responses.ok({
        'month': sheet['month'],
        'timezone': sheet['timezone'],
        'days': sheet['days'],
        'employee': sheet['employees'][0],
        'window': attendance.window_payload(now.astimezone(attendance.BUSINESS_TIMEZONE)),
    })
