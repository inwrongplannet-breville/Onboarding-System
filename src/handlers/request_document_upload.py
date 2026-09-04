"""
POST /employees/{id}/documents/{slot} - mint a ticket to upload one document.

This route does not receive a file. It returns a presigned POST that the browser
then sends the file to directly, so the bytes never pass through API Gateway or
Lambda. That is not an optimisation: API Gateway caps a request at 10 MB, Lambda
at 6 MB, and base64-ing a binary through a JSON API inflates it by a third on the
way. See common/documents.presigned_upload for what is in the ticket and why the
size limit lives in its policy.

Employee only, and their own record only. An official gets a 403 with no special
case needed - `hr.admin` folds to something that is not an employee number, so
require_self refuses it - and that refusal is the product decision made
structural: HR can read every document and mint no ticket for any of them, so a
document's presence is evidence the *employee* supplied it. The upload function's
IAM has no s3:GetObject either, so this route cannot even read back what it
authorised.

One race worth naming, because it cannot be closed from here. A ticket is valid
for five minutes and S3 knows nothing about archive state, so an employee archived
inside that window can still land an object on a frozen record. Closing it would
mean proxying uploads through Lambda, which is the thing presigned POST exists to
avoid. The TTL is the mitigation.
"""
from common import responses
from common.documents import (
    SLOT_IDS,
    clean_filename,
    presigned_upload,
    validate_upload,
)
from common.handler import (
    api_handler,
    employee_id_param,
    parse_body,
    path_param,
    require_role,
    require_self,
)
from common.repository import find_record

# Deliberately does not repeat the slot the caller sent. That value is
# attacker-controlled and this message ends up in a JSON body the UI renders, so
# it names what is valid instead of echoing what was not.
_UNKNOWN_SLOT = 'Unknown document slot.'


@api_handler
def lambda_handler(event, context):
    # Identity before anything else. A caller who was never going to be allowed
    # to write this record gets a 403 about their account, not a 400 about a body
    # nobody was going to read - the same ordering handlers/create_employee uses.
    require_role(event)
    employee_id = employee_id_param(event)
    require_self(event, employee_id)

    slot = path_param(event, 'slot')
    if slot not in SLOT_IDS:
        return responses.bad_request(_UNKNOWN_SLOT, {'slot': _UNKNOWN_SLOT})

    # The employee has to exist and must not be archived, and this is the only
    # DynamoDB call in the file. Without it S3 would happily take an object at
    # employees/E9999/resume - and E9999 *can* sign in, because POST /login has no
    # table access by design and so cannot check a number is real.
    #
    # find_record(), not load_archive_state(): a promoted employee's documents
    # live at the same S3 key as before (employees/<id>/... never moved), but
    # their profile is no longer in the onboarding table, and load_archive_state
    # only ever looked there. A promoted or intern record can never be archived,
    # so the None check is the only one that still applies to those two sources.
    employee, source = find_record(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')
    if employee.get('archived'):
        return responses.conflict('This employee is archived. Their record is read-only.')

    body = parse_body(event)
    filename = clean_filename(body.get('filename'))
    content_type = body.get('contentType')

    errors = validate_upload(filename, content_type)
    if errors:
        return responses.bad_request('That file cannot be uploaded.', errors)

    ticket = presigned_upload(employee_id, slot, filename, content_type)

    # `filename` is echoed back as the *cleaned* value, not the one that was sent.
    # The browser needs it to render the slot optimistically, and it should be
    # looking at what will actually be stored rather than what it asked for.
    return responses.ok({
        'slot': slot,
        'filename': filename,
        'url': ticket['url'],
        'fields': ticket['fields'],
    })
