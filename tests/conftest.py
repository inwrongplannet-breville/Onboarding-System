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
import json
import os
import sys

import pytest

TABLE_NAME = 'onboarding-test'

# The document store. Same shape of decision as TABLE_NAME above: common/documents
# builds its S3 client at module scope, so this has to exist before any handler
# that imports it is imported.
BUCKET_NAME = 'onboarding-test-documents'

# Set before any handler module is imported - common/db.py reads TABLE_NAME and
# builds its boto3 clients at import time, and fake credentials keep botocore
# from picking up a real profile.
os.environ.setdefault('TABLE_NAME', TABLE_NAME)
os.environ.setdefault('BUCKET_NAME', BUCKET_NAME)
os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-southeast-2')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
os.environ.setdefault('AWS_SECURITY_TOKEN', 'testing')
os.environ.setdefault('AWS_SESSION_TOKEN', 'testing')

# The login route and the authorizer read this at call time. A fixed value rather
# than a random one so a token minted in one test is verifiable in another, and
# obviously not a real key so nobody is tempted to reuse it.
os.environ.setdefault('JWT_SECRET', 'test-signing-key-not-for-any-deployment')

# common/accounts.py reads this the same way, in the same ACCOUNTS_JSON-or-
# ACCOUNTS_SECRET_ARN shape as JWT_SECRET/JWT_SECRET_ARN above - the plain env
# var is the test/local path, so no Secrets Manager call happens here. These are
# the same salt/hash values that used to live in accounts.py directly; moving
# them to Secrets Manager for a deployed stack does not change what a test needs
# to sign in as.
os.environ.setdefault('ACCOUNTS_JSON', json.dumps({
    'accounts': {
        'hr.admin': {
            'salt': '89cfb6356f147f197d5131fbc77a9e3f',
            'hash': 'd5de7d5e4a770324e5d7a48f2a257581e832072e3ff0cfe7a049baa18d57555b',
            'role': 'official',
            'displayName': 'HR Admin',
        },
    },
    'employeeCredential': {
        'salt': '821ffd37011d86d96e9551dbe76a3776',
        'hash': '33f8556d1f6946cd31f21f031ca333f0cfd5ed2c3b48b913760f148d2ec68374',
    },
}))

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
    'handlers.update_own_contact',
    'handlers.get_documents',
    'handlers.request_document_upload',
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

        boto3.client('s3').create_bucket(
            Bucket=BUCKET_NAME,
            # Required in every region but us-east-1, and this file sets
            # AWS_DEFAULT_REGION above. Omitting it is an
            # IllegalLocationConstraintException rather than a silent pass.
            CreateBucketConfiguration={
                'LocationConstraint': os.environ['AWS_DEFAULT_REGION'],
            },
        )

        for name in ('common.db', 'common.documents') + HANDLER_MODULES:
            sys.modules.pop(name, None)

        loaded = {}
        for name in HANDLER_MODULES:
            module = importlib.import_module(name)
            loaded[name.rsplit('.', 1)[1]] = module.lambda_handler

        yield loaded

        for name in ('common.db', 'common.documents') + HANDLER_MODULES:
            sys.modules.pop(name, None)
