"""
GET /employees/{id} - brief task 3.

One Query on the partition key returns the profile and all 8 checklist rows in a
single round trip. That is the whole point of keeping them under one PK.
"""
from boto3.dynamodb.conditions import Key

from common import responses
from common.db import table
from common.handler import api_handler, path_param
from common.keys import pk
from common.models import to_api_employee


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')

    result = table.query(KeyConditionExpression=Key('PK').eq(pk(employee_id)))
    employee = to_api_employee(result.get('Items', []))

    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    return responses.ok(employee)
