from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account
import re, datetime
from services.ai_requests import get_case_type
import yaml, json
from utils.logger import log


with open("./config.yaml", "r") as f:
    config = yaml.safe_load(f)

# Docs
SCOPES = ['https://www.googleapis.com/auth/documents.readonly']
SERVICE_ACCOUNT_FILE = "./data/service_account2.json"



SHEET_ID = config['google']['sheet_id']
PENDING_CASES_RANGE = config['google']['pending_cases_tab_range']

CASE_LOG_RANGE_CIVIL = config['google'].get('case_log_tab_range_for_civil')
CASE_LOG_RANGE_CRIMINAL = config['google'].get('case_log_tab_range_for_criminal')





# Data read range is the pending cases range from config (e.g. "Pending Cases!A2:F2")
# Build an open-ended data range from the configured pending_cases_tab_range
# If config gives 'Pending Cases!A2:F2', we want to read 'Pending Cases!A2:F' so we get all rows
_sheet, _range_part = PENDING_CASES_RANGE.split('!', 1)
_m_cols = re.match(r'([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)', _range_part)
if _m_cols:
    start_col, start_row, end_col, end_row = _m_cols.group(1), _m_cols.group(2), _m_cols.group(3), _m_cols.group(4)
    DATA_SHEET_RANGE = f"{_sheet}!{start_col}{start_row}:{end_col}"
else:
    # fallback: use the provided range as-is
    DATA_SHEET_RANGE = PENDING_CASES_RANGE

# Tab name (sheet title) extracted from the pending cases range
SHEET_NAME = _sheet
# Append range: use full A:F for appends
APPEND_RANGE = f"{SHEET_NAME}!A:F"

# Determine the starting data row by parsing the first row number in the provided range (e.g. A2 -> 2)
_m = re.search(r'!(?:.*?)(\d+)', DATA_SHEET_RANGE)
DATA_START_ROW = int(_m.group(1)) if _m else 2

SCOPES_SHEETS = ['https://www.googleapis.com/auth/spreadsheets']

# Last-available-case-number cell references (taken verbatim from config)
LAST_AVAILABLE_CASE_NUMBER_CRIMINAL = config['google'].get('last_criminalcase_number')
LAST_AVAILABLE_CASE_NUMBER_CIVIL = config['google'].get('lasts_civilcase_number')


def _normalize_range_ref(r: str) -> str | None:
    """Normalize a config-provided cell reference like 'Data:O3' to 'Data!O3'."""
    if not r:
        return None
    if ':' in r and '!' not in r:
        return r.replace(':', '!', 1)
    return r

def extract_google_docs_links(text):
    """
    Extracts Google Docs/Sheets/Slides/Form links from text, ending at the document ID.
    """
    # Matches URLs like https://docs.google.com/document/d/<ID> or similar
    pattern = r'https?://(?:docs|drive)\.google\.com/(?:document|spreadsheets|presentation|forms)/d/([a-zA-Z0-9-_]+)'
    matches = re.finditer(pattern, text)
    links = []
    for match in matches:
        doc_type = match.group(0).split('/')[3]  # e.g., 'document'
        doc_id = match.group(1)
        base_url = f"https://docs.google.com/{doc_type}/d/{doc_id}"
        links.append(base_url)
    return links





def get_gdoc_text(gdoc_link: str):
    """
    Extracts text content from a gdoc given its link.
    """
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)

        # Extract document ID from the link (case-insensitive)
        match = re.search(r"/document/d/([a-zA-Z0-9-_]+)", gdoc_link, re.IGNORECASE)
        if not match:
            return False, "Invalid Google Docs link format."
        document_id = match.group(1)

        service = build('docs', 'v1', credentials=creds)
        document = service.documents().get(documentId=document_id).execute()
        
        content = document.get('body', {}).get('content', [])
        
        text_content = []
        for element in content:
            if 'paragraph' in element:
                for paragraph_element in element.get('paragraph', {}).get('elements', []):
                    if 'textRun' in paragraph_element:
                        text_content.append(paragraph_element.get('textRun', {}).get('content', ''))
        
        return True, "".join(text_content)

    except HttpError as error:
        return False, f"An HTTP error occurred: {error}"
    except Exception as e:
        return False, f"An unexpected error occurred: {e}"












# Sheets configuration is defined above from config.yaml (SHEET_ID, SHEET_NAME, DATA_START_ROW, DATA_SHEET_RANGE)



