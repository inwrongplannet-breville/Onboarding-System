"""
An in-memory DynamoDB (moto) with the real table schema, so the handlers can be
exercised end to end without AWS.

This does NOT replace testing against a deployed stack. moto emulates the API,
not IAM - and IAM is the most likely thing to be wrong on a first deploy. What it
does catch, cheaply, is the stuff that is tedious to debug through CloudWatch:
UpdateExpression syntax, nested document paths into the embedded checklist, and
whether the condition expressions actually fire.
"""
import importlib
import os
import sys

import pytest

TABLE_NAME = 'onboarding-test'

# Set before any handler module is imported - common/db.py reads TABLE_NAME and
# builds its boto3 clients at import time, and fake credentials keep botocore
# from picking up a real profile.
os.environ.setdefault('TABLE_NAME', TABLE_NAME)
os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-southeast-2')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
os.environ.setdefault('AWS_SECURITY_TOKEN', 'testing')
os.environ.setdefault('AWS_SESSION_TOKEN', 'testing')

HANDLER_MODULES = (
    'handlers.create_employee',
    'handlers.get_employee',
    'handlers.list_employees',
    'handlers.update_employee',
    'handlers.delete_employee',
    'handlers.set_checklist_item',
)


@pytest.fixture
def handlers():
    """
    A fresh table plus freshly imported handlers, per test.

    The reimport matters: common/db.py caches its boto3 clients at module scope
    (deliberately - that is how a warm Lambda reuses connections), so those
    clients have to be built while moto is active.
    """
    import boto3
    from moto import mock_aws

    with mock_aws():
        boto3.client('dynamodb').create_table(
            TableName=TABLE_NAME,
            BillingMode='PAY_PER_REQUEST',
            # PK only. One item per employee, so there is nothing to sort within
            # a partition - and an AttributeDefinition no key refers to is a
            # validation error, so SK has to leave both lists together.
            AttributeDefinitions=[
                {'AttributeName': 'PK', 'AttributeType': 'S'},
            ],
            KeySchema=[
                {'AttributeName': 'PK', 'KeyType': 'HASH'},
            ],
        )

        for name in ('common.db',) + HANDLER_MODULES:
            sys.modules.pop(name, None)

        loaded = {}
        for name in HANDLER_MODULES:
            module = importlib.import_module(name)
            loaded[name.rsplit('.', 1)[1]] = module.lambda_handler

        yield loaded

        for name in ('common.db',) + HANDLER_MODULES:
            sys.modules.pop(name, None)
