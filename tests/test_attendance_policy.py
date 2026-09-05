"""Fast unit coverage for attendance policy and report construction."""
from datetime import date, datetime, timezone

import pytest

from common import attendance


def ist(year, month, day, hour, minute=0):
    return datetime(
        year, month, day, hour, minute,
        tzinfo=attendance.BUSINESS_TIMEZONE,
    )


@pytest.mark.parametrize(('value', 'expected'), [
    (ist(2026, 9, 4, 8, 29), False),
    (ist(2026, 9, 4, 8, 30), True),
    (ist(2026, 9, 4, 17, 59), True),
    (ist(2026, 9, 4, 18, 0), False),
])
def test_marking_window_boundaries(value, expected):
    assert attendance.marking_window_open(value) is expected


def test_window_payload_uses_the_business_date_and_exact_hours():
    payload = attendance.window_payload(ist(2026, 9, 4, 8, 30))
    assert payload == {
        'timezone': 'Asia/Kolkata',
        'opensAt': '08:30',
        'closesAt': '18:00',
        'today': '2026-09-04',
        'isOpen': True,
    }


@pytest.mark.parametrize('value', [None, 202609, '', '2026-9', '2026-00', '2026-13'])
def test_month_validation_rejects_noncanonical_or_impossible_values(value):
    with pytest.raises(ValueError):
        attendance.validate_month(value)


def test_month_validation_accepts_a_real_month():
    assert attendance.validate_month('2024-02') == (date(2024, 2, 1), '2024-02')


@pytest.mark.parametrize('value', [None, 20260904, '', '2026-9-04', '2026-02-30'])
def test_date_validation_rejects_noncanonical_or_impossible_values(value):
    with pytest.raises(ValueError):
        attendance.validate_date(value)


def test_submission_is_binary_and_normalizes_the_optional_note():
    assert attendance.validate_submission({'status': 'present'}) == {
        'status': 'present', 'note': ''
    }
    assert attendance.validate_submission({'status': 'leave', 'note': '  Approved  '}) == {
        'status': 'leave', 'note': 'Approved'
    }


@pytest.mark.parametrize(('body', 'field'), [
    ({'status': 'remote'}, 'status'),
    ({'status': 'present', 'note': 7}, 'note'),
    ({'status': 'present', 'note': 'x' * 301}, 'note'),
])
def test_submission_rejects_extra_states_and_invalid_notes(body, field):
    with pytest.raises(ValueError) as caught:
        attendance.validate_submission(body)
    assert field in caught.value.args[0]


def test_attendance_item_preserves_creation_time_and_refreshes_profile_snapshot():
    now = ist(2026, 9, 4, 11, 15).astimezone(timezone.utc)
    item = attendance.attendance_item(
        {
            'id': 'E1001', 'firstName': 'Priya', 'lastName': 'Sharma',
            'jobTitle': 'Lead Engineer', 'department': 'Engineering',
        },
        '2026-09-04',
        {'status': 'present', 'note': ''},
        'hr.admin',
        'official',
        existing={'markedAt': '2026-09-04T03:00:00Z'},
        now=now,
    )

    assert item['employeeKey'] == 'EMP#E1001'
    assert item['attendanceMonth'] == '2026-09'
    assert item['dateEmployeeKey'] == '2026-09-04#E1001'
    assert item['employeeName'] == 'Priya Sharma'
    assert item['employeeRole'] == 'Lead Engineer'
    assert item['markedAt'] == '2026-09-04T03:00:00Z'
    assert item['updatedAt'] == '2026-09-04T05:45:00Z'
    assert 'note' not in item


def test_report_sorts_employees_and_calculates_leave_without_storing_it():
    report = attendance.build_sheet(
        '2026-03',
        [
            {
                'id': 'E1002', 'firstName': 'Zoe', 'lastName': 'Young',
                'jobTitle': 'Designer', 'department': 'Design',
                'startDate': '2026-03-02',
            },
            {
                'id': 'E1001', 'firstName': 'Amy', 'lastName': 'Adams',
                'jobTitle': 'Engineer', 'department': 'Engineering',
                'startDate': '2026-03-01',
            },
        ],
        [
            {
                'employeeId': 'E1001', 'attendanceDate': '2026-03-01',
                'status': 'present', 'note': 'Office',
            },
            {
                'employeeId': 'E1001', 'attendanceDate': '2026-03-02',
                'status': 'legacy-state', 'note': 'Old value',
            },
        ],
        now=ist(2026, 3, 3, 9).astimezone(timezone.utc),
    )

    assert report['days'][0] == '2026-03-01'
    assert report['days'][-1] == '2026-03-31'
    assert [row['employeeId'] for row in report['employees']] == ['E1001', 'E1002']

    amy, zoe = report['employees']
    assert [day['status'] for day in amy['days'][:4]] == [
        'present', 'leave', 'leave', None
    ]
    assert [day['stored'] for day in amy['days'][:3]] == [True, True, False]
    assert amy['days'][1]['note'] == 'Old value'
    assert amy['totals'] == {'present': 1, 'leave': 2}
    assert [day['status'] for day in zoe['days'][:4]] == [None, 'leave', 'leave', None]
    assert zoe['totals'] == {'present': 0, 'leave': 2}


def test_leap_year_report_contains_every_calendar_day():
    report = attendance.build_sheet(
        '2024-02', [], [], now=ist(2024, 2, 29, 9).astimezone(timezone.utc)
    )
    assert len(report['days']) == 29
    assert report['days'][-1] == '2024-02-29'
