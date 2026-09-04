"""
POST /staff/interns - step one of "Move to intern dashboard" for an intern.

Three-step frontend sequence, this endpoint being the first:

    1. POST /staff/interns                              (this file)
    2. POST /staff/employees/{managerId}/interns         (handlers/add_manager_intern.py)
    3. DELETE /onboarding/{id}                            (handlers/delete_onboarding_record.py)

Same reasoning as promote_to_employee.py for why this is three small endpoints
and not one transaction: see docs/database-design.md#promotion. Each step is
idempotent and the destructive one (3) is last and gated on this one having
already run, so re-running the sequence from step 1 after any interruption is
always safe.

Requires reportingManagerId, and validates it names a live EmployeeTable item
whose entityType is 'Employee' - not merely a live item - before writing
anything. EmployeeTable holds both employees and interns now, so "the id
resolves to a record in EmployeeTable" is no longer proof the manager is an
employee: without the entityType check here, one intern could be named as
another intern's reporting manager, which the whole manager mapping exists to
rule out. The only fact an onboarding record has about a manager (the
free-text `manager` string) is not enough to establish any of this on its own.
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
from common.keys import KEY_ATTRIBUTE, NOT_EXISTS, key, pk
from common.models import (
    clean_employee_id,
    is_employee_item,
    is_intern,
    promoted_item,
    to_api_intern,
)
from common.repository import load_employee

_ID_REQUIRED = 'employeeId is required.'

_NOT_ONBOARDED = ('This intern has not finished onboarding yet ({done} of '
                   '{total} items complete). Finish the checklist before moving them.')

_WRONG_TYPE = ('This person is not an intern. Use "Move to main employee '
               'dashboard" instead of the intern dashboard.')

_MANAGER_REQUIRED = 'Pick the reporting manager before moving this intern.'

_MANAGER_NOT_FOUND = ('That reporting manager could not be found on the employee '
                       'dashboard. They must be promoted as an employee first.')


@api_handler
def lambda_handler(event, context):
    require_official(event)

    body = parse_body(event)
    employee_id = clean_employee_id(body.get('employeeId'))
    if not employee_id:
        return responses.bad_request(_ID_REQUIRED, {'employeeId': _ID_REQUIRED})

    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    if not is_intern(employee):
        return responses.bad_request(_WRONG_TYPE, {'employmentType': _WRONG_TYPE})

    if employee['status'] != 'Onboarded':
        progress = employee['progress']
        message = _NOT_ONBOARDED.format(done=progress['done'], total=progress['total'])
        return responses.conflict(message)

    reporting_manager_id = clean_employee_id(body.get('reportingManagerId'))
    if not reporting_manager_id:
        return responses.bad_request(_MANAGER_REQUIRED, {'reportingManagerId': _MANAGER_REQUIRED})

    # The manager has to already be a promoted employee - never onboarding,
    # and never an intern, which is exactly what sharing one table with
    # interns means this check has to name explicitly now.
    manager = employee_table.get_item(Key=key(reporting_manager_id)).get('Item')
    if manager is None or not is_employee_item(manager):
        return responses.bad_request(
            _MANAGER_NOT_FOUND, {'reportingManagerId': _MANAGER_NOT_FOUND})

    now = datetime.now(timezone.utc)
    item = promoted_item(employee, now, reporting_manager_id=reporting_manager_id)
    item[KEY_ATTRIBUTE] = pk(employee_id)

    try:
        employee_table.put_item(Item=item, ConditionExpression=NOT_EXISTS)
    except ClientError as error:
        if is_condition_failure(error):
            return responses.conflict(employee_id + ' is already on a staff dashboard.')
        raise

    return responses.created(to_api_intern(item), '/staff/interns/' + employee_id)
