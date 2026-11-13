"""Export a trial's criteria as HL7 FHIR R5 (ResearchStudy + Group). Mockup quality: check against the spec before use."""
import mock_data as data


def _characteristic(c):
    code, display = data.SNOMED.get(c["id"], ("", c["text"]))
    ch = {"code": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": display}], "text": c["text"]},
          "exclude": c["type"] == "exclusion"}
    if c["kind"] == "structured" and c["op"] == "between":
        ch["valueRange"] = {"low": {"value": c["value"][0]}, "high": {"value": c["value"][1]}}
    elif c["kind"] == "structured":
        ch["valueRange"] = {"low": {"value": c["value"]}}
    else:
        ch["valueBoolean"] = True
        ch["description"] = "Free-text criterion, checked in GP notes by the local model"
    return ch


def to_fhir(trial, criteria):
    group_id = f"{trial['id']}-eligibility"
    return {"resourceType": "Bundle", "type": "collection", "entry": [
        {"resource": {"resourceType": "ResearchStudy", "id": trial["id"], "title": trial["title"], "status": "active",
                      "recruitment": {"targetNumber": trial["target"], "eligibility": {"reference": f"Group/{group_id}"}},
                      "description": trial["eligibility_text"]}},
        {"resource": {"resourceType": "Group", "id": group_id, "type": "person", "membership": "definitional",
                      "characteristic": [_characteristic(c) for c in criteria]}},
    ]}
