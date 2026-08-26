"""
The document store: who may read, who may upload, and what a ticket looks like.

**What these tests can and cannot prove.** moto emulates the S3 API, not IAM - and
`generate_presigned_post` makes no API call at all, so moto never sees it. So
these pin the ticket's *shape* and the authorisation rules around it; they cannot
prove S3 will accept a ticket. Four things only the deployed stack can tell you,
all listed in the plan: that GetDocumentsFunction has s3:ListBucket (without it a
missing key is a 403, not a 404, and every empty slot 500s), that the browser
sends the file part last, that no extra form field sneaks in, and that the bucket
carries its CORS rule.

The helpers come from test_handlers for the same reason test_own_profile's do:
`create`, `archive` and the rest already know how to build a valid request, and
the only thing changing here is the authorizer context they carry.
"""
import os

import boto3
import pytest

from common.documents import (
    ALLOWED_CONTENT_TYPES,
    BUCKET_NAME,
    DOCUMENT_SLOTS,
    FILENAME_MAX_LENGTH,
    MAX_UPLOAD_BYTES,
    clean_filename,
    content_disposition,
    document_key,
)
from common.models import ARCHIVED_MESSAGE
from test_handlers import (
    OFFICIAL,
    archive,
    as_employee,
    body,
    create,
    signed_in,
)

PDF = 'application/pdf'


# --------------------------------------------------------------------- helpers

def get_docs(handlers, employee_id, context=None):
    return handlers['get_documents'](
        signed_in({'pathParameters': {'id': employee_id}}, context), None)


def request_upload(handlers, employee_id, slot, payload=None, context=None):
    import json

    if payload is None:
        payload = {'filename': 'resume.pdf', 'contentType': PDF}
    return handlers['request_document_upload'](signed_in(
        {'pathParameters': {'id': employee_id, 'slot': slot},
         'body': json.dumps(payload)}, context), None)


def put_object(employee_id, slot, data=b'%PDF-1.4 hello', filename='resume.pdf',
               content_type=PDF):
    """Land an object the way a browser's presigned POST would."""
    boto3.client('s3').put_object(
        Bucket=BUCKET_NAME,
        Key=document_key(employee_id, slot),
        Body=data,
        ContentType=content_type,
        Metadata={'filename': filename},
    )


def slot_of(documents, slot):
    return [entry for entry in documents if entry['slot'] == slot][0]


def keys_in_bucket():
    listing = boto3.client('s3').list_objects_v2(Bucket=BUCKET_NAME)
    return sorted(item['Key'] for item in listing.get('Contents', []))


def docs_of(handlers, employee_id, context=None):
    response = get_docs(handlers, employee_id, context)
    assert response['statusCode'] == 200, response['body']
    return body(response)['documents']


# ------------------------------------------------------------ the empty state

def test_every_slot_is_reported_even_when_nothing_is_uploaded(handlers):
    """
    Three slots always, because the UI draws an empty drop zone from
    `uploaded: False`. A filtered list would leave it with nothing to render.
    """
    create(handlers, employeeId='E1001')

    documents = docs_of(handlers, 'E1001')
    assert [entry['slot'] for entry in documents] == \
        [entry['slot'] for entry in DOCUMENT_SLOTS]
    assert all(entry['uploaded'] is False for entry in documents)


def test_an_empty_slot_carries_no_download_url_at_all(handlers):
    """Absent, not empty. An empty slot must have nothing the UI could link to."""
    create(handlers, employeeId='E1001')

    for entry in docs_of(handlers, 'E1001'):
        assert 'downloadUrl' not in entry
        assert 'filename' not in entry
        assert 'size' not in entry


def test_reading_documents_creates_nothing(handlers):
    create(handlers, employeeId='E1001')
    get_docs(handlers, 'E1001')

    assert keys_in_bucket() == []


def test_an_employee_with_no_record_reads_three_empty_slots(handlers):
    """
    No DynamoDB check on the read path. A number with no record has no objects
    either, so three empty slots is the honest answer - and it keeps this function
    out of the table entirely.
    """
    documents = docs_of(handlers, 'E9999', as_employee('E9999'))
    assert all(entry['uploaded'] is False for entry in documents)


