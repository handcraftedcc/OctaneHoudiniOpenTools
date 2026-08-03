"""
Live Cargo receiver for Houdini + Octane.

Cargo's Houdini protocol is handled here, while all asset interpretation and
node creation remains in CargoImporter.  Incoming materials are created in
/mat; incoming models are created as geometry objects in /obj.

The wire protocol in this module is compatible with py_cargo_io_server.
See CARGO_IO_LICENSE.txt for its copyright and MIT license.
"""

from __future__ import print_function

import atexit
import json
import os
import queue
import shutil
import socket
import struct
import threading
import time

try:
    import hou
except ImportError:
    hou = None

import CargoImporter
import openToolsUtils


PLUGIN_VERSION = "1.0.0"
MESSAGE_FORMAT_JSON = 0
ASSET_IMPORT_REQUEST = 803
ASSET_IMPORT_PROGRESS_REPLY = 1100
PLUGIN_HANDSHAKE_REQUEST = 99994
PLUGIN_HANDSHAKE_REPLY = 99995
SERVER_SHUTDOWN_REQUEST = 99996
SERVER_HEARTBEAT_REPLY = 99997
SERVER_SUCCESS_REPLY = 99998
SERVER_ERROR_REPLY = 99999
HEADER_FORMAT = "<qii"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
SOCKET_TIMEOUT = 0.1
HEARTBEAT_INTERVAL = 5.0
SERVER_START_TIMEOUT = 5.0
DEFAULT_DESTINATION = "$HIP/cargo/"
DEFAULT_COPY_TO_DESTINATION = True
DEFAULT_TEXTURE_RESOLUTION = "2k"
TEXTURE_RESOLUTIONS = ("1k", "2k", "4k")
DEFAULT_DISPLACEMENT_MODE = CargoImporter.HEIGHT_MODE_TEXTURE_DISPLACEMENT
DEFAULT_DISPLACEMENT_HEIGHT = CargoImporter.DEFAULT_DISPLACEMENT_HEIGHT
DEFAULT_DISPLACEMENT_DISCONNECTED = CargoImporter.DEFAULT_DISPLACEMENT_CREATE_DISCONNECTED
DEFAULT_PROJECTION_MODE = CargoImporter.PROJECTION_MODE_UV
DISPLACEMENT_MODES = (
    CargoImporter.HEIGHT_MODE_TEXTURE_DISPLACEMENT,
    CargoImporter.HEIGHT_MODE_VERTEX_DISPLACEMENT,
    CargoImporter.HEIGHT_MODE_BUMP,
)
PROJECTION_MODES = (
    CargoImporter.PROJECTION_MODE_UV,
    CargoImporter.PROJECTION_MODE_TRIPLANAR,
    CargoImporter.PROJECTION_MODE_BOX,
    CargoImporter.PROJECTION_MODE_LINEAR,
    CargoImporter.PROJECTION_MODE_CYLINDRICAL,
    CargoImporter.PROJECTION_MODE_SPHERICAL,
    CargoImporter.PROJECTION_MODE_PERSPECTIVE,
)
SETTINGS_TOOL_NAME = "CargoOctaneListener"