def add_to_docket(case_info: dict) -> dict:
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        # Unpack fields with defaults
        judge = case_info.get("judge", "")
        case_status = case_info.get("case_status", "")
        case_name = case_info.get("case_name", "")
        case_number = case_info.get("case_number", "")
        filing_date = case_info.get("filing_date", "")
        filing_link = case_info.get("filing_link", "")
        spreadsheetId = case_info.get("spreadsheetId", "")
        append_range = case_info.get("range", "")

        if spreadsheetId:
            spreadsheetId = spreadsheetId
        else:
            spreadsheetId = SHEET_ID

        if append_range:
            append_range = append_range
        else:
            append_range = APPEND_RANGE

        hyperlink = f'=HYPERLINK("{filing_link}", "Link")' if filing_link else ""

        new_row = [
            judge,
            case_status,
            case_name,
            case_number,
            filing_date,
            hyperlink
        ]

        body = {"values": [new_row]}

        service.spreadsheets().values().append(
            spreadsheetId=spreadsheetId,
            range=append_range,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        return {"success": True, "message": f"Case '{case_name}' added to docket."}

    except Exception as e:
        return {"success": False, "message": f"Error adding to docket: {e}"}
    

    

def edit_docket(case_number: str, changes: dict) -> dict:
    """
    Edits an existing case entry in the docket by its case number.
    Works with columns A-F: Judge, Case Status, Case Name, Case Number, Filing Date, Filing Link
    """
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        # Column mapping
        column_indices = {
            "judge": 0,
            "case_status": 1,
            "case_name": 2,
            "case_number": 3,
            "filing_date": 4,
            "filing_link": 5,
        }

        # Read the configured data range (skips header rows)
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=DATA_SHEET_RANGE
        ).execute()

        values = result.get('values', [])
        if not values:
            return {"success": False, "message": "No data found in the sheet."}

        row_index_to_update = -1
        for offset, row in enumerate(values):
            sheet_row = DATA_START_ROW + offset
            # Skip rows that don't have a case number column
            if len(row) <= column_indices["case_number"]:
                continue
            if (row[column_indices["case_number"]] or "").strip().lower() == case_number.strip().lower():
                row_index_to_update = sheet_row
                break

        if row_index_to_update == -1:
            return {"success": False, "message": f"Case with number '{case_number}' not found."}

        row_to_edit_range = f"{SHEET_NAME}!A{row_index_to_update}:F{row_index_to_update}"
        existing_row_result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=row_to_edit_range
        ).execute()

        existing_row_values = existing_row_result.get('values', [])
        if not existing_row_values:
            return {"success": False, "message": f"Could not retrieve data for row {row_index_to_update}."}

        updated_row_values = existing_row_values[0][:]

        # Update fields
        for key, new_value in {k.lower(): v for k, v in changes.items()}.items():
            col_index = column_indices.get(key)
            if col_index is not None:
                if key == "filing_link" and new_value:
                    updated_row_values[col_index] = f'=HYPERLINK("{new_value}", "Link")'
                else:
                    updated_row_values[col_index] = new_value

        body = {"values": [updated_row_values]}
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=row_to_edit_range,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        return {"success": True, "message": f"Case '{case_number}' successfully updated."}

    except Exception as e:
        return {"success": False, "message": f"Error updating docket: {e}"}





def get_available_case_number(case_type: str) -> str:
    """
    Returns the next available case number string for the given type ("criminal" or "civil").
    Retrieves data from the google sheet to find the last available case number which is in the "Data" sheet.
    The last available case number for criminal cases is in O3 and for civil cases is in O4.
    The function accesses the google sheet and gets that number and returns it with its case type prefix, e.g., "Crim 40" or "Civ 80".
    """

    # Map input to correct prefix
    case_type_map = {
        "criminal": "Crim",
        "crim": "Crim",
        "civil": "Civ",
        "civ": "Civ"
    }

    # Get the last available case number from the google sheet
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        if case_type.lower() in ["criminal", "crim"]:
            cell_ref = _normalize_range_ref(LAST_AVAILABLE_CASE_NUMBER_CRIMINAL) or 'Data!O3'
            last_available_case_number = service.spreadsheets().values().get(
                spreadsheetId=SHEET_ID,
                range=cell_ref
            ).execute().get('values', [])[0][0]
            prefix = case_type_map["criminal"]
        elif case_type.lower() in ["civil", "civ"]:
            cell_ref = _normalize_range_ref(LAST_AVAILABLE_CASE_NUMBER_CIVIL) or 'Data!O4'
            last_available_case_number = service.spreadsheets().values().get(
                spreadsheetId=SHEET_ID,
                range=cell_ref
            ).execute().get('values', [])[0][0]
            prefix = case_type_map["civil"]
        else:
            raise ValueError("Invalid case type. Must be 'criminal' or 'civil'.")

    except Exception as e:
        raise Exception(f"Error getting available case number: {e}")

    # Extract numeric part and return with prefix
    m = re.search(r'(\d+)', str(last_available_case_number))
    number = m.group(1) if m else str(last_available_case_number)
    return f"{prefix} {number}"



