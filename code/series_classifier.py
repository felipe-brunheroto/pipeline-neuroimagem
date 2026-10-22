"""
series_classifier.py
====================

WHAT THIS IS (in plain language)
--------------------------------
A single, shared place that decides, for ONE MRI series, two things at once:

  1. which BIDS-like datatype folder it belongs in
     (anat / func / dwi / fmap / perf / swi / misc), and
  2. its BIDS suffix and entities when we can tell
     (e.g. T1w, or bold + task-rest, or dwi + desc-ADC).

Every other script imports this module so the rules live in ONE file instead of
being copy-pasted. `dicom_to_bids.py` uses it to sort series into folders;
`make_bids.py` and `make_derivatives.py` use it to build correct BIDS names.

TWO LAYERS, AND "THE MAP WINS"
------------------------------
  1. series_map.csv  -- an operator-editable file (shipped with the project's
     current CBR defaults). Each row maps a description match to a datatype +
     suffix + entities. This is how you teach the pipeline about a NEW project
     (e.g. a future BOLD study) WITHOUT touching any code.
  2. built-in keyword rules -- sensible defaults for typical Siemens/GE series
     descriptions, used when no map row matches.

A series_map.csv row always wins over the built-in rules.

THE "NEW STRANGER" RULE
-----------------------
Anything that NEITHER layer recognizes is returned with datatype "unknown".
The caller then quarantines it (copies it aside) and writes it to a report with
the DICOM hints needed to classify it -- so unfamiliar data never crashes the
pipeline and is never silently mis-sorted. You then add one row to series_map.csv
and re-run.

This module is standard-library only and safe to import anywhere.
"""

import os
import re
import csv
from pathlib import Path

# The folders we sort into. "swi" and "misc" are working buckets (not official
# BIDS raw datatypes); "unknown" is the quarantine bucket for unrecognized data.
KNOWN_DATATYPES = ("anat", "func", "dwi", "fmap", "perf", "swi", "misc", "unknown")

# Default location of the operator-editable map (next to this module).
DEFAULT_MAP_PATH = str(Path(__file__).resolve().parent / "series_map.csv")


# ============================================================================
# Built-in keyword rules (the defaults used when series_map.csv has no match).
# Matched as lowercase SUBSTRINGS against the series description, so we are NOT
# hardcoding exact sequence names. Checked in the order below: the SPECIFIC
# modalities come before the broad anat keywords so, e.g., an SWI magnitude or a
# BOLD EPI is not mistaken for anatomy.
# ============================================================================
FMAP_KEYWORDS = ["field_map", "fieldmap", "field map", "fmap", "gre_field",
                 "b0map", "b0 map", "phasediff", "phase diff"]
PERF_KEYWORDS = ["asl", "perfusion", "perfusion_weighted", "pwi", "pasl",
                 "pcasl", "casl", "_cbf", "cerebral_blood"]
SWI_KEYWORDS  = ["swi", "susceptibility", "mip_images", "minip", "min_ip",
                 "venobold", "veno_bold", "mag_images", "pha_images"]
DWI_KEYWORDS  = ["dwi", "dti", "dki", "diffusion", "difusao", "ep2d_diff",
                 "_diff", "trace", "_adc", "adc"]
# Functional / BOLD. Kept broad on purpose so an unfamiliar BOLD name is still
# caught; the ImageType signal (below) is an extra net for names like "ep2d".
FUNC_KEYWORDS = ["bold", "fmri", "rsfmri", "rs-fmri", "resting", "_rest",
                 "task-", "task_", "ep2d_bold", "epfid", "func"]
ANAT_KEYWORDS = ["t1", "t2", "flair", "mprage", "mp2rage", "tfl", "spgr",
                 "fspgr", "bravo", "tse", "space", "black blood", "vibe",
                 "mp-rage", "t1w", "t2w", "pd"]
# Localizers / scouts: legitimately not BIDS data, but KNOWN junk we can safely
# drop in misc/ rather than flagging as "unknown" and bothering the operator.
SCOUT_KEYWORDS = ["scout", "localizer", "localiser", "aahead", "survey",
                  "3-plane", "3 plane", "plane_loc", "calibration"]


