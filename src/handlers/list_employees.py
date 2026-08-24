"""
GET /employees - brief task 4.

Scan vs Query, since the brief asks:

Query needs a partition key. Employees are spread across every partition by
design, so there is no key to query on - Scan is the correct operation here, not
a workaround.

The usual counter-suggestion is a sparse GSI with a constant partition key
(GSI1PK = "EMPLOYEE") so this becomes a Query. Here that is strictly worse: the
list view filters by derived status and draws a progress bar, both of which need
the checklist. A profile-only index gives you N profiles and then N follow-up
Queries. One Scan already returns everything.

Scale: one item x ~1.1 KB per employee, so a 1 MB Scan page holds roughly 900 of
them. Fine for hundreds. Wrong for a million - at which point you denormalise
doneCount onto the item and add that GSI.

The rule: Query when you know the partition key, Scan only when you genuinely
need every item.

Note the Scan still reads archived employees off the table and then drops them
here, because there is no index that would let it skip them. That is the price of
soft deletion on a Scan-based list. At the scale above it is noise; the sparse-GSI
rewrite is where you would stop paying it.
"""
from boto3.dynamodb.conditions import Attr

from common import responses
from common.db import table
from common.handler import api_handler
from common.models import to_api_employee


def _scan_all():
    """Scan is paginated at 1 MB. Follow LastEvaluatedKey or silently lose rows."""
    items = []
    kwargs = {
        # Employees are the only kind of item in the table today, so this filters
        # nothing out. It is here so that stays true by construction rather than
        # by luck: anything else that ever lands in this table - an audit row, a
        # reintroduced email guard - is excluded from the list without another
        # visit to this file. Filtering happens after the read, so it costs the
        # same either way.
        'FilterExpression': Attr('entityType').eq('Employee'),
    }

    while True:
        result = table.scan(**kwargs)
        items.extend(result.get('Items', []))

        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


@api_handler
def lambda_handler(event, context):
    employees = []
    for item in _scan_all():
        employee = to_api_employee(item)
        # Archived employees are still in the table and still readable by id.
        # This is the endpoint that decides they are off the list, which is what
        # "removed" means to everyone using the UI. See handlers/delete_employee.
        if employee['archived']:
            continue
        employees.append(employee)

    employees.sort(key=lambda employee: (employee['startDate'], employee['lastName']))
    return responses.ok({'employees': employees, 'count': len(employees)})
