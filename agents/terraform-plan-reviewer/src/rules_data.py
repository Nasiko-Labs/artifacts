"""Pure: stored-data rules. ST01/ST02 (a stateful resource destroyed or replaced) and DL01-DL14 (settings
that lose data silently). Each rule reads only parsed values; the reason and default question are fixed text."""

from __future__ import annotations

from collections.abc import Iterator

import resources
from changes import (
    Change,
    Finding,
    changed,
    is_true,
    known,
    make,
    number,
    turned_false,
    turned_true,
)
from rules_retention import dl12_force, dl13_14_azure

RDS = ("aws_db_instance", "aws_rds_cluster")
BUCKETS = ("aws_s3_bucket", "google_storage_bucket")
DISKS = ("aws_ebs_volume", "google_compute_disk", "google_compute_region_disk", "azurerm_managed_disk")
ENCRYPTED_STORES = ("aws_ebs_volume", "aws_db_instance", "aws_efs_file_system", "aws_rds_cluster")
GCP_DELETE_POLICY = ("google_sql_database_instance", "google_spanner_database", "google_bigtable_instance",
                     "google_firestore_database")
KMS_MIN_WINDOW_DAYS = 14
UPDATES = ("update", "replace")


def stateful(change: Change, prod: bool) -> Iterator[Finding]:
    if change.mode != "managed" or not change.is_destructive:
        return
    verb = "destroying" if change.action == "delete" else "replacing"
    if resources.is_stateful(change.resource_type):
        why = f" ({change.reason})" if change.reason else ""
        reason = (f"{change.resource_type} holds data; {verb} it{why} loses that data unless it is restored"
                  + (". In prod this is blocked until you confirm it." if prod else "."))
        yield make("ST01", "high", change, reason,
                   f"Is {verb} {change.address} intended, and is a snapshot or restore path ready?", blocking=prod)
    elif resources.maybe_stateful(change.resource_type):
        yield make("ST02", "medium", change, f"the type name suggests stored data; {verb} it may lose that data.",
                   f"Does {change.address} hold data that must survive this change?")


def data_loss(change: Change) -> Iterator[Finding]:
    rtype, act = change.resource_type, change.action
    for check in _CHECKS:
        yield from check(change, rtype, act)


