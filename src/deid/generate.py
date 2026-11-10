"""Synthetic clinical note generator with gold labels that are correct by construction.

Templates contain {placeholders}. Each placeholder value is plain text, an `Ident`
(an identifier with a category), a `Trap` (a clinical term that contains a person's name
but is NOT an identifier), or a list of those. `render` records the exact character
offsets of every Ident and Trap while it builds the string, so labels never drift.

Run: python -m deid.generate [--n 200] [--seed 1234]
"""

from __future__ import annotations

import argparse
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta

from faker import Faker

from deid.categories import CATEGORIES
from deid.config import N_NOTES, SEED
from deid.io import NOTES_PATH, write_jsonl

# --------------------------------------------------------------------------- pieces


@dataclass(frozen=True)
class Ident:
    text: str
    category: str
    subtype: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        assert self.category in CATEGORIES, self.category


@dataclass(frozen=True)
class Trap:
    text: str


Piece = str | Ident | Trap | list

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render(template: str, values: dict[str, Piece]) -> tuple[str, list[dict], list[dict]]:
    """Fill a template and return (text, identifier spans, trap spans)."""
    out: list[str] = []
    spans: list[dict] = []
    traps: list[dict] = []
    pos = 0

    def emit(piece: Piece) -> None:
        nonlocal pos
        if isinstance(piece, str):
            text = piece
        elif isinstance(piece, Ident):
            text = piece.text
            spans.append(
                {
                    "start": pos,
                    "end": pos + len(text),
                    "text": text,
                    "category": piece.category,
                    "subtype": piece.subtype,
                    "tags": list(piece.tags),
                }
            )
        elif isinstance(piece, Trap):
            text = piece.text
            traps.append({"start": pos, "end": pos + len(text), "text": text})
        elif isinstance(piece, list | tuple):
            for p in piece:
                emit(p)
            return
        else:  # pragma: no cover
            raise TypeError(type(piece))
        out.append(text)
        pos += len(text)

    last = 0
    for m in _PLACEHOLDER.finditer(template):
        emit(template[last : m.start()])
        emit(values[m.group(1)])
        last = m.end()
    emit(template[last:])
    return "".join(out), spans, traps


# --------------------------------------------------------------------------- vocab

# First names that are also ordinary English words.
COMMON_WORD_FIRST = {
    "F": ["Hope", "Rose", "Faith", "Grace", "June", "April", "Joy", "Dawn", "Summer", "Ivy"],
    "M": ["Will", "Mark", "Bill", "Frank", "Rusty", "Hunter", "Chase", "Guy", "Miles", "Gene"],
}
COMMON_WORD_LAST = [
    "Young",
    "White",
    "Brown",
    "Wood",
    "Hall",
    "King",
    "Rice",
    "Price",
    "Bell",
    "Strong",
    "Little",
    "Moody",
    "Rich",
    "Hill",
    "Love",
    "Fox",
    "Stone",
    "Cook",
    "Long",
    "Black",
]

# Real US city/state pairs. Faker's invented cities ("East Jessicaport") would be unfairly
# hard for NER models, which know real place names.
CITIES = [
    ("Springfield", "IL"),
    ("Columbus", "OH"),
    ("Fresno", "CA"),
    ("Tucson", "AZ"),
    ("Albany", "NY"),
    ("Madison", "WI"),
    ("Raleigh", "NC"),
    ("Boise", "ID"),
    ("Spokane", "WA"),
    ("Dayton", "OH"),
    ("Savannah", "GA"),
    ("Lansing", "MI"),
    ("Tulsa", "OK"),
    ("Reno", "NV"),
    ("Lincoln", "NE"),
    ("Durham", "NC"),
    ("Worcester", "MA"),
    ("Provo", "UT"),
    ("Eugene", "OR"),
    ("Mobile", "AL"),
    ("Augusta", "GA"),
    ("Charleston", "SC"),
    ("Knoxville", "TN"),
    ("Wichita", "KS"),
    ("Burlington", "VT"),
    ("Hartford", "CT"),
    ("Fargo", "ND"),
    ("Billings", "MT"),
    ("Pueblo", "CO"),
    ("Shreveport", "LA"),
    ("Lafayette", "IN"),
    ("Odessa", "TX"),
    ("Bangor", "ME"),
    ("Duluth", "MN"),
    ("Peoria", "IL"),
    ("Akron", "OH"),
]
SAINTS = ["Mary's", "Joseph's", "Luke's", "Vincent's", "Francis", "Anne's", "Jude"]

