"""
An in-memory DynamoDB (moto) with the real table schemas, so the handlers can be
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
import secrets
import sys

import pytest

from common.accounts import _derive

ONBOARDING_TABLE_NAME = 'onboarding-test'
# Holds employees AND interns, told apart by entityType - see the merge note
# in template.yaml's EmployeeTable resource.
EMPLOYEE_TABLE_NAME = 'employee-test'
ATTENDANCE_TABLE_NAME = 'attendance-test'

# The document store. Same shape of decision as the table names above: common/documents
# builds its S3 client at module scope, so this has to exist before any handler
# that imports it is imported.
BUCKET_NAME = 'onboarding-test-documents'

# Set before any handler module is imported - common/db.py reads these at import
# time, and fake credentials keep botocore from picking up a real profile.
os.environ.setdefault('ONBOARDING_TABLE_NAME', ONBOARDING_TABLE_NAME)
os.environ.setdefault('EMPLOYEE_TABLE_NAME', EMPLOYEE_TABLE_NAME)
os.environ.setdefault('ATTENDANCE_TABLE_NAME', ATTENDANCE_TABLE_NAME)
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

# Random per-process credentials keep login tests realistic without committing
# a reusable plaintext password or a matching hash. The tests read the two
# TEST_* values below; neither value is used by deployed code.
TEST_OFFICIAL_PASSWORD = secrets.token_urlsafe(32)
TEST_EMPLOYEE_PASSWORD = secrets.token_urlsafe(32)
os.environ['TEST_OFFICIAL_PASSWORD'] = TEST_OFFICIAL_PASSWORD
os.environ['TEST_EMPLOYEE_PASSWORD'] = TEST_EMPLOYEE_PASSWORD


def generated_credential(password):
    salt = secrets.token_hex(16)
    return {'salt': salt, 'hash': _derive(password, salt)}


# common/accounts.py reads this through the local/test ACCOUNTS_JSON path, so no
# Secrets Manager call happens during the suite.
os.environ['ACCOUNTS_JSON'] = json.dumps({
    'accounts': {
        'hr.admin': dict(generated_credential(TEST_OFFICIAL_PASSWORD),
                         role='official', displayName='HR Admin'),
    },
    'employeeCredential': generated_credential(TEST_EMPLOYEE_PASSWORD),
})

# The `aud` claim tokens are minted for and checked against. Set here because
# common/tokens.py refuses to guess it - a default is what lets two deployments
# share an audience, which is the thing `aud` exists to stop.
os.environ.setdefault('STAGE', 'dev')

# Read by common/responses.py for the CORS header. '*' keeps the existing
# assertions honest about what the tests are checking, which is presence rather
# than policy - the deployed value comes from the AllowedOrigins parameter.
os.environ.setdefault('ALLOWED_ORIGINS', '*')

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
    # The promote / un-promote / reassign / staff-list routes. Each is its own
    # Lambda, matching the one-function-per-route convention the rest of this
    # stack already follows - see template.yaml.
    'handlers.promote_to_employee',
    'handlers.promote_to_intern',
    'handlers.add_manager_intern',
    'handlers.remove_manager_intern',
    'handlers.set_intern_manager',
    'handlers.delete_onboarding_record',
    'handlers.restore_onboarding',
    'handlers.delete_staff_employee',
    'handlers.delete_staff_intern',
    'handlers.list_staff_employees',
    'handlers.list_interns',
    'handlers.upsert_own_attendance',
    'handlers.get_own_attendance',
    'handlers.get_attendance_sheet',
    'handlers.download_attendance_csv',
    'handlers.update_employee_attendance',
)


@pytest.fixture
def handlers():
    """
    Fresh onboarding, staff and attendance tables plus freshly imported
    handlers, per test. EmployeeTable still holds employees and interns both.

    The reimport matters: common/db.py caches its boto3 clients at module scope
    (deliberately - that is how a warm Lambda reuses connections), so those
    clients have to be built while moto is active.
    """
    import boto3
    from moto import mock_aws

    with mock_aws():
        boto3.client('dynamodb').create_table(
            TableName=ONBOARDING_TABLE_NAME,
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

        boto3.client('dynamodb').create_table(
            TableName=EMPLOYEE_TABLE_NAME,
            BillingMode='PAY_PER_REQUEST',
            # employeeKey is the table's own partition key - shared by
            # employees and interns alike, told apart by entityType, not by
            # key. reportingManagerId is declared here only because the
            # ByReportingManager GSI needs it in AttributeDefinitions -
            # DynamoDB requires every key attribute of every index to be
            # declared at the table level, even though only intern items ever
            # carry it.
            AttributeDefinitions=[
                {'AttributeName': 'employeeKey', 'AttributeType': 'S'},
                {'AttributeName': 'reportingManagerId', 'AttributeType': 'S'},
            ],
            KeySchema=[
                {'AttributeName': 'employeeKey', 'KeyType': 'HASH'},
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'ByReportingManager',
                    'KeySchema': [
                        {'AttributeName': 'reportingManagerId', 'KeyType': 'HASH'},
                    ],
                    'Projection': {'ProjectionType': 'ALL'},
                },
            ],
        )

        boto3.client('dynamodb').create_table(
            TableName=ATTENDANCE_TABLE_NAME,
            BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[
                {'AttributeName': 'employeeKey', 'AttributeType': 'S'},
                {'AttributeName': 'attendanceDate', 'AttributeType': 'S'},
                {'AttributeName': 'attendanceMonth', 'AttributeType': 'S'},
                {'AttributeName': 'dateEmployeeKey', 'AttributeType': 'S'},
            ],
            KeySchema=[
                {'AttributeName': 'employeeKey', 'KeyType': 'HASH'},
                {'AttributeName': 'attendanceDate', 'KeyType': 'RANGE'},
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'AttendanceByMonth',
                    'KeySchema': [
                        {'AttributeName': 'attendanceMonth', 'KeyType': 'HASH'},
                        {'AttributeName': 'dateEmployeeKey', 'KeyType': 'RANGE'},
                    ],
                    'Projection': {'ProjectionType': 'ALL'},
                },
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

        shared_modules = (
            'common.db',
            'common.documents',
            'common.attendance_repository',
        )
        for name in shared_modules + HANDLER_MODULES:
            sys.modules.pop(name, None)

        loaded = {}
        for name in HANDLER_MODULES:
            module = importlib.import_module(name)
            loaded[name.rsplit('.', 1)[1]] = module.lambda_handler

        yield loaded

        for name in shared_modules + HANDLER_MODULES:
            sys.modules.pop(name, None)
