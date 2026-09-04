"""GET /attendance/sheet - HR's complete monthly attendance register."""
from common import attendance, responses
from common.attendance_repository import attendance_roster, query_month
from common.handler import api_handler, require_official


@api_handler
def lambda_handler(event, context):
    require_official(event, 'Only HR can view the attendance sheet.')
    now = attendance.utc_now()
    params = event.get('queryStringParameters') or {}
    month = params.get('month') or now.astimezone(attendance.BUSINESS_TIMEZONE).strftime('%Y-%m')
    try:
        attendance.validate_month(month)
    except ValueError as error:
        return responses.bad_request(str(error), {'month': str(error)})

    return responses.ok(attendance.build_sheet(
        month,
        attendance_roster(),
        query_month(month),
        now=now,
    ))
