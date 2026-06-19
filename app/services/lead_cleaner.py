"""Lead Cleaner — normalize, validate, and enrich phone numbers in lead pools.

Common problems it fixes:
  - Two numbers concatenated   ("+27731234567+27739876543")
  - Extra digits from scraping ("+27731234567ext123")
  - Missing country code       ("0731234567" → "+27731234567")
  - Spaces/dashes/hyphens      ("+27 73 123 4567")
  - Wrong country codes
  - Garbage text masquerading as numbers
  - Duplicate detection across a list

Strategy:
  1. Strip everything except digits and a leading +
  2. Try to split concatenated numbers (when length > 15)
  3. Normalize to E.164 (+27731234567)
  4. Validate against known SA mobile/landline ranges
  5. Score confidence: high / medium / low / invalid
"""

import re
from typing import Any

# South African number patterns (primary market)
# Mobile: +27 6X XX XX XXX, +27 7X XX XX XXX, +27 8X XX XX XXX
# Landline: +27 10/11/12/13/14/15/16/17/18/21/31/41/51/61/71 (area codes)
SA_MOBILE_PREFIXES = {"60", "61", "62", "63", "64", "65", "66", "67", "68", "69",
                       "71", "72", "73", "74", "75", "76", "77", "78", "79",
                       "81", "82", "83", "84", "85", "86", "87", "88"}
SA_LANDLINE_PREFIXES = {"10", "11", "12", "13", "14", "15", "16", "17", "18", "21",
                         "31", "41", "51", "61", "71", "010", "011", "012", "013",
                         "014", "015", "016", "017", "018", "021", "031", "041",
                         "051", "061", "071"}
VALID_SA_LENGTH = 12  # +27 XX XXX XXXX (country code + 9 digits)
LOCAL_LENGTH = 10     # 0XX XXX XXXX


def _strip(raw: str) -> str:
    """Remove everything except digits and a leading +.

    Also strips common extension markers (ext, x, #) and everything after
    them to avoid including desk-phone extensions in the number.
    """
    raw = raw.strip().lower()
    # Strip extensions: "ext123", " x123", "#123"
    for ext_marker in ["ext", " x ", " #", "x."]:
        if ext_marker in raw:
            raw = raw.split(ext_marker)[0]
    # Strip trailing non-digit garbage after a clean number
    # e.g. "+27731234567call" or "0731234567abc"
    raw = re.sub(r"[a-z#]+.*$", "", raw)
    # Keep leading + if present, strip all other non-digits
    has_plus = raw.startswith("+")
    digits = re.sub(r"[^\d]", "", raw)
    if has_plus:
        digits = "+" + digits
    return digits


def _split_concatenated(raw: str) -> list[str]:
    """Try to split two valid numbers that were concatenated (length > 15).

    Common pattern: "+27731234567+27739876543" or "2773123456727739876543"
    Scans for a valid split point where both halves are plausible numbers.
    """
    clean = raw.lstrip("+")
    results = []

    # Try splitting at every position that could be a country code boundary
    for i in range(9, len(clean) - 5):  # +27 is 3 chars, need min 6 digits per half
        first = clean[:i]
        second = clean[i:]

        first_norm = _normalize_e164(first)
        second_norm = _normalize_e164(second)

        if first_norm and second_norm:
            results.append(first_norm)
            results.append(second_norm)
            return results  # Take first valid split

    return results


def _normalize_e164(digits: str) -> str | None:
    """Convert digits to E.164 (+27XXXXXXXXX), return None if invalid."""
    if not digits:
        return None

    # Strip any leading + for processing
    clean = digits.lstrip("+")

    # If it starts with 0, replace with +27 (SA)
    if clean.startswith("0") and len(clean) == 10:
        return "+27" + clean[1:]

    # If it starts with 27 and is 11+ digits, add +
    if clean.startswith("27") and len(clean) >= 11:
        return "+" + clean[:12]  # Trim to max valid length

    # If it starts with +27 and has 12 chars, already valid
    if digits.startswith("+27") and len(digits) == 12:
        return digits

    # If it's a bare 9-digit number, assume SA mobile and add +27
    if len(clean) == 9:
        return "+27" + clean

    # If it's just digits with no context and wrong length, flag it
    return None


def _validate_e164(number: str) -> tuple[str, str]:
    """Validate an E.164 number and return (confidence, reason).

    Confidence levels: high, medium, low, invalid
    """
    if not number or not number.startswith("+"):
        return "invalid", "Not an E.164 number"

    if len(number) < 10:
        return "invalid", "Too short"
    if len(number) > 15:
        return "invalid", "Too long (may be concatenated)"

    # Check SA numbers
    if number.startswith("+27"):
        local = number[3:]  # digits after +27
        if len(local) != 9:
            return "low", f"SA number with {len(local)} digits (expected 9)"

        prefix = local[:2]
        if prefix in SA_MOBILE_PREFIXES:
            return "high", "Valid SA mobile number"
        elif prefix in SA_LANDLINE_PREFIXES:
            return "high", "Valid SA landline number"
        else:
            # Check first 3 digits for 3-digit area codes
            prefix3 = local[:3]
            if prefix3 in SA_LANDLINE_PREFIXES:
                return "high", "Valid SA landline number"
            return "low", f"Unusual SA prefix: +27{prefix}"

    # International number
    return "medium", "International number"


