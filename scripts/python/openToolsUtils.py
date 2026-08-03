"""
Shared utility API for Octane Houdini Open Tools.

User-editable state lives under userSettings/ so script files can be replaced
during tool updates without overwriting local user configuration.
"""

from __future__ import print_function

import json
import os
import re


USER_SETTINGS_DIRNAME = "userSettings"
GLOBAL_SETTINGS_FILENAME = "globalSettings.json"
TOOL_SETTINGS_FILENAME = "settings.json"


def packageRoot():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))


def settingsDirectory():
    return os.path.join(packageRoot(), USER_SETTINGS_DIRNAME)


def _safeToolName(tool_name):
    if not tool_name:
        raise ValueError("tool_name is required for tool settings.")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tool_name)).strip("._") or "tool"


def _settingsFilePath(tool_name=None):
    if tool_name is None:
        return os.path.join(settingsDirectory(), GLOBAL_SETTINGS_FILENAME)
    return os.path.join(settingsDirectory(), _safeToolName(tool_name), TOOL_SETTINGS_FILENAME)


def createSettingsFile(tool_name=None):
    path = _settingsFilePath(tool_name)
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)
    if not os.path.exists(path):
        _writeSettingsFile(path, {})
    return path


def _readSettingsFile(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as handle:
            settings = json.load(handle)
    except Exception:
        return {}
    return settings if isinstance(settings, dict) else {}


def _writeSettingsFile(path, settings):
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)
    with open(path, "w") as handle:
        json.dump(settings, handle, indent=4, sort_keys=True)
        handle.write("\n")


def getSettings(tool_name=None):
    path = createSettingsFile(tool_name)
    return _readSettingsFile(path)


def setSettings(settings, tool_name=None):
    if not isinstance(settings, dict):
        raise ValueError("settings must be a dictionary.")
    path = createSettingsFile(tool_name)
    _writeSettingsFile(path, settings)
    return settings


def getSetting(key, default=None, tool_name=None):
    return getSettings(tool_name).get(key, default)


def setSetting(key, value, tool_name=None):
    settings = getSettings(tool_name)
    settings[key] = value
    setSettings(settings, tool_name)
    return value


def getGlobalSettings():
    return getSettings()


def setGlobalSettings(settings):
    return setSettings(settings)


def getGlobalSetting(key, default=None):
    return getSetting(key, default)


def setGlobalSetting(key, value):
    return setSetting(key, value)


def getToolSettings(tool_name):
    return getSettings(tool_name)


def setToolSettings(tool_name, settings):
    return setSettings(settings, tool_name)


def getToolSetting(tool_name, key, default=None):
    return getSetting(key, default, tool_name)


def setToolSetting(tool_name, key, value):
    return setSetting(key, value, tool_name)
