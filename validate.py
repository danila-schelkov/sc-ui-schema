#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["click"]
# ///

import json
import re
import subprocess
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import click

SCHEMA = Path("src/ui.schema.json").absolute()


def validate(json_file: Path) -> int:
    return subprocess.run(
        [
            "jsonschema",
            "validate",
            str(SCHEMA),
            str(json_file),
        ]
    ).returncode


type BindingId = str


class FileRegistry:
    """Registry of all loaded .ui files, keyed by their 'id'.

    Used during semantic validation to resolve cross-file references
    (e.g., copy_configs) without re-parsing files.
    """

    def __init__(self) -> None:
        self._files: dict[str, dict] = {}

    def register(self, file_id: str, data: dict) -> None:
        """Register a parsed .ui file by its 'id'."""
        self._files[file_id] = data

    def get(self, file_id: str) -> dict | None:
        """Retrieve a registered file by its 'id'."""
        return self._files.get(file_id)

    def register_from_path(self, path: Path) -> None:
        """Load a .ui file from disk and register it by its 'id'."""
        with path.open("rb") as f:
            data = tomllib.load(f)
        file_id = data.get("id")
        if file_id:
            self.register(file_id, data)


# Global registry, shared across all semantic validation runs
_registry = FileRegistry()


def _resolve_bindings_for_file(
    root: dict, registry: FileRegistry | None = None, allow_asset_id_list: bool = True
) -> set[BindingId]:
    """Resolve all bindings for a root .ui file.

    This collects bindings from:
    1. The root file's own 'bindings' section
    2. All files referenced via 'copy_configs'
    3. All files sharing the same 'sc_file_asset_id_list' (AssetIdList source)

    Returns a merged dict of all binding IDs -> values.
    """
    if registry is None:
        registry = _registry

    collected: set[BindingId] = set()

    # 1.1 Direct bindings from the root file
    root_bindings: dict[BindingId, Any] | None = root.get("bindings")
    if root_bindings:
        collected.update(root_bindings.keys())

    # 1.2 Direct button bindings from the root file
    root_buttons: dict[BindingId, Any] | None = root.get("buttons")
    if root_buttons:
        collected.update(root_buttons.keys())

    # 2. Walk copy_configs references
    copy_configs = root.get("copy_configs")
    if copy_configs:
        configs = copy_configs if isinstance(copy_configs, list) else [copy_configs]
        for config_id in configs:
            config_file = registry.get(config_id)
            if config_file is None:
                continue
            collected.update(
                _resolve_bindings_for_file(
                    config_file,
                    registry,
                    allow_asset_id_list,  # Should we allow it?
                )
            )

    # 3. AssetIdList-based binding resolution
    # If this file has sc_file_source == 'AssetIdList', find all files
    # sharing the same sc_file_asset_id_list and collect their bindings.
    file_source = root.get("sc_file_source")
    if file_source == "AssetIdList" and allow_asset_id_list:
        asset_id_list = root.get("sc_file_asset_id_list")
        if asset_id_list:
            # Collect bindings from all files in the same asset_id_list
            for file_id, file_data in registry._files.items():
                # Skip the root file itself (its bindings already collected)
                if file_id == root.get("id"):
                    continue
                # Check if this file belongs to the same asset_id_list
                other_source = file_data.get("sc_file_source")
                other_asset_list = file_data.get("sc_file_asset_id_list")
                if other_source == "AssetIdList" and other_asset_list == asset_id_list:
                    collected.update(
                        _resolve_bindings_for_file(
                            file_data, registry, allow_asset_id_list=False
                        )
                    )

    # 4. OtherTomlConfig-based binding resolution
    # If this file has sc_file_source == 'OtherTomlConfig', the sc_file
    # field references another .ui file by its ID. Collect bindings from
    # that referenced file (recursively).
    if file_source == "OtherTomlConfig":
        sc_file = root.get("sc_file")
        if sc_file:
            referenced_file = registry.get(sc_file)
            if referenced_file is not None:
                collected.update(
                    _resolve_bindings_for_file(
                        referenced_file,
                        registry,
                        allow_asset_id_list,  # Should we allow it?
                    )
                )
            else:
                # Referenced file not found in registry
                click.echo(
                    f"Warning: {root.get('id', '<unknown>')}: "
                    f"OtherTomlConfig references '{sc_file}' which was not found in registry",
                    err=True,
                )

    return collected


_verbosity = 0


