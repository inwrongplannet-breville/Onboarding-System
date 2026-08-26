"""
Unit tests for the pure logic in common/. No AWS, no network, no credentials -
these are the parts worth testing offline. Everything that talks to DynamoDB is
verified against a real dev stack instead, because the failure mode there is IAM,
which no emulator reproduces.
"""
import pytest

from common.checklist_template import CHECKLIST_INDEX, CHECKLIST_TEMPLATE, VALID_ITEM_IDS
from common.keys import employee_id_from_pk, key, pk
from common.models import (
    ADDRESS_MAX_LENGTH,
    ARCHIVED_CANCELLED,
    ARCHIVED_ONBOARDED,
    EDITABLE_FIELDS,
    OWN_PROFILE_FIELDS,
    PHONE_MAX_LENGTH,
    PROFILE_FIELDS,
    REQUIRED_FIELDS,
    SELF_EDITABLE_FIELDS,
    archive_state,
    clean_comment,
    clean_employee_id,
    derive_status,
    is_archived,
    new_checklist_items,
    own_checklist_item,
    own_profile_view,
    pick_editable,
    pick_self,
    progress,
    to_api_checklist_item,
    to_api_employee,
    validate_employee,
    validate_employee_id,
    validate_self_fields,
)

VALID = {
    'firstName': 'Priya', 'lastName': 'Sharma',
    'email': 'priya.sharma@breville.com', 'phone': '+61 412 883 016',
    'department': 'Engineering', 'jobTitle': 'Software Engineer',
    'manager': 'Santosh Kumar', 'startDate': '2026-07-06',
    'employmentType': 'Full-time',
}


# ----------------------------------------------------------------------- keys

def test_round_trips_the_employee_id_through_the_partition_key():
    assert employee_id_from_pk(pk('E1024')) == 'E1024'


def test_a_hyphenated_id_survives_the_round_trip():
    # The prefix is stripped by length, not by splitting on the separator, so a
    # hyphen in the id is not a place the key could be cut in the wrong spot.
    assert employee_id_from_pk(pk('BRV-1024')) == 'BRV-1024'


# ---------------------------------------------------------- the employee id

@pytest.mark.parametrize('raw, expected', [
    ('E1024', 'E1024'),
    ('e1024', 'E1024'),
    ('  e1024  ', 'E1024'),
    ('brv-1024', 'BRV-1024'),
])
def test_an_employee_id_is_trimmed_and_upper_cased(raw, expected):
    assert clean_employee_id(raw) == expected


@pytest.mark.parametrize('raw', [None, 1024, ['E1024'], {'id': 'E1024'}, True])
def test_a_non_string_employee_id_collapses_to_empty_rather_than_raising(raw):
    # It becomes "required" further down instead of a 500 out of the handler.
    assert clean_employee_id(raw) == ''


@pytest.mark.parametrize('employee_id', ['E1', 'E1024', 'BRV-1024', '1024', 'A' * 20])
def test_accepts_a_well_formed_employee_id(employee_id):
    assert validate_employee_id(employee_id) is None


@pytest.mark.parametrize('employee_id', ['', 'E', 'A' * 21, 'E 1024', 'E#1024',
                                         '-E1024', 'E1024!', 'E_1024', 'e1024'])
def test_rejects_a_malformed_employee_id(employee_id):
    # Note 'e1024' is on this list. The pattern is upper-case only by design -
    # clean_employee_id runs first, so anything reaching here in lower case
    # skipped the fold and should not be trusted to be the id it looks like.
    assert validate_employee_id(employee_id) is not None


def test_the_pattern_refuses_the_key_separator():
    # '#' separates the prefix from the id. Letting one through would let a
    # caller write "EMP#EMP#x" and address a key the rest of the system does not
    # believe exists.
    assert validate_employee_id('EMP#X') is not None


def test_a_missing_id_says_it_is_required_rather_than_describing_the_format():
    assert validate_employee_id('') == 'Employee ID is required.'


# --------------------------------------------------------------- whitelisting

