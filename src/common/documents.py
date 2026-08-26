"""
The document store: three slots per employee, one S3 object each.

Layout, and the whole design is in it:

    employees/E1001/resume
    employees/E1001/id-document
    employees/E1001/signed-offer-letter

**The slot names are a closed vocabulary and the key is built here, never from a
request.** `document_key` takes a slot that has already been checked against
DOCUMENT_SLOTS and an employee id that came out of the caller's token, so nothing
a client sends reaches the key. That is what makes the usual presigned-POST
traversal attack a non-event: there is no `${filename}` in a key to escape from.

**Nothing is pre-created.** A slot with no upload has no object - not a zero-byte
placeholder, not a tombstone - so `head_object` 404s and describe_slots reports
`uploaded: False`. The API still returns all three slots either way, because the
UI needs to draw an empty drop zone and "absent" is the answer rather than an
error.

**One object per slot, overwritten in place.** No versioning and no timestamped
keys: a slot holds exactly one document or none, so nothing has to decide which
upload is current. The original filename cannot live in the key then, so it rides
along as `x-amz-meta-filename` and comes back out on the download.

**A key can never be inherited by a different person.** Archiving leaves the
employee's item in the table and create_employee's NOT_EXISTS guard means the
number is taken permanently - so `employees/E1001/resume` is E1001's forever. That
property depends on archiving rather than deleting; if that ever reverses, this
layout needs revisiting before the table does.

Uploads never pass through Lambda. The browser posts straight to S3 with a
presigned ticket minted by handlers/request_document_upload, which is why
API Gateway's 10 MB request cap and Lambda's 6 MB payload cap never come into it -
and why the size limit is expressed as a policy condition instead.
"""
import os
import re
import unicodedata
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

BUCKET_NAME = os.environ['BUCKET_NAME']

# Two settings, and both were learned the hard way against the real thing.
#
# signature_version, because botocore picks the signer from the region: eu-north-1
# signs v4 while the test region (ap-southeast-2) signs v2 unless told otherwise.
# Left alone, the tests would exercise a different signing path from the deployed
# stack - and a v2 presigned POST is refused outright by newer regions.
#
# addressing_style='virtual', because without it botocore builds a presigned POST
# against the legacy *global* host, `<bucket>.s3.amazonaws.com`, while signing the
# policy with a credential scoped to the bucket's real region. S3 answers that
# with a 307 redirect to the regional endpoint - and a redirect part-way through a
# cross-origin upload is not something a browser can recover from, so the upload
# simply fails. Neither passing region_name nor pinning the signature fixes it;
# the addressing style is the thing. Verified: this yields
# `<bucket>.s3.<region>.amazonaws.com`, which is also why the bucket must have no
# dots in its name - see the DocumentsBucket comment in template.yaml.
#
# No region_name and no endpoint_url on purpose. boto3 resolves the region from
# AWS_REGION in Lambda and AWS_DEFAULT_REGION under test, so naming one here would
# be a third place for it to be wrong.
_client = boto3.client('s3', config=Config(
    signature_version='s3v4',
    s3={'addressing_style': 'virtual'},
))

# The three slots, in the order they are rendered as columns. This tuple *is* the
# column order - js/ui.js mirrors it, and a test pins it, because a reshuffle is
# a product decision and nothing else in the system would notice one.
DOCUMENT_SLOTS = (
    {'slot': 'resume', 'label': 'Resume'},
    {'slot': 'id-document', 'label': 'ID document'},
    {'slot': 'signed-offer-letter', 'label': 'Signed offer letter'},
)

SLOT_IDS = frozenset(entry['slot'] for entry in DOCUMENT_SLOTS)

_PREFIX = 'employees/'

# 10 MB. Enforced by S3 itself as a policy condition on the presigned POST, not
# by this code and not only by the browser - see presigned_upload.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Five minutes, for both directions. Deliberately short in both cases, and for
# download links there is no point asking for longer: a URL signed with a
# Lambda's STS credentials stops working when those expire, whatever ExpiresIn
# says.
UPLOAD_TTL_SECONDS = 300
DOWNLOAD_TTL_SECONDS = 300

# What a new hire plausibly has to hand: a PDF, a photo of a document, or a Word
# resume. Client-asserted and unverified - this is the type the browser claims,
# pinned into the upload policy so the stored object cannot disagree with it.
# Checking magic bytes would need a Lambda triggered after the upload, which is
# more machinery than this buys.
ALLOWED_CONTENT_TYPES = {
    'application/pdf': '.pdf',
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
}

