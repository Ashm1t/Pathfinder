import os
import re
from typing import List, Dict, Any

def scan_directory(base_path: str) -> List[Dict[str, Any]]:
    """
    Scans the base directory for FIR folders and extracts basic metadata.
    """
    cases = []
    
    if not os.path.exists(base_path):
        return []

    # Iterate over items in the base path
    for item in os.listdir(base_path):
        item_path = os.path.join(base_path, item)
        
        # Check if it's a directory and looks like an FIR folder (e.g., "FIR 201-25")
        if os.path.isdir(item_path) and "FIR" in item.upper():
            case_info = {
                "id": item,
                "path": item_path,
                "name": item,
                "suspects": [],
                "documents": [],
                "status": "Active" # Default
            }
            
            # Scan inside the FIR folder
            for sub_item in os.listdir(item_path):
                sub_item_path = os.path.join(item_path, sub_item)
                
                if os.path.isdir(sub_item_path):
                    # Likely a suspect folder or category
                    case_info["suspects"].append({
                        "name": sub_item,
                        "path": sub_item_path
                    })
                else:
                    # It's a file
                    # Try to extract date from filename (DD.MM.YY or DD-MM-YYYY)
                    date_match = re.search(r'(\d{1,2}[.-]\d{1,2}[.-]\d{2,4})', sub_item)
                    extracted_date = date_match.group(1) if date_match else None
                    
                    doc_info = {
                        "name": sub_item,
                        "path": sub_item_path,
                        "type": os.path.splitext(sub_item)[1].lower(),
                        "date": extracted_date
                    }
                    case_info["documents"].append(doc_info)
            
            cases.append(case_info)
            
    return cases

def get_schedule(base_path: str) -> List[Dict[str, Any]]:
    """
    Extracts scheduled events based on dates in filenames.
    """
    cases = scan_directory(base_path)
    schedule = []
    
    for case in cases:
        for doc in case["documents"]:
            if doc["date"]:
                schedule.append({
                    "id": f"{case['id']}-{doc['name']}",
                    "title": f"Event in {case['id']}",
                    "description": f"Document: {doc['name']}",
                    "date": doc["date"],
                    "caseId": case["id"]
                })
    
    return schedule

def get_case_context(case_id: str, base_path: str) -> Dict[str, Any]:
    """
    Retrieves detailed context for a specific case to feed into the AI agent.
    """
    cases = scan_directory(base_path)
    target_case = next((c for c in cases if c["id"] == case_id), None)
    
    if not target_case:
        return None
        
    # In a real implementation, we would read the content of the documents here.
    # For now, we'll just list the document names and types.
    
    context = {
        "id": target_case["id"],
        "name": target_case["name"],
        "suspects": [s["name"] for s in target_case["suspects"]],
        "documents": [d["name"] for d in target_case["documents"]],
        "status": target_case["status"]
    }
    
    return context

if __name__ == "__main__":
    # Test with the known path
    test_path = r"d:\Tunday Kebabi\Tempest"
    results = scan_directory(test_path)
    import json
    print(json.dumps(results, indent=2))
