import uuid
import os
from json.decoder import JSONDecodeError
from os.path import splitext
import re
from traceback import format_exc
from stat import FILE_ATTRIBUTE_HIDDEN

from asyncio import sleep, start_server, gather, open_connection
from aiohttp import ClientSession, web

from logging import getLogger
from injector import inject_to_tab, get_gamepadui_tab, close_old_tabs, get_tab
from pathlib import Path
from localplatform import ON_WINDOWS
import helpers
import subprocess
from localplatform import service_stop, service_start, get_home_path, get_username

class Utilities:
    def __init__(self, context) -> None:
        self.context = context
        self.util_methods = {
            "ping": self.ping,
            "http_request": self.http_request,
            "install_plugin": self.install_plugin,
            "install_plugins": self.install_plugins,
            "cancel_plugin_install": self.cancel_plugin_install,
            "confirm_plugin_install": self.confirm_plugin_install,
            "uninstall_plugin": self.uninstall_plugin,
            "execute_in_tab": self.execute_in_tab,
            "inject_css_into_tab": self.inject_css_into_tab,
            "remove_css_from_tab": self.remove_css_from_tab,
            "allow_remote_debugging": self.allow_remote_debugging,
            "disallow_remote_debugging": self.disallow_remote_debugging,
            "set_setting": self.set_setting,
            "get_setting": self.get_setting,
            "filepicker_ls": self.filepicker_ls,
            "disable_rdt": self.disable_rdt,
            "enable_rdt": self.enable_rdt,
            "get_tab_id": self.get_tab_id,
            "get_user_info": self.get_user_info,
        }

        self.logger = getLogger("Utilities")

        self.rdt_proxy_server = None
        self.rdt_script_id = None
        self.rdt_proxy_task = None

        if context:
            context.web_app.add_routes([
                web.post("/methods/{method_name}", self._handle_server_method_call)
            ])

            context.ws.add_route("utilities/ping", self.ping)
            context.ws.add_route("utilities/settings/get", self.get_setting)
            context.ws.add_route("utilities/settings/set", self.set_setting)

    async def _handle_server_method_call(self, request):
        method_name = request.match_info["method_name"]
        try:
            args = await request.json()
        except JSONDecodeError:
            args = {}
        res = {}
        try:
            r = await self.util_methods[method_name](**args)
            res["result"] = r
            res["success"] = True
        except Exception as e:
            res["result"] = str(e)
            res["success"] = False
        return web.json_response(res)

    async def install_plugin(self, artifact="", name="No name", version="dev", hash=False, install_type=0):
        return await self.context.plugin_browser.request_plugin_install(
            artifact=artifact,
            name=name,
            version=version,
            hash=hash,
            install_type=install_type
        )

    async def install_plugins(self, requests):
        return await self.context.plugin_browser.request_multiple_plugin_installs(
            requests=requests
        )

    async def confirm_plugin_install(self, request_id):
        return await self.context.plugin_browser.confirm_plugin_install(request_id)

    def cancel_plugin_install(self, request_id):
        return self.context.plugin_browser.cancel_plugin_install(request_id)

    async def uninstall_plugin(self, name):
        return await self.context.plugin_browser.uninstall_plugin(name)

    async def http_request(self, method="", url="", **kwargs):
        async with ClientSession() as web:
            res = await web.request(method, url, ssl=helpers.get_ssl_context(), **kwargs)
            text = await res.text()
        return {
            "status": res.status,
            "headers": dict(res.headers),
            "body": text
        }

    async def ping(self, **kwargs):
        return "pong"

    async def execute_in_tab(self, tab, run_async, code):
        try:
            result = await inject_to_tab(tab, code, run_async)
            if "exceptionDetails" in result["result"]:
                return {
                    "success": False,
                    "result": result["result"]
                }

            return {
                "success": True,
                "result": result["result"]["result"].get("value")
            }
        except Exception as e:
            return {
              "success": False,
              "result": e
            }

    async def inject_css_into_tab(self, tab, style):
        try:
            css_id = str(uuid.uuid4())

            result = await inject_to_tab(tab,
                f"""
                (function() {{
                    const style = document.createElement('style');
                    style.id = "{css_id}";
                    document.head.append(style);
                    style.textContent = `{style}`;
                }})()
                """, False)

            if "exceptionDetails" in result["result"]:
                return {
                    "success": False,
                    "result": result["result"]
                }

            return {
                "success": True,
                "result": css_id
            }
        except Exception as e:
            return {
                "success": False,
                "result": e
            }

    async def remove_css_from_tab(self, tab, css_id):
        try:
            result = await inject_to_tab(tab,
                f"""
                (function() {{
                    let style = document.getElementById("{css_id}");

                    if (style.nodeName.toLowerCase() == 'style')
                        style.parentNode.removeChild(style);
                }})()
                """, False)

            if "exceptionDetails" in result["result"]:
                return {
                    "success": False,
                    "result": result
                }

            return {
                "success": True
            }
        except Exception as e:
            return {
                "success": False,
                "result": e
            }

    async def get_setting(self, key, default):
        return self.context.settings.getSetting(key, default)

    async def set_setting(self, key, value):
        return self.context.settings.setSetting(key, value)

    async def allow_remote_debugging(self):
        await service_start(helpers.REMOTE_DEBUGGER_UNIT)
        return True

    async def disallow_remote_debugging(self):
        await service_stop(helpers.REMOTE_DEBUGGER_UNIT)
        return True

    async def filepicker_ls(self, 
                            path : str | None = None, 
                            include_files: bool = True,
                            include_folders: bool = True,
                            include_ext: list[str] = [],
                            include_hidden: bool = False,
                            order_by: str = "name_asc",
                            filter_for: str | None = None,
                            page: int = 1,
                            max: int = 1000):
        
        if path == None:
            path = get_home_path()

        path = Path(path).resolve()

        files, folders = [], []

        #Resolving all files/folders in the requested directory
        for file in path.iterdir():
            if file.exists():
                filest = file.stat()
                is_hidden = file.name.startswith('.')
                if ON_WINDOWS and not is_hidden:
                    is_hidden = bool(filest.st_file_attributes & FILE_ATTRIBUTE_HIDDEN)
                if include_folders and file.is_dir():
                    if (is_hidden and include_hidden) or not is_hidden:
                        folders.append({"file": file, "filest": filest, "is_dir": True})
                elif include_files:
                    # Handle requested extensions if present
                    if len(include_ext) == 0 or 'all_files' in include_ext \
                        or splitext(file.name)[1].lstrip('.') in include_ext:
                        if (is_hidden and include_hidden) or not is_hidden:
                            files.append({"file": file, "filest": filest, "is_dir": False})
        # Filter logic
        if filter_for is not None:
            try:
                if re.compile(filter_for):
                    files = filter(lambda file: re.search(filter_for, file.name) != None, files)
            except re.error:
                files = filter(lambda file: file.name.find(filter_for) != -1, files)
        
        # Ordering logic
        ord_arg = order_by.split("_")
        ord = ord_arg[0]
        rev = True if ord_arg[1] == "asc" else False
        match ord:
            case 'name':
                files.sort(key=lambda x: x['file'].name.casefold(), reverse = rev)
                folders.sort(key=lambda x: x['file'].name.casefold(), reverse = rev)
            case 'modified':
                files.sort(key=lambda x: x['filest'].st_mtime, reverse = not rev)
                folders.sort(key=lambda x: x['filest'].st_mtime, reverse = not rev)
            case 'created':
                files.sort(key=lambda x: x['filest'].st_ctime, reverse = not rev)
                folders.sort(key=lambda x: x['filest'].st_ctime, reverse = not rev)
            case 'size':
                files.sort(key=lambda x: x['filest'].st_size, reverse = not rev)
                # Folders has no file size, order by name instead
                folders.sort(key=lambda x: x['file'].name.casefold())
        
        #Constructing the final file list, folders first
        all =   [{
                    "isdir": x['is_dir'],
                    "name": str(x['file'].name),
                    "realpath": str(x['file']),
                    "size": x['filest'].st_size,
                    "modified": x['filest'].st_mtime,
                    "created": x['filest'].st_ctime,
                } for x in folders + files ]

        return {
            "realpath": str(path),
            "files": all[(page-1)*max:(page)*max],
            "total": len(all),
        }
        

    # Based on https://stackoverflow.com/a/46422554/13174603
    def start_rdt_proxy(self, ip, port):
        async def pipe(reader, writer):
            try:
                while not reader.at_eof():
                    writer.write(await reader.read(2048))
            finally:
                writer.close()
        async def handle_client(local_reader, local_writer):
            try:
                remote_reader, remote_writer = await open_connection(
                    ip, port)
                pipe1 = pipe(local_reader, remote_writer)
                pipe2 = pipe(remote_reader, local_writer)
                await gather(pipe1, pipe2)
            finally:
                local_writer.close()

        self.rdt_proxy_server = start_server(handle_client, "127.0.0.1", port)
        self.rdt_proxy_task = self.context.loop.create_task(self.rdt_proxy_server)

    def stop_rdt_proxy(self):
        if self.rdt_proxy_server:
            self.rdt_proxy_server.close()
            self.rdt_proxy_task.cancel()

    async def _enable_rdt(self):
        # TODO un-hardcode port
        try:
            self.stop_rdt_proxy()
            ip = self.context.settings.getSetting("developer.rdt.ip", None)

            if ip != None:
                self.logger.info("Connecting to React DevTools at " + ip)
                async with ClientSession() as web:
                    res = await web.request("GET", "http://" + ip + ":8097", ssl=helpers.get_ssl_context())
                    script = """
                    if (!window.deckyHasConnectedRDT) {
                        window.deckyHasConnectedRDT = true;
                        // This fixes the overlay when hovering over an element in RDT
                        Object.defineProperty(window, '__REACT_DEVTOOLS_TARGET_WINDOW__', {
                            enumerable: true,
                            configurable: true,
                            get: function() {
                                return (GamepadNavTree?.m_context?.m_controller || FocusNavController)?.m_ActiveContext?.ActiveWindow || window;
                            }
                        });
                    """ + await res.text() + "\n}"
                if res.status != 200:
                    self.logger.error("Failed to connect to React DevTools at " + ip)
                    return False
                self.start_rdt_proxy(ip, 8097)
                self.logger.info("Connected to React DevTools, loading script")
                tab = await get_gamepadui_tab()
                # RDT needs to load before React itself to work.
                await close_old_tabs()
                result = await tab.reload_and_evaluate(script)
                self.logger.info(result)

        except Exception:
            self.logger.error("Failed to connect to React DevTools")
            self.logger.error(format_exc())

    async def enable_rdt(self):
        self.context.loop.create_task(self._enable_rdt())

    async def disable_rdt(self):
        self.logger.info("Disabling React DevTools")
        tab = await get_gamepadui_tab()
        self.rdt_script_id = None
        await close_old_tabs()
        await tab.evaluate_js("location.reload();", False, True, False)
        self.logger.info("React DevTools disabled")

    async def get_user_info(self) -> dict:
        return {
            "username": get_username(),
            "path": get_home_path()
        }
    
    async def get_tab_id(self, name):
        return (await get_tab(name)).id
