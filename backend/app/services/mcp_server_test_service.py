"""Live connectivity tests for configured MCP servers."""
import asyncio
import hashlib
import json
import logging
import shutil
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.credentials_service import CredentialsService
from app.services.mcp_config_service import MCPConfigService

logger = logging.getLogger(__name__)


class MCPServerTestService(MCPConfigService):
    """Tests connectivity to configured MCP servers (stdio, http, sse)."""

    @staticmethod
    def _compute_config_hash(server_config: dict[str, Any]) -> str:
        """Compute hash of server configuration for cache invalidation."""
        config_str = json.dumps(server_config, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()

    async def test_connection(
        self, name: str, scope: str, project_path: str | None = None, db: AsyncSession | None = None
    ) -> dict[str, Any]:
        """
        Test connection to an MCP server.

        Args:
            name: Server name
            scope: Server scope ("user" or "project")
            project_path: Optional path to project directory
            db: Optional database session for caching results

        Returns:
            Dictionary with success status and message
        """
        # Get server config
        server = await self.get_server(name, scope)
        if not server:
            return {"success": False, "message": f"Server '{name}' not found"}

        # Test based on type
        if server.type == "stdio":
            # Check if command exists
            if not server.command:
                return {"success": False, "message": "No command specified for stdio server"}

            # First check if command exists
            command_path = shutil.which(server.command)
            if not command_path:
                return {
                    "success": False,
                    "message": f"Command '{server.command}' not found in PATH",
                }

            # Try to actually start the MCP server and send initialize
            process = None
            try:
                cmd = [server.command] + (server.args or [])
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                # Send MCP initialize request
                init_request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "claude-cockpit-test", "version": "1.0.0"},
                    },
                }
                request_str = json.dumps(init_request)
                is_npx = server.command == "npx"

                # For npx commands, wait for server to be ready by monitoring stderr
                if is_npx:
                    for _ in range(60):  # up to 30 seconds
                        await asyncio.sleep(0.5)
                        if process.returncode is not None:
                            stderr_data = await process.stderr.read(4096)
                            error_output = stderr_data.decode().strip() if stderr_data else "Process exited"
                            return {
                                "success": False,
                                "message": f"Server failed: {error_output[:300]}",
                            }
                        try:
                            stderr = await asyncio.wait_for(process.stderr.read(4096), timeout=0.3)
                            if stderr and b"running on stdio" in stderr.lower() or b"server" in stderr.lower():
                                break
                        except TimeoutError:
                            pass
                    await asyncio.sleep(0.5)  # Small delay after ready

                # Send request as raw JSON with newline (many MCP servers use this format)
                raw_message = request_str + "\n"
                process.stdin.write(raw_message.encode())
                await process.stdin.drain()

                # Give process time to respond or fail
                await asyncio.sleep(0.5)

                # Check if process already exited with error
                if process.returncode is not None:
                    stderr_data = await process.stderr.read(4096)
                    stdout_data = await process.stdout.read(4096)
                    error_output = stderr_data.decode().strip() if stderr_data else ""
                    if not error_output:
                        error_output = stdout_data.decode().strip() if stdout_data else "Process exited"
                    return {
                        "success": False,
                        "message": f"Server failed: {error_output[:300]}",
                    }

                # Read response - try raw JSON first (newline-delimited), then Content-Length format
                read_timeout = 30.0
                try:
                    response_line = await asyncio.wait_for(
                        process.stdout.readline(), timeout=read_timeout
                    )
                    if not response_line:
                        stderr_data = await process.stderr.read(4096)
                        stderr_str = stderr_data.decode().strip() if stderr_data else "No output"
                        return {
                            "success": False,
                            "message": f"Server closed without response: {stderr_str[:300]}",
                        }

                    response_str = response_line.decode().strip()

                    # Check if it's Content-Length header (LSP format) or raw JSON
                    if response_str.startswith("Content-Length:"):
                        content_length = int(response_str.split(":")[1].strip())
                        await asyncio.wait_for(process.stdout.readline(), timeout=5.0)  # blank line
                        json_data = await asyncio.wait_for(
                            process.stdout.readexactly(content_length), timeout=5.0
                        )
                        response = json.loads(json_data.decode())
                    else:
                        # Raw JSON format
                        response = json.loads(response_str)
                    if "result" in response:
                        server_info = response.get("result", {}).get("serverInfo", {})
                        server_name = server_info.get("name", "unknown")
                        server_version = server_info.get("version")
                        capabilities = response.get("result", {}).get("capabilities", {})

                        # Helper to send a JSON-RPC request and read response
                        async def _send_jsonrpc(method: str, req_id: int, timeout_s: float = 10.0):
                            request = {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "method": method,
                                "params": {},
                            }
                            msg = json.dumps(request) + "\n"
                            process.stdin.write(msg.encode())
                            await process.stdin.drain()

                            resp_line = await asyncio.wait_for(
                                process.stdout.readline(), timeout=timeout_s
                            )
                            if not resp_line:
                                return None
                            resp_str = resp_line.decode().strip()
                            if resp_str.startswith("Content-Length:"):
                                cl = int(resp_str.split(":")[1].strip())
                                await process.stdout.readline()  # blank line
                                json_data = await process.stdout.readexactly(cl)
                                return json.loads(json_data.decode())
                            return json.loads(resp_str)

                        # Fetch tools list
                        tools = []
                        tool_count = 0
                        try:
                            tools_response = await _send_jsonrpc("tools/list", 2, 10.0)
                            if tools_response and "result" in tools_response:
                                tools_list = tools_response["result"].get("tools", [])
                                tool_count = len(tools_list)
                                for tool in tools_list[:self.MAX_CACHED_ITEMS]:
                                    tools.append({
                                        "name": tool.get("name", "unknown"),
                                        "description": tool.get("description"),
                                        "inputSchema": tool.get("inputSchema"),
                                    })
                        except Exception:
                            pass  # Tools fetch failed, but init succeeded

                        # Fetch resources list (only if server advertises support)
                        resources = []
                        resource_count = 0
                        if capabilities.get("resources"):
                            try:
                                res_response = await _send_jsonrpc("resources/list", 3, 5.0)
                                if res_response and "result" in res_response:
                                    res_list = res_response["result"].get("resources", [])
                                    resource_count = len(res_list)
                                    for r in res_list[:self.MAX_CACHED_ITEMS]:
                                        resources.append({
                                            "uri": r.get("uri", ""),
                                            "name": r.get("name", ""),
                                            "description": r.get("description"),
                                            "mimeType": r.get("mimeType"),
                                        })
                            except Exception:
                                pass  # Resources fetch failed

                        # Fetch prompts list (only if server advertises support)
                        prompts = []
                        prompt_count = 0
                        if capabilities.get("prompts"):
                            try:
                                prompts_response = await _send_jsonrpc("prompts/list", 4, 5.0)
                                if prompts_response and "result" in prompts_response:
                                    prompts_list = prompts_response["result"].get("prompts", [])
                                    prompt_count = len(prompts_list)
                                    for p in prompts_list[:self.MAX_CACHED_ITEMS]:
                                        arguments = None
                                        if p.get("arguments"):
                                            arguments = [
                                                {
                                                    "name": a.get("name", ""),
                                                    "description": a.get("description"),
                                                    "required": a.get("required"),
                                                }
                                                for a in p["arguments"]
                                            ]
                                        prompts.append({
                                            "name": p.get("name", ""),
                                            "description": p.get("description"),
                                            "arguments": arguments,
                                        })
                            except Exception:
                                pass  # Prompts fetch failed

                        result = {
                            "success": True,
                            "message": f"MCP server '{server_name}' initialized successfully",
                            "server_name": server_name,
                            "server_version": server_version,
                            "tools": tools if tools else None,
                            "tool_count": tool_count,
                            "resources": resources if resources else None,
                            "resource_count": resource_count,
                            "prompts": prompts if prompts else None,
                            "prompt_count": prompt_count,
                            "capabilities": capabilities if capabilities else None,
                        }

                        # Cache the result if database session is provided
                        if db and server:
                            config_dict = {
                                "type": server.type,
                                "command": server.command,
                                "args": server.args,
                                "url": server.url,
                            }
                            config_hash = self._compute_config_hash(config_dict)
                            await self.update_server_cache(name, scope, result, config_hash, db)

                        return result
                    elif "error" in response:
                        error_msg = response["error"].get("message", "Unknown error")
                        return {"success": False, "message": f"MCP error: {error_msg}"}

                    return {
                        "success": True,
                        "message": f"Server responded (command: {server.command})",
                    }

                except TimeoutError:
                    # Check if process exited with error
                    if process.returncode is not None:
                        stderr_data = await process.stderr.read(1024)
                        stderr_str = stderr_data.decode().strip() if stderr_data else "Unknown error"
                        return {
                            "success": False,
                            "message": f"Server exited: {stderr_str[:200]}",
                        }
                    return {
                        "success": False,
                        "message": "Server did not respond within timeout",
                    }
                except json.JSONDecodeError as e:
                    return {
                        "success": False,
                        "message": f"Invalid JSON response: {str(e)}",
                    }

            except FileNotFoundError:
                return {
                    "success": False,
                    "message": f"Command '{server.command}' not found",
                }
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to start server: {str(e)}",
                }
            finally:
                # Always clean up the process
                if process and process.returncode is None:
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except Exception:
                        process.kill()

        elif server.type == "http":
            # MCP Streamable HTTP: POST JSON-RPC to the server URL
            if not server.url:
                return {"success": False, "message": "No URL specified for http server"}

            try:
                # Include stored OAuth token if available
                headers = {
                    **(server.headers or {}),
                    "Accept": "application/json, text/event-stream",
                }
                creds_svc = CredentialsService()
                token = creds_svc.get_mcp_token(server.name, server.url)
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                async with httpx.AsyncClient(timeout=30.0) as client:
                    # Send MCP initialize
                    init_request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "claude-cockpit-test", "version": "1.0.0"},
                        },
                    }
                    response = await client.post(
                        server.url, json=init_request, headers=headers,
                        follow_redirects=True, timeout=10.0,
                    )

                    # Capture session ID for subsequent requests
                    session_id = response.headers.get("mcp-session-id")
                    if session_id:
                        headers["mcp-session-id"] = session_id

                    if response.status_code >= 400:
                        return {
                            "success": False,
                            "message": f"HTTP server returned error status {response.status_code}",
                        }

                    resp_data = response.json()
                    if "error" in resp_data:
                        error_msg = resp_data["error"].get("message", "Unknown error")
                        return {"success": False, "message": f"MCP error: {error_msg}"}

                    if "result" not in resp_data:
                        return {
                            "success": True,
                            "message": f"Server responded (status {response.status_code})",
                        }

                    server_info = resp_data["result"].get("serverInfo", {})
                    server_name_val = server_info.get("name", "unknown")
                    server_version = server_info.get("version")
                    capabilities = resp_data["result"].get("capabilities", {})

                    # Helper to send JSON-RPC over HTTP
                    async def _http_jsonrpc(method: str, req_id: int, timeout_s: float = 10.0):
                        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": {}}
                        r = await client.post(
                            server.url, json=req, headers=headers,
                            follow_redirects=True, timeout=timeout_s,
                        )
                        if r.status_code < 400:
                            return r.json()
                        return None

                    # Fetch tools
                    tools = []
                    tool_count = 0
                    try:
                        tools_resp = await _http_jsonrpc("tools/list", 2, 10.0)
                        if tools_resp and "result" in tools_resp:
                            tools_list = tools_resp["result"].get("tools", [])
                            tool_count = len(tools_list)
                            for t in tools_list[:self.MAX_CACHED_ITEMS]:
                                tools.append({
                                    "name": t.get("name", "unknown"),
                                    "description": t.get("description"),
                                    "inputSchema": t.get("inputSchema"),
                                })
                    except Exception:
                        pass

                    # Fetch resources
                    resources = []
                    resource_count = 0
                    if capabilities.get("resources"):
                        try:
                            res_resp = await _http_jsonrpc("resources/list", 3, 5.0)
                            if res_resp and "result" in res_resp:
                                res_list = res_resp["result"].get("resources", [])
                                resource_count = len(res_list)
                                for r in res_list[:self.MAX_CACHED_ITEMS]:
                                    resources.append({
                                        "uri": r.get("uri", ""),
                                        "name": r.get("name", ""),
                                        "description": r.get("description"),
                                        "mimeType": r.get("mimeType"),
                                    })
                        except Exception:
                            pass

                    # Fetch prompts
                    prompts = []
                    prompt_count = 0
                    if capabilities.get("prompts"):
                        try:
                            prompts_resp = await _http_jsonrpc("prompts/list", 4, 5.0)
                            if prompts_resp and "result" in prompts_resp:
                                prompts_list = prompts_resp["result"].get("prompts", [])
                                prompt_count = len(prompts_list)
                                for p in prompts_list[:self.MAX_CACHED_ITEMS]:
                                    arguments = None
                                    if p.get("arguments"):
                                        arguments = [
                                            {
                                                "name": a.get("name", ""),
                                                "description": a.get("description"),
                                                "required": a.get("required"),
                                            }
                                            for a in p["arguments"]
                                        ]
                                    prompts.append({
                                        "name": p.get("name", ""),
                                        "description": p.get("description"),
                                        "arguments": arguments,
                                    })
                        except Exception:
                            pass

                    result = {
                        "success": True,
                        "message": f"MCP server '{server_name_val}' initialized successfully",
                        "server_name": server_name_val,
                        "server_version": server_version,
                        "tools": tools if tools else None,
                        "tool_count": tool_count,
                        "resources": resources if resources else None,
                        "resource_count": resource_count,
                        "prompts": prompts if prompts else None,
                        "prompt_count": prompt_count,
                        "capabilities": capabilities if capabilities else None,
                    }

                    # Cache the result
                    if db:
                        config_dict = {"type": server.type, "url": server.url}
                        config_hash = self._compute_config_hash(config_dict)
                        await self.update_server_cache(name, scope, result, config_hash, db)

                    return result

            except httpx.TimeoutException:
                return {"success": False, "message": "Connection timeout"}
            except httpx.RequestError as e:
                return {"success": False, "message": f"Request error: {str(e)}"}
            except Exception as e:
                return {"success": False, "message": f"Unexpected error: {str(e)}"}

        elif server.type == "sse":
            # Test SSE (Server-Sent Events) connection
            if not server.url:
                return {"success": False, "message": "No URL specified for SSE server"}

            try:
                # Include stored OAuth token if available
                creds_svc = CredentialsService()
                token = creds_svc.get_mcp_token(server.name, server.url)

                async with httpx.AsyncClient(timeout=10.0) as client:
                    # SSE servers should respond to GET with text/event-stream
                    # First try a HEAD request to check availability
                    headers = {**(server.headers or {}), "Accept": "text/event-stream"}
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                    response = await client.get(
                        server.url,
                        headers=headers,
                        follow_redirects=True,
                        timeout=5.0,
                    )

                    content_type = response.headers.get("content-type", "")

                    if response.status_code < 400:
                        if "text/event-stream" in content_type:
                            return {
                                "success": True,
                                "message": f"SSE server connected (status {response.status_code})",
                            }
                        else:
                            return {
                                "success": True,
                                "message": f"Server responded (status {response.status_code}, type: {content_type})",
                            }
                    else:
                        return {
                            "success": False,
                            "message": f"SSE server returned error status {response.status_code}",
                        }
            except httpx.TimeoutException:
                return {"success": False, "message": "Connection timeout"}
            except httpx.RequestError as e:
                return {"success": False, "message": f"Request error: {str(e)}"}
            except Exception as e:
                return {"success": False, "message": f"Unexpected error: {str(e)}"}

        else:
            return {"success": False, "message": f"Unknown server type: {server.type}"}