# ---
def increment_available_case_number(case_type: str) -> bool:
    """
    Read the last available case number for the given case_type, increment the numeric part,
    and write the new value back to the appropriate cell in the "Data" sheet.

    Accepts case_type like "criminal", "crim", "civil", "civ" (case-insensitive).
    Returns True on success, raises Exception on failure.
    """
    # local mapping and target cell selection
    case_type_key = (case_type or "").strip().lower()
    prefix_map = {"criminal": "Crim", "crim": "Crim", "civil": "Civ", "civ": "Civ"}
    # Normalize config-provided cell refs for writing back the incremented numbers
    cell_map = {
        "criminal": _normalize_range_ref(LAST_AVAILABLE_CASE_NUMBER_CRIMINAL) or 'Data!O3',
        "crim": _normalize_range_ref(LAST_AVAILABLE_CASE_NUMBER_CRIMINAL) or 'Data!O3',
        "civil": _normalize_range_ref(LAST_AVAILABLE_CASE_NUMBER_CIVIL) or 'Data!O4',
        "civ": _normalize_range_ref(LAST_AVAILABLE_CASE_NUMBER_CIVIL) or 'Data!O4',
    }

    if case_type_key not in prefix_map:
        raise ValueError("Invalid case type. Must be 'criminal'/'crim' or 'civil'/'civ'.")

    try:
        # get current value (uses existing helper)
        last_available_case_number = get_available_case_number(case_type_key)
    except Exception as e:
        raise Exception(f"Error reading current available case number: {e}")

    orig = str(last_available_case_number).strip()

    # Parse numeric suffix robustly; prefix is optional (handles "Crim193", "Crim 193", "193", "Crim-193")
    m = re.search(r'^(?:([A-Za-z]+)\s*[-]?\s*)?0*([0-9]+)\s*$', orig)
    if not m:
        raise ValueError(f"Could not parse numeric suffix from '{last_available_case_number}'")

    prefix_in_cell = m.group(1)  # may be None if only digits present
    try:
        current_num = int(m.group(2))
    except Exception as e:
        raise ValueError(f"Invalid numeric part in '{last_available_case_number}': {e}")

    new_num = current_num + 1

    # Decide whether to include a space between prefix and number.
    # Preserve a space if the original had letters + space before digits; if original was digits-only, use space.
    has_letters = bool(re.search(r'[A-Za-z]', orig))
    had_space_between = bool(re.search(r'[A-Za-z]+\s+[0-9]+$', orig))
    if not has_letters:
        use_space = True
    else:
        use_space = had_space_between

    standard_prefix = prefix_map[case_type_key]

    if use_space:
        incremented_case_number_str = f"{new_num:03d}"
    else:
        incremented_case_number_str = f"{new_num:03d}"

    # Write the incremented case number back to the sheet
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        target_range = cell_map[case_type_key]

        body = {"values": [[incremented_case_number_str]]}
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=target_range,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        return True

    except Exception as e:
        raise Exception(f"Error incrementing available case number: {e}")