POSSIBLE_INTERNATIONAL = {
    "1": "US/CA", "44": "UK", "91": "India", "61": "Australia",
    "64": "NZ", "49": "Germany", "33": "France", "39": "Italy",
    "34": "Spain", "31": "Netherlands", "46": "Sweden", "41": "Switzerland",
    "55": "Brazil", "52": "Mexico", "81": "Japan", "86": "China",
    "82": "Korea", "65": "Singapore", "971": "UAE", "966": "Saudi",
}


def _guess_country(number: str) -> str:
    """Try to guess the country from a raw number string."""
    clean = number.lstrip("+")
    for code, country in sorted(POSSIBLE_INTERNATIONAL.items(), key=lambda x: -len(x[0])):
        if clean.startswith(code):
            return country
    return "unknown"


def clean_phone(raw: str) -> dict[str, Any]:
    """Clean and validate a single phone number.

    Returns:
        {
            "original": raw,
            "cleaned": "+27731234567" or None,
            "confidence": "high" | "medium" | "low" | "invalid",
            "reason": "explanation",
            "splits": ["+27731234567", "+27739876543"] or None,
            "country": "ZA" | "US" | ...
        }
    """
    result = {
        "original": raw,
        "cleaned": None,
        "confidence": "invalid",
        "reason": "",
        "splits": None,
        "country": "",
    }

    if not raw or not raw.strip():
        result["reason"] = "Empty"
        return result

    stripped = _strip(raw)

    # Check for concatenated numbers
    if len(stripped) > 15:
        splits = _split_concatenated(stripped)
        if splits:
            result["splits"] = splits
            result["cleaned"] = splits[0]
            conf, reason = _validate_e164(splits[0])
            result["confidence"] = conf
            result["reason"] = f"Split into {len(splits)} numbers: {' | '.join(splits)}"
            result["country"] = "ZA"
            return result

    # Normalize
    normalized = _normalize_e164(stripped)
    if not normalized:
        # Try to guess country
        country = _guess_country(stripped)
        if country != "unknown":
            result["cleaned"] = stripped[:15]
            result["confidence"] = "low"
            result["reason"] = f"Could not normalize, guessed country: {country}"
            result["country"] = country
        else:
            result["cleaned"] = stripped[:15] if stripped else None
            result["reason"] = "Could not normalize to valid number"
        return result

    # Validate
    confidence, reason = _validate_e164(normalized)
    result["cleaned"] = normalized
    result["confidence"] = confidence
    result["reason"] = reason
    result["country"] = "ZA" if normalized.startswith("+27") else "intl"

    return result


# ─── Batch list cleaning ─────────────────────────────────────────────────────

def clean_lead_list(leads: list[dict]) -> dict:
    """Clean all phone numbers in a list of leads.

    Args:
        leads: list of lead dicts with at least {"id": ..., "phone": ...}

    Returns:
        {
            "total": N,
            "valid": N,
            "fixed": N,
            "invalid": N,
            "duplicates_removed": N,
            "results": [{"lead_id": ..., "original_phone": ..., "cleaned_phone": ..., "changes": ...}]
        }
    """
    results = []
    valid_count = 0
    fixed_count = 0
    invalid_count = 0

    for lead in leads:
        raw = (lead.get("phone") or "").strip()
        if not raw:
            results.append({
                "lead_id": str(lead.get("id", "")),
                "name": lead.get("name", ""),
                "original_phone": "",
                "cleaned_phone": "",
                "status": "no_phone",
                "changes": [],
            })
            continue

        cleaned = clean_phone(raw)
        changes = []

        if cleaned.get("splits"):
            changes.append(f"Split into {len(cleaned['splits'])} numbers")
            fixed_count += 1
        elif cleaned["cleaned"] and cleaned["cleaned"] != raw.strip():
            changes.append(f"Normalized format: {raw} -> {cleaned['cleaned']}")
            fixed_count += 1

        if cleaned["confidence"] == "invalid":
            invalid_count += 1
            changes.append(f"Invalid: {cleaned['reason']}")
        elif cleaned["confidence"] in ("high", "medium"):
            valid_count += 1
            if not changes:
                changes.append("Already valid")

        results.append({
            "lead_id": str(lead.get("id", "")),
            "name": lead.get("name", ""),
            "original_phone": raw,
            "cleaned_phone": cleaned["cleaned"] or "",
            "confidence": cleaned["confidence"],
            "reason": cleaned["reason"],
            "status": cleaned["confidence"],
            "changes": changes,
        })

    # Count duplicates among cleaned numbers
    seen_phones: dict[str, list[int]] = {}
    dup_lead_ids = set()
    for i, r in enumerate(results):
        if r["cleaned_phone"] and r["confidence"] != "invalid":
            if r["cleaned_phone"] in seen_phones:
                dup_lead_ids.add(r["lead_id"])
                dup_lead_ids.update(seen_phones[r["cleaned_phone"]])
            seen_phones.setdefault(r["cleaned_phone"], []).append(i)

    return {
        "total": len(leads),
        "valid": valid_count,
        "fixed": fixed_count,
        "invalid": invalid_count,
        "duplicates_removed": len(dup_lead_ids) if len(dup_lead_ids) > 1 else 0,
        "results": results,
    }
