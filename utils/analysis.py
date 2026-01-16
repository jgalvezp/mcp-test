from typing import Dict, List, Any, Union

def search_database_references(config: Dict[str, Any], prefixes: List[str] = None) -> List[Dict[str, Any]]:
    """
    Recursively searches for database references in the configuration based on prefixes.
    
    Args:
        config: The parsed Serverless configuration (dict).
        prefixes: List of database prefixes to search for (e.g., ['AX', 'AE']). 
                  Defaults to ['AX', 'AE', 'SAS', 'RSA'].
        
    Returns:
        List of findings. Each finding contains match details.
    """
    if prefixes is None:
        prefixes = ["AX", "AE", "SAS", "RSA"]
        
    findings = []
    
    def _recurse(obj: Union[Dict, List, str, int, float, bool, None], current_path: str):
        if isinstance(obj, dict):
            for k, v in obj.items():
                # Check key
                for prefix in prefixes:
                    # Check if prefix matches key (case insensitive)
                    # We look for the prefix as a distinct part or contained
                    if prefix.lower() in k.lower():
                        findings.append({
                            "type": "key_match",
                            "prefix": prefix,
                            "path": f"{current_path}.{k}" if current_path else k,
                            "key": k,
                            "value": v if isinstance(v, (str, int, float, bool)) else f"<{type(v).__name__}>"
                        })
                
                _recurse(v, f"{current_path}.{k}" if current_path else k)
                
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _recurse(item, f"{current_path}[{i}]")
                
        elif isinstance(obj, str):
            # Check value
            for prefix in prefixes:
                if prefix.lower() in obj.lower():
                     findings.append({
                        "type": "value_match",
                        "prefix": prefix,
                        "path": current_path,
                        "value": obj
                    })

    _recurse(config, "")
    
    # Deduplicate findings based on path and prefix to avoid noise?
    # For now, let's return all.
    return findings
