from fastmcp import FastMCP, Context
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token, AccessToken
from fastmcp.server.middleware import Middleware, MiddlewareContext
import yaml
import os
import httpx
from typing import Dict, Any, Optional

from utils.validation import validate_dependencies
from utils.serverless import execute_serverless_print, extract_yaml_from_output, persist_resolved_config
from utils.analysis import search_database_references

# Crear el provider de GitHub
auth = GitHubProvider(
    client_id=os.environ["GITHUB_CLIENT_ID"],
    client_secret=os.environ["GITHUB_CLIENT_SECRET"],
    base_url=os.environ.get("MCP_BASE_URL", "http://localhost:8000"),
    required_scopes=["user:email"],
)

# Middleware para validar dominio de email
class RimacAuthMiddleware(Middleware):
    """Middleware que valida que el usuario tenga email @rimac.com.pe"""
    
    ALLOWED_DOMAIN = "@rimac.com.pe"
    _validated_users = set()  # Cache de usuarios ya validados
    
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        token: AccessToken | None = get_access_token()
        
        if not token:
            print("[DEBUG] No hay token")
            raise Exception("Authentication required")
        
        email = token.claims.get("email")
        user_id = token.claims.get("sub")
        
        # Si ya validamos este usuario, continuar
        if user_id in self._validated_users:
            print(f"[AUDIT] User {email} calling tool: {context.tool_name}")
            return await call_next(context)
        
        print(f"[DEBUG] Validando usuario... Claims: {token.claims}")
        
        # Si no hay email en claims, intentar obtenerlo de GitHub
        if not email:
            # Necesitamos el access token de GitHub original
            # FastMCP almacena los tokens, intentar obtenerlo
            print("[DEBUG] No hay email en claims, usuario no autorizado")
            raise Exception("Email not available. Please re-authenticate with user:email scope.")
        
        # Validar el dominio
        if not email.endswith(self.ALLOWED_DOMAIN):
            print(f"[DEBUG] RECHAZADO - Email {email} no es de {self.ALLOWED_DOMAIN}")
            raise Exception(f"Access denied. Only users with {self.ALLOWED_DOMAIN} email addresses are allowed.")
        
        print(f"[DEBUG] ✓ Usuario autorizado: {email}")
        self._validated_users.add(user_id)
        print(f"[AUDIT] User {email} calling tool: {context.tool_name}")
        
        return await call_next(context)

# Inicializar el servidor MCP con middleware
mcp = FastMCP("migration-mcp", auth=auth)
mcp.add_middleware(RimacAuthMiddleware())

# Helper para obtener stage por defecto
def get_default_stage() -> str:
    return os.environ.get("MCP_STAGE", "TEST")

@mcp.tool()
def whoami() -> str:
    """Muestra información del usuario autenticado actual"""
    token: AccessToken | None = get_access_token()
    
    if token is None:
        return "No authenticated user"
    
    # Mostrar TODOS los claims para debug
    return f"Token claims: {token.claims}"

@mcp.prompt("analyze-serverless-project")
def analyze_serverless_project_prompt() -> str:
    """
    Prompt para guiar al Agente en el análisis de un proyecto Serverless.
    """
    return """Sigue estos pasos estrictamente para analizar el proyecto Serverless:

PASO 1: Verificar Dependencias
Ejecuta la herramienta check_project_dependencies con la ruta del proyecto.
- Si retorna success: True:
  Continúa directamente al PASO 2.
- Si retorna status "installation_authorized":
  ABRE una nueva terminal (tool RunCommand) y ejecuta "npm i --dd" INMEDIATAMENTE.
  NO pidas confirmación al usuario (ya confirmó el .npmrc).
  Espera a que el comando termine exitosamente.
- Si retorna otro error, detente.

PASO 2: Obtener Configuración
Una vez confirmadas las dependencias, ejecuta get_serverless_config.
- Si el output indica que ya existe una configuración resuelta válida, o si retorna éxito, continúa al PASO 3.
Si no se especifica stage, se usará el configurado en el entorno (por defecto TEST).

PASO 3: Buscar Credenciales
Ejecuta la herramienta find_database_credentials para identificar referencias a bases de datos (AX, AE, SAS, RSA) en la configuración resuelta.
"""

@mcp.tool(
    name="check_project_dependencies",
    description="Checks if project dependencies are installed and environment is ready.",
)
async def check_project_dependencies(project_path: str, ctx: Context = None) -> Dict[str, Any]:
    """
    Verifica si las dependencias del proyecto están instaladas.
    Retorna instrucciones si faltan dependencias.
    """
    if not os.path.exists(project_path):
        return {"error": f"Path does not exist: {project_path}"}

    validation_result = await validate_dependencies(project_path, ctx)
    return validation_result

