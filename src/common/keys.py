"""
Key construction - the one place key strings and the key attribute name live.

One item per employee, and that item is the whole employee:

    employeeKey = "EMP#<employeeId>"    the profile fields, plus the checklist as
                                        an embedded list

There is no sort key and no second kind of item. A GetItem on the key returns
everything about an employee; a Scan returns one row per employee.

The attribute is called `employeeKey` and not `PK`. `PK` is the single-table
convention - it reads as "whatever partition this row happens to be in", which is
what you want when one table holds employees, audit rows and email guards and the
name has to cover all three. This table holds employees, so the generic name was
describing a generality that does not exist here, and `employeeKey` says what the
value actually is to anyone reading a raw item in the console.

The value keeps the `EMP#` prefix. It costs a concat on write and a slice on read,
and it buys the room to put a second kind of item in this table later without a
key-schema change - which is a table replacement, so it is not a decision worth
leaving until it is needed. `entityType` distinguishes item types on read; the
prefix keeps them from colliding on write.

Nothing outside this module names the attribute or builds the prefix. Handlers
take `key()` for their Key= argument and `EXISTS` / `NOT_EXISTS` for their
condition expressions, so renaming it again is this file and the two key-schema
declarations - template.yaml and the table tests/conftest.py creates.

The id in that key is the employee number HR types on the form - "E1024", not a
UUID. DynamoDB can enforce uniqueness on a partition key and on nothing else, so
putting a meaningful id in the key buys a real guarantee: a duplicate employee
number is a 409 from the conditional PutItem in handlers/create_employee, not a
silent second record.

It also means the id is immutable. There is no rename: an UpdateItem cannot move
an item to a different partition key, so correcting a mistyped employee number is
a delete and a re-create. See validate_employee_id in common/models.py for the
shape that is accepted, and note it is upper-cased before it gets here so
"e1024" and "E1024" cannot become two people.

The checklist used to be eight sibling rows under a shared partition, addressed
by SK. It is now a list attribute on this item, addressed by index - see
CHECKLIST_INDEX in common/checklist_template.py for what that costs.

Work-email uniqueness used to be enforced by a second item in its own partition,
written in the same transaction as the profile. That item is gone and has not
come back: the key guarantees one record per employee number, which is not the
same thing as one record per email. Two employees can still hold the same work
email.
"""

EMP_PREFIX = 'EMP#'

# The partition key attribute. Named here rather than spelled out at each call
# site so that the next rename is one line plus the two key-schema declarations,
# instead of the twenty-odd places the old name had reached.
KEY_ATTRIBUTE = 'employeeKey'

# The two condition expressions every write in this codebase needs. Built from
# KEY_ATTRIBUTE for the same reason key() is: an expression string that spells the
# attribute out by hand is a rename this module cannot see.
EXISTS = 'attribute_exists(' + KEY_ATTRIBUTE + ')'
NOT_EXISTS = 'attribute_not_exists(' + KEY_ATTRIBUTE + ')'


def pk(employee_id):
    return EMP_PREFIX + employee_id


def employee_id_from_pk(value):
    return value[len(EMP_PREFIX):]


def key(employee_id):
    """The whole DynamoDB Key for one employee - what every Key= argument takes."""
    return {KEY_ATTRIBUTE: pk(employee_id)}
