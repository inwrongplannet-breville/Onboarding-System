"""
Unit tests for the pure logic in common/. No AWS, no network, no credentials -
these are the parts worth testing offline. Everything that talks to DynamoDB is
verified against a real dev stack instead, because the failure mode there is IAM,
which no emulator reproduces.
"""
import pytest

from common.checklist_template import CHECKLIST_INDEX, CHECKLIST_TEMPLATE, VALID_ITEM_IDS
from common.keys import employee_id_from_pk, pk
from common.models import (
    ARCHIVED_CANCELLED,
    ARCHIVED_ONBOARDED,
    archive_state,
    clean_comment,
    derive_status,
    is_archived,
    new_checklist_items,
    pick_editable,
    progress,
    to_api_checklist_item,
    to_api_employee,
    validate_employee,
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
    assert employee_id_from_pk(pk('abc-123')) == 'abc-123'


# --------------------------------------------------------------- whitelisting

def test_drops_fields_that_are_not_on_the_whitelist():
    picked = pick_editable(dict(VALID, id='hacked', checklist=[{'id': 'laptop'}]))
    assert 'id' not in picked
    assert 'checklist' not in picked


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

def _item(done_ids=(), employee_id='abc-123'):
    """One stored employee item, which is now the whole employee."""
    return dict(
        VALID,
        PK=pk(employee_id),
        employeeId=employee_id,
        entityType='Employee',
        checklist=[
            dict(entry, done=entry['itemId'] in done_ids)
            for entry in new_checklist_items()
        ],
    )


def test_rebuilds_exactly_the_shape_the_phase_1_frontend_expects():
    employee = to_api_employee(_item(done_ids={'offer-letter', 'id-proof'}))

    assert employee['id'] == 'abc-123'
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
    for internal in ('PK', 'SK', 'employeeId', 'entityType', 'order'):
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
