"""
Employee validation and the DynamoDB-item <-> API-object translation.

The API object is deliberately the exact shape the frontend renders, which is why
the Phase 3 swap touched only the function bodies in `js/store.js` and left every
existing view in `js/ui.js` alone.
"""
import re
from datetime import date, datetime, timedelta

from common.checklist_template import CHECKLIST_TEMPLATE
from common.keys import KEY_ATTRIBUTE, employee_id_from_pk

DEPARTMENTS = ('Engineering', 'HR', 'Finance', 'Operations')
EMPLOYMENT_TYPES = ('Full-time', 'Contract', 'Intern')

# The one place the routing rule lives: an onboarding record with this
# employmentType promotes with entityType 'Intern' (promoted_item()), everyone
# else with entityType 'Employee' - both into the same EmployeeTable row shape.
# Named rather than compared as a literal at each call site - the handler, the
# seed script and the frontend all need this same test.
INTERN_EMPLOYMENT_TYPE = 'Intern'

# How long a promoted record stays undoable. Measured from `onboardedAt`, which
# every promoted record carries - see promoted_item() below.
UNPROMOTE_WINDOW_DAYS = 7

# Mirrors EDITABLE_FIELDS in js/store.js - checklist is ours, never settable from
# a request body, and neither is the employee id.
#
# The id is absent here for a stronger reason than the checklist is. It is the
# partition key, and DynamoDB cannot move an item between partitions - an
# UpdateItem naming a different PK does not rename anything, it upserts a second
# employee and leaves the first one sitting there. So `employeeId` is set once by
# create and is structurally unchangeable afterwards; correcting a typo means
# archiving that record and creating the right one.
EDITABLE_FIELDS = (
    'firstName', 'lastName', 'email', 'phone',
    'department', 'jobTitle', 'manager', 'startDate', 'employmentType',
)

# What an employee may change on their own record. Mirrors SELF_FIELDS in
# js/store.js.
#
# Deliberately three fields and not four. Everything else on the record is a fact
# HR asserts about the employment - the name on the contract, the department, the
# start date - and an employee editing those is not self-service, it is an
# unaudited amendment. These three are the ones only the employee knows.
#
# `phone` is on this list and on EDITABLE_FIELDS, so it has two writers and the
# last one wins. That is accepted rather than overlooked: removing it from HR's
# whitelist would leave nobody able to correct the number for a hire who has
# never signed in. See handlers/update_own_contact.py.
SELF_EDITABLE_FIELDS = ('phone', 'personalEmail', 'address')

# Every profile attribute to_api_employee puts on the wire.
#
# `personalEmail` and `address` are here and in SELF_EDITABLE_FIELDS but *not* in
# EDITABLE_FIELDS, which is what stops HR's full-replace PUT from setting them.
# The employee owns those two outright; the officials form never sees them.
PROFILE_FIELDS = EDITABLE_FIELDS + ('personalEmail', 'address')

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

# The employee number HR types on the create form, and the thing the partition key
# is built from. Letters, digits and hyphens; 2 to 20 characters; starts with a
# letter or a digit.
#
# Tighter than it strictly needs to be, on purpose. This string is concatenated
# into the PK, so anything that could collide with the key format is a problem
# rather than a preference: '#' would let a caller forge a key in another
# namespace, and whitespace would produce two ids that look identical in the
# console and are not. The pattern refuses both by only allowing what it names.
EMPLOYEE_ID_PATTERN = re.compile(r'^[A-Z0-9][A-Z0-9-]{1,19}$')

# A checklist comment is a note, not an essay - "chased payroll twice, still no
# bank details". The cap is here to keep one item well under the 400 KB DynamoDB
# limit and to stop a paste of an entire email thread from becoming the record.
COMMENT_MAX_LENGTH = 500

# Same reasoning as COMMENT_MAX_LENGTH, applied to the two fields an employee
# fills in themselves. Mirrored as `maxlength` on the controls in js/ui.js, so the
# browser stops a paste before the request rather than after it.
ADDRESS_MAX_LENGTH = 300

# Long enough for '+61 (0)4 1234 5678 ext 221'. There is no format check on a
# phone number anywhere in this system and there should not be: an international
# numbering regex's failure mode is rejecting somebody's real number.
PHONE_MAX_LENGTH = 40

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