def test_the_slot_order_is_the_column_order(handlers):
    """
    Pinned literally, because the order is a decision the user made and nothing
    else in the system would notice a reshuffle - the same reasoning as the
    checklist order test in test_models.py.
    """
    assert [entry['slot'] for entry in DOCUMENT_SLOTS] == [
        'resume', 'id-document', 'signed-offer-letter',
    ]


# ------------------------------------------------------------ the filled state

def test_an_uploaded_document_reports_its_name_size_and_date(handlers):
    create(handlers, employeeId='E1001')
    put_object('E1001', 'resume', data=b'x' * 2048, filename='Priya Sharma CV.pdf')

    resume = slot_of(docs_of(handlers, 'E1001'), 'resume')
    assert resume['uploaded'] is True
    assert resume['filename'] == 'Priya Sharma CV.pdf'
    assert resume['size'] == 2048
    assert resume['downloadUrl']
    # The other two are untouched.
    assert slot_of(docs_of(handlers, 'E1001'), 'id-document')['uploaded'] is False


def test_uploaded_at_is_a_string_and_not_a_datetime(handlers):
    """
    head_object hands back a datetime, and responses.py's encoder knows about
    Decimal and nothing else - so an unconverted one is a TypeError inside
    json.dumps and reaches the caller as a bare 500.
    """
    create(handlers, employeeId='E1001')
    put_object('E1001', 'resume')

    uploaded_at = slot_of(docs_of(handlers, 'E1001'), 'resume')['uploadedAt']
    assert isinstance(uploaded_at, str)
    assert uploaded_at.endswith('Z')
    assert 'T' in uploaded_at


def test_an_object_with_no_filename_metadata_is_still_usable(handlers):
    """
    Reachable for anything uploaded outside this app - a console upload, a
    migration. A document that is plainly there must not fail the whole page.
    """
    create(handlers, employeeId='E1001')
    boto3.client('s3').put_object(
        Bucket=BUCKET_NAME, Key=document_key('E1001', 'resume'),
        Body=b'x', ContentType=PDF)

    resume = slot_of(docs_of(handlers, 'E1001'), 'resume')
    assert resume['uploaded'] is True
    assert resume['filename'] == 'resume.pdf'


def test_a_second_upload_overwrites_the_first(handlers):
    """One file per slot. The key is fixed, so there is nothing to clean up."""
    create(handlers, employeeId='E1001')
    put_object('E1001', 'resume', data=b'x' * 10, filename='first.pdf')
    put_object('E1001', 'resume', data=b'y' * 20, filename='second.pdf')

    resume = slot_of(docs_of(handlers, 'E1001'), 'resume')
    assert resume['filename'] == 'second.pdf'
    assert resume['size'] == 20
    assert keys_in_bucket() == ['employees/E1001/resume']


# ------------------------------------------------------------------ who may read

def test_an_employee_reads_their_own_documents(handlers):
    create(handlers, employeeId='E1001')
    put_object('E1001', 'resume')

    assert get_docs(handlers, 'E1001', as_employee('E1001'))['statusCode'] == 200


def test_an_employee_cannot_read_another_employees_documents(handlers):
    create(handlers, employeeId='E1001')
    create(handlers, employeeId='E1002')
    put_object('E1002', 'resume', filename='someone-elses.pdf')

    response = get_docs(handlers, 'E1002', as_employee('E1001'))
    assert response['statusCode'] == 403
    assert 'someone-elses.pdf' not in response['body']
    assert BUCKET_NAME not in response['body']


def test_a_refused_read_is_identical_whether_or_not_the_record_exists(handlers):
    """
    require_self runs before anything reads S3, so probing ids says nothing about
    which employees are real.
    """
    create(handlers, employeeId='E1001')
    create(handlers, employeeId='E1002')

    existing = get_docs(handlers, 'E1002', as_employee('E1001'))
    missing = get_docs(handlers, 'E7777', as_employee('E1001'))

    assert existing['statusCode'] == missing['statusCode'] == 403
    assert existing['body'] == missing['body']


