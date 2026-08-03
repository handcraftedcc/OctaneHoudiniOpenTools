# Octane Houdini Open Tools Docs

This folder documents shared APIs intended for importer and shelf-tool code.

## Shared Utilities

- [Settings API](settings-api.md): persistent per-user settings stored outside tool scripts.
- [Material API](material-api.md): unified Octane material, texture, projection, and displacement creation.
- [Cargo Live Import](cargo-live-import.md): receive Cargo assets directly into Octane `/mat` or `/obj` networks.
- [Megascans Importer](megascans-importer.md): browse and import preview-backed Megascans materials and assets.

## Design Rule

Tool scripts should stay mostly stateless and safe to overwrite during updates.
User-specific data belongs under `userSettings/`, accessed through `openToolsUtils.py`.
Common Houdini/Octane node creation belongs in shared utility modules instead of being duplicated in each importer.