def test_drops_fields_that_are_not_on_the_whitelist():
    picked = pick_editable(dict(VALID, id='hacked', employeeId='E9999',
                                checklist=[{'id': 'laptop'}]))
    assert 'id' not in picked
    assert 'checklist' not in picked
    # The one that matters most: this whitelist is what PUT builds its SET clause
    # from, and the id is the partition key. It must not be reachable from a body.
    assert 'employeeId' not in picked


def test_trims_whitespace_and_defaults_missing_fields_to_empty():
    picked = pick_editable({'firstName': '  Priya  '})
    assert picked['firstName'] == 'Priya'
    assert picked['manager'] == ''


# ----------------------------------------------------------------- validation

def test_accepts_a_complete_valid_employee():
    assert validate_employee(VALID) == {}


def test_treats_phone_and_manager_as_optional():
    assert validate_employee(dict(VALID, phone='', manager='')) == {}


@pytest.mark.parametrize('field', [
    'firstName', 'lastName', 'email', 'department',
    'jobTitle', 'startDate', 'employmentType',
])
def test_rejects_each_missing_required_field(field):
    assert field in validate_employee(dict(VALID, **{field: ''}))


@pytest.mark.parametrize('email', ['nope', 'no@domain', 'spaces in@it.com', '@breville.com'])
def test_rejects_malformed_emails(email):
    assert 'email' in validate_employee(dict(VALID, email=email))


def test_rejects_a_department_outside_the_enum():
    assert 'department' in validate_employee(dict(VALID, department='Marketing'))


def test_rejects_an_employment_type_outside_the_enum():
    assert 'employmentType' in validate_employee(dict(VALID, employmentType='Casual'))


@pytest.mark.parametrize('date', ['06/07/2026', '2026-7-6', 'soon', '20260706'])
def test_rejects_start_dates_that_are_not_iso(date):
    assert 'startDate' in validate_employee(dict(VALID, startDate=date))


@pytest.mark.parametrize('date', ['2026-13-01', '2026-02-30', '2026-00-10', '2027-02-29'])
def test_rejects_dates_that_are_the_right_shape_and_still_not_real(date):
    # The regex alone accepted every one of these. 2027 is not a leap year.
    assert 'startDate' in validate_employee(dict(VALID, startDate=date))


@pytest.mark.parametrize('date', ['2026-07-06', '2024-02-29', '2026-12-31'])
def test_accepts_real_dates_including_a_leap_day(date):
    assert validate_employee(dict(VALID, startDate=date)) == {}


# ------------------------------------------------------------ derived status

def test_status_is_pending_when_nothing_is_ticked():
    assert derive_status([{'done': False}, {'done': False}]) == 'Pending'


def test_status_is_in_progress_when_some_are_ticked():
    assert derive_status([{'done': True}, {'done': False}]) == 'In Progress'


def test_status_is_onboarded_only_when_all_are_ticked():
    assert derive_status([{'done': True}, {'done': True}]) == 'Onboarded'


def test_status_of_an_empty_checklist_is_pending_not_onboarded():
    # The trap: 0 of 0 is arithmetically "complete". It is not onboarded.
    assert derive_status([]) == 'Pending'


def test_progress_reports_counts_and_a_rounded_percentage():
    assert progress([{'done': True}, {'done': False}, {'done': False}]) == {
        'done': 1, 'total': 3, 'percent': 33,
    }


def test_progress_of_an_empty_checklist_does_not_divide_by_zero():
    assert progress([]) == {'done': 0, 'total': 0, 'percent': 0}


@pytest.mark.parametrize('done,percent', [
    (0, 0), (1, 13), (2, 25), (3, 38), (4, 50), (5, 63), (6, 75), (7, 88), (8, 100),
])
def test_percent_matches_javascript_math_round_at_every_step(done, percent):
    # 1/8 and 5/8 land exactly on .5, where Python's round() rounds to even and
    # JS Math.round rounds up. The server must agree with App.progress in
    # js/data.js or the UI and the API would report different numbers.
    checklist = [{'done': i < done} for i in range(8)]
    assert progress(checklist)['percent'] == percent


# ------------------------------------------------------------------ template