def get_gdoccase_info(link: str) -> dict:
    """
    Extracts case information from a Google Doc link.
    Returns a dictionary with success status, case_name, case_number, case_type, and errors.
    """
    # Check testing mode first - if enabled, return mock data without API calls
    # Load config safely and fall back to module-level `config` if available.
    try:
        with open("./config.yaml", "r") as f:
            _cfg = yaml.safe_load(f) or {}
    except Exception:
        _cfg = {}

    # prefer local file config, but fall back to top-level `config` if present
    merged_cfg = {}
    if isinstance(_cfg, dict):
        merged_cfg.update(_cfg)
    if 'config' in globals() and isinstance(config, dict):
        # overlay any missing values from module-level config
        for k, v in config.items():
            if k not in merged_cfg:
                merged_cfg[k] = v

    testing_result = False
    try:
        testing_result = bool(merged_cfg.get("AI", {}).get("testing_result", False))
    except Exception:
        testing_result = False

    if testing_result:
        return {
            "success": True,
            "case_name": "SD v. Ed",
            "case_number": "Crim 193",
            "case_type": "Criminal",
            "errors": []
        }
    
    # Only make API calls if not in testing mode
    errors = []

    success, gdoc_text = get_gdoc_text(link)
    if not success:
        errors.append(gdoc_text)
        return {
            "success": False,
            "case_name": None,
            "case_number": None,
            "case_type": None,
            "errors": errors
        }

    case_type_result = get_case_type(gdoc_text[:600])  # limit to first 600 characters
    if not case_type_result.get("success"):
        errors.append(f"AI Error: {case_type_result.get('error', 'Unknown error')}")
        case_type = "Unknown"
        case_name = "Unknown"
    else:
        case_type = case_type_result.get("case_type", "Unknown")
        case_name = case_type_result.get("case_name", "Unknown")

    case_number = get_available_case_number(case_type)



    return {
        "success": len(errors) == 0,
        "case_name": case_name,
        "case_number": case_number,
        "case_type": case_type,
        "errors": errors
    }



def get_case_info_from_number(case_number: str) -> dict:
    """
    Look up a case by case_number in the docket sheet and return a structured dict.

    Returns:
      {
        "success": bool,
        "message": str,            # only present on failure or extra info
        "row_number": int,         # 1-based sheet row (if found)
        "case_name": str|None,
        "case_status": str|None,
        "filing_date": str|None,
        "link": str|None
      }
    Assumptions: sheet columns match add_to_docket order:
      [judge, case_status, case_name, case_number, filing_date, filing_link, ...]
    """
    def _extract_url(cell: str) -> str | None:
        if not cell:
            return None
        # Strip leading/trailing whitespace and formulas
        cell = str(cell).strip()
        if not cell:
            return None
        # handle =HYPERLINK("url","Label") or =HYPERLINK('url','Label')
        # Try double quotes first
        m = re.search(r'HYPERLINK\s*\(\s*"([^"]+)"', cell, re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            if url.startswith('http://') or url.startswith('https://'):
                return url
        # Try single quotes
        m = re.search(r"HYPERLINK\s*\(\s*'([^']+)'", cell, re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            if url.startswith('http://') or url.startswith('https://'):
                return url
        # Find a plain URL if present
        m = re.search(r'(https?://[^\s"\']+)', cell)
        if m:
            url = m.group(1).rstrip('"\')')
            if url.startswith('http://') or url.startswith('https://'):
                return url
        return None

    def _normalize(s: str) -> str:
        if s is None:
            return ""
        # Replace NBSP and other common invisible chars, collapse whitespace, lower-case
        s = s.replace('\u00A0', ' ').replace('\u200b', '').strip()
        s = re.sub(r'\s+', ' ', s)

        return s.lower()
    

    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build('sheets', 'v4', credentials=creds)

        # Read with valueRenderOption="FORMULA" to get formulas, then extract URLs from HYPERLINK
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=DATA_SHEET_RANGE,
            valueRenderOption="FORMULA"
        ).execute()

        rows = result.get('values', [])
        if not rows:
            return {"success": False, "message": "Sheet is empty or no values returned."}

        target = _normalize(case_number)
        # columns per add_to_docket: 0=judge,1=case_status,2=case_name,3=case_number,4=filing_date,5=link
        case_number_col = 3

        # iterate rows and compute the actual sheet row number using DATA_START_ROW
        for offset, row in enumerate(rows):
            sheet_row = DATA_START_ROW + offset
            # protect against short rows
            if len(row) <= case_number_col:
                continue

            cell_raw = row[case_number_col] or ""
            visible_text = cell_raw
            m_label = re.search(r'HYPERLINK\(\s*"[^"]+"\s*,\s*"([^"]+)"', cell_raw, re.IGNORECASE)
            if m_label:
                visible_text = m_label.group(1)
            normalized_cell = _normalize(visible_text)

            if normalized_cell == target:
                case_name = row[2] if len(row) > 2 else None
                case_status = row[1] if len(row) > 1 else None
                filing_date = row[4] if len(row) > 4 else None
                link_cell = row[5] if len(row) > 5 else None
                # Extract URL from HYPERLINK formula; if no formula, try to extract plain URL
                link = _extract_url(link_cell) if link_cell else None
                judge = row[0] if len(row) > 0 else "NA"

                return {
                    "success": True,
                    "row_number": sheet_row,
                    "case_name": case_name,
                    "case_status": case_status,
                    "filing_date": filing_date,
                    "link": link,
                    "judge": judge
                }

        return {"success": False, "message": f"Case number '{case_number}' not found."}

    except Exception as e:
        return {"success": False, "message": f"Error reading sheet: {e}"}





def get_all_cases() -> dict:
    """
    Retrieves all cases from the docket sheet.
    Returns a dictionary with success status, list of cases, and error message if any.
    Each case is represented as a dictionary with keys:
      judge, case_status, case_name, case_number, filing_date, filing_link
    """
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        # Read with valueRenderOption="FORMULA" to get formulas for HYPERLINK extraction
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=DATA_SHEET_RANGE,
            valueRenderOption="FORMULA"
        ).execute()

        values = result.get('values', [])
        if not values:
            return {"success": True, "cases": [], "message": "No data found in the sheet."}

        cases = []
        for offset, row in enumerate(values):
            # compute actual sheet row number
            sheet_row = DATA_START_ROW + offset
            case = {
                "judge": row[0] if len(row) > 0 else "",
                "case_status": row[1] if len(row) > 1 else "",
                "case_name": row[2] if len(row) > 2 else "",
                "case_number": row[3] if len(row) > 3 else "",
                "filing_date": row[4] if len(row) > 4 else "",
                "filing_link": None,
                "row_number": sheet_row
            }
            if len(row) > 5:
                link_cell = row[5]
                # Extract URL from HYPERLINK formula
                m = re.search(r'HYPERLINK\(\s*"([^\"]+)"', link_cell, re.IGNORECASE)
                if m:
                    case["filing_link"] = m.group(1)
                else:
                    # Try plain URL if present
                    m_plain = re.search(r'(https?://\S+)', link_cell)
                    if m_plain:
                        case["filing_link"] = m_plain.group(1).rstrip('\")')
            cases.append(case)

        return {"success": True, "cases": cases}

    except Exception as e:
        return {"success": False, "cases": [], "message": f"Error retrieving cases: {e}"}
    