# Clinical terms that contain a person's name but are not identifiers.
EPONYM_DX = [
    "Parkinson's disease",
    "Down syndrome",
    "Hodgkin lymphoma",
    "Crohn's disease",
    "Alzheimer's disease",
    "Bell's palsy",
    "Graves' disease",
    "Hashimoto's thyroiditis",
    "Cushing syndrome",
    "Addison's disease",
    "Raynaud phenomenon",
    "Guillain-Barre syndrome",
]
PLAIN_PMH = [
    "hypertension",
    "hyperlipidemia",
    "type 2 diabetes mellitus",
    "CKD stage 3",
    "GERD",
    "osteoarthritis",
    "major depressive disorder",
    "hypothyroidism",
    "obstructive sleep apnea",
    "atrial fibrillation",
    "COPD",
    "asthma",
    "coronary artery disease",
]
CHILD_PMH = ["asthma", "eczema", "ADHD", "type 1 diabetes mellitus", "seizure disorder"]
CHILD_MEDICATIONS = [
    ("albuterol", "90 mcg", "2 puffs every 4 hours as needed"),
    ("cetirizine", "5 mg", "daily"),
    ("methylphenidate", "10 mg", "every morning"),
    ("levetiracetam", "250 mg", "twice daily"),
    ("fluticasone", "44 mcg", "2 puffs twice daily"),
    ("insulin lispro", "4 units", "with meals"),
]
ACUTE_DX = [
    "community-acquired pneumonia",
    "acute exacerbation of COPD",
    "acute decompensated heart failure",
    "cellulitis of the left lower extremity",
    "pyelonephritis",
    "atrial fibrillation with rapid ventricular response",
    "acute kidney injury",
    "small bowel obstruction",
    "diabetic ketoacidosis",
    "upper gastrointestinal bleed",
    "pulmonary embolism",
]
COMPLAINTS = [
    "shortness of breath",
    "chest pain",
    "fever and productive cough",
    "abdominal pain",
    "dizziness",
    "left leg swelling",
    "nausea and vomiting",
    "a fall at home",
]
# (name, dose, frequency). Names are exported for the content-integrity check.
MEDICATIONS = [
    ("metoprolol tartrate", "25 mg", "twice daily"),
    ("lisinopril", "10 mg", "daily"),
    ("atorvastatin", "40 mg", "nightly"),
    ("metformin", "500 mg", "twice daily"),
    ("apixaban", "5 mg", "twice daily"),
    ("furosemide", "40 mg", "daily"),
    ("levothyroxine", "75 mcg", "daily"),
    ("carbidopa-levodopa", "25/100 mg", "three times daily"),
    ("amoxicillin-clavulanate", "875 mg", "twice daily"),
    ("prednisone", "40 mg", "daily"),
    ("insulin glargine", "20 units", "nightly"),
    ("sertraline", "50 mg", "daily"),
    ("omeprazole", "20 mg", "daily"),
    ("gabapentin", "300 mg", "three times daily"),
    ("amlodipine", "5 mg", "daily"),
    ("tamsulosin", "0.4 mg", "nightly"),
]
MEDICATION_NAMES = [m[0] for m in MEDICATIONS + CHILD_MEDICATIONS] + ["metformin", "levothyroxine"]
SPECIALTIES = ["Neurology", "Cardiology", "Oncology", "Gastroenterology", "Endocrinology"]
RELATIVE_ROLES = {"adult": ["daughter", "son", "wife", "husband", "sister"], "child": ["mother"]}

