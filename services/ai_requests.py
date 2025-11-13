import json
import os
from pathlib import Path
import os
import json
import dotenv
import yaml
import google.generativeai as genai

dotenv.load_dotenv()

# Config & keys
google_api_key = os.getenv("GOOGLE_API_KEY")
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = os.getenv("CONFIG_PATH", str(BASE_DIR.parent / "config.yaml"))

# Load config
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f) or {}

model_name = config.get("AI", {}).get("google_model", "")
testing_mode = config.get("AI", {}).get("testing_mode", False)

# Prompt file: prefer PROMPT_PATH env, otherwise use ../data/prompt.txt (module-relative)
prompt_candidates = [
    str(BASE_DIR.parent / "data" / "prompt.txt"),
    str(BASE_DIR / "prompt.txt"),
]

promptbase = ""
for p in prompt_candidates:
    if not p:
        continue
    try:
        with open(p, "r", encoding="utf-8") as f:
            promptbase = f.read()
            break
    except FileNotFoundError:
        continue

# Configure API client once (no exception propagation here)
if google_api_key:
    try:
        genai.configure(api_key=google_api_key)
    except Exception:
        pass


def ai_function(dict) -> dict:
    """"
    Input dict will include prompt, model, K-Tempreture, etc etc
    output dict with success status, and prompt output
    """

    

    try:



        AIoutput = ""
        return {"sucess": True, "AIoutput": AIoutput}

    except Exception as e:
        return {"success": False, "message": f"Error getting AI response: {e}"}

    model = [] or model_name
    prompt = []
    k_temp = [] or 0


    pass


def clean_triple_backticks(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        parts = s.split("\n", 1)
        s = parts[1] if len(parts) == 2 else s.lstrip("`")
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    return s.strip()


def get_case_type(casetext: str) -> dict:
    """Call the AI once and return dict: success, case_type, case_name, error."""
    if not isinstance(casetext, str) or not casetext.strip():
        return {"success": False, "case_type": "Unknown", "case_name": "Unknown", "error": "Empty case text provided"}

    # Short-circuit deterministic testing mode
    if testing_mode:
        return {"success": True, "case_type": "Criminal", "case_name": "SD v. Ed", "error": None}

    # Basic checks
    if not google_api_key:
        return {"success": False, "case_type": "Unknown", "case_name": "Unknown", "error": "Missing GOOGLE_API_KEY environment variable"}
    if not model_name:
        return {"success": False, "case_type": "Unknown", "case_name": "Unknown", "error": "Model name not set in config.yaml (AI.google_model)"}

    # Prepare prompt (limit input length)
    casetext = casetext[:600]
    prompt = (promptbase) + "\n" + casetext + "\n" + "[DOCUMENT TEXT END]"
    prompt = prompt.replace("`", "")

    # Call the generative model once
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
    except Exception as e:
        return {"success": False, "case_type": "Unknown", "case_name": "Unknown", "error": str(e)}

    # Extract text robustly from possible response shapes
    text = ""
    try:
        if hasattr(response, "text") and response.text:
            text = response.text
        elif hasattr(response, "candidates") and response.candidates:
            cand = response.candidates[0]
            if hasattr(cand, "content"):
                text = cand.content
            elif isinstance(cand, dict) and cand.get("content"):
                text = cand.get("content")
        elif isinstance(response, dict):
            # common keys to try
            for key in ("content", "output", "data", "result"):
                val = response.get(key)
                if isinstance(val, str) and val:
                    text = val
                    break
                if isinstance(val, list) and val:
                    # flatten if list of dicts with "content"
                    parts = []
                    for item in val:
                        if isinstance(item, dict) and item.get("content"):
                            parts.append(item.get("content"))
                    if parts:
                        text = "\n".join(parts)
                        break
        else:
            text = str(response)
    except Exception:
        text = str(response)

    text = clean_triple_backticks(text)

    # Try JSON parse then simple key:value parse
    try:
        response_json = json.loads(text)
        return {
            "success": True,
            "case_type": response_json.get("case_type", "Unknown"),
            "case_name": response_json.get("case_name", "Unknown"),
            "error": None,
        }
    except Exception:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        parsed = {}
        for ln in lines:
            if ":" in ln:
                k, v = ln.split(":", 1)
                parsed[k.strip().lower()] = v.strip()
        case_type = parsed.get("case_type", parsed.get("type", "Unknown"))
        case_name = parsed.get("case_name", parsed.get("name", "Unknown"))
        if case_type == "Unknown" and case_name == "Unknown":
            return {"success": False, "case_type": "Unknown", "case_name": "Unknown", "error": f"Unable to parse response. Raw: {text}"}
        return {"success": True, "case_type": case_type, "case_name": case_name, "error": None}


if __name__ == "__main__":
    sample = """
CRIMINAL COMPLAINT

State of SimDemocracy, 			
                Prosecution,
v.
boho43 (1342813181319446598)
Defendant.
...
"""
    print(get_case_type(sample))