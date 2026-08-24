"""
Key construction - the one place PK strings are built.

One item per employee, and that item is the whole employee:

    PK = "EMP#<uuid>"    the profile fields, plus the checklist as an embedded list

There is no sort key and no second kind of item. A GetItem on the PK returns
everything about an employee; a Scan returns one row per employee.

The checklist used to be eight sibling rows under a shared partition, addressed
by SK. It is now a list attribute on this item, addressed by index - see
CHECKLIST_INDEX in common/checklist_template.py for what that costs.

Work-email uniqueness used to be enforced by a second item in its own partition,
written in the same transaction as the profile. That item is gone, and with it
the guarantee: DynamoDB can only enforce uniqueness on a partition key, and the
partition key here is a UUID. Two employees can now hold the same work email.
"""

EMP_PREFIX = 'EMP#'


def pk(employee_id):
    return EMP_PREFIX + employee_id


def employee_id_from_pk(value):
    return value[len(EMP_PREFIX):]
