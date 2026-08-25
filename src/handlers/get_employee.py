"""
GET /employees/{id} - brief task 3.

One GetItem on the partition key returns the whole employee, checklist included -
the checklist is a list attribute on that item, not eight rows beside it.

Eventually consistent, deliberately - see common/repository.py. PUT and PATCH are
the calls that need the strong read, because they wrote a moment earlier.

Archived employees are returned here, carrying `archived` and `archivedAs`. Only
the list endpoint hides them: "removed" means off the list, and a record nobody
can read is not an archive, it is a slower delete.
"""
from common import responses
from common.accounts import ROLE_OFFICIAL
from common.handler import api_handler, employee_id_param, require_role
from common.models import restrict_for_employee
from common.repository import load_employee


@api_handler
def lambda_handler(event, context):
    # Before the read, so a request with no identified caller never reaches
    # DynamoDB at all.
    role = require_role(event)

    employee_id = employee_id_param(event)
    employee = load_employee(employee_id)

    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    if role == ROLE_OFFICIAL:
        return responses.ok(employee)

    # The employee role is served the directory shape, and archived records read
    # as absent to it. The list endpoint already hides them, so returning one
    # here would make an employee's only route to an archived colleague a URL
    # they had to guess - and the record would still be a record of somebody who
    # left. 404 rather than 403 for the same reason: "no employee with id X" is
    # what the list said too, and the pair should not disagree.
    if employee['archived']:
        return responses.not_found('No employee with id ' + employee_id + '.')

    return responses.ok(restrict_for_employee(employee))
