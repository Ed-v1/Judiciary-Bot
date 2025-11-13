# add testing for ai, docket entry, reading docs, etc etc.

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from services.ai_requests import aiprompt, get_case_type
import json

state= "sd" for "Simdemocracy"                                                                                                                                                                                                          

ai_test_prompt = ["A case between State and Ed in civil", " "]
ai_test_answer = ["SD v Ed", "Civil"]
# Test cases for AI prompts to classify case and get name
def ai_unit_test() -> dict:
    results = []
    for i, prompt in enumerate(ai_test_prompt):
        expected = ai_test_answer[i]
        response = get_case_type(prompt)
        if response["case_name"] == expected or response["case_type"] == expected:
            results.append((i, True, response))
        else:
            results.append((i, False, response))
    return results



print(ai_unit_test())