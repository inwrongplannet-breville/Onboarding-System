"""
GET /staff/interns - backs the Interns dashboard.

Scans/queries EmployeeTable, the same table handlers/list_staff_employees.py
reads - interns and employees live side by side there now, told apart by
`entityType`. Two shapes depending on the query string:

  GET /staff/interns                 Scan filtered to entityType 'Intern',
                                      paginated the same way
                                      handlers/list_employees.py's is.
  GET /staff/interns?managerId=E1001 Query against the ByReportingManager GSI -
                                      genuinely used, not decorative, since this
                                      is the read the interns dashboard's
                                      "interns reporting to me" view and the
                                      reassign-manager screen both need. No
                                      entityType filter needed here: the GSI is
                                      sparse - only intern items carry
                                      reportingManagerId at all, so an employee
                                      item is never a candidate to skip.

Projection: ALL on the index, so the Query branch needs no follow-up GetItem per
intern - the index already carries the whole record.
"""
from boto3.dynamodb.conditions import Attr, Key

from common import responses
from common.db import employee_table
from common.handler import api_handler, require_official
from common.models import to_api_intern

_INDEX_NAME = 'ByReportingManager'


def _scan_all():
    items = []
    kwargs = {'FilterExpression': Attr('entityType').eq('Intern')}

    while True:
        result = employee_table.scan(**kwargs)
        items.extend(result.get('Items', []))

        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


def _query_by_manager(manager_id):
    items = []
    kwargs = {
        'IndexName': _INDEX_NAME,
        'KeyConditionExpression': Key('reportingManagerId').eq(manager_id),
    }

    while True:
        result = employee_table.query(**kwargs)
        items.extend(result.get('Items', []))

        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


@api_handler
def lambda_handler(event, context):
    require_official(event)

    manager_id = (event.get('queryStringParameters') or {}).get('managerId')
    raw_items = _query_by_manager(manager_id) if manager_id else _scan_all()

    interns = [to_api_intern(item) for item in raw_items]
    interns.sort(key=lambda intern: (intern['joinedOn'], intern['lastName']))

    return responses.ok({'interns': interns, 'count': len(interns)})
