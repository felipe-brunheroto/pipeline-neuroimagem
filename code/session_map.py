"""
session_map.py
==============

Visit numbering by exam DATE ORDER, for projects with
``"sessions": "date_order"`` in project.json (COGAGING).

Each subject's exams become ses-1, ses-2, ... in date order, and the numbers
never change once given. The date <-> number map is a small TSV kept in
``projects/<NAME>/sourcedata/session_map.tsv`` (paths.SESSION_MAP): exam dates
are identifiers, so the map stays outside bids/ and never travels with the
dataset.

Columns: subject, session, study_date (YYYYMMDD, or YYYYMM from a delivered
``ses-YYYYMM`` label; empty when unknown), study_uid (the DICOM
StudyInstanceUID, or ``imported:<label>`` for BIDS delivered by another team).

Used by dicom_to_bids.py (exams read from DICOM) and import_bids.py (exams
delivered already in BIDS), so both number visits the same way.
"""

import csv
import os

FIELDS = ["subject", "session", "study_date", "study_uid"]


def load(map_path):
    """All rows of the map (empty list if it does not exist yet)."""
    if not os.path.isfile(map_path):
        return []
    with open(map_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def save(map_path, rows):
    os.makedirs(os.path.dirname(map_path), exist_ok=True)
    with open(map_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["subject"],
                                                     int(r["session"].split("-")[1]))))


def assign(exams, map_path):
    """
    Number exams by date. `exams` is a list of (key, subject, study_date, study_uid);
    `key` is whatever the caller uses to look the result up (an exam folder, a
    delivered session label).

    Exams already in the map keep their number. A new exam later than every
    numbered one gets the next number. The run stops (nothing saved) when the
    order cannot be decided safely:
      * a new exam OLDER than one already numbered (renumbering would ripple
        through BIDS and FreeSurfer), or
      * an already-numbered exam of the same subject has no known date.
    Returns (result, problems): result is {key: (subject, session)}; problems is
    a list of messages (empty when everything was assigned and saved).
    """
    known = load(map_path)
    by_uid = {r["study_uid"]: r for r in known if r.get("study_uid")}
    result, problems = {}, []

    for subject in sorted({e[1] for e in exams}):
        numbered = [r for r in known if r["subject"] == subject]
        dates = [r["study_date"] for r in numbered if r["study_date"]]
        last_date = max(dates, default="")
        next_n = 1 + max((int(r["session"].split("-")[1]) for r in numbered), default=0)
        for key, _sub, date, uid in sorted((e for e in exams if e[1] == subject),
                                           key=lambda e: (e[2], e[3])):
            if uid in by_uid:
                result[key] = (subject, by_uid[uid]["session"])
                continue
            if not uid:
                problems.append(f"{subject}: an exam has no StudyInstanceUID")
                continue
            if numbered and (not date or any(not r["study_date"] for r in numbered)):
                problems.append(f"{subject}: cannot order a new exam against the "
                                "ones already numbered (a date is missing); fill "
                                f"study_date in {os.path.basename(map_path)} first")
                continue
            if date and date < last_date:
                problems.append(f"{subject}: a new exam is OLDER than one already "
                                "numbered; decide how to renumber, then edit "
                                f"{os.path.basename(map_path)} by hand")
                continue
            row = {"subject": subject, "session": f"ses-{next_n}",
                   "study_date": date, "study_uid": uid}
            known.append(row)
            numbered.append(row)
            by_uid[uid] = row
            result[key] = (subject, row["session"])
            next_n += 1
            last_date = max(last_date, date)

    if not problems:
        save(map_path, known)
    return result, problems
