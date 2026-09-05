"""DynamoDB access patterns for attendance and its employee roster."""
from boto3.dynamodb.conditions import Attr, Key

from common.db import attendance_table, employee_table, onboarding_table
from common.keys import attendance_key, pk


def get_attendance(employee_id, attendance_date, consistent=False):
    result = attendance_table.get_item(
        Key=attendance_key(employee_id, attendance_date),
        ConsistentRead=consistent,
    )
    return result.get('Item')


def put_attendance(item):
    attendance_table.put_item(Item=item)


def query_employee_month(employee_id, month):
    items = []
    kwargs = {
        'KeyConditionExpression': (
            Key('employeeKey').eq(pk(employee_id)) &
            Key('attendanceDate').begins_with(month)
        ),
    }
    while True:
        result = attendance_table.query(**kwargs)
        items.extend(result.get('Items', []))
        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


def query_month(month):
    items = []
    kwargs = {
        'IndexName': 'AttendanceByMonth',
        'KeyConditionExpression': Key('attendanceMonth').eq(month),
    }
    while True:
        result = attendance_table.query(**kwargs)
        items.extend(result.get('Items', []))
        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


def _scan_all(table, **kwargs):
    items = []
    while True:
        result = table.scan(**kwargs)
        items.extend(result.get('Items', []))
        last_key = result.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


def attendance_roster():
    """Active onboarding and promoted staff, collapsed by immutable employee id."""
    onboarding = _scan_all(
        onboarding_table,
        FilterExpression=(
            Attr('entityType').eq('Employee') & Attr('archivedAs').not_exists()
        ),
    )
    staff = _scan_all(
        employee_table,
        FilterExpression=(
            Attr('entityType').eq('Employee') | Attr('entityType').eq('Intern')
        ),
    )

    roster = {}
    for item in onboarding + staff:
        employee_id = item.get('employeeId')
        if not employee_id:
            continue
        roster[employee_id] = {
            'id': employee_id,
            'firstName': item.get('firstName', ''),
            'lastName': item.get('lastName', ''),
            'jobTitle': item.get('jobTitle', ''),
            'department': item.get('department', ''),
            'startDate': item.get('startDate', ''),
        }
    return list(roster.values())