class Classification:
    """
    The result of classifying one series.

      datatype : one of KNOWN_DATATYPES (folder to sort into).
      suffix   : BIDS suffix when known (T1w/T2w/FLAIR/bold/dwi/asl/swi/...) or "".
      entities : dict of BIDS entities we could infer, e.g. {"task": "rest"} or
                 {"desc": "ADC"}. Order is preserved by the callers.
      derived  : True if this is a scanner-DERIVED image (ADC, TRACEW, SWI,
                 Perfusion_Weighted, ...) -> belongs under derivatives/, not raw.
      source   : "series_map" | "builtin" | "unknown" -- where the decision came
                 from (useful for logs / the readiness report).
      reason   : short human-readable explanation.
    """

    __slots__ = ("datatype", "suffix", "entities", "derived", "source", "reason")

    def __init__(self, datatype, suffix="", entities=None, derived=False,
                 source="builtin", reason=""):
        self.datatype = datatype
        self.suffix = suffix or ""
        self.entities = entities or {}
        self.derived = derived
        self.source = source
        self.reason = reason

    def __repr__(self):
        return (f"Classification(datatype={self.datatype!r}, suffix={self.suffix!r}, "
                f"entities={self.entities!r}, derived={self.derived}, "
                f"source={self.source!r})")


# ----------------------------------------------------------------------------
# series_map.csv  -- load + apply
# ----------------------------------------------------------------------------

def _parse_entities(text):
    """Parse an entities cell like 'task=rest;acq=highres' into an ordered dict."""
    out = {}
    for piece in (text or "").split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        key, _, value = piece.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            out[key] = value
    return out


def load_series_map(map_path=DEFAULT_MAP_PATH):
    """
    Read series_map.csv into a list of rule dicts. Missing file -> empty list
    (the built-in rules then handle everything). One bad row never breaks the run.

    Expected columns (header, case-insensitive):
        match_type, pattern, datatype, suffix, entities, derived, notes
      match_type : 'substring' (default) | 'regex' | 'exact'
      pattern    : the text/regex matched against the (lowercased) description
      datatype   : anat/func/dwi/fmap/perf/swi/misc/unknown
      suffix     : BIDS suffix (optional)
      entities   : 'k=v;k=v' (optional)
      derived    : 'yes'/'true'/'1' to mark a derived image (optional)
      notes      : free text (ignored by code)
    """
    rules = []
    if not map_path or not os.path.isfile(map_path):
        return rules
    try:
        with open(map_path, newline="", encoding="utf-8") as f:
            # Drop comment (#...) and blank lines BEFORE parsing, so commented
            # example rows (which contain commas) are never read as real rules.
            # The first surviving line is the header.
            lines = [ln for ln in f
                     if ln.strip() and not ln.lstrip().startswith("#")]
        for row in csv.DictReader(lines):
                # Normalize keys to lowercase so the header is forgiving.
                row = { (k or "").strip().lower(): (v or "").strip()
                        for k, v in row.items() }
                pattern = row.get("pattern", "")
                datatype = row.get("datatype", "")
                # A row needs at least a pattern AND a datatype to do anything;
                # blank stub rows (added for the operator to fill) are skipped.
                if not pattern or not datatype:
                    continue
                rules.append({
                    "match_type": (row.get("match_type") or "substring").lower(),
                    "pattern": pattern,
                    "datatype": datatype.lower(),
                    "suffix": row.get("suffix", ""),
                    "entities": _parse_entities(row.get("entities", "")),
                    "derived": (row.get("derived", "").lower()
                                in ("yes", "true", "1", "y")),
                    "notes": row.get("notes", ""),
                })
    except Exception:
        # A malformed map must never take the pipeline down; fall back to builtins.
        return []
    return rules


def _row_matches(rule, description_lc):
    """True if one series_map rule matches the lowercased description."""
    pat = rule["pattern"].lower()
    kind = rule["match_type"]
    try:
        if kind == "regex":
            return re.search(rule["pattern"], description_lc, re.IGNORECASE) is not None
        if kind == "exact":
            return description_lc == pat
        return pat in description_lc          # 'substring' (default)
    except re.error:
        return False


# ----------------------------------------------------------------------------
# Built-in suffix helpers
# ----------------------------------------------------------------------------

def anat_suffix(description):
    """
    Map a description to a BIDS anatomical suffix, or None. FLAIR is checked
    first because FLAIR protocols here are themselves T2-SPACE variants
    (e.g. 't2_space_da-fl3').
    """
    text = (description or "").lower()
    if any(k in text for k in ["flair", "fl3", "da-fl", "dark"]):
        return "FLAIR"
    if any(k in text for k in ["t1", "mprage", "mp2rage", "spgr", "bravo", "tfl"]):
        return "T1w"
    if any(k in text for k in ["t2", "space", "tse"]):
        return "T2w"
    return None


def _func_entities(description):
    """Infer func entities from the description. Resting-state -> task-rest;
    otherwise leave 'task' unset so the caller inserts a clear placeholder."""
    text = (description or "").lower()
    if any(k in text for k in ["rest", "resting", "rsfmri", "rs-fmri"]):
        return {"task": "rest"}
    return {}


def _looks_func(description_lc, image_type_lc):
    """BOLD detection from description keywords OR the DICOM ImageType."""
    if any(k in description_lc for k in FUNC_KEYWORDS):
        return True
    # ImageType is the extra net: Siemens/GE BOLD often carries these tokens.
    return any(tok in image_type_lc for tok in ["bold", "fmri"])