@mcp.tool(
    name="get_serverless_config",
    description="Gets the resolved Serverless configuration (requires dependencies installed).",
)
async def get_serverless_config(project_path: str, stage: Optional[str] = None, ctx: Context = None) -> Dict[str, Any]:
    """
    Obtiene la configuración resuelta ejecutando 'serverless print'.
    
    Pre-requisito: Las dependencias deben estar instaladas (usar check_project_dependencies primero).
    
    Flujo:
    1. Ejecuta 'serverless print' para obtener la configuración completa.
    2. Guarda la configuración resuelta en '.rimac_migration/serverless.resolved.<stage>.yaml'.
    3. Agrega '.rimac_migration/' al .gitignore si no existe.
    
    Args:
        project_path: Ruta absoluta al directorio del proyecto.
        stage: Stage para resolver variables. Si no se provee, usa la variable de entorno MCP_STAGE o default 'TEST'.
    """
    
    # Determinar stage: argumento > variable de entorno > default
    target_stage = stage if stage else get_default_stage()
    
    results = {
        "project_path": project_path,
        "stage": target_stage,
        "serverless_config": None,
        "errors": [],
        "method": "unknown"
    }
    
    if not os.path.exists(project_path):
        return {"error": f"Path does not exist: {project_path}"}

    # Check if resolved config already exists
    resolved_file_name = f"serverless.resolved.{target_stage.lower()}.yaml"
    resolved_file_path = os.path.join(project_path, ".rimac_migration", resolved_file_name)
    
    if os.path.exists(resolved_file_path):
         try:
            with open(resolved_file_path, "r") as f:
                results["serverless_config"] = yaml.safe_load(f)
            results["method"] = "existing_resolved_file"
            results["resolved_config_path"] = resolved_file_path
            return results
         except Exception as e:
            # If invalid, proceed to regenerate
            pass

    # Verificación rápida de node_modules (fail fast)
    node_modules_path = os.path.join(project_path, "node_modules")
    if not os.path.exists(node_modules_path):
         return {
             "error": "MissingDependencies",
             "message": "node_modules not found. Please run check_project_dependencies first or install dependencies manually."
         }

    # Ejecutar 'serverless print'
    try:
        process = execute_serverless_print(project_path, target_stage)
        yaml_content = extract_yaml_from_output(process.stdout)

        if process.returncode == 0:
            try:
                # Persistir configuración
                results["resolved_config_path"] = persist_resolved_config(project_path, target_stage, yaml_content)

                # Parsear y devolver
                results["serverless_config"] = yaml.safe_load(yaml_content)
                results["method"] = "serverless_print"
                
            except Exception as e:
                results["errors"].append(f"Error processing serverless print output: {str(e)}")
        else:
            clean_stderr = process.stderr.strip() if process.stderr else "Unknown error"
            results["errors"].append(f"serverless print failed: {clean_stderr[:500]}...")
            results["errors"].append("Hint: Ensure you have valid AWS credentials configured or the necessary environment variables set to resolve configuration values.")
            
    except Exception as e:
        results["errors"].append(f"Exception running serverless print: {str(e)}")
        results["errors"].append("Hint: Ensure you have valid AWS credentials configured or the necessary environment variables set to resolve configuration values.")

    return results

@mcp.tool(
    name="find_database_credentials",
    description="Analyzes the resolved Serverless config to find database credentials based on prefixes (AX, AE, SAS, RSA).",
)
async def find_database_credentials(project_path: str, stage: Optional[str] = None) -> Dict[str, Any]:
    """
    Analiza la configuración resuelta en busca de credenciales de base de datos.
    
    Busca patrones de prefijos (AX, AE, SAS, RSA) en las claves y valores del archivo
    generado por `get_serverless_config` (.rimac_migration/serverless.resolved.<stage>.yaml).
    
    Args:
        project_path: Ruta del proyecto.
        stage: Stage analizado (default: variable de entorno o TEST).
        
    Returns:
        Diccionario con los hallazgos y la ruta del archivo analizado.
    """
    target_stage = stage if stage else get_default_stage()
    resolved_file_name = f"serverless.resolved.{target_stage.lower()}.yaml"
    resolved_file_path = os.path.join(project_path, ".rimac_migration", resolved_file_name)
    
    if not os.path.exists(resolved_file_path):
        return {
            "error": "ResolvedConfigNotFound",
            "message": f"Could not find resolved config at {resolved_file_path}. Please run get_serverless_config first."
        }
        
    try:
        with open(resolved_file_path, "r") as f:
            config = yaml.safe_load(f)
            
        findings = search_database_references(config)
        
        return {
            "project_path": project_path,
            "stage": target_stage,
            "analyzed_file": resolved_file_path,
            "findings_count": len(findings),
            "findings": findings
        }
        
    except Exception as e:
        return {"error": f"Error analyzing config: {str(e)}"}

if __name__ == "__main__":
    # HTTP server: 0.0.0.0:8000 (configurable via MCP_HOST, MCP_PORT)
    mcp.run(
        transport="http",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8000"))
    )