def delete_case_row(case_name: str, case_number: str) -> dict:
    """
    Deletes a case from the docket completely by removing the entire row from the sheet.
    Takes in the case name and case number, finds the matching row, and deletes it.
    """
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        # Column mapping
        column_indices = {
            "judge": 0,
            "case_status": 1,
            "case_name": 2,
            "case_number": 3,
            "filing_date": 4,
            "filing_link": 5,
        }

        # Read data from configured data range (skips headers)
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=DATA_SHEET_RANGE
        ).execute()

        values = result.get("values", [])
        if not values:
            return {"success": False, "message": "No data found in the sheet."}

        row_index_to_delete = -1
        # find matching row and compute 0-based sheet index for deletion
        for offset, row in enumerate(values):
            sheet_row = DATA_START_ROW + offset
            if len(row) <= column_indices["case_number"]:
                continue
            if (
                (row[column_indices["case_name"]] or "").strip().lower() == case_name.strip().lower()
                and (row[column_indices["case_number"]] or "").strip().lower() == case_number.strip().lower()
            ):
                # batchUpdate expects 0-based row index
                row_index_to_delete = sheet_row - 1
                break

        if row_index_to_delete == -1:
            return {
                "success": False,
                "message": f"Case with name '{case_name}' and number '{case_number}' not found."
            }


        # Get sheetId for "Pending Cases"
        sheet_metadata = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
        sheets = sheet_metadata.get("sheets", [])
        pending_cases_id = None
        for s in sheets:
            if s["properties"]["title"] == SHEET_NAME:
                pending_cases_id = s["properties"]["sheetId"]
                break

        if pending_cases_id is None:
            return {"success": False, "message": f"Sheet '{SHEET_NAME}' not found."}

        # Delete the entire row
        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": pending_cases_id,
                                "dimension": "ROWS",
                                "startIndex": row_index_to_delete,
                                "endIndex": row_index_to_delete + 1,
                            }
                        }
                    }
                ]
            },
        ).execute()

        return {"success": True, "message": f"Deleted case with name '{case_name}' and number '{case_number}'."}

    except Exception as e:
        return {"success": False, "message": f"Error deleting case: {e}"}