def test_an_official_reads_anybodys_documents(handlers):
    create(handlers, employeeId='E1001')
    put_object('E1001', 'resume')

    assert get_docs(handlers, 'E1001', OFFICIAL)['statusCode'] == 200


def test_a_read_with_no_authorizer_context_is_refused(handlers):
    assert handlers['get_documents'](
        {'pathParameters': {'id': 'E1001'}}, None)['statusCode'] == 403


# ---------------------------------------------------------------- who may upload

def test_an_employee_gets_a_ticket_for_their_own_slot(handlers):
    create(handlers, employeeId='E1001')

    response = request_upload(handlers, 'E1001', 'resume',
                              context=as_employee('E1001'))
    assert response['statusCode'] == 200, response['body']

    ticket = body(response)
    assert ticket['slot'] == 'resume'
    assert ticket['url']
    assert ticket['fields']['key'] == 'employees/E1001/resume'


def test_an_employee_cannot_get_a_ticket_for_another_employees_slot(handlers):
    create(handlers, employeeId='E1001')
    create(handlers, employeeId='E1002')

    response = request_upload(handlers, 'E1002', 'resume',
                              context=as_employee('E1001'))
    assert response['statusCode'] == 403
    # And no ticket leaked - a ticket is the whole capability.
    assert 'fields' not in body(response)
    assert 'url' not in body(response)
    # Nothing was written, and nothing could have been.
    assert keys_in_bucket() == []


def test_an_official_cannot_get_an_upload_ticket(handlers):
    """
    The product decision, made structural. HR reads every document and mints no
    ticket for any of them, so a document's presence is evidence the *employee*
    supplied it.
    """
    create(handlers, employeeId='E1001')

    response = request_upload(handlers, 'E1001', 'resume', context=OFFICIAL)
    assert response['statusCode'] == 403
    assert 'fields' not in body(response)
    assert 'url' not in body(response)


def test_an_upload_with_no_authorizer_context_is_refused(handlers):
    assert handlers['request_document_upload'](
        {'pathParameters': {'id': 'E1001', 'slot': 'resume'},
         'body': '{"filename": "cv.pdf", "contentType": "application/pdf"}'},
        None)['statusCode'] == 403


def test_a_ticket_for_an_employee_who_does_not_exist_is_404(handlers):
    """
    E9999 can sign in - POST /login has no table access by design - so this is
    where a mistyped number is caught. Without it S3 would take an object under a
    prefix for nobody.
    """
    response = request_upload(handlers, 'E9999', 'resume',
                              context=as_employee('E9999'))
    assert response['statusCode'] == 404
    assert keys_in_bucket() == []


@pytest.mark.parametrize('slot', ['not-a-slot', 'RESUME', '../resume', ''])
def test_an_unknown_slot_is_refused(handlers, slot):
    create(handlers, employeeId='E1001')

    response = request_upload(handlers, 'E1001', slot,
                              context=as_employee('E1001'))
    assert response['statusCode'] == 400
    # The slot is attacker-controlled and this message is rendered by the UI, so
    # it must not be echoed back.
    assert slot not in response['body'] or slot == ''


# --------------------------------------------------------------- archived records

def test_an_archived_employees_documents_are_still_readable(handlers):
    """
    Matching get_employee: a record nobody can read is not an archive, it is a
    slower delete. The documents are the evidence half of that record.
    """
    create(handlers, employeeId='E1001')
    put_object('E1001', 'resume')
    archive(handlers, 'E1001')

    assert slot_of(docs_of(handlers, 'E1001', OFFICIAL), 'resume')['uploaded'] is True
    # And the employee can still see their own.
    assert get_docs(handlers, 'E1001', as_employee('E1001'))['statusCode'] == 200


def test_uploading_to_an_archived_record_is_409(handlers):
    create(handlers, employeeId='E1001')
    archive(handlers, 'E1001')

    response = request_upload(handlers, 'E1001', 'resume',
                              context=as_employee('E1001'))
    assert response['statusCode'] == 409
    assert ARCHIVED_MESSAGE in response['body']


