"""
What an employee can see and change about themselves.

The read half and the write half of one feature, so they live together: the whole
point of `own_profile_view` is that both paths apply the same trim, and a suite
that tested them in two files would be the one place that never noticed they had
diverged.

Separate from test_roles.py, which asks who may call what. These ask what the
answer looks like once the caller is allowed through.

The helpers are imported rather than rewritten - `create`, `patch_contact` and the
rest already know how to build a valid request, and the only thing changing here
is the authorizer context they carry.
"""
import pytest

from common.models import (
    ARCHIVED_MESSAGE,
    OWN_PROFILE_FIELDS,
    SELF_EDITABLE_FIELDS,
    ADDRESS_MAX_LENGTH,
)
from test_handlers import (
    OFFICIAL,
    archive,
    as_employee,
    body,
    create,
    get,
    listing,
    patch,
    patch_contact,
    patch_raw,
    put,
    item_of,
    VALID,
)

# Every field HR's record carries that the employee's own view must not. Exactly
# one, and spelled out rather than derived, so that adding a field to the model
# and forgetting it here is a failing test rather than a silent pass.
HIDDEN_FROM_OWN_VIEW = ('comment',)

CONTACT = {
    'phone': '+61 400 111 222',
    'personalEmail': 'priya@example.com',
    'address': '12 Smith Street\nSydney NSW 2000',
}


def own(handlers, employee_id):
    """GET their own record, as them. Asserts the 200 and returns the body."""
    response = get(handlers, employee_id, as_employee(employee_id))
    assert response['statusCode'] == 200, response['body']
    return body(response)


# --------------------------------------------------------------------- reading


def test_an_employee_reads_their_own_whole_record(handlers):
    employee = create(handlers, employeeId='E1001')
    seen = own(handlers, 'E1001')

    assert sorted(seen) == sorted(OWN_PROFILE_FIELDS + ('checklist',))
    # Not a trimmed directory row any more: the facts a directory hid are here.
    assert seen['email'] == employee['email']
    assert seen['manager'] == employee['manager']
    assert seen['employmentType'] == employee['employmentType']
    assert len(seen['checklist']) == len(employee['checklist'])


def test_the_own_view_names_its_fields_rather_than_deleting_them(handlers):
    """
    The fail-safe property, asserted rather than trusted.

    A field added to the model must be invisible to the employee until somebody
    names it in OWN_PROFILE_FIELDS. If own_profile_view ever grew a `del` list
    instead, this is the test that would still pass and the next one that would
    not - which is why both exist.
    """
    create(handlers, employeeId='E1001')
    seen = own(handlers, 'E1001')
    assert set(seen) - set(OWN_PROFILE_FIELDS) == {'checklist'}


@pytest.mark.parametrize('field', HIDDEN_FROM_OWN_VIEW)
def test_a_checklist_item_hides_each_hr_only_field(handlers, field):
    create(handlers, employeeId='E1001')
    assert patch_raw(handlers, 'E1001', 'offer-letter',
                     {'comment': 'chased payroll twice'})['statusCode'] == 200

    # The comment is on the record - HR can read it back.
    assert item_of(body(get(handlers, 'E1001')), 'offer-letter')['comment'] == \
        'chased payroll twice'

    for item in own(handlers, 'E1001')['checklist']:
        assert field not in item


def test_an_employee_sees_their_own_ticks_and_progress(handlers):
    """
    The comment is hidden; the tick is not. Onboarding progress is the one thing
    this app exists to show, and a profile that cannot say whether payroll has
    your bank details is not worth signing in to.
    """
    create(handlers, employeeId='E1001')
    assert patch(handlers, 'E1001', 'offer-letter', True)['statusCode'] == 200

    seen = own(handlers, 'E1001')
    assert item_of(seen, 'offer-letter')['done'] is True
    assert item_of(seen, 'offer-letter')['owner']
    assert seen['progress']['done'] == 1
    assert seen['status'] == 'In Progress'


def test_progress_is_computed_before_the_trim(handlers):
    """
    status and progress must describe the whole checklist, not the trimmed copy.
    They agree with what HR sees or they are worse than useless.
    """
    create(handlers, employeeId='E1001')
    assert patch(handlers, 'E1001', 'offer-letter', True)['statusCode'] == 200
    assert patch_raw(handlers, 'E1001', 'laptop',
                     {'comment': 'ordered'})['statusCode'] == 200

    official_view = body(get(handlers, 'E1001'))
    assert own(handlers, 'E1001')['progress'] == official_view['progress']
    assert own(handlers, 'E1001')['status'] == official_view['status']


def test_reading_somebody_elses_record_is_403(handlers):
    create(handlers, employeeId='E1001')
    create(handlers, employeeId='E1002')

    response = get(handlers, 'E1002', as_employee('E1001'))
    assert response['statusCode'] == 403


def test_a_record_that_does_not_exist_is_the_same_403(handlers):
    """
    Byte-identical to the refusal above, because require_self runs before the
    read. Otherwise this endpoint tells an employee which numbers are real, one
    guess at a time.
    """
    create(handlers, employeeId='E1001')

    missing = get(handlers, 'E9999', as_employee('E1001'))
    existing = get(handlers, 'E1002', as_employee('E1001'))

    assert missing['statusCode'] == existing['statusCode'] == 403
    assert missing['body'] == existing['body']


