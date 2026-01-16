# Rimac Migration MCP

Este servidor MCP (Model Context Protocol) está diseñado para asistir en la migración y análisis de proyectos de Rimac, con un enfoque principal en arquitecturas **Serverless**.

Proporciona herramientas inteligentes para entender la estructura de los proyectos, resolver configuraciones complejas y validar el entorno de desarrollo.

## Características Principales

### 🔍 Análisis Profundo de Serverless
En lugar de simplemente leer archivos `serverless.yaml`, este MCP utiliza `serverless print` para obtener la **configuración resuelta**. Esto permite:
- Entender proyectos que usan variables de entorno (`${env:VAR}`).
- Soportar inclusiones de archivos (e.g., `config/functions.cloud.yaml`).
- Resolver referencias a otros stacks o plugins.

### 🛡️ Validación Interactiva de Dependencias
El sistema asegura que el entorno esté listo antes de intentar analizar:
1. Verifica si existen `node_modules`.
2. Si faltan, **pregunta interactivamente** al usuario (vía `ctx.elicit`) si su configuración `.npmrc` es correcta.
3. Tras la confirmación, **ejecuta automáticamente** `npm i --dd` en una terminal visible para instalar las dependencias.

### 💾 Persistencia y Limpieza
- Las configuraciones resueltas se guardan automáticamente en `.rimac_migration/serverless.resolved.<stage>.yaml` dentro del proyecto analizado.
- La carpeta `.rimac_migration/` se agrega automáticamente al `.gitignore` para no ensuciar el repositorio.

### 🌍 Soporte de Stages
Soporta análisis por ambiente (Stage). Por defecto utiliza `TEST`, pero puede configurarse vía variable de entorno `MCP_STAGE` o parámetro directo.

## Herramientas Disponibles

### `check_project_dependencies`
Verifica si el proyecto tiene las dependencias instaladas.
- Si faltan, inicia el flujo interactivo de confirmación de `.npmrc`.
- Retorna instrucciones precisas para que el Agente ejecute la instalación si es autorizado.

### `get_serverless_config`
Obtiene la configuración completa y resuelta del proyecto Serverless.
- **Requiere**: Dependencias instaladas.
- **Output**: JSON con la configuración (funciones, eventos, recursos) y guarda el YAML resuelto en disco.

## Prompts

### `analyze-serverless-project`
Un prompt predefinido que guía al Agente a través del flujo ideal:
1. Verificar dependencias (y remediar si faltan).
2. Obtener la configuración resuelta.
3. Analizar la estructura para reportar hallazgos.

## Requisitos

- Python 3.10+
- `uv` (recomendado para gestión de dependencias)
- `serverless` framework instalado (global o en el proyecto)
- Credenciales de AWS configuradas (para `serverless print` si el proyecto usa variables de SSM/CloudFormation).

## Uso

1. **Instalar dependencias del MCP**:
   ```bash
   uv sync
   ```

2. **Ejecutar el servidor**:
   ```bash
   uv run main.py
   ```

3. **Configurar en Trae/Claude Desktop**:
   Asegúrate de tener configurado el servidor MCP en tu cliente. Aquí tienes un ejemplo de configuración (`claude_desktop_config.json` o similar):

   ```json
   {
     "servers": {
       "migration-oci": {
         "command": "/Users/jsiapo/Developer/Rimac/migration_mcp/.venv/bin/python",
         "args": [
           "/Users/jsiapo/Developer/Rimac/migration_mcp/main.py"
         ],
         "env": {
           "MCP_STAGE": "${input:stage}"
         }
       }
     },
     "inputs": [
       {
         "id": "stage",
         "type": "promptString",
         "description": "Project Stage (DESA, TEST, PROD)",
         "password": false
       }
     ]
   }
   ```
   > **Nota**: Ajusta las rutas absolutas a tu entorno local.