def _dl01_buckets(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if rtype not in BUCKETS:
        return
    a = c.attr("force_destroy")
    if act in ("create", "update") and turned_true(a):
        yield make("DL01", "high", c, "force_destroy is set: a later destroy deletes every object in the bucket.",
                   "Is deleting all objects on destroy intended?", a)
    elif c.is_destructive and a is not None and is_true(a.before):
        yield make("DL01", "high", c, "force_destroy is true: this destroy deletes every object in the bucket.",
                   "Is deleting all objects in this bucket intended, and are they backed up?", a)


def _dl02_final_snapshot(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if rtype not in RDS:
        return
    skip = c.attr("skip_final_snapshot")
    if act == "update" and turned_true(skip):
        yield make("DL02", "high", c, "skip_final_snapshot becomes true: no final snapshot will be taken on destroy.",
                   "Why is the final snapshot being turned off?", skip)
    elif c.is_destructive and skip is not None and is_true(skip.before):
        yield make("DL02", "high", c, "skip_final_snapshot = true: no final snapshot is taken.",
                   "Is a recent snapshot or backup available before this database is removed?", skip)
    ident = c.attr("final_snapshot_identifier")
    if act == "update" and ident is not None and known(ident.before) and ident.after is None:
        yield make("DL02", "high", c, "final_snapshot_identifier is removed: no named final snapshot on destroy.",
                   "Why is the final snapshot name being removed?", ident)


def _dl03_protection(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if act not in UPDATES:
        return
    for a in c.attrs_named("deletion_protection", "deletion_protection_enabled"):
        if turned_false(a):
            yield make("DL03", "high", c, "deletion protection is turned off: an accidental destroy is no longer stopped.",
                       "Why is deletion protection being removed?", a)


def _dl04_backups(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if rtype not in RDS or act != "update":
        return
    a = c.attr("backup_retention_period")
    before, after = number(a.before) if a else None, number(a.after) if a else None
    if before and after is not None and (after == 0 or after < before / 2):
        yield make("DL04", "high", c, f"backup retention drops from {a.before} to {a.after} days: point-in-time "
                   "recovery is lost or shortened.", "Is shortening the backup window intended?", a)
    d = c.attr("delete_automated_backups")
    if turned_true(d):
        yield make("DL04", "medium", c, "automated backups will be deleted with the database.",
                   "Should the automated backups outlive the database?", d)


def _dl05_dynamo_pitr(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    a = c.attr("point_in_time_recovery.enabled")
    if rtype == "aws_dynamodb_table" and act == "update" and turned_false(a):
        yield make("DL05", "high", c, "point-in-time recovery is turned off.", "Why is point-in-time recovery off?", a)


def _dl06_versioning(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    status = c.attr("versioning_configuration.status")
    if rtype == "aws_s3_bucket_versioning" and status is not None and status.before == "Enabled" \
            and status.after in ("Suspended", "Disabled"):
        yield make("DL06", "high", c, "versioning is suspended: overwritten or deleted objects can't be recovered.",
                   "Is suspending versioning intended?", status)
    enabled = c.attr("versioning.enabled")
    if rtype == "aws_s3_bucket" and act == "update" and turned_false(enabled):
        yield make("DL06", "high", c, "versioning is turned off: overwritten or deleted objects can't be recovered.",
                   "Is turning versioning off intended?", enabled)
    if rtype in ("aws_s3_bucket", "aws_s3_bucket_lifecycle_configuration") and act in ("create", "update"):
        added = [a for a in c.attrs if "expiration" in a.name and a.before is None and known(a.after)]
        if added:
            yield make("DL06", "medium", c, "a lifecycle expiration rule is added: objects will be deleted on a schedule.",
                       "Which objects will the new expiration rule delete?", added[0])


def _dl07_kms(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if rtype != "aws_kms_key":
        return
    if c.is_destructive:
        yield make("DL07", "high", c, "the KMS key is scheduled for deletion: data encrypted with it becomes "
                   "unreadable after the window.", "Is nothing still encrypted with this key?")
        return
    window = c.attr("deletion_window_in_days")
    after = number(window.after) if window else None
    if after is not None and after < KMS_MIN_WINDOW_DAYS and changed(window):
        yield make("DL07", "high", c, f"the key deletion window drops to {window.after} days.",
                   "Why shorten the key deletion window?", window)
    rotation = c.attr("enable_key_rotation")
    if act == "update" and turned_false(rotation):
        yield make("DL07", "high", c, "key rotation is turned off.", "Why is key rotation being turned off?", rotation)


def _dl08_encryption_replace(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if rtype not in ENCRYPTED_STORES or act not in UPDATES:
        return
    for a in c.attrs_named("encrypted", "storage_encrypted", "kms_key_id"):
        if changed(a):
            yield make("DL08", "high", c, f"{a.name} changes, which recreates the store: data is lost without a "
                       "snapshot restore.", "Is a snapshot restore planned for this encryption change?", a)


def _dl09_disks(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if rtype not in DISKS or act not in UPDATES:
        return
    for a in c.attrs_named("snapshot_id", "snapshot", "image", "source", "source_uri"):
        if changed(a):
            yield make("DL09", "high", c, f"the disk's {a.name} changes: the disk is rebuilt from a different source.",
                       "Is the current disk content backed up?", a)
    for a in c.attrs_named("size", "disk_size_gb"):
        before, after = number(a.before), number(a.after)
        if before is not None and after is not None and after < before:
            yield make("DL09", "high", c, f"the disk shrinks from {a.before} to {a.after}: disks can't shrink in place.",
                       "Has the data been moved off this disk?", a)


def _dl10_11_gcp(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    a = c.attr("delete_contents_on_destroy")
    if rtype == "google_bigquery_dataset" and turned_true(a):
        yield make("DL10", "high", c, "delete_contents_on_destroy is set: destroying the dataset deletes its tables.",
                   "Is deleting the tables with the dataset intended?", a)
    policy = c.attr("deletion_policy")
    if rtype in GCP_DELETE_POLICY and policy is not None and (policy.after or "").upper() == "DELETE" and changed(policy):
        yield make("DL11", "high", c, "deletion_policy now allows delete.", "Why allow this to be deleted?", policy)


_CHECKS = (_dl01_buckets, _dl02_final_snapshot, _dl03_protection, _dl04_backups, _dl05_dynamo_pitr, _dl06_versioning,
           _dl07_kms, _dl08_encryption_replace, _dl09_disks, _dl10_11_gcp, dl12_force, dl13_14_azure)