# What an employee sees of their OWN record - everything on it, with one
# exception.
#
# The exception is the checklist comments. Ticks are facts about the hire and
# belong to them; a comment is an HR working note written by one official for
# another - the example in this codebase's own docstrings is "chased payroll
# twice, still no bank details". Showing the employee the tick and not the note is
# the whole distinction.
#
# Built by naming what is included, never by deleting from the full object. The
# difference matters the next time a field is added to the model: a constructed
# dict leaves it invisible until somebody deliberately adds it here, where a `del`
# list leaks it from the moment it exists until somebody remembers. One of those
# fails safe.
OWN_PROFILE_FIELDS = ('id',) + PROFILE_FIELDS + (
    'status', 'progress', 'archived', 'archivedAs', 'archivedAt',
)


def own_checklist_item(item):
    """One checklist item as its subject sees it: everything but the comment."""
    return {
        'id': item['id'],
        'label': item['label'],
        'owner': item['owner'],
        'done': item['done'],
    }


def own_profile_view(employee):
    """
    The employee-role view of the employee's own record.

    Takes an already-built API employee, like the directory view it replaces, so
    `status` and `progress` are computed from the *whole* checklist before
    anything here trims it - the numbers cannot disagree with the list HR sees.

    Both the read and the write path go through this. handlers/get_employee is the
    obvious one; handlers/update_own_contact is the one that would hand back every
    comment on the record if it returned its own re-read instead.
    """
    if employee is None:
        return None
    view = {field: employee[field] for field in OWN_PROFILE_FIELDS}
    view['checklist'] = [own_checklist_item(item) for item in employee['checklist']]
    return view


def pick_editable(body):
    """Whitelist and trim. Anything not on the list is dropped, not rejected."""
    picked = {}
    for field in EDITABLE_FIELDS:
        value = body.get(field, '')
        picked[field] = value.strip() if isinstance(value, str) else ''
    return picked


def pick_self(body):
    """
    Whichever of SELF_EDITABLE_FIELDS this body actually named, trimmed.

    Returns {field: value} holding only the keys that were present, because PATCH
    semantics need the difference: an absent field is left alone, and a field sent
    empty is cleared.

    Raises ValueError carrying a {field: message} map rather than coercing, which
    is the one place this disagrees with pick_editable. There, a non-string
    collapsing to '' is a sensible default for a form that always sends all nine
    fields. Here the same coercion would turn a malformed request into a deletion.
    """
    picked = {}
    errors = {}

    for field in SELF_EDITABLE_FIELDS:
        if field not in body:
            continue
        value = body[field]
        # None is how JSON says "clear this", and it is the one non-string that
        # means something. Anything else is a client bug, not an intention.
        if value is None:
            picked[field] = ''
        elif isinstance(value, str):
            picked[field] = value.strip()
        else:
            errors[field] = 'This field must be text.'

    if errors:
        raise ValueError(errors)
    return picked


def clean_employee_id(value):
    """
    Trim, and upper-case.

    The case fold is not cosmetic. This value becomes the partition key, and the
    key is the only uniqueness guarantee in the table - so "e1024" and "E1024"
    reaching DynamoDB as two different keys would mean one employee with two
    records and no error to say so. Folding here makes the second one a 409.

    Non-strings collapse to '' rather than raising, so a JSON body sending a
    number is a validation error with a message under the input, not a 500.
    """
    if not isinstance(value, str):
        return ''
    return value.strip().upper()


def validate_employee_id(employee_id):
    """The message to show under the Employee ID input, or None if it is fine."""
    if not employee_id:
        return 'Employee ID is required.'
    if not EMPLOYEE_ID_PATTERN.match(employee_id):
        return ('Employee ID must be 2-20 characters, using letters, digits and '
                'hyphens only.')
    return None


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


