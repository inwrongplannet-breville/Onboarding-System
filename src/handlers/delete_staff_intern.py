"""
DELETE /staff/interns/{id} - the destructive last step of un-promoting an
intern.

Mirrors delete_staff_employee.py exactly, against the same EmployeeTable and
the same key. See that file for why both guards - onboarding row restored,
still inside the undo window - are checked here too rather than trusted from
an earlier step in the sequence.

Also checks `is_intern_item` before deleting anything: the id could equally
resolve to an *employee* row in this shared table, and that must 404 here
rather than be silently deleted through the wrong route.

Deliberately does NOT touch the reporting manager's `interns` list - that link
is removed by a separate call to remove_manager_intern.py earlier in the
un-promote sequence (see restore_onboarding.py), so this endpoint only ever
needs to remove the intern's own record.
"""
from datetime import datetime, timezone

from common import responses
from common.db import employee_table
from common.handler import api_handler, employee_id_param, require_official
from common.keys import key
from common.models import UNPROMOTE_WINDOW_DAYS, is_intern_item, unpromote_window_open
from common.repository import load_employee

_NOT_RESTORED_YET = 'Restore this intern to the onboarding dashboard before removing them here.'

_WINDOW_CLOSED = ('This record can no longer be removed here - the move happened '
                   'more than ' + str(UNPROMOTE_WINDOW_DAYS) + ' days ago.')


@api_handler
def lambda_handler(event, context):
    require_official(event)

    employee_id = employee_id_param(event)

    staff_item = employee_table.get_item(Key=key(employee_id), ConsistentRead=True).get('Item')
    if staff_item is None:
        return responses.ok({'id': employee_id})
    if not is_intern_item(staff_item):
        return responses.not_found('No intern with id ' + employee_id + '.')

    now = datetime.now(timezone.utc)
    if not unpromote_window_open(staff_item.get('onboardedAt', ''), now):
        return responses.conflict(_WINDOW_CLOSED)

    onboarding_record = load_employee(employee_id, consistent=True)
    if onboarding_record is None:
        return responses.conflict(_NOT_RESTORED_YET)

    employee_table.delete_item(Key=key(employee_id))

    return responses.ok({'id': employee_id})