def _validate_binding_ref(
    node: BindingId, root: dict, path: str, errors: list[str], registry: FileRegistry
) -> None:
    """Validate that a bindingId value (string) exists in root's bindings.

    When node is a string (direct bindingId ref), use it directly.
    When node is a dict (binding wrapper), extract the binding property value.

    Also checks bindings resolved from copy_configs references.
    """
    binding_id = node

    # Resolve all bindings (direct + copy_configs)
    bindings = _resolve_bindings_for_file(root, registry)
    if not bindings:
        errors.append(
            f"{path}: bindingId '{binding_id}' references, but no 'bindings' section exists in root"
        )
        return
    if binding_id not in bindings:
        error_message = f"{path}: bindingId '{binding_id}' not found in 'bindings'"
        if _verbosity >= 1:
            error_message += f" (available: {bindings})"
        errors.append(error_message)


def _register_semantic_validator(ref_key: str, validator_fn) -> None:
    """Register a binding validator for a given schema definition ref key.

    Call this from your own code to extend semantic validation for new
    bindingRef-style definitions without touching the core validator.

    Example::

        register_binding_validator("#/definitions/myBindingRef", my_validator_fn)
    """
    _semantic_validators[ref_key] = validator_fn


# First argument type must always be the same as definition type
_semantic_validators: dict[str, Any] = {
    "#/definitions/bindingId": _validate_binding_ref,
}


def _walk_schema_and_validate(
    node: Any,
    schema_node: dict,
    root: dict,
    path: str,
    errors: list[str],
    schema_definitions: dict[str, dict],
    registry: FileRegistry | None = None,
) -> None:
    """Recursively walk the schema tree, applying semantic validators
    whenever a $ref to a registered binding definition is encountered."""

    # Resolve $ref — call semantic validator if registered, then
    # always recurse into the referenced definition's properties.
    # Must come BEFORE the dict check since bindingId refs have string nodes.
    if "$ref" in schema_node:
        ref = schema_node["$ref"]
        if ref.startswith("#/definitions/"):
            def_name = ref.split("/")[-1]
            referenced = schema_definitions.get(def_name, {})
            if ref in _semantic_validators:
                validator = _semantic_validators[ref]
                validator(node, root, path, errors, registry)
            _walk_schema_and_validate(
                node,
                referenced,
                root,
                path,
                errors,
                schema_definitions,
                registry,
            )
            return  # Resolved, skip further processing of $ref schema itself

    # if path.endswith("uninteractive"):
    #     raise Exception

    if "items" in schema_node:
        items = node if isinstance(node, list) else [node]
        for i, item in enumerate(items):
            item_path = f"{path}[{i}]" if path else f"[{i}]"
            _walk_schema_and_validate(
                item,
                schema_node["items"],
                root,
                item_path,
                errors,
                schema_definitions,
                registry,
            )

    if "oneOf" in schema_node:
        for sub_schema in schema_node["oneOf"]:
            # NOTE: it is temporary solution, am I right?

            # Resolve $ref to check type compatibility with node.
            # This prevents e.g. calling bindingId validator with a dict node
            # when childReferenceOrId oneOf contains both childReference and bindingId.
            target_type = None
            if "$ref" in sub_schema:
                ref = sub_schema["$ref"]
                if ref.startswith("#/definitions/"):
                    def_name = ref.split("/")[-1]
                    target_type = schema_definitions.get(def_name, {}).get("type")
            else:
                target_type = sub_schema.get("type")

            # Skip oneOf branches that don't match the node's actual type.
            if isinstance(node, dict) and target_type == "string":
                continue
            if not isinstance(node, dict) and target_type == "object":
                continue

            _walk_schema_and_validate(
                node,
                sub_schema,
                root,
                path,
                errors,
                schema_definitions,
                registry,
            )

    if not isinstance(node, dict):
        return

    if "properties" in schema_node:
        for prop_name, prop_schema in schema_node["properties"].items():
            child = node.get(prop_name)
            if child is not None:
                child_path = f"{path}.{prop_name}" if path else prop_name
                _walk_schema_and_validate(
                    child,
                    prop_schema,
                    root,
                    child_path,
                    errors,
                    schema_definitions,
                    registry,
                )

    if "additionalProperties" in schema_node:
        for key, child in node.items():
            if key.startswith("$"):
                continue
            child_path = f"{path}.{key}" if path else key
            _walk_schema_and_validate(
                child,
                schema_node["additionalProperties"],
                root,
                child_path,
                errors,
                schema_definitions,
                registry,
            )

    if "patternProperties" in schema_node:
        for pattern_str, properties_schema in schema_node["patternProperties"].items():
            pattern = re.compile(pattern_str)
            for key, child in node.items():
                if not pattern.match(key):
                    continue

                child_path = f"{path}.{key}" if path else key
                _walk_schema_and_validate(
                    child,
                    properties_schema,
                    root,
                    child_path,
                    errors,
                    schema_definitions,
                    registry,
                )

    if "allOf" in schema_node:
        for sub_schema in schema_node["allOf"]:
            # For non-$ref subschemas, extract the matching property value
            # and recurse with that for type-based validation.
            sub_prop = sub_schema.get("properties")
            if sub_prop:
                sub_node = None
                for sub_name in sub_prop:
                    val = node.get(sub_name)
                    if val is not None:
                        sub_node = val
                        break
                if sub_node is not None:
                    _walk_schema_and_validate(
                        sub_node,
                        sub_schema,
                        root,
                        path,
                        errors,
                        schema_definitions,
                        registry,
                    )
                    continue
            _walk_schema_and_validate(
                node,
                sub_schema,
                root,
                path,
                errors,
                schema_definitions,
                registry,
            )

    # TODO: handle unevaluatedProperties