def validate_self_fields(values):
    """
    Returns {field: message} for the fields this body named. Empty dict means valid.

    Every one of the three is optional - an employee who has not filled in their
    address yet is not an invalid record, they are a new starter. So there is no
    required-field loop here, which is why this cannot just be validate_employee:
    that one demands the seven facts HR asserts and the employee never sends.
    """
    errors = {}

    personal_email = values.get('personalEmail')
    if personal_email and not EMAIL_PATTERN.match(personal_email):
        # Same wording as validate_employee's, so js/app.js needs no second copy.
        errors['personalEmail'] = 'Enter a valid email address.'

    address = values.get('address')
    if address and len(address) > ADDRESS_MAX_LENGTH:
        errors['address'] = ('Keep the address under ' + str(ADDRESS_MAX_LENGTH) +
                             ' characters.')

    phone = values.get('phone')
    if phone and len(phone) > PHONE_MAX_LENGTH:
        errors['phone'] = ('Keep the phone number under ' + str(PHONE_MAX_LENGTH) +
                           ' characters.')

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

    employee = {'id': employee_id_from_pk(item[KEY_ATTRIBUTE])}
    # PROFILE_FIELDS, not EDITABLE_FIELDS: the two self-service fields go on the
    # wire like everything else, and absent reads as '' for the same reason
    # archivedAs does - absent and empty mean the same thing to a reader.
    for field in PROFILE_FIELDS:
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


def is_intern(employee):
    """True for an onboarding record that will promote as an intern."""
    return employee.get('employmentType') == INTERN_EMPLOYMENT_TYPE


def is_intern_item(item):
    """True for a stored EmployeeTable item that is an intern, not an employee."""
    return item.get('entityType') == 'Intern'


def is_employee_item(item):
    """
    True for a stored EmployeeTable item that is an employee, not an intern.

    The two functions are deliberately not opposites of each other in code -
    each names its own check against `entityType` - so a third entityType
    value added later fails both rather than one of them silently claiming
    the other's territory.
    """
    return item.get('entityType') == 'Employee'


def unpromote_window_open(onboarded_at, now):
    """
    True while a promoted record is still inside the UNPROMOTE_WINDOW_DAYS
    undo window, measured from the ISO8601 `onboardedAt` timestamp every
    promoted record carries.

    `now` is a parameter rather than datetime.now() called inside this function,
    so a test can pin both ends of the boundary instead of racing the clock.
    """
    onboarded = datetime.fromisoformat(onboarded_at.replace('Z', '+00:00'))
    return now - onboarded <= timedelta(days=UNPROMOTE_WINDOW_DAYS)


# Every profile attribute a staff (employee or intern) item carries, once the
# onboarding-only fields are set aside. Same PROFILE_FIELDS as an onboarding
# record - the profile shape does not change on promotion, only where the
# checklist and the archive stamp live.
_STAFF_PROFILE_FIELDS = PROFILE_FIELDS


def to_api_staff_employee(item):
    """
    One EmployeeTable item, entityType 'Employee' -> the object the tracking
    dashboard renders. Callers pick this translator over to_api_intern by
    checking is_employee_item()/is_intern_item() on the raw item first - the
    two are never applied to the same item.

    `interns` is surfaced as [] when the attribute is absent - the attribute
    itself stays sparse in storage (see promoted_item() and the intern-link
    handlers), but a caller reading the API object should not have to
    distinguish "no interns" from "the key is missing", the same reasoning
    to_api_employee already applies to a checklist comment.
    """
    if not item:
        return None

    checklist = [to_api_checklist_item(entry) for entry in item.get('onboardingChecklist', [])]

    employee = {'id': employee_id_from_pk(item[KEY_ATTRIBUTE])}
    for field in _STAFF_PROFILE_FIELDS:
        employee[field] = item.get(field, '')

    employee['checklist'] = checklist
    employee['status'] = derive_status(checklist)
    employee['progress'] = progress(checklist)
    employee['onboardedAt'] = item.get('onboardedAt') or ''
    employee['joinedOn'] = item.get('joinedOn') or ''
    employee['interns'] = list(item.get('interns') or [])
    # A staff record can never be archived - that is an onboarding-table
    # concept - but own_profile_view() reads these three keys unconditionally
    # off every record it trims, onboarding or not, so they have to exist here
    # too. False/'' is not a placeholder, it is the honest answer.
    employee['archived'] = False
    employee['archivedAs'] = ''
    employee['archivedAt'] = ''
    return employee


