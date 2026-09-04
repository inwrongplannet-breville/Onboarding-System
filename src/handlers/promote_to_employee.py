"""
POST /staff/employees - step one of "Move to main employee dashboard" for a
non-intern.

Individual endpoint by design, not a cross-table transaction - see
docs/database-design.md#promotion for why. This is the first of two calls the
frontend makes in sequence: this one copies the onboarding record into
EmployeeTable; DELETE /onboarding/{id} (handlers/delete_onboarding_record.py) is
the second, and it refuses to run until this one has already succeeded. That
ordering is the whole safety net - an interrupted sequence leaves someone on two
dashboards, never on none.

Gated on completion. The onboarding table holds unfinished onboarding and
EmployeeTable holds finished onboarding; an ungated promote would put a
half-ticked checklist on the employee dashboard with no route left to finish it,
since PATCH .../checklist only ever reaches the onboarding table.

Idempotent. A second call with the same id is a plain 409, not an error the
caller has to treat specially - re-running the whole promote sequence after a
partial failure is always safe (see js/store.js's promote() for the frontend
half of that contract).
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import employee_table
from common.handler import (
    api_handler,
    is_condition_failure,
    parse_body,
    require_official,
)
from common.keys import KEY_ATTRIBUTE, NOT_EXISTS, pk
from common.models import clean_employee_id, is_intern, promoted_item, to_api_staff_employee
from common.repository import load_employee

_ID_REQUIRED = 'employeeId is required.'

_NOT_ONBOARDED = ('This employee has not finished onboarding yet ({done} of '
                   '{total} items complete). Finish the checklist before moving them.')

_WRONG_TYPE = ('This person is an intern. Use "Move to intern dashboard" '
               'instead of the employee dashboard.')


@api_handler
def lambda_handler(event, context):
    require_official(event)

    body = parse_body(event)
    employee_id = clean_employee_id(body.get('employeeId'))
    if not employee_id:
        return responses.bad_request(_ID_REQUIRED, {'employeeId': _ID_REQUIRED})

    # Consistent: this decides whether the promotion is allowed to happen at
    # all, and a stale read here could wave through a checklist that has since
    # been un-ticked.
    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    if is_intern(employee):
        return responses.bad_request(_WRONG_TYPE, {'employmentType': _WRONG_TYPE})

    if employee['status'] != 'Onboarded':
        progress = employee['progress']
        message = _NOT_ONBOARDED.format(done=progress['done'], total=progress['total'])
        return responses.conflict(message)

    now = datetime.now(timezone.utc)
    item = promoted_item(employee, now)
    item[KEY_ATTRIBUTE] = pk(employee_id)

    try:
        employee_table.put_item(Item=item, ConditionExpression=NOT_EXISTS)
    except ClientError as error:
        if is_condition_failure(error):
            return responses.conflict(employee_id + ' is already on the employee dashboard.')
        raise

    return responses.created(to_api_staff_employee(item), '/staff/employees/' + employee_id)
