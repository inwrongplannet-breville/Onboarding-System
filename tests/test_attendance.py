"""Attendance policy, storage, reporting and parent HR access."""
from datetime import datetime, timedelta, timezone
import json

import boto3
import pytest

from conftest import ATTENDANCE_TABLE_NAME
from test_handlers import OFFICIAL, VALID, as_employee, body, create, signed_in


def at_ist(hour, minute=0):
    """4 September 2026 at the requested Asia/Kolkata wall time."""
    ist = timezone(timedelta(hours=5, minutes=30), 'Asia/Kolkata')
    return datetime(2026, 9, 4, hour, minute, tzinfo=ist).astimezone(timezone.utc)


def set_clock(monkeypatch, value):
    import common.attendance as attendance
    monkeypatch.setattr(attendance, 'utc_now', lambda: value)


def mark(handlers, status='present', note='', context=None):
    return handlers['upsert_own_attendance'](signed_in({
        'body': json.dumps({'status': status, 'note': note}),
    }, context or as_employee('E1001')), None)


def sheet(handlers, month='2026-09', context=None):
    return handlers['get_attendance_sheet'](signed_in({
        'queryStringParameters': {'month': month},
    }, context), None)


@pytest.mark.parametrize(('instant', 'expected'), [
    (at_ist(8, 29), 409),
    (at_ist(8, 30), 200),
    (at_ist(17, 59), 200),
    (at_ist(18, 0), 409),
])
def test_employee_marking_window_boundaries(handlers, monkeypatch, instant, expected):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, instant)
    assert mark(handlers)['statusCode'] == expected


def test_attendance_accepts_only_present_or_leave(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(9, 0))

    assert mark(handlers, 'present')['statusCode'] == 200
    assert mark(handlers, 'leave')['statusCode'] == 200
    assert mark(handlers, 'remote')['statusCode'] == 400


def test_daily_record_carries_name_role_and_updates_in_place(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(9, 0))

    first = mark(handlers, 'present')
    second = mark(handlers, 'leave', 'Approved leave')

    assert first['statusCode'] == 200
    saved = body(second)['attendance']
    assert saved['employeeName'] == 'Priya Sharma'
    assert saved['employeeRole'] == 'Software Engineer'
    assert saved['department'] == 'Engineering'
    assert saved['status'] == 'leave'
    assert saved['note'] == 'Approved leave'

    table = boto3.resource('dynamodb').Table(ATTENDANCE_TABLE_NAME)
    items = table.scan()['Items']
    assert len(items) == 1
    assert items[0]['attendanceDate'] == '2026-09-04'


def test_missing_attendance_is_leave_from_start_time(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(9, 0))

    row = body(sheet(handlers))['employees'][0]
    statuses = {day['date']: day['status'] for day in row['days']}
    assert statuses['2026-09-03'] == 'leave'
    assert statuses['2026-09-04'] == 'leave'
    assert statuses['2026-09-05'] is None
    assert set(row['totals']) == {'present', 'leave'}

    table = boto3.resource('dynamodb').Table(ATTENDANCE_TABLE_NAME)
    assert table.scan()['Items'] == []


def test_today_is_upcoming_before_the_window_opens(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(8, 29))
    row = body(sheet(handlers))['employees'][0]
    today = next(day for day in row['days'] if day['date'] == '2026-09-04')
    assert today['status'] is None


@pytest.mark.parametrize('month', ['2026-9', '2026-13', 'not-a-month'])
def test_attendance_reads_reject_invalid_months(handlers, monkeypatch, month):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(10, 0))

    own = handlers['get_own_attendance'](signed_in({
        'queryStringParameters': {'month': month},
    }, as_employee('E1001')), None)
    csv_response = handlers['download_attendance_csv'](signed_in({
        'queryStringParameters': {'month': month},
    }), None)

    assert own['statusCode'] == 400
    assert sheet(handlers, month)['statusCode'] == 400
    assert csv_response['statusCode'] == 400