def _receive_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("Cargo connection closed before the message was complete.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _receive_message(sock):
    header = _receive_exact(sock, HEADER_SIZE)
    message_size, message_format, message_type = struct.unpack(HEADER_FORMAT, header)
    content_size = message_size - HEADER_SIZE
    if content_size < 0:
        raise RuntimeError("Cargo sent an invalid message size.")
    content = _receive_exact(sock, content_size) if content_size else b""
    return message_format, message_type, content


def _send_json(sock, message_type, content=None):
    content_bytes = json.dumps(content or {}).encode("utf-8")
    header = struct.pack(
        HEADER_FORMAT,
        HEADER_SIZE + len(content_bytes),
        MESSAGE_FORMAT_JSON,
        message_type,
    )
    sock.sendall(header + content_bytes)


def _path_metadata(asset_path, marker):
    parts = list(asset_path.parts)
    lowered = [part.lower() for part in parts]
    try:
        marker_index = lowered.index(marker.lower())
    except ValueError:
        return "", ""
    kit = parts[marker_index - 2] if marker_index >= 2 else ""
    version = parts[marker_index - 1] if marker_index >= 1 else ""
    return kit, version


def _preferred_variants(texture_variant):
    preferred = []
    if texture_variant:
        preferred.append(str(texture_variant).lower())
    for variant in CargoImporter.PREFERRED_TEXTURE_VARIANTS:
        if variant not in preferred:
            preferred.append(variant)
    return preferred


def destination_from_settings():
    return openToolsUtils.getToolSetting(
        SETTINGS_TOOL_NAME,
        "destination",
        DEFAULT_DESTINATION,
    )


def save_destination(destination):
    destination = str(destination or "").strip() or DEFAULT_DESTINATION
    openToolsUtils.setToolSetting(SETTINGS_TOOL_NAME, "destination", destination)
    return destination


def copy_to_destination_from_settings():
    value = openToolsUtils.getToolSetting(
        SETTINGS_TOOL_NAME,
        "copy_to_destination",
        DEFAULT_COPY_TO_DESTINATION,
    )
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def save_copy_to_destination(enabled):
    enabled = bool(enabled)
    openToolsUtils.setToolSetting(
        SETTINGS_TOOL_NAME,
        "copy_to_destination",
        enabled,
    )
    return enabled


def texture_resolution_from_settings():
    resolution = openToolsUtils.getToolSetting(
        SETTINGS_TOOL_NAME,
        "texture_resolution",
        DEFAULT_TEXTURE_RESOLUTION,
    )
    return normalize_texture_resolution(resolution)


def normalize_texture_resolution(resolution):
    resolution = str(resolution or "").strip().lower()
    return resolution if resolution in TEXTURE_RESOLUTIONS else DEFAULT_TEXTURE_RESOLUTION


def save_texture_resolution(resolution):
    resolution = normalize_texture_resolution(resolution)
    openToolsUtils.setToolSetting(
        SETTINGS_TOOL_NAME,
        "texture_resolution",
        resolution,
    )
    return resolution


def texture_variant_for_resolution(cargo_variant, resolution):
    resolution = normalize_texture_resolution(resolution)
    cargo_variant = str(cargo_variant or "").strip().lower()
    image_format = "jpg" if cargo_variant.startswith("jpg") else "png"
    return "{0}{1}".format(image_format, resolution)


def _setting(key, default):
    return openToolsUtils.getToolSetting(SETTINGS_TOOL_NAME, key, default)


def displacement_mode_from_settings():
    mode = str(_setting("displacement_mode", DEFAULT_DISPLACEMENT_MODE))
    return mode if mode in DISPLACEMENT_MODES else DEFAULT_DISPLACEMENT_MODE


def displacement_height_from_settings():
    try:
        return float(_setting("displacement_height", DEFAULT_DISPLACEMENT_HEIGHT))
    except (TypeError, ValueError):
        return float(DEFAULT_DISPLACEMENT_HEIGHT)


def displacement_disconnected_from_settings():
    value = _setting(
        "displacement_create_disconnected",
        DEFAULT_DISPLACEMENT_DISCONNECTED,
    )
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def projection_mode_from_settings():
    mode = str(_setting("projection_mode", DEFAULT_PROJECTION_MODE))
    return mode if mode in PROJECTION_MODES else DEFAULT_PROJECTION_MODE


def save_import_settings(
    displacement_mode,
    displacement_height,
    create_disconnected,
    projection_mode,
):
    displacement_mode = (
        displacement_mode
        if displacement_mode in DISPLACEMENT_MODES
        else DEFAULT_DISPLACEMENT_MODE
    )
    projection_mode = (
        projection_mode if projection_mode in PROJECTION_MODES else DEFAULT_PROJECTION_MODE
    )
    try:
        displacement_height = float(displacement_height)
    except (TypeError, ValueError):
        displacement_height = float(DEFAULT_DISPLACEMENT_HEIGHT)
    openToolsUtils.setToolSetting(
        SETTINGS_TOOL_NAME,
        "displacement_mode",
        displacement_mode,
    )
    openToolsUtils.setToolSetting(
        SETTINGS_TOOL_NAME,
        "displacement_height",
        displacement_height,
    )
    openToolsUtils.setToolSetting(
        SETTINGS_TOOL_NAME,
        "displacement_create_disconnected",
        bool(create_disconnected),
    )
    openToolsUtils.setToolSetting(
        SETTINGS_TOOL_NAME,
        "projection_mode",
        projection_mode,
    )
    return {
        "mode": displacement_mode,
        "height": displacement_height,
        "create_disconnected": bool(create_disconnected),
        "projection_mode": projection_mode,
    }


def resolve_destination(destination=None):
    destination = str(destination or destination_from_settings()).strip()
    if hou is not None:
        destination = hou.expandString(destination)
    destination = os.path.abspath(os.path.expandvars(os.path.expanduser(destination)))
    return destination


def _cargo_version_root(asset_path):
    path = asset_path.parent
    while path != path.parent:
        if path.name.lower() in ("models", "materials"):
            return path.parent
        path = path.parent
    raise RuntimeError(
        "Cargo asset is not inside a Models or Materials directory: {0}".format(asset_path)
    )


def _path_is_within(path, directory):
    try:
        path = os.path.normcase(os.path.abspath(str(path)))
        directory = os.path.normcase(os.path.abspath(str(directory)))
        return os.path.commonpath([path, directory]) == directory
    except (TypeError, ValueError):
        return False


def asset_is_in_project_directory(usd_file_path, texture_variant=None):
    from pathlib import Path

    if hou is None:
        return False
    try:
        if hou.hipFile.isNewFile():
            return False
    except Exception:
        pass
    project_directory = hou.expandString("$HIP")
    if not project_directory or "$HIP" in project_directory:
        return False
    source_path = Path(os.path.abspath(os.path.expandvars(str(usd_file_path))))
    if not source_path.is_file() or not _path_is_within(source_path, project_directory):
        return False

    dependency_paths, unresolved = _usd_dependency_manifest(
        source_path,
        texture_variant,
    )
    if unresolved:
        return False
    return all(
        dependency_path.is_file()
        and _path_is_within(dependency_path, project_directory)
        for dependency_path in dependency_paths
    )


def _include_dependency_asset(asset_path, texture_variant):
    """Keep non-texture assets and only the requested texture resolution."""
    path_parts = [part.lower() for part in asset_path.parts]
    texture_variant_folders = set(
        "{0}{1}".format(image_format, resolution)
        for image_format in ("jpg", "png", "exr")
        for resolution in ("1k", "2k", "4k", "8k")
    )
    variants_in_path = texture_variant_folders.intersection(path_parts)
    if not variants_in_path:
        return True

    selected_variant = str(texture_variant or _preferred_variants(None)[0]).lower()
    selected_resolution = (
        selected_variant[-2:]
        if selected_variant[-2:] in ("1k", "2k", "4k", "8k")
        else ""
    )
    allowed_variants = set([selected_variant])
    if selected_resolution:
        allowed_variants.add("exr{0}".format(selected_resolution))
    return bool(variants_in_path.intersection(allowed_variants))


def _usd_dependency_manifest(source_path, texture_variant):
    """Build a copy manifest with Houdini's bundled Pixar USD API."""
    from pathlib import Path

    try:
        from pxr import UsdUtils
    except ImportError:
        raise RuntimeError("Houdini's bundled Pixar USD modules are unavailable.")

    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(source_path.as_posix())
    manifest = set([source_path])

    for layer in layers:
        real_path = getattr(layer, "realPath", None)
        if real_path:
            manifest.add(Path(real_path))

    for asset in assets:
        asset_path = Path(asset)
        if _include_dependency_asset(asset_path, texture_variant):
            manifest.add(asset_path)

    return manifest, unresolved


def localize_received_asset(usd_file_path, destination=None, texture_variant=None):
    """Copy an incoming USD and its resolved dependencies into the destination."""
    from pathlib import Path

    source_path = Path(os.path.abspath(os.path.expandvars(str(usd_file_path))))
    if not source_path.is_file():
        raise RuntimeError("Cargo USD file does not exist: {0}".format(source_path))

    source_root = _cargo_version_root(source_path)
    kit_name = source_root.parent.name
    version_name = source_root.name
    localized_root = Path(resolve_destination(destination)) / kit_name / version_name
    localized_asset_path = localized_root / source_path.relative_to(source_root)

    paths_to_copy, unresolved = _usd_dependency_manifest(source_path, texture_variant)

    copied = set()
    for file_path in paths_to_copy:
        file_path = Path(os.path.abspath(str(file_path)))
        if not file_path.is_file():
            print("Cargo Octane: dependency does not exist; skipping {0}".format(file_path))
            continue
        try:
            relative_path = file_path.relative_to(source_root)
        except ValueError:
            print("Cargo Octane: dependency is outside the Cargo asset root; skipping {0}".format(file_path))
            continue
        destination_path = localized_root / relative_path
        source_key = os.path.normcase(os.path.abspath(str(file_path)))
        destination_key = os.path.normcase(os.path.abspath(str(destination_path)))
        if source_key == destination_key or destination_key in copied:
            continue
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(file_path), str(destination_path))
        copied.add(destination_key)

    if unresolved:
        print(
            "Cargo Octane: unresolved USD dependencies: {0}".format(
                ", ".join(str(item) for item in unresolved)
            )
        )
    return str(localized_asset_path)


