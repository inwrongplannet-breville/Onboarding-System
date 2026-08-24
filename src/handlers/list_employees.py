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

Scale: ~9 items x ~300 bytes = ~2.7 KB per employee, so a 1 MB Scan page holds
roughly 370 of them. Fine for hundreds. Wrong for a million - at which point you
denormalise doneCount onto the profile and add that GSI.

The rule: Query when you know the partition key, Scan only when you genuinely
need every item.

Note the Scan still reads archived employees off the table and then drops them
here, because there is no index that would let it skip them. That is the price of
soft deletion on a Scan-based list. At the scale above it is noise; the sparse-GSI
rewrite is where you would stop paying it.
"""
from common import responses
from common.db import table
from common.handler import api_handler
from common.models import group_by_partition, to_api_employee


def _scan_all():
    """Scan is paginated at 1 MB. Follow LastEvaluatedKey or silently lose rows."""
    items = []
    kwargs = {}

    while True:
        result = table.scan(**kwargs)
        items.extend(result.get('Items', []))

        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


@api_handler
def lambda_handler(event, context):
    partitions = group_by_partition(_scan_all())

    employees = []
    for items in partitions.values():
        employee = to_api_employee(items)
        # Skip orphan checklist rows with no profile. Archiving never strands any
        # - it deletes nothing - but historical partitions from before the switch
        # might, and a list endpoint shouldn't crash on one.
        if employee is None:
            continue
        # Archived employees are still in the table and still readable by id.
        # This is the endpoint that decides they are off the list, which is what
        # "removed" means to everyone using the UI. See handlers/delete_employee.
        if employee['archived']:
            continue
        employees.append(employee)

    employees.sort(key=lambda employee: (employee['startDate'], employee['lastName']))
    return responses.ok({'employees': employees, 'count': len(employees)})
