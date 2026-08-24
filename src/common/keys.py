"""
Key construction for the single-table design - the one place PK/SK strings are built.

Layout, all under one partition per employee:

    PK = "EMP#<uuid>"   SK = "PROFILE"          the employee record
    PK = "EMP#<uuid>"   SK = "CHK#<itemId>"     one row per checklist item

Nine items per employee, so a single Query on the PK returns the whole thing.

Plus one item per employee outside that partition, the email uniqueness guard
described at the bottom of this file.
"""

EMP_PREFIX = 'EMP#'
CHK_PREFIX = 'CHK#'
PROFILE_SK = 'PROFILE'


def pk(employee_id):
    return EMP_PREFIX + employee_id


def chk_sk(item_id):
    return CHK_PREFIX + item_id


def employee_id_from_pk(value):
    return value[len(EMP_PREFIX):]


def item_id_from_sk(value):
    return value[len(CHK_PREFIX):]


def is_profile(item):
    return item.get('SK') == PROFILE_SK


# ------------------------------------------------------------- email guards
#
# A uniqueness guard is a second item in its own partition, written inside the
# same transaction as the profile it belongs to:
#
#     PK = "EMAIL#<lowercased email>"   SK = "EMAIL"
#
# `attribute_not_exists(PK)` on that Put is what makes "one employee per work
# email" a property of the table rather than a hope. Lowercased because
# Priya@ and priya@ are the same mailbox to every mail server that matters.

EMAIL_PREFIX = 'EMAIL#'
EMAIL_SK = 'EMAIL'


def email_pk(email):
    return EMAIL_PREFIX + (email or '').strip().lower()


def is_employee_pk(value):
    return str(value).startswith(EMP_PREFIX)