FILENAME_MAX_LENGTH = 120

# Everything outside this is dropped rather than escaped. The filename reaches
# two places that punish surprises: S3 user metadata, which must be US-ASCII, and
# a Content-Disposition response header, where CR/LF is the response-splitting
# shape and a bare double quote closes the quoted-string early.
_UNSAFE_FILENAME = re.compile(r'[^A-Za-z0-9 ._()-]')
_RUNS_OF_SPACE = re.compile(r'\s+')


def document_key(employee_id, slot):
    """The S3 key for one slot. Both arguments are already validated."""
    return _PREFIX + employee_id + '/' + slot


def slot_label(slot):
    for entry in DOCUMENT_SLOTS:
        if entry['slot'] == slot:
            return entry['label']
    return slot


def clean_filename(value):
    """
    A filename safe to store as metadata and to echo into a header, or '' if
    nothing usable survives.

    Note this is *not* what protects the key - the key never contains a filename.
    It protects the two sinks named at _UNSAFE_FILENAME, and it runs on the way
    out as well as in: describe_slots re-cleans whatever it reads off the object,
    because that metadata could have been written by `aws s3 cp`, by an older
    build, or by a migration, and trusting stored data is how a stored injection
    happens.
    """
    if not isinstance(value, str):
        return ''

    # Any directory the client put in front goes, both separators, so '../' and
    # 'C:\\Windows\\' collapse to the last segment. Belt and braces given the key
    # is built elsewhere, but a filename is not the place to leave a path.
    name = value.replace('\\', '/').rsplit('/', 1)[-1]

    # Decompose accents to their ASCII base, then drop what is left. 'Résumé.pdf'
    # becomes 'Resume.pdf' rather than 'Rsum.pdf'.
    name = unicodedata.normalize('NFKD', name)
    name = name.encode('ascii', 'ignore').decode('ascii')

    name = _UNSAFE_FILENAME.sub('', name)
    name = _RUNS_OF_SPACE.sub(' ', name)
    # Leading dots go with this, so '..' and '.hidden' cannot come out the far
    # side. Trailing dots and spaces go because Windows silently drops them.
    name = name.strip(' .')

    if not name:
        return ''

    if len(name) > FILENAME_MAX_LENGTH:
        stem, dot, extension = name.rpartition('.')
        if dot and 0 < len(extension) <= 8:
            keep = FILENAME_MAX_LENGTH - len(extension) - 1
            name = stem[:keep].strip(' .') + '.' + extension
        else:
            name = name[:FILENAME_MAX_LENGTH].strip(' .')

    return name


def fallback_filename(slot, content_type):
    """
    A name for an object whose metadata has none.

    Reachable for anything uploaded outside this app - a console upload, a
    migration - and it is a name rather than a KeyError because a document that
    is plainly there should not make the whole page fail.
    """
    return slot + ALLOWED_CONTENT_TYPES.get(content_type, '')


def validate_upload(filename, content_type):
    """{field: message} for an upload request. Empty dict means valid."""
    errors = {}

    if not filename:
        errors['filename'] = 'A file name is required.'

    if not content_type:
        errors['contentType'] = 'A file type is required.'
    elif content_type not in ALLOWED_CONTENT_TYPES:
        # Names the types rather than the rejected one. The rejected value is
        # attacker-controlled and would be echoed into a JSON body the UI renders.
        errors['contentType'] = ('Upload a PDF, JPG, PNG or Word document.')

    return errors


def content_disposition(filename):
    """
    An `attachment` disposition carrying the original name, RFC 6266 style.

    Two spellings of the same name on purpose: a quoted ASCII `filename` every
    browser understands, and a percent-encoded `filename*` for the ones that
    prefer it. `filename` has already been through clean_filename, so there is no
    quote or newline left in it to break out of the quoted-string.
    """
    return ('attachment; filename="' + filename + '"; '
            "filename*=UTF-8''" + quote(filename))