def prepare_received_asset(
    usd_file_path,
    copy_to_destination,
    destination=None,
    texture_variant=None,
):
    source_path = os.path.abspath(os.path.expandvars(str(usd_file_path)))
    if not copy_to_destination:
        return source_path
    if asset_is_in_project_directory(source_path, texture_variant):
        print(
            "Cargo Octane: asset is already inside the Houdini project; "
            "using source files without copying."
        )
        return source_path
    return localize_received_asset(source_path, destination, texture_variant)


def _asset_kind_and_record(usd_file_path):
    from pathlib import Path

    incoming_path = Path(os.path.abspath(os.path.expandvars(str(usd_file_path))))
    if not incoming_path.is_file():
        raise RuntimeError("Cargo USD file does not exist: {0}".format(incoming_path))
    if incoming_path.suffix.lower() not in CargoImporter.MATERIAL_USD_EXTENSIONS:
        raise RuntimeError("Cargo sent an unsupported file type: {0}".format(incoming_path))

    asset_folder = incoming_path.parent
    geo_path = asset_folder / "geo.usd"
    mtl_path = asset_folder / "mtl.usd"
    path_parts = [part.lower() for part in incoming_path.parts]
    is_model = (
        (geo_path.is_file() and mtl_path.is_file())
        or "models" in path_parts
        or incoming_path.name.lower() in ("geo.usd", "mtl.usd", "payload.usd")
    )

    if is_model:
        if not geo_path.is_file():
            raise RuntimeError(
                "Cargo model folder does not contain geo.usd: {0}".format(asset_folder)
            )
        kit, version = _path_metadata(incoming_path, "Models")
        return "model", {
            "name": asset_folder.name,
            "path": str(geo_path),
            "mtl_path": str(mtl_path),
            "kit": kit,
            "version": version,
            "folder": str(asset_folder),
        }

    kit, version = _path_metadata(incoming_path, "Materials")
    return "material", {
        "name": incoming_path.stem,
        "path": str(incoming_path),
        "kit": kit,
        "version": version,
    }


