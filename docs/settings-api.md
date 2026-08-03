# Settings API

Module: `scripts/python/openToolsUtils.py`

User settings are stored under the package root in `userSettings/`. This folder is ignored by git except for `.gitkeep`, so users can update tool scripts without overwriting local preferences.

## Layout

```text
userSettings/
  globalSettings.json
  CargoImporter/
    settings.json
  SomeFutureTool/
    settings.json
```

## Core API

```python
import openToolsUtils

openToolsUtils.createSettingsFile()
openToolsUtils.createSettingsFile("CargoImporter")

global_settings = openToolsUtils.getSettings()
cargo_settings = openToolsUtils.getSettings("CargoImporter")

openToolsUtils.setSetting("default_projection", "uv")
openToolsUtils.setSetting("root_directory", "X:/Resources/3d Assets/Cargo", "CargoImporter")

root = openToolsUtils.getSetting("root_directory", default=None, tool_name="CargoImporter")
```

## Convenience API

```python
openToolsUtils.getGlobalSetting("key", default=None)
openToolsUtils.setGlobalSetting("key", value)

openToolsUtils.getToolSetting("CargoImporter", "root_directory", default=None)
openToolsUtils.setToolSetting("CargoImporter", "root_directory", value)

openToolsUtils.getToolSettings("CargoImporter")
openToolsUtils.setToolSettings("CargoImporter", {"root_directory": value})
```

## Usage Guidance

Use global settings only for preferences shared across multiple tools. Use per-tool settings for importer-specific paths, options, cache state, and UI preferences.

Do not read or write JSON settings directly from importer scripts. Use this API so one tool does not overwrite another tool's data.
