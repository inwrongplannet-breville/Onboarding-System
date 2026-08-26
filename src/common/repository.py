"""
The one way to read an employee back out of the table.

It exists for the `consistent` flag. DynamoDB reads are *eventually consistent by
default*, so a handler that writes and then re-reads to build its response can
legitimately be handed the pre-write values - and PUT and PATCH both do exactly
that. The UI then repaints from that response (paintChecklist in js/app.js), so a
stale read shows the user a checkbox snapping back to where it was.

GET pays no such price and stays eventually consistent: nothing it returns was
written a millisecond earlier by the same caller, and consistent reads cost twice
as much.
"""
from common import responses
from common.db import table
from common.keys import EXISTS, key
from common.models import ARCHIVED_MESSAGE, to_api_employee

# The condition every profile write in this codebase shares: the employee has to
# exist and must not be archived.
#
# UpdateItem *upserts* by default, so without the EXISTS half a write to an
# unknown id would happily create a half-employee with no checklist behind it.
# The other half is the archive freeze - checked as a condition on the write
# itself rather than by reading first, so an archive landing mid-request cannot
# be overwritten.
#
# Spelled with an alias because `archivedAs` travels in the same
# ExpressionAttributeNames map as the SET clause it guards. Every caller must add
# '#archivedAs' to that map; DynamoDB rejects a names entry no expression uses,
# so the two only ever travel together.
ACTIVE_GUARD = EXISTS + ' AND attribute_not_exists(#archivedAs)'


def load_employee(employee_id, consistent=False):
    """The full API employee, or None if there is none."""
    result = table.get_item(
        Key=key(employee_id),
        ConsistentRead=consistent,
    )
    return to_api_employee(result.get('Item'))


def load_archive_state(employee_id):
    """
    Whether this employee exists and whether they are frozen, or None if there is
    no such employee. The one fact a failed conditional write needs in order to
    say which of 404 and 409 it was.

    A projection rather than the whole item - the caller does not want the
    employee, it wants to know why its write bounced.

    `employeeId` is in the projection and is not optional. An active employee has
    no `archivedAs` attribute at all, and a projection that names only absent
    attributes comes back with no `Item` - which would have this function report
    "no such employee" for someone who plainly exists. Projecting one
    always-present attribute alongside it is what keeps the None meaningful.

    Read consistently: this decides whether a write is rejected as frozen, and a
    stale read of that lets an edit land on an archived record.
    """
    result = table.get_item(
        Key=key(employee_id),
        ProjectionExpression='employeeId, #archivedAs',
        ExpressionAttributeNames={'#archivedAs': 'archivedAs'},
        ConsistentRead=True,
    )
    item = result.get('Item')
    if item is None:
        return None
    return {'archivedAs': item.get('archivedAs', '')}


def guard_failure_response(employee_id):
    """
    Turn a fired ACTIVE_GUARD into the right status code.

    Re-reads rather than guessing which half of the condition it was, because
    'archived' and 'never existed' are a 409 and a 404, and telling a caller the
    wrong one sends them looking in the wrong place.

    Shared by the two handlers that write a profile - HR's full replace and the
    employee's own contact patch. One condition expression, one decision about
    what its failure meant.
    """
    state = load_archive_state(employee_id)
    if state is not None and state['archivedAs']:
        return responses.conflict(ARCHIVED_MESSAGE)
    return responses.not_found('No employee with id ' + employee_id + '.')
