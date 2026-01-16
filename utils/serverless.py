import os
import subprocess

def execute_serverless_print(project_path: str, stage: str) -> subprocess.CompletedProcess:
    """Executes serverless print command."""
    cmd = ["npx", "serverless", "print", "--format", "yaml", "--stage", stage]
    return subprocess.run(
        cmd,
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=60
    )

def extract_yaml_from_output(stdout_content: str) -> str:
    """Cleans stdout to extract valid YAML."""
    lines = stdout_content.splitlines()
    clean_yaml_lines = []
    yaml_started = False
    
    for line in lines:
         if not yaml_started:
             stripped = line.strip()
             if stripped.startswith("service:") or stripped.startswith("frameworkVersion:"):
                 yaml_started = True
                 clean_yaml_lines.append(line)
             continue
         else:
             if line.strip().startswith("Serverless:") or "Deprecation warning:" in line:
                 break
             clean_yaml_lines.append(line)

    return "\n".join(clean_yaml_lines) if clean_yaml_lines else stdout_content

def persist_resolved_config(project_path: str, stage: str, yaml_content: str) -> str:
    """Saves resolved YAML and updates .gitignore."""
    migration_dir = os.path.join(project_path, ".rimac_migration")
    os.makedirs(migration_dir, exist_ok=True)
    
    resolved_file_name = f"serverless.resolved.{stage.lower()}.yaml"
    resolved_file_path = os.path.join(migration_dir, resolved_file_name)
    
    with open(resolved_file_path, "w") as f:
        f.write(yaml_content)
    
    # Update .gitignore
    gitignore_path = os.path.join(project_path, ".gitignore")
    gitignore_entry = ".rimac_migration/"
    
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r") as f:
            gitignore_content = f.read()
        
        if gitignore_entry not in gitignore_content:
            with open(gitignore_path, "a") as f:
                f.write(f"\n# Rimac Migration MCP\n{gitignore_entry}\n")
    else:
        with open(gitignore_path, "w") as f:
            f.write(f"# Rimac Migration MCP\n{gitignore_entry}\n")
            
    return resolved_file_path