DATE_STYLES = ["mdy_slash", "mdy_short", "iso", "long", "abbrev_no_year", "dmy_abbrev", "md"]
_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_LONG = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def format_date(d: date, style: str) -> str:
    match style:
        case "mdy_slash":
            return d.strftime("%m/%d/%Y")  # 03/04/2024
        case "mdy_short":
            return f"{d.month}/{d.day}/{d.year % 100:02d}"  # 3/4/24
        case "iso":
            return d.isoformat()  # 2024-03-04
        case "long":
            return f"{_MONTH_LONG[d.month - 1]} {d.day}, {d.year}"  # March 4, 2024
        case "abbrev_no_year":
            return f"{_MONTH_ABBR[d.month - 1]} {d.day}"  # Mar 4
        case "dmy_abbrev":
            return f"{d.day:02d}-{_MONTH_ABBR[d.month - 1]}-{d.year}"  # 04-Mar-2024
        case "md":
            return f"{d.month}/{d.day}"  # 3/4
        case "month_year":
            return f"{_MONTH_LONG[d.month - 1]} {d.year}"  # March 2024 (still a date element)
    raise ValueError(style)


# --------------------------------------------------------------------------- one case


class CaseBuilder:
    """Draws one synthetic patient encounter and exposes its template values."""

    def __init__(self, fake: Faker, rng: random.Random, note_type: str):
        self.fake, self.rng, self.note_type = fake, rng, note_type
        # Sloppy notes write names in lowercase (more common in quick messages).
        p_lower = 0.35 if note_type == "phone_message" else 0.1
        self.lowercase = rng.random() < p_lower

    # -- people
    def _first(self, sex: str) -> tuple[str, bool]:
        if self.rng.random() < 0.3:
            return self.rng.choice(COMMON_WORD_FIRST[sex]), True
        return (self.fake.first_name_female() if sex == "F" else self.fake.first_name_male()), False

    def _last(self) -> tuple[str, bool]:
        if self.rng.random() < 0.2:
            return self.rng.choice(COMMON_WORD_LAST), True
        return self.fake.last_name(), False

    def name(self, text: str, role: str, common: bool, lower_ok: bool = True) -> Ident:
        tags = []
        if common:
            tags.append("common_word")
        if self.lowercase and lower_ok:
            text = text.lower()
            tags.append("lowercase")
        return Ident(text, "NAME", role, tuple(tags))

    def date(self, d: date, styles: list[str] | None = None, subtype: str = "") -> Ident:
        style = self.rng.choice(styles or DATE_STYLES)
        return Ident(format_date(d, style), "DATE", subtype, (style,))

    def phone(self) -> Ident:
        r = self.rng
        a, e, n = r.randint(201, 989), r.randint(201, 999), r.randint(0, 9999)
        fmt = r.choice(["({a}) {e}-{n}", "{a}-{e}-{n}", "{a}.{e}.{n}", "+1 {a} {e} {n}"])
        return Ident(fmt.format(a=a, e=e, n=f"{n:04d}"), "PHONE", tags=(fmt,))

    def build(self) -> dict[str, Piece]:
        r, f = self.rng, self.fake
        v: dict[str, Piece] = {}

        # Patient demographics. ~20% are over 89 so AGE_OVER_89 has real support.
        band = r.choices(["child", "adult", "old"], weights=[12, 63, 25])[0]
        target_age = {
            "child": r.randint(2, 16),
            "adult": r.randint(19, 87),
            "old": r.randint(90, 102),
        }[band]
        visit = date(2019, 1, 1) + timedelta(days=r.randint(0, 6 * 365))
        dob = visit - timedelta(days=int(target_age * 365.25) + r.randint(0, 300))
        age = visit.year - dob.year - ((visit.month, visit.day) < (dob.month, dob.day))
        sex = r.choice("FM")
        first, c1 = self._first(sex)
        last, c2 = self._last()
        common = c1 or c2
        self.is_child = band == "child"

        v["pt_full"] = self.name(f"{first} {last}", "patient", common)
        v["pt_lf"] = self.name(f"{last}, {first}", "patient", common)
        v["pt_first"] = self.name(first, "patient", c1)
        honorific = "" if self.is_child else ("Mr. " if sex == "M" else "Ms. ")
        v["pt_formal"] = (
            self.name(first, "patient", c1)
            if self.is_child
            else [honorific, self.name(last, "patient", c2)]
        )
        v["he"], v["his"], v["him"] = ("he", "his", "him") if sex == "M" else ("she", "her", "her")
        v["He"], v["His"] = str(v["he"]).capitalize(), str(v["his"]).capitalize()
        v["sex_word"] = (
            ("boy" if sex == "M" else "girl")
            if self.is_child
            else ("man" if sex == "M" else "woman")
        )
        v["sex_short"] = sex
        v["age"] = Ident(str(age), "AGE_OVER_89") if age >= 90 else str(age)

        # Dates
        los = r.randint(2, 9)
        v["dob"] = self.date(dob, ["mdy_slash", "mdy_short", "iso", "long", "dmy_abbrev"], "dob")
        v["date_admit"] = self.date(visit - timedelta(days=los), subtype="admit")
        v["date_admit2"] = self.date(visit - timedelta(days=los), subtype="admit")
        v["date_visit"] = self.date(visit, subtype="visit")
        v["date_fu"] = self.date(visit + timedelta(days=r.randint(7, 30)), subtype="followup")
        v["date_lab"] = self.date(visit - timedelta(days=r.randint(1, 6)), subtype="lab")
        onset = visit - timedelta(days=r.randint(20, 120))
        v["date_onset"] = self.date(onset, ["month_year", "abbrev_no_year", "long"], "onset")
        v["year_dx"] = str(visit.year - r.randint(2, 15))  # a bare year is NOT an identifier

        # Identifiers
        mrn = (
            f"{r.randint(0, 99_999_999):08d}"
            if r.random() < 0.7
            else str(r.randint(1_000_000, 9_999_999))
        )
        label = r.choice(["MRN: ", "MRN ", "MR#: ", "Medical Record Number: ", "Med Rec #"])
        v["mrn_lab"] = [label, Ident(mrn, "MRN", "labeled")]
        v["mrn_bare"] = Ident(mrn, "MRN", "unlabeled")
        v["mrn_any"] = v["mrn_lab"] if r.random() < 0.5 else ["(", v["mrn_bare"], ")"]
        ssn = f.ssn()
        ssn = r.choices([ssn, ssn.replace("-", " "), ssn.replace("-", "")], weights=[8, 1, 1])[0]
        v["ssn"] = Ident(ssn, "SSN")
        v["phone_pt"], v["phone_rel"], v["phone_clinic"] = self.phone(), self.phone(), self.phone()
        domain = r.choice(["gmail.com", "yahoo.com", "outlook.com", "icloud.com", "aol.com"])
        sep = r.choice([".", "_", ""])
        local = f"{first}{sep}{last}{r.randint(1, 99)}".lower().replace("'", "").replace(" ", "")
        v["email_pt"] = Ident(f"{local}@{domain}", "EMAIL")

        city, state = r.choice(CITIES)
        zipc = f.zipcode_in_state(state)
        if r.random() < 0.15:
            zipc += f"-{r.randint(0, 9999):04d}"
        v["city"] = Ident(city, "CITY")
        v["addr"] = [
            Ident(f.street_address(), "ADDRESS"),
            ", ",
            Ident(city, "CITY"),
            f", {state} ",
            Ident(zipc, "ZIP"),
        ]

        def hospital() -> Ident:
            c = r.choice(CITIES)[0]
            pattern = r.choice(
                [
                    f"{c} General Hospital",
                    f"St. {r.choice(SAINTS)} Medical Center",
                    f"{f.last_name()} Memorial Hospital",
                    f"{c} Regional Medical Center",
                    f"University Hospital of {c}",
                ]
            )
            return Ident(pattern, "HOSPITAL")

        v["hospital"], v["hospital2"] = hospital(), hospital()

        # Clinicians and family
        def clinician(key: str) -> None:
            dsex = r.choice("FM")
            dfirst, dc1 = self._first(dsex)
            dlast, dc2 = self._last()
            full = self.name(f"{dfirst} {dlast}", "clinician", dc1 or dc2, lower_ok=False)
            lastn = self.name(dlast, "clinician", dc2, lower_ok=False)
            v[f"{key}_full"] = r.choice([["Dr. ", full], [full, ", MD"], [full, " MD"]])
            v[f"{key}_last"] = lastn
            v[f"{key}_short"] = ["Dr. ", lastn]

        clinician("doc")
        clinician("doc2")
        nfirst, nc = self._first(r.choice("FM"))
        v["nurse"] = [self.name(nfirst, "clinician", nc, lower_ok=False), ", RN"]

        role = r.choice(RELATIVE_ROLES["child" if self.is_child else "adult"])
        rfirst, rc = self._first("M" if role in ("son", "husband") else "F")
        rlast = last if r.random() < 0.6 else self._last()[0]
        v["rel_role"] = role
        v["rel"] = self.name(f"{rfirst} {rlast}", "relative", rc)
        v["rel_first"] = self.name(rfirst, "relative", rc)

        # Clinical content (not identifiers), with eponym traps mixed in.
        pmh_pool = CHILD_PMH if self.is_child else PLAIN_PMH
        pmh = r.sample(pmh_pool, r.randint(2, 3))
        pmh_pieces: list[Piece] = list(pmh)
        for t in r.sample(EPONYM_DX, r.choices([0, 1, 2], weights=[3, 5, 2])[0]):
            pmh_pieces.insert(r.randint(0, len(pmh_pieces)), Trap(t))
        v["pmh"] = _join_list(pmh_pieces)
        v["pmh_item"] = pmh[0]
        v["dx"] = r.choice(ACUTE_DX)
        v["dx_chronic"] = r.choice([Trap(r.choice(EPONYM_DX)), r.choice(pmh_pool)])
        v["referral_reason"] = r.choice(
            [
                [Trap(r.choice(EPONYM_DX))],
                ["possible ", Trap(r.choice(EPONYM_DX))],
                "new-onset tremor",
                "an abnormal thyroid panel",
                "iron deficiency anemia",
            ]
        )
        v["complaint"] = r.choice(COMPLAINTS)
        v["duration"] = r.choice(["3 days", "2 weeks", "48 hours", "5 days"])
        meds = r.sample(CHILD_MEDICATIONS if self.is_child else MEDICATIONS, r.randint(3, 5))
        v["med_lines"] = "\n".join(f"- {n} {d} {q}" for n, d, q in meds)
        v["med_inline"] = "; ".join(f"{n} {d} {q}" for n, d, q in meds)
        v["med1"] = f"{meds[0][0]} {meds[0][1]}"
        v["vitals"] = (
            f"BP {r.randint(100, 168)}/{r.randint(58, 98)}, HR {r.randint(58, 118)}, "
            f"RR {r.randint(12, 24)}, T {r.choice(['36.8', '37.2', '38.1', '38.6'])} C, "
            f"SpO2 {r.randint(89, 99)}% on room air"
        )
        v["labs"] = (
            f"WBC {r.uniform(4, 16):.1f}, Hgb {r.uniform(8, 15):.1f}, Plt {r.randint(140, 420)}, "
            f"Na {r.randint(129, 144)}, K {r.uniform(3.2, 5.4):.1f}, Cr {r.uniform(0.6, 2.4):.2f}"
        )
        v["a1c"] = f"{r.uniform(5.8, 10.5):.1f}%"
        v["tsh"] = f"{r.uniform(0.2, 9.8):.2f}"
        v["strength"] = f"strength {r.choice(['4/5', '5/5', '4+/5'])} in the left upper extremity"
        v["procedure"] = r.choice(
            [
                [
                    "A ",
                    Trap("Foley catheter"),
                    " was placed for urinary retention and removed on hospital day 3.",
                ],
                ["Abdominal exam notable for a positive ", Trap("Murphy's sign"), "."],
                [Trap("Glasgow Coma Scale"), " 15 on arrival."],
                "A peripherally inserted central catheter was placed for IV antibiotics.",
                "",
            ]
        )
        v["birth_history"] = (
            ["Birth history: term vaginal delivery, ", Trap("Apgar score"), "s 8 and 9.\n"]
            if self.is_child
            else ""
        )
        return v


