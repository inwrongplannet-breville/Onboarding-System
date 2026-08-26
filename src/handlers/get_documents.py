"""
GET /employees/{id}/documents - what this employee has handed over.

Its own resource rather than a field on the employee, and that is three decisions
rather than one.

It keeps S3 out of `GET /employees/{id}`, which is also what `GET /employees`
builds every row from - a HeadObject in there would be three network calls per
employee inside a Scan loop. It keeps GetEmployeeFunction with no S3 permissions
at all. And the download links here are signed and short-lived, so they cannot
sensibly be part of a profile response that a caller might hold on to.

Both roles read it. An official reads anybody's; an employee reads their own and
gets 403 for anyone else's, checked before the read for the same reason
handlers/get_employee checks it there - so that probing employee numbers says
nothing about which ones exist.

Archived employees are readable here. `handlers/get_employee` already argues the
point: a record nobody can read is not an archive, it is a slower delete. The
documents are the evidence half of that record.
"""
from common import responses
from common.accounts import ROLE_OFFICIAL
from common.documents import describe_slots
from common.handler import api_handler, employee_id_param, require_role, require_self


@api_handler
def lambda_handler(event, context):
    role = require_role(event)

    employee_id = employee_id_param(event)

    if role != ROLE_OFFICIAL:
        require_self(event, employee_id)

    # No existence check against DynamoDB, deliberately. An employee number with
    # no record has no objects either, so the honest answer is the same three
    # empty slots - and a GetItem here would buy a 404 nobody acts on at the cost
    # of giving this function table permissions. The upload route is where a
    # nonexistent employee has to be refused, because that one writes.
    return responses.ok({'documents': describe_slots(employee_id)})
