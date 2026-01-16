import os
from typing import Dict, Any
from fastmcp import Context
from pydantic import BaseModel, Field

class NpmrcConfirmation(BaseModel):
    is_npmrc_correct: bool = Field(description="Is the .npmrc configuration correct?")

async def validate_dependencies(project_path: str, ctx: Context) -> Dict[str, Any]:
    """Validates node_modules existence."""
    node_modules_path = os.path.join(project_path, "node_modules")
    if os.path.exists(node_modules_path):
        return {"success": True}

    # Ask user for confirmation via FastMCP
    if ctx:
        result = await ctx.elicit(
            message=f"Missing node_modules in {project_path}. Is your .npmrc correct? If yes, I will install dependencies.",
            response_type=NpmrcConfirmation
        )
        
        if result.action == "accept" and result.data.is_npmrc_correct:
            return {
                "success": False,
                "status": "installation_authorized",
                "message": "User has CONFIRMED .npmrc configuration. REQUIRED ACTION: Open a terminal and run 'npm i --dd' immediately. Do not ask for confirmation.",
                "command": "npm i --dd"
            }
        else:
             return {
                "success": False,
                "status": "aborted_by_user",
                "message": "User declined dependency installation or .npmrc is incorrect."
             }

    # Fallback if no ctx
    msg = f"Missing node_modules in {project_path}. Please install dependencies first."
    return {
        "success": False,
        "error": "MissingDependencies",
        "message": msg,
        "suggested_command": "npm i --dd",
        "action_required": "install_dependencies"
    }
