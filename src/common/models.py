"""
Employee validation and the DynamoDB-item <-> API-object translation.

The API object is deliberately the exact shape the frontend renders, which is why
the Phase 3 swap touched only the function bodies in `js/store.js` and left every
existing view in `js/ui.js` alone.
"""
import re

from common.checklist_template import CHECKLIST_TEMPLATE
from common.keys import employee_id_from_pk, is_profile

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


def pick_editable(body):
    """Whitelist and trim. Anything not on the list is dropped, not rejected."""
    picked = {}
    for field in EDITABLE_FIELDS:
        value = body.get(field, '')
        picked[field] = value.strip() if isinstance(value, str) else ''
    return picked


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
    if start_date and not DATE_PATTERN.match(start_date):
        errors['startDate'] = 'Start date must be in YYYY-MM-DD format.'

    return errors


def derive_status(checklist):
    """
    Status is computed, never stored - it cannot drift from the checklist it
    describes. Same three rules as App.computeStatus in js/model.js.
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
        # (round-half-to-even), so round(12.5) is 12 - while the JS Math.round
        # in App.progress gives 13. At 1/8 and 5/8 complete the two disagree by
        # a point. Match the frontend rather than the language default.
        'percent': int(done / total * 100 + 0.5) if total else 0,
    }


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
    """DynamoDB item -> the {id, label, owner, done} shape js/ui.js renders."""
    return {
        'id': item['itemId'],
        'label': item['label'],
        'owner': item['owner'],
        'done': bool(item['done']),
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
    # Additive conveniences. The Phase 1 UI computes these itself and ignores
    # them; a future client can just read them.
    employee['status'] = derive_status(checklist)
    employee['progress'] = progress(checklist)
    return employee


def group_by_partition(items):
    """Scan returns every item flat. Bucket them back into employees."""
    partitions = {}
    for item in items:
        partitions.setdefault(item['PK'], []).append(item)
    return partitions
