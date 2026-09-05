"""Attendance policy, validation and report construction.

Attendance is a daily declaration, not a time clock. Employees may create or
replace today's declaration from 08:30 until 18:00 Asia/Kolkata. Officials are
not constrained by that window. A missing applicable declaration is reported
as leave without manufacturing a DynamoDB item for it.
"""
import calendar
import re
from datetime import date, datetime, time, timedelta, timezone

BUSINESS_TIMEZONE_NAME = 'Asia/Kolkata'
# Asia/Kolkata has observed UTC+05:30 without daylight-saving transitions since
# 1945. A fixed offset keeps local development working on Windows installations
# that do not bundle the optional IANA tzdata package, while remaining exact for
# every attendance date this application can represent.
BUSINESS_TIMEZONE = timezone(timedelta(hours=5, minutes=30), BUSINESS_TIMEZONE_NAME)
MARKING_OPENS = time(8, 30)
MARKING_CLOSES = time(18, 0)

STATUSES = ('present', 'leave')
STATUS_LABELS = {
    'present': 'Present',
    'leave': 'Leave',
}
NOTE_MAX_LENGTH = 300
MONTH_PATTERN = re.compile(r'^\d{4}-\d{2}$')


def utc_now():
    """Separate clock seam so boundary tests do not depend on wall time."""
    return datetime.now(timezone.utc)


def business_now():
    return utc_now().astimezone(BUSINESS_TIMEZONE)


def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def marking_window_open(value):
    local = value.astimezone(BUSINESS_TIMEZONE)
    return MARKING_OPENS <= local.time().replace(tzinfo=None) < MARKING_CLOSES


def window_payload(value=None):
    local = value or business_now()
    return {
        'timezone': BUSINESS_TIMEZONE_NAME,
        'opensAt': '08:30',
        'closesAt': '18:00',
        'today': local.date().isoformat(),
        'isOpen': marking_window_open(local),
    }


def validate_month(value):
    if not isinstance(value, str) or not MONTH_PATTERN.fullmatch(value):
        raise ValueError('Month must use YYYY-MM.')
    try:
        parsed = date.fromisoformat(value + '-01')
    except ValueError as error:
        raise ValueError('Month must be a real calendar month.') from error
    return parsed, value


def validate_date(value):
    if not isinstance(value, str):
        raise ValueError('Date must use YYYY-MM-DD.')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError('Date must use YYYY-MM-DD and be a real calendar date.') from error
    if parsed.isoformat() != value:
        raise ValueError('Date must use YYYY-MM-DD.')
    return parsed


def validate_submission(body):
    status = body.get('status')
    note = body.get('note', '')
    fields = {}

    if status not in STATUSES:
        fields['status'] = 'Choose Present or Leave.'
    if not isinstance(note, str):
        fields['note'] = 'Note must be text.'
    else:
        note = note.strip()
        if len(note) > NOTE_MAX_LENGTH:
            fields['note'] = 'Note must be ' + str(NOTE_MAX_LENGTH) + ' characters or fewer.'

    if fields:
        raise ValueError(fields)
    return {'status': status, 'note': note}


def profile_snapshot(profile):
    return {
        'employeeId': profile['id'],
        'employeeName': (profile.get('firstName', '') + ' ' + profile.get('lastName', '')).strip(),
        'employeeRole': profile.get('jobTitle', ''),
        'department': profile.get('department', ''),
        'startDate': profile.get('startDate', ''),
    }


def attendance_item(profile, attendance_date, submission, actor, actor_role,
                    existing=None, now=None):
    now = now or utc_now()
    timestamp = iso_utc(now)
    snapshot = profile_snapshot(profile)
    item = {
        'employeeKey': 'EMP#' + profile['id'],
        'employeeId': profile['id'],
        'employeeName': snapshot['employeeName'],
        'employeeRole': snapshot['employeeRole'],
        'department': snapshot['department'],
        'attendanceDate': attendance_date,
        'attendanceMonth': attendance_date[:7],
        'dateEmployeeKey': attendance_date + '#' + profile['id'],
        'status': submission['status'],
        'markedAt': (existing or {}).get('markedAt', timestamp),
        'updatedAt': timestamp,
        'updatedBy': actor,
        'updatedByRole': actor_role,
    }
    if submission['note']:
        item['note'] = submission['note']
    return item


def record_view(item):
    return {
        'employeeId': item['employeeId'],
        'employeeName': item.get('employeeName', ''),
        'employeeRole': item.get('employeeRole', ''),
        'department': item.get('department', ''),
        'date': item['attendanceDate'],
        'status': item['status'],
        'note': item.get('note', ''),
        'markedAt': item.get('markedAt', ''),
        'updatedAt': item.get('updatedAt', ''),
        'updatedBy': item.get('updatedBy', ''),
        'updatedByRole': item.get('updatedByRole', ''),
    }


def _month_dates(month_start):
    count = calendar.monthrange(month_start.year, month_start.month)[1]
    return [date(month_start.year, month_start.month, day) for day in range(1, count + 1)]


def _missing_status(day, employee_start, local_now):
    if employee_start and day < employee_start:
        return None
    if day > local_now.date():
        return None
    if day == local_now.date() and local_now.time().replace(tzinfo=None) < MARKING_OPENS:
        return None
    return 'leave'


def build_sheet(month, roster, records, now=None):
    month_start, month_value = validate_month(month)
    local_now = (now or utc_now()).astimezone(BUSINESS_TIMEZONE)
    dates = _month_dates(month_start)
    by_employee_date = {
        (item['employeeId'], item['attendanceDate']): item for item in records
    }

    rows = []
    for profile in roster:
        snapshot = profile_snapshot(profile)
        start_value = snapshot['startDate']
        try:
            employee_start = date.fromisoformat(start_value) if start_value else None
        except ValueError:
            employee_start = None

        days = []
        totals = {status: 0 for status in STATUSES}
        for day in dates:
            day_value = day.isoformat()
            stored = by_employee_date.get((snapshot['employeeId'], day_value))
            if stored:
                status = stored.get('status')
                # Older or otherwise unknown non-present values collapse to
                # leave now that attendance has one non-present state.
                if status not in STATUSES:
                    status = 'leave'
            else:
                status = _missing_status(day, employee_start, local_now)
            if status:
                totals[status] += 1
            days.append({
                'date': day_value,
                'status': status,
                'note': stored.get('note', '') if stored else '',
                'stored': bool(stored),
            })

        rows.append({
            'employeeId': snapshot['employeeId'],
            'employeeName': snapshot['employeeName'],
            'employeeRole': snapshot['employeeRole'],
            'department': snapshot['department'],
            'days': days,
            'totals': totals,
        })

    rows.sort(key=lambda row: (row['employeeName'].lower(), row['employeeId']))
    return {
        'month': month_value,
        'timezone': BUSINESS_TIMEZONE_NAME,
        'generatedAt': iso_utc(now or utc_now()),
        'days': [day.isoformat() for day in dates],
        'employees': rows,
        'count': len(rows),
    }
