"""
Unit tests for the pure logic in common/. No AWS, no network, no credentials -
these are the parts worth testing offline. Everything that talks to DynamoDB is
verified against a real dev stack instead, because the failure mode there is IAM,
which no emulator reproduces.
"""
import pytest

from common.checklist_template import CHECKLIST_TEMPLATE, VALID_ITEM_IDS
from common.keys import chk_sk, email_pk, employee_id_from_pk, is_profile, pk
from common.models import (
    clean_comment,
    derive_status,
    group_by_partition,
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


def test_distinguishes_profile_rows_from_checklist_rows():
    assert is_profile({'SK': 'PROFILE'})
    assert not is_profile({'SK': chk_sk('laptop')})


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


# ------------------------------------------------------------ item -> API shape

def _items(done_ids=(), employee_id='abc-123'):
    partition = pk(employee_id)
    profile = dict(VALID, PK=partition, SK='PROFILE', employeeId=employee_id,
                   entityType='Employee')
    rows = [
        dict(item, PK=partition, SK=chk_sk(item['itemId']),
             done=item['itemId'] in done_ids)
        for item in new_checklist_items()
    ]
    return [profile] + rows


def test_rebuilds_exactly_the_shape_the_phase_1_frontend_expects():
    employee = to_api_employee(_items(done_ids={'offer-letter', 'id-proof'}))

    assert employee['id'] == 'abc-123'
    assert employee['firstName'] == 'Priya'
    assert set(employee['checklist'][0]) == {'id', 'label', 'owner', 'done', 'comment'}
    assert employee['checklist'][0]['id'] == 'offer-letter'
    assert employee['status'] == 'In Progress'
    assert employee['progress']['done'] == 2


def test_never_leaks_internal_attributes_onto_the_wire():
    employee = to_api_employee(_items())
    for internal in ('PK', 'SK', 'employeeId', 'entityType', 'order'):
        assert internal not in employee


def test_orders_checklist_items_by_order_not_by_scan_order():
    items = _items()
    shuffled = [items[0]] + list(reversed(items[1:]))
    employee = to_api_employee(shuffled)
    assert [entry['id'] for entry in employee['checklist']] == [
        item['id'] for item in CHECKLIST_TEMPLATE
    ]


def test_a_partition_with_no_profile_is_not_an_employee():
    orphans = [row for row in _items() if row['SK'] != 'PROFILE']
    assert to_api_employee(orphans) is None


def test_an_unknown_id_yields_no_items_and_therefore_no_employee():
    assert to_api_employee([]) is None


def test_groups_a_flat_scan_result_back_into_employees():
    partitions = group_by_partition(_items(employee_id='a') + _items(employee_id='b'))
    assert set(partitions) == {pk('a'), pk('b')}
    assert all(len(rows) == 9 for rows in partitions.values())


def test_email_guards_in_a_scan_are_not_employees():
    guard = {'PK': email_pk('priya.sharma@breville.com'), 'SK': 'EMAIL',
             'entityType': 'EmailGuard'}
    partitions = group_by_partition(_items(employee_id='a') + [guard])
    assert set(partitions) == {pk('a')}


@pytest.mark.parametrize('email', ['Priya@Breville.com', ' priya@breville.com ', 'priya@breville.com'])
def test_the_guard_key_is_the_same_whatever_the_casing_or_padding(email):
    assert email_pk(email) == 'EMAIL#priya@breville.com'