# --------------------------------------------------------- the ticket's contents

def test_the_ticket_pins_the_size_limit_and_the_content_type(handlers):
    """
    The size cap is the reason this is a POST rather than a presigned PUT: a PUT
    URL cannot express a maximum, so S3 would accept a 5 GB upload against one.
    """
    import base64
    import json

    create(handlers, employeeId='E1001')
    ticket = body(request_upload(handlers, 'E1001', 'resume',
                                 context=as_employee('E1001')))

    policy = json.loads(base64.b64decode(ticket['fields']['policy']))
    assert ['content-length-range', 1, MAX_UPLOAD_BYTES] in policy['conditions']
    assert {'Content-Type': PDF} in policy['conditions']
    assert {'key': 'employees/E1001/resume'} in policy['conditions']


def test_every_field_the_browser_sends_is_also_a_condition(handlers):
    """
    botocore derives neither from the other, and S3 refuses both mismatches: a
    condition with no field is a policy failure, a field with no condition is
    "Extra input fields". So the two have to be written together.
    """
    import base64
    import json

    create(handlers, employeeId='E1001')
    ticket = body(request_upload(handlers, 'E1001', 'resume',
                                 context=as_employee('E1001')))

    policy = json.loads(base64.b64decode(ticket['fields']['policy']))
    conditioned = set()
    for condition in policy['conditions']:
        if isinstance(condition, dict):
            conditioned.update(condition)

    # The signature machinery's own fields are not policy-conditioned, by design.
    signing = {'policy', 'x-amz-signature', 'signature', 'AWSAccessKeyId',
               'x-amz-algorithm', 'x-amz-credential', 'x-amz-date',
               'x-amz-security-token'}
    for field in set(ticket['fields']) - signing:
        assert field in conditioned, field


def test_the_upload_url_is_the_regional_endpoint(handlers):
    """
    The regression test for a bug the deployed stack found and no local test had.

    Without addressing_style='virtual', botocore builds the URL against the legacy
    global host `<bucket>.s3.amazonaws.com` while signing the policy for the
    bucket's real region - and S3 answers that with a 307 redirect to the regional
    endpoint. A browser cannot follow a redirect part-way through a cross-origin
    multipart upload, so every upload failed with the ticket looking perfectly
    valid.

    Asserting the shape of the host rather than the exact string: moto and real S3
    resolve different regions, so the region name itself is not the point - having
    one at all is.
    """
    create(handlers, employeeId='E1001')
    ticket = body(request_upload(handlers, 'E1001', 'resume',
                                 context=as_employee('E1001')))

    host = ticket['url'].split('/')[2]
    assert host.startswith(BUCKET_NAME + '.s3.'), host
    # Not the bare global endpoint: there must be a region between `s3` and
    # `amazonaws`.
    assert host != BUCKET_NAME + '.s3.amazonaws.com'
    assert os.environ['AWS_DEFAULT_REGION'] in host


def test_the_bucket_name_has_no_dots_in_it():
    """
    A dotted bucket name breaks TLS against the *.s3.<region>.amazonaws.com
    wildcard certificate that the virtual-hosted URL above relies on. The deployed
    name is generated by CloudFormation, which is dot-free; this pins the test one
    to the same rule so the two cannot diverge.
    """
    assert '.' not in BUCKET_NAME


def test_the_ticket_carries_no_acl_field(handlers):
    """
    The bucket is BucketOwnerEnforced, so an `acl` field is a 400 - and anything
    extra in the form is a 403 against the policy.
    """
    create(handlers, employeeId='E1001')
    ticket = body(request_upload(handlers, 'E1001', 'resume',
                                 context=as_employee('E1001')))

    assert 'acl' not in ticket['fields']
    assert 'x-amz-server-side-encryption' not in ticket['fields']


@pytest.mark.parametrize('content_type', sorted(ALLOWED_CONTENT_TYPES))
def test_every_allowed_content_type_is_accepted(handlers, content_type):
    create(handlers, employeeId='E1001')

    response = request_upload(
        handlers, 'E1001', 'resume',
        {'filename': 'doc', 'contentType': content_type},
        as_employee('E1001'))
    assert response['statusCode'] == 200, response['body']