def _join_list(items: list[Piece]) -> list[Piece]:
    out: list[Piece] = []
    for i, it in enumerate(items):
        if i:
            out.append(", " if i < len(items) - 1 else ", and ")
        out.append(it)
    return out


# --------------------------------------------------------------------------- templates


def t_discharge(r: random.Random) -> str:
    ssn_line = "SSN: {ssn}\n" if r.random() < 0.5 else ""
    return (
        "DISCHARGE SUMMARY\n{hospital}\n\n"
        "Patient: {pt_lf}    {mrn_lab}\n"
        "DOB: {dob}    Age: {age}\n" + ssn_line + "Admission Date: {date_admit}    "
        "Discharge Date: {date_visit}\nAttending: {doc_full}\n\n"
        "Principal Diagnosis: {dx}\n\n"
        "History of Present Illness:\n"
        "{pt_formal} is a {age}-year-old {sex_word} with a history of {pmh} who presented on "
        "{date_admit2} with {complaint} for {duration}. {He} was diagnosed with {pmh_item} in "
        "{year_dx}.\n\n"
        "Hospital Course:\n"
        "{pt_formal} was admitted for {dx} and treated accordingly. {procedure} Admission labs: "
        "{labs}. {He} improved and will be discharged home with {his} {rel_role}, {rel}.\n\n"
        "Discharge Address: {addr}\n\n"
        "Discharge Medications:\n{med_lines}\n\n"
        "Follow-up: with {doc2_full} at {hospital2} on {date_fu}. Questions: call {phone_clinic}.\n\n"
        "Electronically signed by {doc_full}"
    )


