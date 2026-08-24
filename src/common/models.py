"""
Employee validation and the DynamoDB-item <-> API-object translation.

The API object is deliberately the exact shape the frontend renders, which is why
the Phase 3 swap touched only the function bodies in `js/store.js` and left every
existing view in `js/ui.js` alone.
"""
import re
from datetime import date

from common.checklist_template import CHECKLIST_TEMPLATE
from common.keys import employee_id_from_pk, is_employee_pk, is_profile

DEPARTMENTS = ('Engineering', 'HR', 'Finance', 'Operations')
EMPLOYMENT_TYPES = ('Full-time', 'Contract', 'Intern')

# Mirrors EDITABLE_FIELDS in js/store.js - id and checklist are ours, never
# settable from a request body.
EDITABLE_FIELDS = (
    'firstName', 'lastName', 'email', 'phone',
    'department', 'jobTitle', 'manager', 'startDate', 'employmentType',
)

# Mirrors REQUIRED_FIELDS in js/app.js. phone and manager stay optional.
REQUIRED_FIELDS = (
    ('firstName', 'First name'),
    ('lastName', 'Last name'),
    ('email', 'Work email'),
    ('department', 'Department'),
    ('jobTitle', 'Job title'),
    ('startDate', 'Start date'),
    ('employmentType', 'Employment type'),
)

# Same pattern as js/app.js:126 - loose on purpose. Real address validation is
# sending an email to it, not a regex.
EMAIL_PATTERN = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# A checklist comment is a note, not an essay - "chased payroll twice, still no
# bank details". The cap is here to keep one item well under the 400 KB DynamoDB
# limit and to stop a paste of an entire email thread from becoming the record.
COMMENT_MAX_LENGTH = 500

# Archiving replaced hard deletion. DELETE /employees/{id} stamps one of these on
# the profile row instead of removing it, and which one depends on whether
# onboarding had finished: someone who completed the checklist and then left is a
# different piece of history from someone whose onboarding was abandoned halfway.
#
# Stored, unlike `status` - and the exception proves the rule. `status` is derived
# because it is a fact about the checklist as it stands right now. This records a
# decision a person made at a point in time, and there is nothing in the table to
# recompute it from. Deriving it would mean a cancelled record silently promoting
# itself to a completed one the moment someone ticked a leftover box.
ARCHIVED_CANCELLED = 'Onboarding Cancelled'
ARCHIVED_ONBOARDED = 'Onboarded'

# One message, because a caller hitting this on PUT and on PATCH is hitting the
# same wall and should not have to notice it was worded twice.
ARCHIVED_MESSAGE = 'This employee is archived. Their record is read-only.'


def pick_editable(body):
    """Whitelist and trim. Anything not on the list is dropped, not rejected."""
    picked = {}
    for field in EDITABLE_FIELDS:
        value = body.get(field, '')
        picked[field] = value.strip() if isinstance(value, str) else ''
    return picked


def clean_comment(value):
    """
    Trim, and treat None as cleared.

    Trailing whitespace is invisible in the UI and would otherwise be the
    difference between "has a comment" and "does not" - so it is stripped before
    anything decides which of those this is.
    """
    if value is None:
        return ''
    return value.strip()


def validate_employee(values):
    """Returns {field: message}. Empty dict means valid."""
    errors = {}

    for field, label in REQUIRED_FIELDS:
        if not values.get(field):
            errors[field] = label + ' is required.'

    email = values.get('email')
    if email and not EMAIL_PATTERN.match(email):
        errors['email'] = 'Enter a valid email address.'

    department = values.get('department')
    if department and department not in DEPARTMENTS:
        errors['department'] = 'Department must be one of: ' + ', '.join(DEPARTMENTS) + '.'

    employment_type = values.get('employmentType')
    if employment_type and employment_type not in EMPLOYMENT_TYPES:
        errors['employmentType'] = 'Employment type must be one of: ' + ', '.join(EMPLOYMENT_TYPES) + '.'

    start_date = values.get('startDate')
    if start_date:
        # Two checks, not one. The regex fixes the shape - date.fromisoformat on
        # its own also accepts '20260706' and would let a typo through in a
        # format no other part of the system reads. The parse then rejects the
        # dates that are the right shape and still not real: 2026-13-45,
        # 2026-02-30, and 29 February in a year that hasn't got one.
        if not DATE_PATTERN.match(start_date):
            errors['startDate'] = 'Start date must be in YYYY-MM-DD format.'
        else:
            try:
                date.fromisoformat(start_date)
            except ValueError:
                errors['startDate'] = 'Start date is not a real calendar date.'

    return errors