def test_a_refused_read_leaks_no_employee_data(handlers):
    create(handlers, employeeId='E1002', firstName='Meera', lastName='Nair')
    response = get(handlers, 'E1002', as_employee('E1001'))

    assert response['statusCode'] == 403
    assert 'Meera' not in response['body']
    assert 'Nair' not in response['body']


def test_an_employee_reads_their_own_record_case_insensitively(handlers):
    """
    The token's `sub` is upper-cased at login and the path is folded on the way
    in, so a lower-case URL still finds the caller's own record.
    """
    create(handlers, employeeId='E1001')
    response = get(handlers, 'e1001', as_employee('e1001'))
    assert response['statusCode'] == 200


def test_an_employee_reads_their_own_archived_record(handlers):
    """
    Archived used to read as 404 to the employee role, to stop them browsing
    ex-colleagues. require_self closes that door properly, and a 404 here would
    only be telling somebody their own record does not exist.
    """
    create(handlers, employeeId='E1001')
    archive(handlers, 'E1001')

    seen = own(handlers, 'E1001')
    assert seen['archived'] is True
    assert seen['archivedAs']


def test_the_directory_is_not_readable_by_an_employee(handlers):
    create(handlers, employeeId='E1001')

    assert listing(handlers, as_employee('E1001'))['statusCode'] == 403
    assert listing(handlers, OFFICIAL)['statusCode'] == 200


# --------------------------------------------------------------------- writing


def test_an_employee_fills_in_their_own_contact_details(handlers):
    create(handlers, employeeId='E1001')

    response = patch_contact(handlers, 'E1001', CONTACT, as_employee('E1001'))
    assert response['statusCode'] == 200, response['body']

    saved = body(response)
    for field, value in CONTACT.items():
        assert saved[field] == value
    # And it is on the record, not just in the response.
    assert own(handlers, 'E1001')['personalEmail'] == CONTACT['personalEmail']


def test_the_patch_response_is_the_same_shape_as_the_read(handlers):
    """
    The write path applies the same trim as the read path. If it ever returned its
    own re-read instead, it would hand back every checklist comment on the record
    with a 200 that looked entirely correct.
    """
    create(handlers, employeeId='E1001')
    assert patch_raw(handlers, 'E1001', 'offer-letter',
                     {'comment': 'chased payroll twice'})['statusCode'] == 200

    saved = body(patch_contact(handlers, 'E1001', {'phone': '+61 400 000 000'},
                               as_employee('E1001')))

    assert sorted(saved) == sorted(OWN_PROFILE_FIELDS + ('checklist',))
    assert 'chased payroll twice' not in str(saved)
    for item in saved['checklist']:
        assert 'comment' not in item


@pytest.mark.parametrize('field,value', [
    ('firstName', 'Somebody'),
    ('lastName', 'Else'),
    ('email', 'promoted@breville.com'),
    ('department', 'Finance'),
    ('jobTitle', 'Chief Executive'),
    ('manager', 'Nobody'),
    ('startDate', '2020-01-01'),
    ('employmentType', 'Contract'),
    ('employeeId', 'E9999'),
])
def test_an_employee_cannot_change_an_hr_owned_field(handlers, field, value):
    """
    One assertion per field, so a leak names itself. The whitelist drops these
    rather than rejecting them, exactly as HR's PUT drops `checklist`.
    """
    created = create(handlers, employeeId='E1001')

    response = patch_contact(handlers, 'E1001',
                             {'phone': '+61 400 000 000', field: value},
                             as_employee('E1001'))
    assert response['statusCode'] == 200, response['body']

    after = own(handlers, 'E1001')
    assert after['phone'] == '+61 400 000 000'
    if field == 'employeeId':
        assert after['id'] == created['id']
    else:
        assert after[field] == created[field]


def test_an_employee_cannot_rewrite_their_own_checklist(handlers):
    create(handlers, employeeId='E1001')
    assert patch(handlers, 'E1001', 'offer-letter', True)['statusCode'] == 200

    response = patch_contact(handlers, 'E1001',
                             {'phone': '+61 400 000 000', 'checklist': []},
                             as_employee('E1001'))
    assert response['statusCode'] == 200, response['body']

    official_view = body(get(handlers, 'E1001'))
    assert len(official_view['checklist']) == 8
    assert item_of(official_view, 'offer-letter')['done'] is True
    assert official_view['progress']['done'] == 1


def test_a_contact_patch_preserves_the_checklist_comments(handlers):
    create(handlers, employeeId='E1001')
    assert patch_raw(handlers, 'E1001', 'laptop',
                     {'comment': 'ordered, arriving Tuesday'})['statusCode'] == 200

    assert patch_contact(handlers, 'E1001', CONTACT,
                         as_employee('E1001'))['statusCode'] == 200

    assert item_of(body(get(handlers, 'E1001')), 'laptop')['comment'] == \
        'ordered, arriving Tuesday'