def t_progress(r: random.Random) -> str:
    social = "Lives in {city} with {his} {rel_role} {rel_first}. " if r.random() < 0.6 else ""
    return (
        "PROGRESS NOTE - {date_visit}\n"
        "Pt: {pt_full} {mrn_any}\n\n"
        "S: {age} yo {sex_short} seen for follow-up of {dx_chronic}. Reports feeling better "
        "since last visit, sleeping well. " + social + "Diagnosed with {pmh_item} in {year_dx}.\n"
        "{birth_history}"
        "O: {vitals}. Exam: {strength}.\n"
        "Labs ({date_lab}): {labs}\n\n"
        "A/P:\n"
        "1. {dx_chronic} - stable, continue current regimen.\n"
        "2. {pmh_item} - continue {med1}.\n"
        "Will recheck labs in 3 months. RTC on {date_fu}.\n\n"
        "-- {doc_short}"
    )


def t_referral(r: random.Random) -> str:
    return (
        "{hospital}\n{date_visit}\n\n"
        "{doc2_full}\nDepartment of " + r.choice(SPECIALTIES) + "\n{hospital2}\n\n"
        "Re: {pt_full}, DOB {dob}, {mrn_lab}\n"
        "Address: {addr}\n\n"
        "Dear Dr. {doc2_last},\n\n"
        "Thank you for seeing {pt_formal}, a {age}-year-old {sex_word} whom I am referring for "
        "evaluation of {referral_reason}. Symptoms began around {date_onset}. Past medical history "
        "includes {pmh}. Current medications: {med_inline}.\n"
        "{birth_history}"
        "Recent labs ({date_lab}): {labs}.\n\n"
        "{He} can be reached at {phone_pt} or by email at {email_pt}. "
        "{His} {rel_role}, {rel}, is also involved in {his} care.\n\n"
        "Sincerely,\n{doc_full}\n{hospital}"
    )