def validate_semantics(root: dict, registry: FileRegistry | None = None) -> list[str]:
    """Run semantic validation on the root data against the schema.

    Uses the global registry to resolve copy_configs references.

    Returns a list of error strings (empty if all OK).
    """
    errors: list[str] = []
    schema = _load_schema()
    definitions = schema.get("definitions", {})
    _walk_schema_and_validate(
        root,
        schema,
        root,
        "",
        errors,
        definitions,
        registry,
    )
    return errors


def _load_schema() -> dict:
    """Load and return the JSON schema."""
    with SCHEMA.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_ui_files(paths: Iterable[Path]) -> list[Path]:
    """Find all .ui files from the given paths.

    Each path can be a file or directory. Directories are walked recursively.
    """
    ui_files: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path

        if resolved.is_file():
            if resolved.suffix == ".ui":
                ui_files.append(resolved)
            else:
                click.echo(
                    f"Warning: {resolved} is not a .ui file, skipping.",
                    err=True,
                )
        elif resolved.is_dir():
            ui_files.extend(resolved.rglob("*.ui"))
        else:
            click.echo(f"Warning: {path} does not exist, skipping.", err=True)

    return sorted(ui_files)


@click.command()
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="Increase output detail. 0: errors only. 1: bindings in errors. 2: full output.",
)
@click.option(
    "-s",
    "--skip-schema-validation",
    is_flag=True,
    help="Skip JSON schema validation and only perform semantic validation.",
)
@click.argument(
    "paths",
    type=click.Path(exists=False),
    required=False,
    nargs=-1,
)
def main(verbose: int, paths: tuple[str, ...], skip_schema_validation: bool) -> None:
    global _verbosity
    _verbosity = verbose
    exit_code = 0

    ui_files = find_ui_files((Path(p) for p in paths) if paths else (Path("."),))

    # Phase 1: Register all .ui files in the registry
    for ui_file in ui_files:
        try:
            _registry.register_from_path(ui_file)
        except Exception as e:
            click.echo(f"Warning: Could not register {ui_file}: {e}", err=True)

    # Phase 2: Validate each file
    for ui_file in ui_files:
        json_file = ui_file.with_suffix(".json")

        try:
            with ui_file.open("rb") as f:
                data = tomllib.load(f)

            with json_file.open("w", encoding="utf-8") as f:
                data["$schema"] = SCHEMA.relative_to(
                    json_file.parent, walk_up=True
                ).as_posix()
                json.dump(data, f, indent=4, ensure_ascii=False)

            if verbose >= 2:
                click.echo(f"Validating {ui_file}...")

            # Schema validation (skipped if --skip-schema-validation is set)
            schema_valid = True
            if not skip_schema_validation:
                result = validate(json_file)
                if result != 0:
                    exit_code = result
                    click.echo(f"  Schema validation failed: {ui_file}")
                    schema_valid = False
                elif verbose >= 1:
                    click.echo(f"  Schema validation passed: {ui_file}")

            if schema_valid:
                # Semantic validation
                semantic_errors = validate_semantics(data, _registry)
                if semantic_errors:
                    click.echo(f"  Semantic errors in {ui_file}:")
                    for err in semantic_errors:
                        click.echo(f"    - {err}")
                    exit_code = 1

                if verbose >= 2:
                    click.echo(f"  {ui_file} OK")

                json_file.unlink(missing_ok=True)
        except Exception as e:
            click.echo(f"Error occurred while loading file: {ui_file}: {e}", err=True)
            exit_code = 1

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()