def test_the_template_has_the_eight_agreed_items_in_order():
    assert [item['id'] for item in CHECKLIST_TEMPLATE] == [
        'offer-letter', 'id-proof', 'bank-details', 'laptop',
        'email-account', 'access-card', 'induction', 'policy-ack',
    ]
    assert [item['order'] for item in CHECKLIST_TEMPLATE] == list(range(1, 9))
    assert VALID_ITEM_IDS == {item['id'] for item in CHECKLIST_TEMPLATE}


def test_a_new_hire_starts_with_every_item_unticked():
    items = new_checklist_items()
    assert len(items) == 8
    assert all(item['done'] is False for item in items)


# -------------------------------------------------------------- comments

@pytest.mark.parametrize('raw,expected', [
    ('  Chased payroll.  ', 'Chased payroll.'),
    (None, ''),
    ('', ''),
    ('   ', ''),
])
def test_comments_are_trimmed_and_none_means_cleared(raw, expected):
    assert clean_comment(raw) == expected


def test_an_item_with_no_comment_attribute_reports_an_empty_one():
    item = {'itemId': 'laptop', 'label': 'Laptop issued', 'owner': 'IT', 'done': False}
    assert to_api_checklist_item(item)['comment'] == ''


def test_a_stored_comment_is_passed_through():
    item = {'itemId': 'laptop', 'label': 'Laptop issued', 'owner': 'IT', 'done': True,
            'comment': 'Dell, collected Friday.'}
    assert to_api_checklist_item(item)['comment'] == 'Dell, collected Friday.'


# --------------------------------------------------------------- archive state

def _checklist(done_count):
    return [{'done': i < done_count} for i in range(8)]


def test_an_untouched_checklist_archives_as_cancelled():
    assert archive_state(_checklist(0)) == ARCHIVED_CANCELLED


@pytest.mark.parametrize('done', [1, 4, 7])
def test_a_partly_done_checklist_archives_as_cancelled(done):
    assert archive_state(_checklist(done)) == ARCHIVED_CANCELLED


def test_a_finished_checklist_archives_as_onboarded():
    assert archive_state(_checklist(8)) == ARCHIVED_ONBOARDED


def test_archive_state_agrees_with_derive_status_at_every_step():
    # The two must never disagree about what "complete" means - archive_state is
    # written in terms of derive_status precisely so they cannot.
    for done in range(9):
        checklist = _checklist(done)
        onboarded = derive_status(checklist) == 'Onboarded'
        assert (archive_state(checklist) == ARCHIVED_ONBOARDED) is onboarded


def test_an_empty_checklist_archives_as_cancelled_not_onboarded():
    # Mirrors derive_status: nothing to do is not the same as everything done.
    assert archive_state([]) == ARCHIVED_CANCELLED


def test_a_profile_with_no_stamp_is_active():
    assert is_archived({'firstName': 'Priya'}) is False
    assert is_archived({'archivedAs': ''}) is False


def test_a_stamped_profile_is_archived():
    assert is_archived({'archivedAs': ARCHIVED_CANCELLED}) is True


# ------------------------------------------------------------ item -> API shape

def _item(done_ids=(), employee_id='E1024'):
    """One stored employee item, which is now the whole employee."""
    return dict(
        VALID,
        # **key(...) rather than a literal, so the fixture cannot go on
        # describing a key attribute the handlers have stopped writing.
        **key(employee_id),
        employeeId=employee_id,
        entityType='Employee',
        checklist=[
            dict(entry, done=entry['itemId'] in done_ids)
            for entry in new_checklist_items()
        ],
    )


def test_rebuilds_exactly_the_shape_the_phase_1_frontend_expects():
    employee = to_api_employee(_item(done_ids={'offer-letter', 'id-proof'}))

    assert employee['id'] == 'E1024'
    assert employee['firstName'] == 'Priya'
    assert set(employee['checklist'][0]) == {'id', 'label', 'owner', 'done', 'comment'}
    assert employee['checklist'][0]['id'] == 'offer-letter'
    assert employee['status'] == 'In Progress'
    assert employee['progress']['done'] == 2


def test_an_unstamped_employee_reports_itself_active_on_the_wire():
    employee = to_api_employee(_item())
    assert employee['archived'] is False
    assert employee['archivedAs'] == ''
    assert employee['archivedAt'] == ''


