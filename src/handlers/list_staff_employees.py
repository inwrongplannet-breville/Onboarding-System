"""
GET /staff/employees - backs the Employee Tracking dashboard, and the reporting-
manager picker on the checklist screen for an intern being promoted.

Same shape as handlers/list_employees.py's Scan, against EmployeeTable instead:
Query needs a partition key, employees are spread across every partition by
design, so Scan is correct here for the same reason it is there.
"""
from boto3.dynamodb.conditions import Attr

from common import responses
from common.db import employee_table
from common.handler import api_handler, require_official
from common.models import to_api_staff_employee


def _scan_all():
    """Scan is paginated at 1 MB. Follow LastEvaluatedKey or silently lose rows."""
    items = []
    kwargs = {'FilterExpression': Attr('entityType').eq('Employee')}

    while True:
        result = employee_table.scan(**kwargs)
        items.extend(result.get('Items', []))

        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


@api_handler
def lambda_handler(event, context):
    require_official(event)

    employees = [to_api_staff_employee(item) for item in _scan_all()]
    employees.sort(key=lambda employee: (employee['joinedOn'], employee['lastName']))

    return responses.ok({'employees': employees, 'count': len(employees)})
