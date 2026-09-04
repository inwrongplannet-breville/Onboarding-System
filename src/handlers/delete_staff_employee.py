"""
DELETE /staff/employees/{id} - the destructive last step of un-promoting a
non-intern.

Step three of un-promote (see restore_onboarding.py for the sequence). Refuses
with 409 unless BOTH:

  - the onboarding row already exists again (POST /onboarding/restore has
    already run) - without this check, this route is an unrestricted "delete
    any employee record" endpoint with no copy anywhere first;
  - the promotion is still inside the UNPROMOTE_WINDOW_DAYS undo window -
    checked here too, even though restore_onboarding.py already checked it,
    because a call arriving out of order (this before that) must not slip past
    on a stale assumption that the window was open when restore ran.

Idempotent: an id that is not in EmployeeTable at all is 200, on the same
reasoning as delete_onboarding_record.py - already gone is success, not a
special case.

Also checks `is_employee_item` before deleting anything: EmployeeTable holds
interns too now, and an intern's id must 404 here rather than be deleted
through the wrong route - see delete_staff_intern.py for the mirror image.
"""
from datetime import datetime, timezone

from common import responses
from common.db import employee_table
from common.handler import api_handler, employee_id_param, require_official
from common.keys import key
from common.models import UNPROMOTE_WINDOW_DAYS, is_employee_item, unpromote_window_open
from common.repository import load_employee

_NOT_RESTORED_YET = 'Restore this employee to the onboarding dashboard before removing them here.'

_WINDOW_CLOSED = ('This record can no longer be removed here - the move happened '
                   'more than ' + str(UNPROMOTE_WINDOW_DAYS) + ' days ago.')


@api_handler
def lambda_handler(event, context):
    require_official(event)

    employee_id = employee_id_param(event)

    staff_item = employee_table.get_item(Key=key(employee_id), ConsistentRead=True).get('Item')
    if staff_item is None:
        # Already removed - idempotent success.
        return responses.ok({'id': employee_id})
    if not is_employee_item(staff_item):
        return responses.not_found('No employee with id ' + employee_id + '.')

    now = datetime.now(timezone.utc)
    if not unpromote_window_open(staff_item.get('onboardedAt', ''), now):
        return responses.conflict(_WINDOW_CLOSED)

    onboarding_record = load_employee(employee_id, consistent=True)
    if onboarding_record is None:
        return responses.conflict(_NOT_RESTORED_YET)

    employee_table.delete_item(Key=key(employee_id))

    return responses.ok({'id': employee_id})