@pytest.mark.parametrize('date_value', ['2026-9-04', '2026-02-30', 'tomorrow'])
def test_hr_update_rejects_invalid_dates(handlers, date_value):
    create(handlers, employeeId='E1001')
    response = handlers['update_employee_attendance'](signed_in({
        'pathParameters': {'employeeId': 'E1001', 'date': date_value},
        'body': json.dumps({'status': 'present'}),
    }), None)
    assert response['statusCode'] == 400


def test_missing_employee_attendance_routes_return_not_found(handlers, monkeypatch):
    set_clock(monkeypatch, at_ist(10, 0))
    own = handlers['get_own_attendance'](signed_in({}, as_employee('E1999')), None)
    mark_missing = mark(handlers, context=as_employee('E1999'))
    hr_update = handlers['update_employee_attendance'](signed_in({
        'pathParameters': {'employeeId': 'E1999', 'date': '2026-09-04'},
        'body': json.dumps({'status': 'present'}),
    }), None)

    assert own['statusCode'] == 404
    assert mark_missing['statusCode'] == 404
    assert hr_update['statusCode'] == 404


def test_hr_can_create_and_change_any_date_after_eod(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(20, 0))
    event = signed_in({
        'pathParameters': {'employeeId': 'E1001', 'date': '2026-08-12'},
        'body': json.dumps({'status': 'leave', 'note': 'Approved leave'}),
    })

    response = handlers['update_employee_attendance'](event, None)
    assert response['statusCode'] == 200
    saved = body(response)['attendance']
    assert saved['status'] == 'leave'
    assert saved['updatedBy'] == 'hr.admin'
    assert saved['updatedByRole'] == 'official'

    status_only = signed_in({
        'pathParameters': {'employeeId': 'E1001', 'date': '2026-08-12'},
        'body': json.dumps({'status': 'present'}),
    })
    corrected = body(handlers['update_employee_attendance'](status_only, None))['attendance']
    assert corrected['status'] == 'present'
    assert corrected['note'] == 'Approved leave'


def test_employee_cannot_use_hr_sheet_or_parent_update(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(10, 0))
    employee = as_employee('E1001')

    assert sheet(handlers, context=employee)['statusCode'] == 403
    response = handlers['update_employee_attendance'](signed_in({
        'pathParameters': {'employeeId': 'E1001', 'date': '2026-09-04'},
        'body': json.dumps({'status': 'present'}),
    }, employee), None)
    assert response['statusCode'] == 403


def test_official_cannot_use_employee_marking_route(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(10, 0))
    assert mark(handlers, context=OFFICIAL)['statusCode'] == 403


def test_csv_matches_calculated_sheet(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(9, 0))
    mark(handlers, 'present')

    response = handlers['download_attendance_csv'](signed_in({
        'queryStringParameters': {'month': '2026-09'},
    }), None)
    assert response['statusCode'] == 200
    assert response['headers']['Content-Type'] == 'text/csv; charset=utf-8'
    assert 'attachment; filename="attendance-2026-09.csv"' == response['headers']['Content-Disposition']
    assert 'Employee name' in response['body']
    assert 'Priya Sharma' in response['body']
    assert 'Present' in response['body']
    assert response['body'].splitlines()[0].split(',')[-2:] == [
        'Present total', 'Leave total'
    ]


def test_employee_can_read_only_their_own_month(handlers, monkeypatch):
    create(handlers, employeeId='E1001')
    set_clock(monkeypatch, at_ist(9, 0))
    mark(handlers, 'present')

    response = handlers['get_own_attendance'](signed_in({
        'queryStringParameters': {'month': '2026-09'},
    }, as_employee('E1001')), None)
    assert response['statusCode'] == 200
    payload = body(response)
    assert payload['employee']['employeeId'] == 'E1001'
    today = next(day for day in payload['employee']['days'] if day['date'] == '2026-09-04')
    assert today['status'] == 'present'
