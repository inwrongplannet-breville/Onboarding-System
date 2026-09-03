"""
GET /employees/{id} - brief task 3.

One GetItem returns the whole record, checklist included - the checklist is a
list attribute on that item, not eight rows beside it.

The id can now be in any of three tables: onboarding, employee or intern -
promotion moves a record out of onboarding, and this route has to keep working
for the person it moved. find_record() (common/repository.py) tries all three
and reports which one it found, in the order a lookup is most likely to hit.

Eventually consistent, deliberately - see common/repository.py. PUT and PATCH are
the calls that need the strong read, because they wrote a moment earlier.

Archived onboarding records are returned here, carrying `archived` and
`archivedAs`. Only the list endpoint hides them: "removed" means off the list,
and a record nobody can read is not an archive, it is a slower delete. That now
holds for the employee role too - an archived employee reading their own record
gets it back, read-only. It used to be a 404, to stop employees browsing
ex-colleagues, and require_self closes that door properly; a 404 would only be
telling somebody their own record does not exist. Employee and intern records
are never archived - there is nothing to hide.

Two roles, two answers. An official gets the whole item. An employee gets
own_profile_view of their own record and a 403 for anybody else's - and the 403 is
raised before the read, so this endpoint cannot be used to find out which employee
numbers exist.
"""
from common import responses
from common.accounts import ROLE_OFFICIAL
from common.handler import api_handler, employee_id_param, require_role, require_self
from common.models import own_profile_view
from common.repository import find_record


@api_handler
def lambda_handler(event, context):
    # Before the read, so a request with no identified caller never reaches
    # DynamoDB at all.
    role = require_role(event)

    employee_id = employee_id_param(event)

    # Before the read, and the ordering is the point: an employee asking about
    # somebody else gets the same 403 whether or not that record exists, so
    # walking the id space tells them nothing.
    if role != ROLE_OFFICIAL:
        require_self(event, employee_id)

    employee, source = find_record(employee_id)

    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    if role == ROLE_OFFICIAL:
        return responses.ok(employee)

    # Their own record, whole, minus the checklist comments. Archived included -
    # own_profile_view carries `archived` and `archivedAs` so the profile screen
    # can say so and drop its edit form. `source` is not part of the response;
    # it only mattered for finding the record.
    return responses.ok(own_profile_view(employee))
