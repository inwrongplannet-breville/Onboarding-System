"""PUT /attendance/me/today - an employee's daily declaration."""
from common import attendance, responses
from common.accounts import ROLE_EMPLOYEE
from common.attendance_repository import get_attendance, put_attendance
from common.handler import (
    Forbidden,
    api_handler,
    caller_employee_id,
    parse_body,
    require_role,
)
from common.repository import find_record


@api_handler
def lambda_handler(event, context):
    if require_role(event) != ROLE_EMPLOYEE:
        raise Forbidden('Attendance on this route can only be marked by an employee.')

    employee_id = caller_employee_id(event)
    if not employee_id:
        raise Forbidden('This request carried no usable employee identity.')

    now = attendance.utc_now()
    local_now = now.astimezone(attendance.BUSINESS_TIMEZONE)
    if not attendance.marking_window_open(local_now):
        return responses.conflict(
            'Attendance can be marked from 8:30 AM until 6:00 PM Asia/Kolkata.'
        )

    profile, _ = find_record(employee_id, consistent=True)
    if profile is None:
        return responses.not_found('No employee with id ' + employee_id + '.')
    if profile.get('archived'):
        return responses.conflict('This employee is archived. Their attendance is read-only.')

    try:
        submission = attendance.validate_submission(parse_body(event))
    except ValueError as error:
        return responses.bad_request('Attendance is not valid.', error.args[0])

    attendance_date = local_now.date().isoformat()
    existing = get_attendance(employee_id, attendance_date, consistent=True)
    item = attendance.attendance_item(
        profile,
        attendance_date,
        submission,
        employee_id,
        ROLE_EMPLOYEE,
        existing=existing,
        now=now,
    )
    put_attendance(item)
    return responses.ok({
        'attendance': attendance.record_view(item),
        'window': attendance.window_payload(local_now),
    })
