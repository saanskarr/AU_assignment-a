import hashlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

IST = timezone(timedelta(hours=5, minutes=30))


def clean_header(header_text):
    if not header_text:
        return ""
    text = header_text.strip().lower()
    # Replace underscores and multiple spaces with a single space
    return re.sub(r'[_\s]+', ' ', text)


def hash_value(val):
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


def mask_phone(canonical_phone):
    # canonical_phone is +91XXXXXXXXXX
    return "+91******" + canonical_phone[-4:]


def parse_phone(raw_phone):
    if not raw_phone:
        return None
    
    digits = re.sub(r'\D', '', str(raw_phone))
    
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in ("6", "7", "8", "9"):
        return f"+91{digits}"
    
    return None


def parse_date(raw_date):
    if not raw_date:
        return None
    
    val = str(raw_date).strip()
    date_formats = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y"]
    
    for fmt in date_formats:
        try:
            dt = datetime.strptime(val, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    return None


def parse_decimal(raw_amount):
    if not raw_amount:
        return None
    
    val = str(raw_amount).strip()
    # Strip currency prefixes like ₹, Rs, Rs., INR
    val = re.sub(r'^(?:₹|rs\.?|inr)\s*', '', val, flags=re.IGNORECASE)
    val = val.replace(",", "").strip()
    
    if not val:
        return None

    try:
        dec = Decimal(val)
        if dec < 0:
            return None
        return str(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def parse_enum(raw_val, valid_options):
    if not raw_val:
        return None
    val = str(raw_val).strip().lower()
    allowed = [opt.lower() for opt in valid_options]
    if val in allowed:
        return val
    return None


def parse_event_timestamp(ts_str):
    if not ts_str or not isinstance(ts_str, str):
        return None
    
    ts_str = ts_str.strip()
    try:
        if ts_str.endswith("Z"):
            dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        elif "T" in ts_str:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
        else:
            # Naive timestamps are in IST per spec
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)

        ist_dt = dt.astimezone(IST)
        return ist_dt.isoformat(), ist_dt.strftime("%Y-%m-%d")
    except ValueError:
        return None