def t_ed(r: random.Random) -> str:
    return (
        "EMERGENCY DEPARTMENT NOTE\n{hospital}\n"
        "Arrival: {date_visit}\n"
        "Name: {pt_full}    {mrn_lab}    SSN: {ssn}\n"
        "Address: {addr}\n"
        "Emergency contact: {rel} ({rel_role}), {phone_rel}\n\n"
        "CC: {complaint}\n"
        "HPI: {age}-year-old {sex_word} brought in by "
        + r.choice(["EMS", "family", "private vehicle"])
        + " with {complaint} for {duration}. Denies recent travel. PMH: {pmh}.\n"
        "Meds: {med_inline}\n"
        "Vitals: {vitals}\n"
        "Exam: {procedure} {strength}.\n"
        "Labs: {labs}\n\n"
        "Assessment: {dx}. Admitted to medicine under {doc2_short}.\n\n"
        "{doc_full}, ED attending"
    )


def t_lab(r: random.Random) -> str:
    result = r.choice(
        [
            "Your hemoglobin A1c was {a1c}, which is above goal. We will increase your "
            "metformin to 1000 mg twice daily.",
            "Your TSH was {tsh}. Please continue levothyroxine 75 mcg daily and repeat the test "
            "in 6 weeks.",
            "Your blood counts and kidney function were stable: {labs}.",
        ]
    )
    return (
        "{hospital} - Lab Results\n"
        "Date: {date_visit}\n"
        "To: {pt_full}\n{addr}\n{mrn_lab}\n\n"
        "Dear {pt_formal},\n\n"
        "Your lab work from {date_lab} has resulted. " + result + "\n\n"
        "A copy has been sent to {email_pt}. If you have questions, please call our office at "
        "{phone_clinic}.\n\n"
        "Sincerely,\n{nurse}, on behalf of {doc_full}"
    )