@pytest.mark.parametrize('content_type', [
    'application/x-msdownload', 'text/html', 'image/svg+xml', '', None, 12,
])
def test_a_content_type_outside_the_allowlist_is_400(handlers, content_type):
    create(handlers, employeeId='E1001')

    response = request_upload(
        handlers, 'E1001', 'resume',
        {'filename': 'thing.pdf', 'contentType': content_type},
        as_employee('E1001'))
    assert response['statusCode'] == 400
    assert body(response)['error']['fields']['contentType']


# ------------------------------------------------------------ filename handling

def test_a_traversing_filename_cannot_move_the_key(handlers):
    """
    The structural win: the key is built from a validated slot and the token's
    employee id, so there is no ${filename} in it to escape from. The filename is
    sanitised as well, but the key never depended on that.
    """
    create(handlers, employeeId='E1001')

    ticket = body(request_upload(
        handlers, 'E1001', 'resume',
        {'filename': '../../../etc/passwd', 'contentType': PDF},
        as_employee('E1001')))

    assert ticket['fields']['key'] == 'employees/E1001/resume'
    assert '..' not in ticket['filename']
    assert '/' not in ticket['filename']


@pytest.mark.parametrize('raw', [
    'in"jected.pdf',
    'line\r\nbreak.pdf',
    'null\x00byte.pdf',
    'C:\\Windows\\system32\\evil.pdf',
    '../../secret.pdf',
    '#anchor.pdf',
])
def test_a_hostile_filename_cannot_reach_a_header(handlers, raw):
    create(handlers, employeeId='E1001')

    response = request_upload(handlers, 'E1001', 'resume',
                              {'filename': raw, 'contentType': PDF},
                              as_employee('E1001'))
    assert response['statusCode'] in (200, 400)

    if response['statusCode'] == 400:
        return

    cleaned = body(response)['filename']
    for forbidden in ('"', '\r', '\n', '\x00', '#', '/', '\\'):
        assert forbidden not in cleaned
    # And the disposition built from it is safe to put in a header.
    disposition = content_disposition(cleaned)
    for forbidden in ('\r', '\n', '\x00'):
        assert forbidden not in disposition
    assert disposition.count('"') == 2


def test_a_filename_that_sanitises_to_nothing_is_400(handlers):
    create(handlers, employeeId='E1001')

    response = request_upload(handlers, 'E1001', 'resume',
                              {'filename': '../../', 'contentType': PDF},
                              as_employee('E1001'))
    assert response['statusCode'] == 400
    assert body(response)['error']['fields']['filename']


def test_a_very_long_filename_is_capped_but_keeps_its_extension(handlers):
    create(handlers, employeeId='E1001')

    ticket = body(request_upload(
        handlers, 'E1001', 'resume',
        {'filename': 'a' * 400 + '.pdf', 'contentType': PDF},
        as_employee('E1001')))

    assert len(ticket['filename']) <= FILENAME_MAX_LENGTH
    assert ticket['filename'].endswith('.pdf')


def test_accents_are_folded_rather_than_dropped():
    """
    S3 user metadata must be US-ASCII. Transliterating beats stripping: 'Resume'
    is a name, 'Rsum' is a bug report.
    """
    assert clean_filename('Résumé final.pdf') == 'Resume final.pdf'


def test_stored_metadata_is_sanitised_on_the_way_out_too(handlers):
    """
    The object could have been written by `aws s3 cp`, an older build or a
    migration. Trusting stored data is how a stored injection happens, so the
    read path re-cleans what it finds.
    """
    create(handlers, employeeId='E1001')
    boto3.client('s3').put_object(
        Bucket=BUCKET_NAME, Key=document_key('E1001', 'resume'),
        Body=b'x', ContentType=PDF,
        Metadata={'filename': 'bad"name.pdf'})

    filename = slot_of(docs_of(handlers, 'E1001'), 'resume')['filename']
    assert '"' not in filename