def import_received_asset(
    usd_file_path,
    texture_variant=None,
    displacement_settings=None,
    projection_mode=None,
):
    """Import one Cargo-delivered asset through CargoImporter without dialogs."""
    if hou is None:
        raise RuntimeError("Cargo live import must run inside Houdini.")

    asset_kind, record = _asset_kind_and_record(usd_file_path)
    preferred_variants = _preferred_variants(texture_variant)
    height_settings = displacement_settings or {
        "mode": DEFAULT_DISPLACEMENT_MODE,
        "height": DEFAULT_DISPLACEMENT_HEIGHT,
        "create_disconnected": DEFAULT_DISPLACEMENT_DISCONNECTED,
    }
    projection_mode = projection_mode or DEFAULT_PROJECTION_MODE

    if asset_kind == "material":
        target = hou.node("/mat")
        if target is None:
            target = hou.node("/").createNode("mat")
        material = CargoImporter.material_record_from_usd(record, preferred_variants)
        result = CargoImporter.create_octane_material(
            target,
            material,
            height_settings,
            projection_mode,
        )
        target.layoutChildren()
        print("Cargo Octane: created material {0}".format(result.path()))
        return result

    target = hou.node("/obj")
    if target is None:
        target = hou.node("/").createNode("obj")
    result = CargoImporter.create_model_import(
        record,
        target,
        0,
        height_settings,
        CargoImporter.PROJECTION_MODE_UV,
        preferred_variants,
        CargoImporter.next_right_position(target, fallback_y=0),
    )
    target.layoutChildren()
    print("Cargo Octane: imported model {0} in /obj".format(record["name"]))
    return result


class _ImportRequest(object):
    def __init__(self, content, connection):
        self.content = content
        self.connection = connection
        self.finished = threading.Event()
        self.error_message = None
        self.send_lock = threading.Lock()

    def finish(self, error_message=None):
        self.error_message = error_message
        self.finished.set()

    def send(self, message_type, content=None):
        # Progress originates on Houdini's UI thread while heartbeat/success
        # messages originate on the connection thread.
        with self.send_lock:
            _send_json(self.connection, message_type, content)