def t_phone(r: random.Random) -> str:
    who = r.choice(
        [
            "Caller: {rel} ({rel_role}), callback {phone_rel}\n",
            "Caller: patient, callback {phone_pt}\n",
        ]
    )
    return (
        "TELEPHONE ENCOUNTER {date_visit}\n"
        "Re: pt {pt_lf}, {age} yo, " + r.choice(["{mrn_bare}", "{mrn_lab}"]) + "\n" + who + "\n"
        "Msg: {rel_first} states {pt_first} has had {complaint} since {date_onset}. "
        "Taking {med1} as prescribed. DOB {dob} verified. "
        + r.choice(["Email on file: {email_pt}. ", ""])
        + "Advised to go to the ED at {hospital} if worse. Will route to {doc_short}.\n\n"
        "Taken by: {nurse}"
    )


NOTE_TYPES = {
    "discharge_summary": t_discharge,
    "progress_note": t_progress,
    "referral_letter": t_referral,
    "ed_note": t_ed,
    "lab_followup": t_lab,
    "phone_message": t_phone,
}


# --------------------------------------------------------------------------- corpus


def generate(n: int = N_NOTES, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    types = list(NOTE_TYPES)
    notes = []
    for i in range(n):
        note_type = types[i % len(types)]
        values = CaseBuilder(fake, rng, note_type).build()
        template = NOTE_TYPES[note_type](rng)
        text, spans, traps = render(template, values)
        notes.append(
            {
                "id": f"note_{i:04d}",
                "note_type": note_type,
                "text": text,
                "spans": spans,
                "traps": traps,
            }
        )
    return notes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=N_NOTES)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    notes = generate(args.n, args.seed)
    write_jsonl(NOTES_PATH, notes)
    n_spans = sum(len(n["spans"]) for n in notes)
    n_traps = sum(len(n["traps"]) for n in notes)
    print(
        f"wrote {len(notes)} notes, {n_spans} identifier spans, {n_traps} trap terms -> {NOTES_PATH}"
    )


if __name__ == "__main__":
    main()