def test_a_stamped_employee_carries_its_archive_state_onto_the_wire():
    item = _item(done_ids={'offer-letter'})
    item['archivedAs'] = ARCHIVED_CANCELLED
    item['archivedAt'] = '2026-08-24T02:15:00Z'

    employee = to_api_employee(item)
    assert employee['archived'] is True
    assert employee['archivedAs'] == ARCHIVED_CANCELLED
    assert employee['archivedAt'] == '2026-08-24T02:15:00Z'


def test_archiving_does_not_disturb_the_derived_status():
    # `status` keeps describing the checklist; `archivedAs` describes the
    # decision. An archived record showing "In Progress" is correct, not a bug.
    item = _item(done_ids={'offer-letter', 'id-proof'})
    item['archivedAs'] = ARCHIVED_CANCELLED

    employee = to_api_employee(item)
    assert employee['status'] == 'In Progress'
    assert employee['progress']['done'] == 2


def test_never_leaks_internal_attributes_onto_the_wire():
    employee = to_api_employee(_item())
    for internal in ('employeeKey', 'PK', 'SK', 'employeeId', 'entityType', 'order'):
        assert internal not in employee


def test_the_embedded_list_is_read_in_stored_order():
    # Position is the order now - there is no sort step to get this right, so the
    # only thing keeping the UI's checklist in the agreed sequence is that the
    # list was written in template order and DynamoDB preserves list order.
    employee = to_api_employee(_item())
    assert [entry['id'] for entry in employee['checklist']] == [
        item['id'] for item in CHECKLIST_TEMPLATE
    ]


def test_an_employee_with_no_checklist_attribute_is_still_an_employee():
    # Not reachable through the API, but a hand-edited item should degrade to an
    # empty checklist rather than raising - to_api_employee is on the read path
    # of every endpoint.
    item = _item()
    del item['checklist']
    employee = to_api_employee(item)
    assert employee['checklist'] == []
    assert employee['status'] == 'Pending'


def test_an_unknown_id_yields_no_item_and_therefore_no_employee():
    # GetItem omits `Item` entirely for a miss. Five callers turn this None into
    # a 404, so it is a contract rather than a convenience.
    assert to_api_employee(None) is None
    assert to_api_employee({}) is None


# --------------------------------------------------------- positional invariant

def test_the_index_map_matches_the_template_order():
    # CHECKLIST_INDEX is what lets a PATCH write checklist[i] without reading the
    # list first. If it ever disagreed with the order new_checklist_items() writes,
    # every tick would land on the wrong box - so pin them to each other.
    assert CHECKLIST_INDEX == {
        item['id']: index for index, item in enumerate(CHECKLIST_TEMPLATE)
    }
    for index, entry in enumerate(new_checklist_items()):
        assert CHECKLIST_INDEX[entry['itemId']] == index


def test_the_stored_order_field_agrees_with_the_list_position():
    # `order` is dead weight that nothing reads, kept only so a raw item is
    # legible in the console. This stops it drifting into a lie.
    for index, entry in enumerate(new_checklist_items()):
        assert entry['order'] == index + 1


# ------------------------------------------------------- who owns which field

def test_the_three_field_lists_are_what_the_frontend_mirrors():
    """
    Pinned literally, because no pytest can see js/store.js. A change to any of
    these is a deliberate two-file edit, and this is the test that says so.
    """
    assert EDITABLE_FIELDS == (
        'firstName', 'lastName', 'email', 'phone',
        'department', 'jobTitle', 'manager', 'startDate', 'employmentType',
    )
    assert SELF_EDITABLE_FIELDS == ('phone', 'personalEmail', 'address')
    assert PROFILE_FIELDS == EDITABLE_FIELDS + ('personalEmail', 'address')


def test_hr_cannot_set_the_two_self_service_fields():
    """
    The mechanism, not a policy. personalEmail and address are absent from
    EDITABLE_FIELDS, so no expression HR's PUT builds can ever name them - which
    is also why a full replace cannot blank them.
    """
    for field in ('personalEmail', 'address'):
        assert field not in EDITABLE_FIELDS
        assert field not in [name for name, _ in REQUIRED_FIELDS]

    picked = pick_editable({'personalEmail': 'x@example.com', 'address': 'somewhere'})
    assert 'personalEmail' not in picked
    assert 'address' not in picked


