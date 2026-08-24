"""
GET /employees/{id} - brief task 3.

One Query on the partition key returns the profile and all 8 checklist rows in a
single round trip. That is the whole point of keeping them under one PK.

Eventually consistent, deliberately - see common/repository.py. PUT and PATCH are
the calls that need the strong read, because they wrote a moment earlier.

Archived employees are returned here, carrying `archived` and `archivedAs`. Only
the list endpoint hides them: "removed" means off the list, and a record nobody
can read is not an archive, it is a slower delete.
"""
from common import responses
from common.handler import api_handler, path_param
from common.repository import load_employee


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')
    employee = load_employee(employee_id)

    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    return responses.ok(employee)
