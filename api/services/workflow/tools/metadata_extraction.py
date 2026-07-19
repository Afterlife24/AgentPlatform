"""Metadata extraction utilities for knowledge base documents.

Extracts structured, filterable metadata from document chunks during ingestion.
Supports structured JSON inputs (auto-extract fields) and unstructured text
(regex-based heuristic extraction).
"""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger


# Common patterns for extracting numeric capacity/weight values
_CAPACITY_PATTERNS = [
    # "75 ton", "700 t", "500 tons"
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:ton(?:s|ne)?|t)\b", re.IGNORECASE),
    # "75,000 kg"
    re.compile(r"([\d,]+)\s*kg\b", re.IGNORECASE),
]

# Pattern to identify manufacturer from model name prefix
_KNOWN_MANUFACTURERS = [
    "Liebherr",
    "Tadano",
    "Terex",
    "Grove",
    "Manitowoc",
    "Kobelco",
    "Link-Belt",
    "Sany",
    "XCMG",
    "Zoomlion",
    "Demag",
    "Kato",
    "Hitachi",
    "Caterpillar",
    "Volvo",
    "JCB",
    "Doosan",
    "Hyundai",
]


def extract_metadata_from_structured_json(
    item: Dict[str, Any],
    category_heading: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract filterable metadata from a structured equipment JSON item.

    This handles the Johnson Arabia-style JSON where each model is already
    a structured object with specs as key-value pairs.

    Args:
        item: A model dict from the structured JSON (has "model" and "specs" keys)
        category_heading: The parent category heading (e.g., "ROUGH TERRAIN CRANES")

    Returns:
        Dictionary of extracted metadata suitable for chunk_metadata storage.
    """
    metadata: Dict[str, Any] = {}

    # Extract model name
    model_name = item.get("model", "")
    if model_name:
        metadata["model"] = model_name

    # Extract manufacturer from model name
    manufacturer = _extract_manufacturer_from_model(model_name)
    if manufacturer:
        metadata["manufacturer"] = manufacturer

    # Extract category from heading
    if category_heading:
        metadata["parent_category"] = _normalize_category(category_heading)
        metadata["equipment_type"] = _extract_equipment_type(category_heading)

    # Extract specs into metadata
    specs = item.get("specs", [])
    for spec in specs:
        key = spec.get("key", "").strip()
        value = spec.get("value", "").strip()

        if not key or not value:
            continue

        # Extract rated capacity as a numeric field
        if key.lower() in ("rated capacity", "rated_capacity"):
            capacity = _parse_capacity(value)
            if capacity is not None:
                metadata["rated_capacity_ton"] = capacity
            metadata["rated_capacity_raw"] = value

        # Extract max speed
        elif key.lower() in ("max speed", "travel speed"):
            speed = _parse_speed(value)
            if speed is not None:
                metadata["max_speed_kph"] = speed

        # Extract engine info
        elif key.lower() in ("engine", "carrier engine", "engine tier iii", "engine tier iv"):
            metadata["engine"] = value[:200]  # Truncate for index efficiency

        # Extract boom length
        elif key.lower() == "boom":
            boom_length = _parse_boom_max_length(value)
            if boom_length is not None:
                metadata["max_boom_length_m"] = boom_length

        # Extract axles/drive info
        elif key.lower() in ("axles / drive", "drive", "carrier"):
            if "4x4" in value.lower() or "4×4" in value:
                metadata["drive_config"] = "4x4"
            elif "6x6" in value.lower() or "6×6" in value:
                metadata["drive_config"] = "6x6"
            elif "8x6" in value.lower() or "8×6" in value:
                metadata["drive_config"] = "8x6"
            elif "8x8" in value.lower() or "8×8" in value:
                metadata["drive_config"] = "8x8"

    return metadata


def extract_metadata_from_text(
    text: str,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract metadata from unstructured chunk text using heuristic patterns.

    This is the fallback for documents that aren't structured JSON.
    It uses regex patterns to identify key metadata fields.

    Args:
        text: The chunk text to extract metadata from.
        filename: Optional source filename for additional context.

    Returns:
        Dictionary of extracted metadata (may be empty if nothing found).
    """
    metadata: Dict[str, Any] = {}

    if not text:
        return metadata

    # Try to identify manufacturer
    for mfr in _KNOWN_MANUFACTURERS:
        if mfr.lower() in text.lower():
            metadata["manufacturer"] = mfr
            break

    # Try to extract capacity
    for pattern in _CAPACITY_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                val = match.group(1).replace(",", "")
                capacity = float(val)
                # Convert kg to tons if value seems like kg
                if capacity > 2000:
                    capacity = capacity / 1000
                metadata["rated_capacity_ton"] = capacity
            except (ValueError, IndexError):
                pass
            break

    # Try to identify equipment category from text
    category_keywords = {
        "rough terrain": "Rough Terrain",
        "all terrain": "All Terrain",
        "crawler crane": "Crawler Crane",
        "tower crane": "Tower Crane",
        "mobile crane": "Mobile Crane",
        "truck crane": "Truck Crane",
    }
    text_lower = text.lower()
    for keyword, category in category_keywords.items():
        if keyword in text_lower:
            metadata["equipment_type"] = category
            break

    return metadata


def _extract_manufacturer_from_model(model_name: str) -> Optional[str]:
    """Extract manufacturer name from model string.

    Examples:
        "Grove RT875E" → "Grove"
        "Liebherr LTM 1030-2.1" → "Liebherr"
        "Tadano GR-300EX" → "Tadano"
        "Terex TRT-35" → "Terex"
    """
    if not model_name:
        return None

    for mfr in _KNOWN_MANUFACTURERS:
        if model_name.lower().startswith(mfr.lower()):
            return mfr

    # Fallback: first word might be manufacturer
    parts = model_name.split()
    if parts:
        candidate = parts[0]
        # Only return if it looks like a proper name (capitalized, not a number)
        if candidate[0].isupper() and not candidate[0].isdigit():
            return candidate

    return None


def _normalize_category(heading: str) -> str:
    """Normalize category heading to a cleaner form.

    "CATEGORY1: ROUGH TERRAIN (4x4) HYDRAULIC MOBILE CRANES"
    → "Rough Terrain Hydraulic Mobile Cranes"
    """
    # Remove "CATEGORY N:" prefix
    cleaned = re.sub(r"^CATEGORY\s*\d+\s*:\s*", "",
                     heading, flags=re.IGNORECASE)
    # Remove parenthetical notes like (4x4)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    # Title case and strip extra spaces
    cleaned = " ".join(cleaned.split()).strip().title()
    return cleaned


def _extract_equipment_type(heading: str) -> str:
    """Extract simplified equipment type from category heading.

    "CATEGORY1: ROUGH TERRAIN (4x4) HYDRAULIC MOBILE CRANES" → "Rough Terrain"
    "CATEGORY 2: ALL TERRAIN HYDRAULIC MOBILE CRANES" → "All Terrain"
    """
    heading_lower = heading.lower()
    if "rough terrain" in heading_lower:
        return "Rough Terrain"
    elif "all terrain" in heading_lower:
        return "All Terrain"
    elif "crawler" in heading_lower:
        return "Crawler"
    elif "tower" in heading_lower:
        return "Tower"
    elif "truck" in heading_lower:
        return "Truck Mounted"
    elif "lattice" in heading_lower:
        return "Lattice Boom"
    else:
        # Fallback: clean up and return
        cleaned = re.sub(r"^CATEGORY\s*\d+\s*:\s*", "",
                         heading, flags=re.IGNORECASE)
        return " ".join(cleaned.split()[:3]).strip().title()


def _parse_capacity(value: str) -> Optional[float]:
    """Parse rated capacity string to numeric tons.

    Examples:
        "75 ton (68 mt)" → 75.0
        "~55 t class" → 55.0
        "30 Ton" → 30.0
        "700 t" → 700.0
        "55,000 kg at 3.0 m" → 55.0
        "160 t class" → 160.0
        "44 t at 2.5 m radius" → 44.0
    """
    if not value:
        return None

    # Try "X ton" or "X t" patterns
    match = re.search(
        r"~?(\d+(?:,\d+)?(?:\.\d+)?)\s*(?:ton(?:s|ne)?|t|USt)\b", value, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass

    # Try "X,XXX kg" pattern (convert to tons)
    match = re.search(r"([\d,]+)\s*kg\b", value, re.IGNORECASE)
    if match:
        try:
            kg = float(match.group(1).replace(",", ""))
            return round(kg / 1000, 1)
        except ValueError:
            pass

    return None


def _parse_speed(value: str) -> Optional[float]:
    """Parse max speed string to numeric km/h.

    Examples:
        "35 kph (22 mph)" → 35.0
        "50 km/h" → 50.0
        "27 kph" → 27.0
    """
    if not value:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kph|km/?h)\b",
                      value, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def _parse_boom_max_length(value: str) -> Optional[float]:
    """Parse boom spec to extract max length in meters.

    Examples:
        "4-section; Length 12.6 m – 39.0 m" → 39.0
        "Length 9.2 m – 30.0 m" → 30.0
        "11.0 m – 43.0 m" → 43.0
    """
    if not value:
        return None

    # Look for "X m – Y m" or "X m - Y m" pattern, take the larger
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*m\b", value)
    if matches:
        try:
            lengths = [float(m) for m in matches]
            # Filter out very small values (likely not boom lengths)
            boom_lengths = [l for l in lengths if l > 5.0]
            if boom_lengths:
                return max(boom_lengths)
        except ValueError:
            pass

    return None