# ------------------------------------------------------------------ pick_self

def test_pick_self_keeps_only_the_keys_the_body_named():
    """
    PATCH semantics need the difference between absent and empty: absent is left
    alone, empty is cleared.
    """
    assert pick_self({'phone': ' +61 400 '}) == {'phone': '+61 400'}
    assert pick_self({'address': ''}) == {'address': ''}
    assert pick_self({}) == {}


def test_pick_self_drops_every_field_it_does_not_own():
    assert pick_self({'firstName': 'Nope', 'department': 'HR', 'checklist': []}) == {}


def test_pick_self_treats_null_as_cleared():
    assert pick_self({'personalEmail': None}) == {'personalEmail': ''}


def test_pick_self_raises_on_a_non_string_rather_than_coercing():
    """
    pick_editable coerces to '' because a form always sends all nine fields. The
    same coercion here would turn a malformed request into a deletion.
    """
    with pytest.raises(ValueError) as raised:
        pick_self({'phone': 5, 'address': ['a']})

    assert set(raised.value.args[0]) == {'phone', 'address'}


# --------------------------------------------------------- validate_self_fields

def test_every_self_field_is_optional():
    assert validate_self_fields({}) == {}
    assert validate_self_fields({'phone': '', 'personalEmail': '', 'address': ''}) == {}


def test_a_personal_email_must_look_like_one():
    assert 'personalEmail' in validate_self_fields({'personalEmail': 'nope'})
    assert validate_self_fields({'personalEmail': 'priya@example.com'}) == {}


@pytest.mark.parametrize('field,cap', [
    ('address', ADDRESS_MAX_LENGTH),
    ('phone', PHONE_MAX_LENGTH),
])
def test_a_self_field_over_its_cap_is_reported(field, cap):
    assert validate_self_fields({field: 'x' * cap}) == {}
    assert field in validate_self_fields({field: 'x' * (cap + 1)})


# --------------------------------------------------------- the own-profile view

def _api_employee():
    """One API employee carrying both self-service fields and an HR comment."""
    item = dict(_item(done_ids={'offer-letter'}),
                personalEmail='priya@example.com',
                address='12 Smith St')
    item['checklist'][0]['comment'] = 'chased payroll twice'
    return to_api_employee(item)


def test_the_own_profile_view_carries_every_named_field():
    view = own_profile_view(_api_employee())
    assert sorted(view) == sorted(OWN_PROFILE_FIELDS + ('checklist',))


def test_the_own_profile_view_is_built_by_naming_not_deleting():
    """
    The fail-safe property. A field added to the model is invisible to the
    employee until somebody names it in OWN_PROFILE_FIELDS - where a `del` list
    would leak it from the moment it existed.
    """
    employee = _api_employee()
    employee['secretNewField'] = 'should not travel'

    assert 'secretNewField' not in own_profile_view(employee)


def test_the_own_profile_view_drops_the_checklist_comments():
    view = own_profile_view(_api_employee())

    assert view['checklist'][0]['done'] is True
    assert 'chased payroll twice' not in str(view)
    for item in view['checklist']:
        assert sorted(item) == ['done', 'id', 'label', 'owner']


def test_the_own_profile_view_of_nothing_is_nothing():
    """The None contract to_api_employee already keeps - five callers branch on it."""
    assert own_profile_view(None) is None


def test_own_checklist_item_builds_a_fresh_dict():
    entry = to_api_checklist_item(dict(new_checklist_items()[0], comment='note'))
    trimmed = own_checklist_item(entry)

    assert 'comment' not in trimmed
    # Not a filtered view of the original - mutating one must not reach the other.
    trimmed['done'] = True
    assert entry['done'] is False


def test_the_self_fields_round_trip_as_empty_when_absent():
    """
    Absent and empty mean the same thing to a reader - the same rule the file
    already applies to archivedAs and to a checklist comment.
    """
    employee = to_api_employee(_item())
    assert employee['personalEmail'] == ''
    assert employee['address'] == ''