def test_patching_somebody_elses_contact_details_is_403(handlers):
    create(handlers, employeeId='E1001')
    created = create(handlers, employeeId='E1002')

    response = patch_contact(handlers, 'E1002', CONTACT, as_employee('E1001'))
    assert response['statusCode'] == 403

    assert body(get(handlers, 'E1002'))['phone'] == created['phone']


def test_an_official_has_no_business_on_this_route(handlers):
    """They have PUT. One route, one audience."""
    create(handlers, employeeId='E1001')
    assert patch_contact(handlers, 'E1001', CONTACT, OFFICIAL)['statusCode'] == 403


def test_a_request_with_no_authorizer_context_is_refused(handlers):
    create(handlers, employeeId='E1001')
    response = handlers['update_own_contact'](
        {'pathParameters': {'id': 'E1001'}, 'body': '{"phone": "1"}'}, None)
    assert response['statusCode'] == 403


def test_patching_a_record_that_does_not_exist_is_404(handlers):
    """
    The end of the login-does-not-verify-existence path: `E9999` signs in fine,
    and this is where it finds out.
    """
    response = patch_contact(handlers, 'E9999', CONTACT, as_employee('E9999'))
    assert response['statusCode'] == 404
    # And the write did not upsert a half-employee behind it.
    assert get(handlers, 'E9999')['statusCode'] == 404


def test_patching_an_archived_record_is_409(handlers):
    create(handlers, employeeId='E1001')
    archive(handlers, 'E1001')

    response = patch_contact(handlers, 'E1001', CONTACT, as_employee('E1001'))
    assert response['statusCode'] == 409
    assert ARCHIVED_MESSAGE in response['body']


def test_a_field_sent_empty_is_cleared(handlers):
    create(handlers, employeeId='E1001')
    assert patch_contact(handlers, 'E1001', CONTACT,
                         as_employee('E1001'))['statusCode'] == 200

    saved = body(patch_contact(handlers, 'E1001', {'address': ''},
                               as_employee('E1001')))
    assert saved['address'] == ''
    # The others were not named, so they are untouched.
    assert saved['personalEmail'] == CONTACT['personalEmail']


def test_clearing_every_named_field_is_still_a_valid_write(handlers):
    """
    A REMOVE-only update expression would be a syntax error. `updatedAt` is always
    in the SET clause, which is what keeps this a 200.
    """
    create(handlers, employeeId='E1001')
    assert patch_contact(handlers, 'E1001', CONTACT,
                         as_employee('E1001'))['statusCode'] == 200

    response = patch_contact(handlers, 'E1001',
                             {'phone': '', 'personalEmail': '', 'address': ''},
                             as_employee('E1001'))
    assert response['statusCode'] == 200, response['body']
    saved = body(response)
    for field in SELF_EDITABLE_FIELDS:
        assert saved[field] == ''


def test_a_body_naming_none_of_the_three_is_400(handlers):
    create(handlers, employeeId='E1001')
    response = patch_contact(handlers, 'E1001', {'department': 'HR'},
                             as_employee('E1001'))
    assert response['statusCode'] == 400


def test_a_malformed_personal_email_is_400_under_its_own_field(handlers):
    create(handlers, employeeId='E1001')
    response = patch_contact(handlers, 'E1001', {'personalEmail': 'not-an-email'},
                             as_employee('E1001'))

    assert response['statusCode'] == 400
    assert body(response)['error']['fields']['personalEmail']


def test_an_over_long_address_is_400(handlers):
    create(handlers, employeeId='E1001')
    response = patch_contact(handlers, 'E1001',
                             {'address': 'x' * (ADDRESS_MAX_LENGTH + 1)},
                             as_employee('E1001'))

    assert response['statusCode'] == 400
    assert body(response)['error']['fields']['address']


def test_a_non_string_value_is_refused_rather_than_clearing_the_field(handlers):
    """
    pick_editable coerces a non-string to '' because a form always sends all nine
    fields. Here the same coercion would turn a client bug into a deletion.
    """
    create(handlers, employeeId='E1001')
    assert patch_contact(handlers, 'E1001', CONTACT,
                         as_employee('E1001'))['statusCode'] == 200

    response = patch_contact(handlers, 'E1001', {'phone': 5},
                             as_employee('E1001'))
    assert response['statusCode'] == 400
    assert body(response)['error']['fields']['phone']
    assert own(handlers, 'E1001')['phone'] == CONTACT['phone']


def test_hr_can_still_replace_the_profile_without_losing_the_self_fields(handlers):
    """
    personalEmail and address are absent from EDITABLE_FIELDS, so HR's full
    replace never names them - which is exactly why a PUT cannot blank them.
    """
    create(handlers, employeeId='E1001')
    assert patch_contact(handlers, 'E1001', CONTACT,
                         as_employee('E1001'))['statusCode'] == 200

    response = put(handlers, 'E1001', dict(VALID, jobTitle='Senior Engineer'))
    assert response['statusCode'] == 200, response['body']

    after = body(response)
    assert after['jobTitle'] == 'Senior Engineer'
    assert after['personalEmail'] == CONTACT['personalEmail']
    assert after['address'] == CONTACT['address']
