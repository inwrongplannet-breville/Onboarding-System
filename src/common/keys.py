"""
Key construction for the single-table design - the one place PK/SK strings are built.

Layout, all under one partition per employee:

    PK = "EMP#<uuid>"   SK = "PROFILE"          the employee record
    PK = "EMP#<uuid>"   SK = "CHK#<itemId>"     one row per checklist item

Nine items per employee, so a single Query on the PK returns the whole thing.
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