class _CargoServer(object):
    def __init__(self, port, identity):
        self.port = port
        self.identity = identity
        self.requests = queue.Queue()
        self.cancelled = threading.Event()
        self.ready = threading.Event()
        self.start_error = None
        self.listener_socket = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._serve, name="CargoOctaneServer")
        self.thread.daemon = True
        self.thread.start()
        if not self.ready.wait(SERVER_START_TIMEOUT):
            raise RuntimeError("Timed out while starting the Cargo listener.")
        if self.start_error:
            raise RuntimeError("Could not start the Cargo listener: {0}".format(self.start_error))

    def _serve(self):
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", self.port))
            self.port = listener.getsockname()[1]
            listener.listen()
            listener.settimeout(SOCKET_TIMEOUT)
            self.listener_socket = listener
        except Exception as error:
            self.start_error = error
            self.ready.set()
            return

        self.ready.set()
        while not self.cancelled.is_set():
            try:
                connection, _address = self.listener_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_connection, args=(connection,))
            thread.daemon = True
            thread.start()

    def _handle_connection(self, connection):
        try:
            connection.settimeout(None)
            message_format, message_type, raw_content = _receive_message(connection)
            if message_format != MESSAGE_FORMAT_JSON:
                raise RuntimeError("Cargo sent a non-JSON message.")

            if message_type == PLUGIN_HANDSHAKE_REQUEST:
                _send_json(connection, PLUGIN_HANDSHAKE_REPLY, self.identity)
                # Cargo's reference server terminates every successfully
                # handled request with a success envelope, including the
                # handshake. Cargo treats a connection that closes after only
                # the identity reply as a plugin communication failure (103).
                _send_json(connection, SERVER_SUCCESS_REPLY)
                return
            if message_type == SERVER_SHUTDOWN_REQUEST:
                self.cancelled.set()
                _send_json(connection, SERVER_SUCCESS_REPLY)
                return
            if message_type != ASSET_IMPORT_REQUEST:
                raise RuntimeError("No handler for Cargo message type {0}.".format(message_type))

            try:
                content = json.loads(raw_content.decode("utf-8")) if raw_content else {}
            except (UnicodeDecodeError, ValueError) as error:
                raise RuntimeError("Cargo sent invalid JSON: {0}".format(error))

            request = _ImportRequest(content, connection)
            self.requests.put(request)
            last_heartbeat = 0.0
            while not request.finished.wait(SOCKET_TIMEOUT) and not self.cancelled.is_set():
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    request.send(SERVER_HEARTBEAT_REPLY)
                    last_heartbeat = now

            if request.error_message:
                request.send(SERVER_ERROR_REPLY, {"errorMessage": request.error_message})
            else:
                request.send(SERVER_SUCCESS_REPLY)
        except Exception as error:
            try:
                _send_json(connection, SERVER_ERROR_REPLY, {"errorMessage": str(error)})
            except Exception:
                pass
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def stop(self):
        self.cancelled.set()
        while True:
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                break
            request.finish("Cargo listener stopped before the import completed.")
        if self.listener_socket is not None:
            try:
                self.listener_socket.close()
            except Exception:
                pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=SERVER_START_TIMEOUT)


def _cargo_port():
    from pathlib import Path

    if os.name == "nt":
        config_root = os.environ.get("APPDATA")
        if not config_root:
            raise RuntimeError("APPDATA is not set; Cargo's port configuration cannot be found.")
        config_path = Path(config_root) / "kitbash3d" / "dccPortsConfig.json"
    else:
        config_root = os.environ.get("HOME")
        if not config_root:
            raise RuntimeError("HOME is not set; Cargo's port configuration cannot be found.")
        config_path = Path(config_root) / ".kitbash3d" / "dccPortsConfig.json"

    try:
        with config_path.open("r") as handle:
            ports = json.load(handle)
        return int(ports["houdini"])
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError("Could not read Houdini's Cargo port from {0}: {1}".format(config_path, error))