def finish_case(case_info: dict) -> dict:
    """
    Function will take i a case name, case number, case ending reason, ending reason link.

    The ending reasons can be one from:
        1. Verdict
        2. Plea Deal
        3. Dismissal
        4. Mistrial
        5. Dropped
        6. Other
    
    The function will check if its a civil case or criminal case based on the case number, so if its "Civ 4", its civil, if its "Crim 88", its criminal.

    If its criminal it will put it in the range found in, "CASE_LOG_RANGE_CRIMINAL"

    The data starts directly at that point, it will go the the end of where the data is and put the new finished case there.

    The function will get the case info: Case name, case number, filling link, filling date.

    it will then put it in the empty data range as so: Case name, case number, filing date, filing link, Verdict date (todays date in MM/DD/YY), case ending type with the type of ending and hyperlinked within it the ending link.

    it will return dict like otehrs to show status of operation
    """

    try:
        # Required input
        case_number = (case_info.get("case_number") or "").strip()
        if not case_number:
            return {"success": False, "message": "Missing case_number in case_info."}

        ending_type = case_info.get("ending_type") or case_info.get("case_ending_type") or "Other"
        ending_link = case_info.get("ending_link") or case_info.get("ending_url") or ""

        # Lookup authoritative case info
        lookup = get_case_info_from_number(case_number)
        if lookup.get("success"):
            case_name = lookup.get("case_name") or case_info.get("case_name") or ""
            filing_date = lookup.get("filing_date") or case_info.get("filing_date") or ""
            filing_link = lookup.get("link") or case_info.get("filing_link") or ""
        else:
            case_name = case_info.get("case_name") or ""
            filing_date = case_info.get("filing_date") or ""
            filing_link = case_info.get("filing_link") or ""

        # Debug: log what we found
        print(f"[finish_case DEBUG] case_number={case_number}, filing_link='{filing_link}', lookup_success={lookup.get('success')}")

        # Determine whether criminal or civil from case_number
        # Match "Crim" or "CRIMINAL" (case-insensitive), but not "Civ"
        is_criminal = bool(re.search(r"\bcrim(inal)?\b", case_number, re.IGNORECASE))

        # Choose the proper case-log range from config
        target_range_config = CASE_LOG_RANGE_CRIMINAL if is_criminal else CASE_LOG_RANGE_CIVIL
        if not target_range_config:
            return {"success": False, "message": "Case log range for the case type is not configured in config.yaml."}

        # Normalize to an appendable range: 'Sheet!A:F'
        try:
            sheet_name = target_range_config.split('!', 1)[0]
        except Exception:
            return {"success": False, "message": "Malformed case log range in config.yaml."}

        log_append_range = f"{sheet_name}!A:F"

        # Verdict date in MM/DD/YY
        verdict_date = datetime.datetime.utcnow().strftime("%m/%d/%y")

        # Ending cell: hyperlink if link provided
        if ending_link:
            ending_cell = f'=HYPERLINK("{ending_link}", "{ending_type}")'
        else:
            ending_cell = ending_type

        # Prepare filing cell as HYPERLINK formula if URL present, else empty
        if filing_link:
            filing_cell = f'=HYPERLINK("{filing_link}", "Link")'
        else:
            filing_cell = ""

        print(f"[finish_case DEBUG] filing_cell before write='{filing_cell}'")

        new_row = [
            case_name,
            case_number,
            filing_date,
            filing_cell,
            verdict_date,
            ending_cell,
        ]

        # Append to the case log
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        # Parse the target range to get the starting column and sheet name
        # e.g., "Case Log!J5:O5" -> sheet="Case Log", start_col="J", start_row=5
        try:
            sheet_name, range_part = target_range_config.split('!', 1)
            m = re.match(r'([A-Za-z]+)(\d+)', range_part)
            if not m:
                return {"success": False, "message": "Invalid case log range format."}
            start_col = m.group(1)
            start_row = int(m.group(2))
        except Exception as e:
            return {"success": False, "message": f"Failed to parse case log range: {e}"}

        # Read the sheet to find the next empty row in the target column range
        try:
            # Read from start_row downward to find the first empty row in the start column
            read_range = f"{sheet_name}!{start_col}{start_row}:{start_col}"
            result = service.spreadsheets().values().get(
                spreadsheetId=SHEET_ID,
                range=read_range
            ).execute()
            values = result.get('values', [])
            
            # Find the first empty row
            next_row = start_row + len(values)
        except Exception as e:
            # If read fails, default to start_row
            return {"success": False, "message": f"Failed to find first column in case log tab: {e}"}

        write_range = f"{sheet_name}!{start_col}{next_row}"
        body = {"values": [new_row]}
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=write_range,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        # if success, delete the message from pending cases with delete case
        try:
            delete_case_row(case_name, case_number)
        except Exception as e:
            return {"success": False, "message": f"Failed to delete case from Pending Cases: {e}"}

        return {"success": True, "message": "Case finished and appended to case log.", "appended_row": new_row}

    except Exception as e:
        return {"success": False, "message": f"Error finishing case: {e}"}