def presigned_upload(employee_id, slot, filename, content_type):
    """
    A one-shot ticket the browser posts a file to. No S3 call is made here.

    Every field the browser will send appears in **both** Fields and Conditions.
    botocore does not derive one from the other, and S3 refuses the upload either
    way round: a condition with no field is a policy failure, and a field with no
    condition is "Extra input fields". So the two lists are written together and
    have to be edited together.

    content-length-range is the reason this is a presigned POST rather than a
    presigned PUT. A PUT URL cannot express a maximum, so S3 would accept a 5 GB
    upload against it and bill for the storage; the policy below makes S3 itself
    refuse anything over MAX_UPLOAD_BYTES, which is a real control rather than the
    browser-side check in js/app.js that a devtools user can skip.

    Note what is *not* in the fields: no `acl` (the bucket sets
    BucketOwnerEnforced, so an ACL field is rejected), and no
    x-amz-server-side-encryption (bucket default encryption applies with no field,
    and adding one would mean adding a matching condition). Anything extra the
    browser sends is a 403.
    """
    return _client.generate_presigned_post(
        Bucket=BUCKET_NAME,
        Key=document_key(employee_id, slot),
        Fields={
            'Content-Type': content_type,
            'x-amz-meta-filename': filename,
        },
        Conditions=[
            {'Content-Type': content_type},
            {'x-amz-meta-filename': filename},
            ['content-length-range', 1, MAX_UPLOAD_BYTES],
        ],
        ExpiresIn=UPLOAD_TTL_SECONDS,
    )


def presigned_download(employee_id, slot, filename):
    """
    A short-lived link to one document.

    The disposition and the content type are signed along with the rest, so the
    client cannot rewrite either - which is what makes them a control and not a
    suggestion.

    ResponseContentType is pinned to octet-stream deliberately. Nothing in this
    app previews a document, and an uploaded SVG or HTML file served as its own
    type would execute against the bucket's origin. Forcing a download costs
    nothing here and closes that.
    """
    return _client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': BUCKET_NAME,
            'Key': document_key(employee_id, slot),
            'ResponseContentDisposition': content_disposition(filename),
            'ResponseContentType': 'application/octet-stream',
        },
        ExpiresIn=DOWNLOAD_TTL_SECONDS,
    )


def _iso(moment):
    """S3 hands back a datetime; the API speaks strings.

    Not a nicety: responses.py's JSON encoder knows about Decimal and nothing
    else, so an unconverted datetime is a TypeError inside json.dumps and reaches
    the caller as a bare 500. Same format as every other timestamp in this system.
    """
    return moment.isoformat(timespec='seconds').replace('+00:00', 'Z')


def describe_slots(employee_id):
    """
    All three slots for one employee, uploaded or not.

    Always three, never a filtered list - the UI draws an empty drop zone from
    `uploaded: False`, so absence has to be reported rather than omitted. An empty
    slot carries no `downloadUrl` key at all, so there is nothing for the UI to
    link to by accident.

    One HeadObject per slot. A missing object is a 404 and expected; **anything
    else is re-raised on purpose**, and the important case is a 403. S3 answers a
    HeadObject for a missing key with 403 rather than 404 when the caller cannot
    list the bucket, because it will not confirm non-existence to someone who has
    no business enumerating. So a GetDocumentsFunction missing s3:ListBucket would
    have every slot come back 403, and swallowing that here would report a
    permissions bug as "nothing uploaded yet" - forever, and silently. Letting it
    escape makes it a 500 with a stack trace in CloudWatch.
    """
    described = []

    for entry in DOCUMENT_SLOTS:
        slot = {'slot': entry['slot'], 'label': entry['label']}

        try:
            head = _client.head_object(
                Bucket=BUCKET_NAME,
                Key=document_key(employee_id, entry['slot']),
            )
        except ClientError as error:
            # The literal string '404', not 'NoSuchKey' - HeadObject has no
            # response body to put an error code in, so botocore reports the
            # status.
            if error.response['Error']['Code'] != '404':
                raise
            slot['uploaded'] = False
            described.append(slot)
            continue

        content_type = head.get('ContentType') or ''
        filename = (clean_filename((head.get('Metadata') or {}).get('filename'))
                    or fallback_filename(entry['slot'], content_type))

        slot['uploaded'] = True
        slot['filename'] = filename
        slot['contentType'] = content_type
        slot['size'] = head['ContentLength']
        slot['uploadedAt'] = _iso(head['LastModified'])
        slot['downloadUrl'] = presigned_download(employee_id, entry['slot'], filename)
        described.append(slot)

    return described
