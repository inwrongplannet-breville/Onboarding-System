"""
The ways to read a record back out of the tables.

It exists for the `consistent` flag. DynamoDB reads are *eventually consistent by
default*, so a handler that writes and then re-reads to build its response can
legitimately be handed the pre-write values - and PUT and PATCH both do exactly
that. The UI then repaints from that response (paintChecklist in js/app.js), so a
stale read shows the user a checkbox snapping back to where it was.

GET pays no such price and stays eventually consistent: nothing it returns was
written a millisecond earlier by the same caller, and consistent reads cost twice
as much.

Two tables now, not one. `load_employee` stays what it always was - an
onboarding-only read, for the handlers that only ever touch onboarding records
(PUT, the checklist PATCH, DELETE-as-archive). `find_record` is the new one: an
id no longer names a row in one obvious place, because promotion moves it. Every
handler that takes an id from the URL and does not already know which table it
lives in - GET /employees/{id}, the contact PATCH, the document upload check -
must go through find_record instead of assuming onboarding.

An employee and an intern share EmployeeTable and share its key shape, told apart
only by `entityType` (common/models.is_employee_item / is_intern_item) - see the
module docstring in common/keys.py for why. So "employee" and "intern" are two
`source` values this module reports, not two tables to reach for: `table_for()`
maps both to the same `employee_table`, and there is no key_for()/guard_for() any
more because both sources read and write with the same key() and ACTIVE_GUARD an
onboarding record always used.
"""
from common import responses
from common.db import employee_table, onboarding_table
from common.keys import EXISTS, key
from common.models import (
    ARCHIVED_MESSAGE,
    is_intern_item,
    to_api_employee,
    to_api_intern,
    to_api_staff_employee,
)

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
#
# Used against EmployeeTable too, for an employee or an intern's own contact
# PATCH - `archivedAs` never exists there (archiving is onboarding-only), so the
# second half is always true and the condition collapses to plain existence.
ACTIVE_GUARD = EXISTS + ' AND attribute_not_exists(#archivedAs)'

# Sources find_record() and load_staff_record() can report. Named here rather
# than as bare strings so a typo in a caller's comparison is a NameError, not a
# silently-false branch. "employee" and "intern" are not two tables - both live
# in EmployeeTable - they are two answers to "what kind of record is this",
# decided by translate_staff_item() below from the item's own `entityType`.
SOURCE_ONBOARDING = 'onboarding'
SOURCE_EMPLOYEE = 'employee'
SOURCE_INTERN = 'intern'

# Which table backs each source. employee and intern share one - see the module
# docstring - so this is not the 1:1 map it looks like it should be.
_TABLE_BY_SOURCE = {
    SOURCE_ONBOARDING: onboarding_table,
    SOURCE_EMPLOYEE: employee_table,
    SOURCE_INTERN: employee_table,
}


def table_for(source):
    """The Table resource backing this source, for a caller that already knows it."""
    return _TABLE_BY_SOURCE[source]


def translate_staff_item(item):
    """
    One EmployeeTable item -> (api_object, source), reading `entityType` to
    decide which translator applies. The one place that decision is made -
    find_record() and load_staff_record() both call this rather than each
    keeping their own copy of the same if/else.
    """
    if is_intern_item(item):
        return to_api_intern(item), SOURCE_INTERN
    return to_api_staff_employee(item), SOURCE_EMPLOYEE


def load_employee(employee_id, consistent=False):
    """
    The onboarding-only read: the full API employee, or None if there is none
    IN THE ONBOARDING TABLE. A promoted employee reads as None here even though
    they exist - this is deliberate, and is what makes PUT and the checklist
    PATCH refuse a promoted id with a 404 rather than reaching across tables.
    """
    result = onboarding_table.get_item(
        Key=key(employee_id),
        ConsistentRead=consistent,
    )
    return to_api_employee(result.get('Item'))


def find_record(employee_id, consistent=False):
    """
    The record for this id, wherever it actually lives, as (api_object, source).

    Tries onboarding first - most reads are onboarding - then EmployeeTable
    (employee or intern, decided by translate_staff_item), and stops at the
    first hit. (None, None) if neither table has this id. Two GetItems in the
    worst case.

    This is the read every id-addressed endpoint needs once a record can leave
    the onboarding table: GET /employees/{id}, the contact PATCH, and the
    document-upload existence check. Each of those used to call load_employee()
    alone, which is exactly what would 404 a promoted employee out of their own
    profile.
    """
    onboarding_result = onboarding_table.get_item(Key=key(employee_id), ConsistentRead=consistent)
    onboarding_item = onboarding_result.get('Item')
    if onboarding_item:
        return to_api_employee(onboarding_item), SOURCE_ONBOARDING

    employee_result = employee_table.get_item(Key=key(employee_id), ConsistentRead=consistent)
    employee_item = employee_result.get('Item')
    if employee_item:
        return translate_staff_item(employee_item)

    return None, None


def load_staff_record(employee_id, consistent=False):
    """
    Like find_record, but only looks in EmployeeTable - never onboarding. Used
    by the promote/restore guards, which must not treat an onboarding row as
    evidence that a promotion already happened.
    """
    employee_result = employee_table.get_item(Key=key(employee_id), ConsistentRead=consistent)
    employee_item = employee_result.get('Item')
    if employee_item:
        return translate_staff_item(employee_item)

    return None, None


def load_archive_state(employee_id):
    """
    Whether this employee exists and whether they are frozen, or None if there is
    no such employee. The one fact a failed conditional write needs in order to
    say which of 404 and 409 it was.

    Onboarding-only, like load_employee - archiving is an onboarding-table
    concept; a promoted record has no `archivedAs` at all.

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
    result = onboarding_table.get_item(
        Key=key(employee_id),
        ProjectionExpression='employeeId, #archivedAs',
        ExpressionAttributeNames={'#archivedAs': 'archivedAs'},
        ConsistentRead=True,
    )
    item = result.get('Item')
    if item is None:
        return None
    return {'archivedAs': item.get('archivedAs', '')}


def guard_failure_response(employee_id, source=SOURCE_ONBOARDING):
    """
    Turn a fired ACTIVE_GUARD into the right status code.

    Re-reads rather than guessing which half of the condition it was, because
    'archived' and 'never existed' are a 409 and a 404, and telling a caller the
    wrong one sends them looking in the wrong place.

    `source` defaults to onboarding, where both halves of the guard are
    meaningful. Employee and intern records can never be archived, so a guard
    failure against EmployeeTable can only be the NOT_EXISTS half - the record
    vanished between find_record() and the write, which is a plain 404 with no
    archive check to run.

    Shared by every handler that writes a profile against ACTIVE_GUARD - HR's
    full replace and the employee's own contact patch, whichever table the
    caller's record turned out to be in.
    """
    if source == SOURCE_ONBOARDING:
        state = load_archive_state(employee_id)
        if state is not None and state['archivedAs']:
            return responses.conflict(ARCHIVED_MESSAGE)
    return responses.not_found('No employee with id ' + employee_id + '.')
