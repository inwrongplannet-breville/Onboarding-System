"""
Employee validation and the DynamoDB-item <-> API-object translation.

The API object is deliberately the exact shape the frontend renders, which is why
the Phase 3 swap touched only the function bodies in `js/store.js` and left every
existing view in `js/ui.js` alone.
"""
import re
from datetime import date

from common.checklist_template import CHECKLIST_TEMPLATE
from common.keys import employee_id_from_pk

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
# the employee's item instead of removing it, and which one depends on whether
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


def is_archived(item):
    """True for an archived employee. The attribute is absent on active ones."""
    return bool(item.get('archivedAs'))


def new_checklist_items():
    """
    A fresh, all-unchecked checklist for a new hire - the value of the embedded
    `checklist` attribute at creation, in CHECKLIST_TEMPLATE order.

    `order` duplicates the list position and nothing reads it. It stays because a
    raw item in the console is much easier to read with it than without, and
    test_models pins it to the position so the two cannot drift.
    """
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


def to_api_checklist_item(entry):
    """One embedded entry -> the {id, label, owner, done, comment} shape js/ui.js renders."""
    return {
        'id': entry['itemId'],
        'label': entry['label'],
        'owner': entry['owner'],
        'done': bool(entry['done']),
        # Always present, always a string. An item with no comment has no such
        # attribute at all, and making the client distinguish absent from empty
        # buys nothing - both mean "nobody has written anything here".
        'comment': entry.get('comment') or '',
    }


def to_api_employee(item):
    """
    One stored item -> the employee object the frontend renders.

    Returns None for a missing item, which is what a nonexistent employee looks
    like - GetItem omits `Item` entirely rather than erroring, and five callers
    branch on this None to decide between 404 and 200. It is a contract, not a
    convenience.

    No sorting here: the embedded list is written in CHECKLIST_TEMPLATE order and
    DynamoDB preserves list order, so position *is* the order. `order` is still
    stored on each entry to keep the raw item legible in the console, but nothing
    reads it - see the test pinning the two together.
    """
    if not item:
        return None

    checklist = [to_api_checklist_item(entry) for entry in item.get('checklist', [])]

    employee = {'id': employee_id_from_pk(item['PK'])}
    for field in EDITABLE_FIELDS:
        employee[field] = item.get(field, '')

    employee['checklist'] = checklist
    # Not conveniences any more - these are the only copy. The UI renders the
    # badge and the progress bar straight from them.
    employee['status'] = derive_status(checklist)
    employee['progress'] = progress(checklist)
    # Read off the item, not derived - see the constants at the top. `archived`
    # is the flag callers branch on; `archivedAs` says which of the two terminal
    # states it was and is '' for an active employee, for the same reason a
    # checklist comment is: absent and empty mean the same thing to a reader.
    employee['archived'] = is_archived(item)
    employee['archivedAs'] = item.get('archivedAs') or ''
    employee['archivedAt'] = item.get('archivedAt') or ''
    return employee