def derive_status(checklist):
    """
    Status is computed, never stored - it cannot drift from the checklist it
    describes. The only implementation of these three rules; the frontend renders
    what this returns.
    """
    total = len(checklist)
    done = sum(1 for item in checklist if item['done'])
    if total == 0 or done == 0:
        return 'Pending'
    if done == total:
        return 'Onboarded'
    return 'In Progress'


def progress(checklist):
    total = len(checklist)
    done = sum(1 for item in checklist if item['done'])
    return {
        'done': done,
        'total': total,
        # int(x + 0.5), not round(x). Python's round() is banker's rounding
        # (round-half-to-even), so round(12.5) is 12 where JS Math.round gives
        # 13 - a point of disagreement at 1/8 and 5/8 complete. It cost a real
        # defect back when the frontend computed its own percentage; it no
        # longer does, but half-up is still the arithmetic a person expects.
        'percent': int(done / total * 100 + 0.5) if total else 0,
    }


def archive_state(checklist):
    """
    Which terminal state an employee archives into, decided by their checklist.

    Deliberately expressed in terms of derive_status rather than re-counting the
    ticks, so "complete" means exactly one thing across the whole system.
    """
    return ARCHIVED_ONBOARDED if derive_status(checklist) == 'Onboarded' else ARCHIVED_CANCELLED


def is_archived(profile):
    """True for an archived profile row. The attribute is absent on active ones."""
    return bool(profile.get('archivedAs'))


def new_checklist_items():
    """A fresh, all-unchecked checklist for a new hire."""
    return [
        {
            'itemId': item['id'],
            'label': item['label'],
            'owner': item['owner'],
            'order': item['order'],
            'done': False,
        }
        for item in CHECKLIST_TEMPLATE
    ]


def to_api_checklist_item(item):
    """DynamoDB item -> the {id, label, owner, done, comment} shape js/ui.js renders."""
    return {
        'id': item['itemId'],
        'label': item['label'],
        'owner': item['owner'],
        'done': bool(item['done']),
        # Always present, always a string. An item with no comment has no such
        # attribute at all, and making the client distinguish absent from empty
        # buys nothing - both mean "nobody has written anything here".
        'comment': item.get('comment') or '',
    }


def to_api_employee(items):
    """
    Fold one partition's items (1 profile + 8 checklist rows) into a single
    employee object. Returns None if the partition has no profile - which is
    what a nonexistent employee looks like.
    """
    profile = None
    checklist_items = []

    for item in items:
        if is_profile(item):
            profile = item
        else:
            checklist_items.append(item)

    if profile is None:
        return None

    checklist_items.sort(key=lambda item: item.get('order', 0))
    checklist = [to_api_checklist_item(item) for item in checklist_items]

    employee = {'id': employee_id_from_pk(profile['PK'])}
    for field in EDITABLE_FIELDS:
        employee[field] = profile.get(field, '')

    employee['checklist'] = checklist
    # Not conveniences any more - these are the only copy. The UI renders the
    # badge and the progress bar straight from them.
    employee['status'] = derive_status(checklist)
    employee['progress'] = progress(checklist)
    # Read off the profile, not derived - see the constants at the top. `archived`
    # is the flag callers branch on; `archivedAs` says which of the two terminal
    # states it was and is '' for an active employee, for the same reason a
    # checklist comment is: absent and empty mean the same thing to a reader.
    employee['archived'] = is_archived(profile)
    employee['archivedAs'] = profile.get('archivedAs') or ''
    employee['archivedAt'] = profile.get('archivedAt') or ''
    return employee


def group_by_partition(items):
    """
    Scan returns every item flat. Bucket them back into employees.

    Anything that is not in an EMP# partition is dropped here - which today means
    the email uniqueness guards, whose whole job is to sit in a partition of
    their own and never be read.
    """
    partitions = {}
    for item in items:
        if not is_employee_pk(item['PK']):
            continue
        partitions.setdefault(item['PK'], []).append(item)
    return partitions