def get_judges(refresh: bool = True) -> dict:
    """
    function to pull all judges from the docket sheet.
    judge data is in the "Data" tab. 
    Judges names START in A3 and go all the way down.
    Judge status is in B3 and goes all the way down, they are either "Valid" or "Not"
    Case taking availability start in C3 and goes all the way down, they are either "Active" or "Unavailable"

    Their discord ID is in K3 and goes all the way down.

    the function will return a dict with all the judges and their info.
    it will only include judges which their judge status is "Valid"
    
    """
    try:

        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range="Data!A3:K"
        ).execute()
        values = result.get("values", [])
        if not values:
            return {"success": True, "judges": [], "message": "No judge data found in the sheet."}
        



        judges = []
        for row in values:
            judge_name = row[0] if len(row) > 0 else ""
            judge_status = row[1] if len(row) > 1 else ""
            case_availability = row[2] if len(row) > 2 else ""
            discord_id = row[10] if len(row) > 10 else ""

            if judge_status.strip().lower() == "valid":
                judges.append({
                    "judge_name": judge_name,
                    "judge_status": judge_status,
                    "case_availability": case_availability,
                    "discord_id": discord_id
                })

        if not refresh:
            pass

        if refresh:
            with open("./data/judge_data.json", "w") as f:
                f.truncate(0)
                json.dump(judges, f, indent=4)

            # also update judge list in config.yaml in ../config.yaml
            # the judges list are in "judges_ids" under nothing. it looks like "judges_ids : []"
            with open("./config.yaml", "r") as f:
                config = yaml.safe_load(f)

            config["judges_ids"] = [j["discord_id"] for j in judges if j["discord_id"]]

            with open("./config.yaml", "w") as f:
                yaml.dump(config, f)


        return {"success": True, "judges": judges}

    except Exception as e:
        return {"success": False, "judges": [], "message": f"Error retrieving judges: {e}"}
    



def toggle_judge_activity_status(judge, activity_status) -> dict:
    """
    Will take in judge name with "Active" or "Unavailable" and update the judge status in the google sheet.
    """

    # get_judges()

    try:
        # check if actvity status passed is valid

        if activity_status.strip().lower() not in ["active", "unavailable"]:
            return {"success": False, "message": "Invalid activity status. Must be 'Active' or 'Unavailable'."}
        
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
        )
        service = build("sheets", "v4", credentials=creds)

        # Read data from configured data range (skips headers)
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range="Data!A3:C"
        ).execute()

        values = result.get("values", [])
        if not values:
            return {"success": False, "message": "No data found in the sheet."}

        row_index_to_update = -1
        # find matching row and compute 0-based sheet index for update
        for offset, row in enumerate(values):
            sheet_row = 3 + offset  # since we started at A3
            if len(row) <= 0:
                continue
            if (row[0] or "").strip().lower() == judge.strip().lower():
                row_index_to_update = sheet_row
                break

        if row_index_to_update == -1:
            return {
                "success": False,
                "message": f"Judge with name '{judge}' not found."
            }

        row_to_edit_range = f"Data!C{row_index_to_update}"
        body = {"values": [[activity_status]]}
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=row_to_edit_range,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        return {"success": True, "message": f"Updated judge '{judge}' activity status to '{activity_status}'."}

    except Exception as e:
        return {"success": False, "message": f"Error updating judge activity status: {e}"}