# ----------------------------------------------------------------------------
# The main entry point
# ----------------------------------------------------------------------------

def classify(description, image_type="", modality="", sequence="",
             series_map=None):
    """
    Classify one series. Returns a Classification.

    description : SeriesDescription (the main signal).
    image_type  : DICOM ImageType joined to a string (extra signal for BOLD/ASL).
    modality / sequence : optional extra hints (reserved; not required).
    series_map  : pre-loaded list from load_series_map(); if None, it is loaded.

    Decision order:
      1. series_map.csv rows (first match wins) -> source="series_map".
      2. built-in keyword rules                 -> source="builtin".
      3. nothing matched                        -> datatype="unknown".
    """
    if series_map is None:
        series_map = load_series_map()

    desc_lc = (description or "").lower()
    itype_lc = (image_type or "").lower()

    # ---- 1) operator map wins ------------------------------------------------
    for rule in series_map:
        if _row_matches(rule, desc_lc):
            return Classification(
                datatype=rule["datatype"],
                suffix=rule["suffix"],
                entities=dict(rule["entities"]),
                derived=rule["derived"],
                source="series_map",
                reason=f"matched series_map ({rule['match_type']}: {rule['pattern']})",
            )

    # ---- 2) built-in keyword rules ------------------------------------------
    # Specific modalities before broad anat. ImageType helps catch unnamed BOLD.
    if any(k in desc_lc for k in FMAP_KEYWORDS):
        # gre field mapping: the phase image is the usual export here.
        suffix = "phasediff" if ("ph" in desc_lc or "phase" in itype_lc) else "magnitude"
        return Classification("fmap", suffix, source="builtin",
                              reason="fmap keyword")
    if any(k in desc_lc for k in PERF_KEYWORDS):
        # Scanner-derived perfusion maps are NOT raw ASL.
        derived = "perfusion" in desc_lc and "asl" not in desc_lc
        suffix = "" if derived else "asl"
        ent = {"desc": "perfusionWeighted"} if derived else {}
        return Classification("perf", suffix, entities=ent, derived=derived,
                              source="builtin",
                              reason="perfusion/ASL keyword")
    if any(k in desc_lc for k in SWI_KEYWORDS):
        # All SWI family images are scanner-derived (mag/phase/SWI/minIP).
        ent = {}
        if "pha" in desc_lc:
            ent = {"desc": "phase"}
        elif "mag" in desc_lc:
            ent = {"desc": "mag"}
        elif "mip" in desc_lc or "minip" in desc_lc or "min_ip" in desc_lc:
            ent = {"desc": "minIP"}
        elif "swi" in desc_lc:
            ent = {"desc": "swi"}
        return Classification("swi", "swi", entities=ent, derived=True,
                              source="builtin", reason="SWI family keyword")
    if any(k in desc_lc for k in DWI_KEYWORDS):
        # TRACEW and ADC are scanner-DERIVED diffusion maps (no gradient table).
        ent, derived = {}, False
        if "adc" in desc_lc:
            ent, derived = {"desc": "ADC"}, True
        elif "trace" in desc_lc:
            ent, derived = {"desc": "traceweighted"}, True
        return Classification("dwi", "dwi", entities=ent, derived=derived,
                              source="builtin", reason="diffusion keyword")
    if _looks_func(desc_lc, itype_lc):
        return Classification("func", "bold", entities=_func_entities(desc_lc),
                              source="builtin", reason="BOLD/func signal")
    suffix = anat_suffix(desc_lc)
    if suffix is not None:
        return Classification("anat", suffix, source="builtin",
                              reason="anatomical keyword")
    if any(k in desc_lc for k in SCOUT_KEYWORDS):
        # Known-ignorable localizer; park in misc/ without flagging as unknown.
        return Classification("misc", "", source="builtin",
                              reason="localizer/scout")

    # ---- 3) the new stranger: nobody recognized it --------------------------
    return Classification("unknown", "", source="unknown",
                          reason="no series_map row or built-in rule matched")


def suggest_for_unknown(description, image_type=""):
    """
    Best-effort hint for the unknown-series report: a short guess at the datatype
    a human might assign, so the operator has a starting point when filling in
    series_map.csv. Never used to auto-route -- only advisory text.
    """
    desc_lc = (description or "").lower()
    itype_lc = (image_type or "").lower()
    if _looks_func(desc_lc, itype_lc):
        return "func? (looks like BOLD/EPI)"
    if "mosaic" in itype_lc:
        return "func? (mosaic EPI) or check sequence"
    if anat_suffix(desc_lc):
        return "anat? (has a structural keyword)"
    return "needs human classification"