class CargoOctaneListener(object):
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if CargoOctaneListener._instance is not None:
            raise RuntimeError("Use CargoOctaneListener.get_instance().")
        self._server = None
        self._callback_registered = False
        self._destination = destination_from_settings()
        self._copy_to_destination = copy_to_destination_from_settings()
        self._texture_resolution = texture_resolution_from_settings()
        self._displacement_settings = {
            "mode": displacement_mode_from_settings(),
            "height": displacement_height_from_settings(),
            "create_disconnected": displacement_disconnected_from_settings(),
        }
        self._projection_mode = projection_mode_from_settings()
        atexit.register(self.stop_listener)

    def start_listener(
        self,
        destination=None,
        texture_resolution=None,
        copy_to_destination=None,
    ):
        if hou is None or not hou.isUIAvailable():
            return False

        if destination is not None:
            self.set_destination(destination)
        if texture_resolution is not None:
            self.set_texture_resolution(texture_resolution)
        if copy_to_destination is not None:
            self.set_copy_to_destination(copy_to_destination)
        if self.is_running():
            return True
        if self._copy_to_destination:
            resolved_destination = resolve_destination(self._destination)
            if not resolved_destination:
                raise RuntimeError("Choose a Cargo destination before starting the listener.")
            if not os.path.isdir(resolved_destination):
                os.makedirs(resolved_destination)

        identity = {
            "dccName": "houdini",
            "dccProject": hou.hipFile.path(),
            "dccVersion": hou.applicationVersionString(),
            "pluginVersion": PLUGIN_VERSION,
        }
        server = _CargoServer(_cargo_port(), identity)
        server.start()
        self._server = server
        hou.ui.addEventLoopCallback(self._process_import_requests)
        self._callback_registered = True
        print("Cargo Octane: listening for Cargo imports on port {0}.".format(server.port))
        return True

    def stop_listener(self):
        if hou is not None and self._callback_registered:
            try:
                hou.ui.removeEventLoopCallback(self._process_import_requests)
            except Exception:
                pass
            self._callback_registered = False
        if self._server is not None:
            self._server.stop()
            self._server = None

    def _process_import_requests(self):
        if self._server is None:
            return
        while True:
            try:
                request = self._server.requests.get_nowait()
            except queue.Empty:
                break
            try:
                content = request.content
                usd_file_path = content["usdFilePath"]
                texture_variant = texture_variant_for_resolution(
                    content.get("textureVariant"),
                    self._texture_resolution,
                )
                progress_message = (
                    "Copying Cargo asset to the project"
                    if self._copy_to_destination
                    else "Using Cargo asset from its source location"
                )
                self._server_progress(request, 0.05, progress_message)
                localized_path = prepare_received_asset(
                    usd_file_path,
                    self._copy_to_destination,
                    self._destination,
                    texture_variant,
                )
                self._server_progress(request, 0.35, "Creating Octane asset in Houdini")
                import_received_asset(
                    localized_path,
                    texture_variant,
                    self._displacement_settings,
                    self._projection_mode,
                )
                self._server_progress(request, 1.0, "Octane asset created")
            except Exception as error:
                message = "Cargo Octane import failed: {0}".format(error)
                print(message)
                request.finish(message)
            else:
                request.finish()

    @staticmethod
    def _server_progress(request, progress, message):
        try:
            request.send(
                ASSET_IMPORT_PROGRESS_REPLY,
                {"progress": float(progress), "message": message},
            )
        except Exception:
            pass

    def is_running(self):
        return self._server is not None and not self._server.cancelled.is_set()

    def set_destination(self, destination):
        self._destination = save_destination(destination)
        return self._destination

    def destination(self):
        return self._destination

    def set_copy_to_destination(self, enabled):
        self._copy_to_destination = save_copy_to_destination(enabled)
        return self._copy_to_destination

    def copy_to_destination(self):
        return self._copy_to_destination

    def set_texture_resolution(self, resolution):
        self._texture_resolution = save_texture_resolution(resolution)
        return self._texture_resolution

    def texture_resolution(self):
        return self._texture_resolution

    def set_import_settings(
        self,
        displacement_mode,
        displacement_height,
        create_disconnected,
        projection_mode,
    ):
        settings = save_import_settings(
            displacement_mode,
            displacement_height,
            create_disconnected,
            projection_mode,
        )
        self._projection_mode = settings.pop("projection_mode")
        self._displacement_settings = settings

    def displacement_settings(self):
        return dict(self._displacement_settings)

    def projection_mode(self):
        return self._projection_mode


def start(destination=None, texture_resolution=None, copy_to_destination=None):
    return CargoOctaneListener.get_instance().start_listener(
        destination,
        texture_resolution,
        copy_to_destination,
    )


def stop():
    CargoOctaneListener.get_instance().stop_listener()
