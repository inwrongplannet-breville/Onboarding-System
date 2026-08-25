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

# The login route and the authorizer read this at call time. A fixed value rather
# than a random one so a token minted in one test is verifiable in another, and
# obviously not a real key so nobody is tempted to reuse it.
os.environ.setdefault('JWT_SECRET', 'test-signing-key-not-for-any-deployment')

# The `aud` claim tokens are minted for and checked against. Set here because
# common/tokens.py refuses to guess it - a default is what lets two deployments
# share an audience, which is the thing `aud` exists to stop.
os.environ.setdefault('STAGE', 'dev')

# Read by common/responses.py for the CORS header. '*' keeps the existing
# assertions honest about what the tests are checking, which is presence rather
# than policy - the deployed value comes from the AllowedOrigin parameter.
os.environ.setdefault('ALLOWED_ORIGIN', '*')

HANDLER_MODULES = (
    'handlers.create_employee',
    'handlers.get_employee',
    'handlers.list_employees',
    'handlers.update_employee',
    'handlers.delete_employee',
    'handlers.set_checklist_item',
    # Neither of these touches DynamoDB, so neither needs the table this fixture
    # builds. They are here so that every route is reachable through one
    # `handlers` dict - a test that logs in and then calls an endpoint reads as
    # one flow rather than importing half of it a different way.
    'handlers.login',
    'handlers.authorizer',
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
            # The partition key only. One item per employee, so there is nothing to
            # sort within a partition - and an AttributeDefinition no key refers to
            # is a validation error, so SK has to leave both lists together.
            #
            # Spelled out rather than imported from common.keys on purpose: this is
            # the test's independent copy of the schema in template.yaml, and a
            # shared constant would let the two rename themselves in lockstep
            # without a single test noticing the deploy needed a table replacement.
            AttributeDefinitions=[
                {'AttributeName': 'employeeKey', 'AttributeType': 'S'},
            ],
            KeySchema=[
                {'AttributeName': 'employeeKey', 'KeyType': 'HASH'},
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