def to_api_intern(item):
    """
    One EmployeeTable item, entityType 'Intern' -> the object the interns
    dashboard renders. Same table and same key attribute as
    to_api_staff_employee - the two differ only in which fields they surface,
    not in where the item lives.
    """
    if not item:
        return None

    checklist = [to_api_checklist_item(entry) for entry in item.get('onboardingChecklist', [])]

    intern = {'id': employee_id_from_pk(item[KEY_ATTRIBUTE])}
    for field in _STAFF_PROFILE_FIELDS:
        intern[field] = item.get(field, '')

    intern['checklist'] = checklist
    intern['status'] = derive_status(checklist)
    intern['progress'] = progress(checklist)
    intern['onboardedAt'] = item.get('onboardedAt') or ''
    intern['joinedOn'] = item.get('joinedOn') or ''
    intern['reportingManagerId'] = item.get('reportingManagerId') or ''
    # Same reasoning as to_api_staff_employee: own_profile_view() needs these
    # three keys on every record it trims, and an intern can never be archived.
    intern['archived'] = False
    intern['archivedAs'] = ''
    intern['archivedAt'] = ''
    return intern


def promoted_item(employee, now, reporting_manager_id=None):
    """
    The onboarding-record-shaped dict handed in -> the attributes to PutItem
    into EmployeeTable. Does not set `employeeKey` itself - both
    promote_to_employee.py and promote_to_intern.py add that from the same
    common.keys.pk(), since an employee and an intern share one key shape.

    `entityType` is the one line that decides which of the two this becomes -
    'Intern' when a reporting_manager_id was passed, 'Employee' otherwise -
    and it is the only thing that does: is_intern_item()/is_employee_item()
    read it back on the way out, and every handler that must not treat one
    kind as the other (the reporting-manager checks in promote_to_intern.py
    and set_intern_manager.py, the two staff-delete handlers) checks it too.

    The whole checklist moves across under the new name `onboardingChecklist`,
    frozen history rather than something PATCH .../checklist can still reach -
    see the rename note in src/common/keys.py's sibling comment in
    set_checklist_item.py.

    `employee` is the already-translated API object (from load_employee), not
    the raw item - so this reads PROFILE_FIELDS off it the same way the wire
    format already does, rather than re-deriving from a DynamoDB Item.
    """
    stamp = now.isoformat(timespec='seconds').replace('+00:00', 'Z')

    item = {field: employee.get(field, '') for field in _STAFF_PROFILE_FIELDS}
    item.update({
        'entityType': 'Intern' if reporting_manager_id is not None else 'Employee',
        'employeeId': employee['id'],
        'onboardingChecklist': [
            {
                'itemId': entry['id'],
                'label': entry['label'],
                'owner': entry['owner'],
                'done': entry['done'],
                'comment': entry.get('comment', ''),
            }
            for entry in employee['checklist']
        ],
        'onboardedAt': stamp,
        'joinedOn': employee.get('startDate', ''),
        'createdAt': stamp,
        'updatedAt': stamp,
    })
    if reporting_manager_id is not None:
        item['reportingManagerId'] = reporting_manager_id
    return item


def restored_item(staff_record, now):
    """
    The reverse of promoted_item(): a staff (employee or intern) API object ->
    the attributes to PutItem back into OnboardingTable, checklist and every
    comment on it intact.

    Deliberately drops onboardedAt, joinedOn, interns and reportingManagerId -
    none of those are onboarding-table attributes, and carrying one across would
    be exactly the kind of leftover field a stale read could later mistake for
    something meaningful.
    """
    stamp = now.isoformat(timespec='seconds').replace('+00:00', 'Z')

    item = {field: staff_record.get(field, '') for field in PROFILE_FIELDS}
    item.update({
        'entityType': 'Employee',
        'employeeId': staff_record['id'],
        'checklist': [
            {
                'itemId': entry['id'],
                'label': entry['label'],
                'owner': entry['owner'],
                'done': entry['done'],
                'comment': entry.get('comment', ''),
            }
            for entry in staff_record['checklist']
        ],
        'createdAt': stamp,
        'updatedAt': stamp,
    })
    return item
