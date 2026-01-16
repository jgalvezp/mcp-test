from fastmcp import FastMCP, Context
from fastmcp.server.auth.providers.github import GitHubTokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
import yaml
import os
import httpx
from typing import Dict, Any, Optional

from utils.validation import validate_dependencies
from utils.serverless import execute_serverless_print, extract_yaml_from_output, persist_resolved_config
from utils.analysis import search_database_references

# TokenVerifier personalizado que valida dominio Rimac DURANTE la autenticación
class RimacGitHubTokenVerifier(GitHubTokenVerifier):
    """
    Token verifier que extiende GitHubTokenVerifier y valida que el email
    sea del dominio @rimac.com.pe DURANTE el proceso de autenticación OAuth.
    
    Si el email no es @rimac.com.pe, el token se rechaza y el usuario no puede
    ni siquiera conectarse al servidor MCP.
    """
    
    ALLOWED_DOMAIN = "@rimac.com.pe"
    
    async def verify_token(self, token: str) -> AccessToken | None:
        # Llamar al verificador base de GitHub
        access_token = await super().verify_token(token)
        
        if access_token is None:
            return None
        
        # Validar el dominio del email
        email = access_token.claims.get("email")
        
        if not email:
            print(f"[AUTH] Token rechazado: no tiene email en claims")
            return None
        
        if not email.endswith(self.ALLOWED_DOMAIN):
            print(f"[AUTH] Token rechazado: email '{email}' no es {self.ALLOWED_DOMAIN}")
            return None
        
        print(f"[AUTH] ✓ Usuario autorizado: {email}")
        return access_token

# Crear el provider de GitHub con el verificador personalizado
rimac_token_verifier = RimacGitHubTokenVerifier(
    required_scopes=["user"],
    timeout_seconds=10
)

auth = OAuthProxy(
    upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
    upstream_token_endpoint="https://github.com/login/oauth/access_token",
    upstream_client_id=os.environ["GITHUB_CLIENT_ID"],
    upstream_client_secret=os.environ["GITHUB_CLIENT_SECRET"],
    token_verifier=rimac_token_verifier,
    base_url=os.environ.get("MCP_BASE_URL", "http://localhost:8000"),
    redirect_path="/auth/callback",
    jwt_signing_key=os.environ.get("JWT_SIGNING_KEY", "change-me-in-production"),
)

# Inicializar el servidor MCP (sin middleware, la validación es en OAuth)
mcp = FastMCP("migration-mcp", auth=auth)

# Helper para obtener stage por defecto
def get_default_stage() -> str:
    return os.environ.get("MCP_STAGE", "TEST")

@mcp.tool()
async def whoami() -> str:
    """Muestra información del usuario autenticado actual y valida acceso Rimac"""
    token: AccessToken | None = get_access_token()
    
    if token is None:
        return "No authenticated user"
    
    user_id = token.claims.get("sub")
    github_login = token.claims.get("login") or token.claims.get("preferred_username")
    email_from_claims = token.claims.get("email")
    
    result = f"User ID: {user_id}\n"
    result += f"GitHub Username: {github_login}\n"
    result += f"Email from claims: {email_from_claims or 'Not in token'}\n\n"
    
    # Intentar obtener email público de GitHub
    if github_login:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://api.github.com/users/{github_login}",
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                
                if response.status_code == 200:
                    user_data = response.json()
                    public_email = user_data.get("email")
                    result += f"Email público en GitHub: {public_email or 'No public (https://github.com/settings/profile)'}\n\n"
                    
                    # Validar si es Rimac
                    email_to_check = public_email or email_from_claims
                    if email_to_check:
                        if email_to_check.endswith("@rimac.com.pe"):
                            result += "✅ ACCESO AUTORIZADO - Email @rimac.com.pe detectado"
                        else:
                            result += f"❌ ACCESO DENEGADO - Email '{email_to_check}' no es @rimac.com.pe"
                    else:
                        result += "⚠️  No se pudo verificar email. Configura tu email público en GitHub."
                else:
                    result += f"⚠️  Error consultando GitHub API: {response.status_code}"
        except Exception as e:
            result += f"⚠️  Error: {str(e)}"
    else:
        result += "⚠️  No se pudo obtener username de GitHub"
    
    result += f"\n\nToken claims completos: {token.claims}"
    return result

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
