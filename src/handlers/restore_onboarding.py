"""
POST /onboarding/restore - "Undo move", step one of un-promoting a staff record.

Body: {"employeeId": "E1024"}. First step of un-promote:

    1. POST /onboarding/restore                                          (this file)
    2. (intern only) DELETE /staff/employees/{managerId}/interns/{id}    (handlers/remove_manager_intern.py)
    3. DELETE /staff/employees/{id}  or  DELETE /staff/interns/{id}       (handlers/delete_staff_employee.py / delete_staff_intern.py)

Available for UNPROMOTE_WINDOW_DAYS (7) from `onboardedAt` - see
common/models.unpromote_window_open. Past the window this returns 409 and there
is no other route back; a mistaken promotion older than a week needs table
access, which is the trade this design explicitly makes (see
docs/database-design.md#promotion) in exchange for not needing a full
compensation/rollback system for the common case.

Writes the checklist and every HR comment on it back exactly as they were -
restored_item() copies the whole thing rather than starting the person over,
which is the point of restoring rather than just re-creating them.

Idempotent in the sense that matters here: calling it twice is a 409 the second
time (the onboarding row already exists), which is the correct "nothing left to
do" answer, not a failure the caller has to distinguish from a real one.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import onboarding_table
from common.handler import (
    api_handler,
    is_condition_failure,
    parse_body,
    require_official,
)
from common.keys import KEY_ATTRIBUTE, NOT_EXISTS, pk
from common.models import (
    UNPROMOTE_WINDOW_DAYS,
    clean_employee_id,
    restored_item,
    to_api_employee,
    unpromote_window_open,
)
from common.repository import load_staff_record

_ID_REQUIRED = 'employeeId is required.'

_WINDOW_CLOSED = ('This move can no longer be undone - it happened more than ' +
                   str(UNPROMOTE_WINDOW_DAYS) + ' days ago.')


@api_handler
def lambda_handler(event, context):
    require_official(event)

    body = parse_body(event)
    employee_id = clean_employee_id(body.get('employeeId'))
    if not employee_id:
        return responses.bad_request(_ID_REQUIRED, {'employeeId': _ID_REQUIRED})

    staff_record, source = load_staff_record(employee_id, consistent=True)
    if staff_record is None:
        return responses.not_found('No employee with id ' + employee_id + ' on either staff dashboard.')

    now = datetime.now(timezone.utc)
    if not unpromote_window_open(staff_record['onboardedAt'], now):
        return responses.conflict(_WINDOW_CLOSED)

    item = restored_item(staff_record, now)
    item[KEY_ATTRIBUTE] = pk(employee_id)

    try:
        onboarding_table.put_item(Item=item, ConditionExpression=NOT_EXISTS)
    except ClientError as error:
        if is_condition_failure(error):
            return responses.conflict(employee_id + ' is already back on the onboarding dashboard.')
        raise

    return responses.created(to_api_employee(item), '/employees/' + employee_id)
