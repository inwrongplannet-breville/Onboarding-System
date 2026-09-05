"""PUT /attendance/{employeeId}/{date} - HR's unrestricted correction route."""
from common import attendance, responses
from common.accounts import ROLE_OFFICIAL
from common.attendance_repository import get_attendance, put_attendance
from common.handler import (
    api_handler,
    caller_username,
    parse_body,
    path_param,
    require_official,
)
from common.models import clean_employee_id
from common.repository import find_record


@api_handler
def lambda_handler(event, context):
    require_official(event)
    employee_id = clean_employee_id(path_param(event, 'employeeId'))
    date_value = path_param(event, 'date')

    try:
        attendance.validate_date(date_value)
    except ValueError as error:
        return responses.bad_request(str(error), {'date': str(error)})

    profile, _ = find_record(employee_id, consistent=True)
    if profile is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    body = parse_body(event)
    try:
        submission = attendance.validate_submission(body)
    except ValueError as error:
        return responses.bad_request('Attendance is not valid.', error.args[0])

    now = attendance.utc_now()
    existing = get_attendance(employee_id, date_value, consistent=True)
    # A table-cell edit sends only a status. Preserve a note already attached
    # to the declaration unless HR deliberately names `note` to replace it.
    if existing and 'note' not in body:
        submission['note'] = existing.get('note', '')
    item = attendance.attendance_item(
        profile,
        date_value,
        submission,
        caller_username(event),
        ROLE_OFFICIAL,
        existing=existing,
        now=now,
    )
    put_attendance(item)
    return responses.ok({'attendance': attendance.record_view(item)})
