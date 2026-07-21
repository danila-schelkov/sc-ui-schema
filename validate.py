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


def _resolve_bindings_for_file(
    root: dict, registry: FileRegistry, allow_asset_id_list: bool = True
) -> set[BindingId]:
    """Resolve all bindings for a root .ui file.

    This collects bindings from:
    1. The root file's own 'bindings' section
    2. All files referenced via 'copy_configs'
    3. All files sharing the same 'sc_file_asset_id_list' (AssetIdList source)

    Returns a merged set of all binding IDs.
    """

    collected: set[BindingId] = set()

    # An empty string path — reference to self
    collected.add("")

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

type AnimationKey = str


def _resolve_animations_for_file(
    root: dict, _registry: FileRegistry | None = None
) -> set[AnimationKey]:
    """Resolve all animations for a root .ui file.

    This collects bindings from:
    1. The root file's "animations" or "animation" section

    Returns a merged set of all animation keys.
    """

    collected: set[BindingId] = set()
    root_animations: dict[BindingId, Any] | None = root.get(
        "animation", root.get("animations")
    )
    if root_animations:
        collected.update(root_animations.keys())

    return collected


def _validate_animation_ref(
    node: AnimationKey, root: dict, path: str, errors: list[str], registry: FileRegistry
) -> None:
    """Validate that a bindingId value (string) exists in root's bindings.

    When node is a string (direct bindingId ref), use it directly.
    When node is a dict (binding wrapper), extract the binding property value.

    Also checks bindings resolved from copy_configs references.
    """
    animation_key = node

    # Resolve all bindings (direct + copy_configs)
    animations = _resolve_animations_for_file(root, registry)
    if not animations:
        errors.append(
            f"{path}: animationKey '{animation_key}' references, but no 'animations' section exists in root"
        )
        return
    if animation_key not in animations:
        error_message = f"{path}: bindingId '{animation_key}' not found in 'animations'"
        if _verbosity >= 1:
            error_message += f" (available: {animations})"
        errors.append(error_message)


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
    "#/definitions/animationKey": _validate_animation_ref,
}


def _is_valid_type(node: Any, type: str | None) -> bool:
    match type:
        case "integer":
            return isinstance(node, int)
        case "number":
            # Should also check for int?
            return isinstance(node, float) or isinstance(node, int)
        case "boolean":
            return isinstance(node, bool)
        case "string":
            return isinstance(node, str)
        case "object":
            return isinstance(node, dict)
        case "array":
            return isinstance(node, list)
        case [*types]:
            return any(_is_valid_type(node, type) for type in types)
        case None:
            pass
        case unexpected_type:
            click.echo(
                f"Got an unexpected type: {unexpected_type}",
                err=True,
            )

    return True


def _walk_schema_and_validate(
    node: Any,
    schema_node: dict,
    root: dict,
    path: str,
    errors: list[str],
    schema_definitions: dict[str, dict],
    registry: FileRegistry | None = None,
) -> bool:
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

            # finish oneOf branching if wrong type.
            referenced_type = referenced.get("type")
            if not _is_valid_type(node, referenced_type):
                return False

            if ref in _semantic_validators:
                validator = _semantic_validators[ref]
                validator(node, root, path, errors, registry)
            # Resolved, skip further processing of $ref schema itself
            return _walk_schema_and_validate(
                node,
                referenced,
                root,
                path,
                errors,
                schema_definitions,
                registry,
            )

    if "items" in schema_node:
        items = node if isinstance(node, list) else [node]

        for i, item in enumerate(items):
            item_path = f"{path}[{i}]" if path else f"[{i}]"
            if not _walk_schema_and_validate(
                item,
                schema_node["items"],
                root,
                item_path,
                errors,
                schema_definitions,
                registry,
            ):
                return False

    if "oneOf" in schema_node:
        for sub_schema in schema_node["oneOf"]:
            sub_schema_type = sub_schema.get("type")
            if not _is_valid_type(node, sub_schema_type):
                continue

            one_of_errors: list[str] = []
            result = _walk_schema_and_validate(
                node,
                sub_schema,
                root,
                path,
                one_of_errors,
                schema_definitions,
                registry,
            )
            if result:
                errors.extend(one_of_errors)
                break

    # NOTE: values themselves
    if not isinstance(node, dict):
        return True

    properties: dict[str, Any] | None = schema_node.get("properties")
    if properties is not None:
        for prop_name, prop_schema in properties.items():
            child = node.get(prop_name)
            if child is not None:
                child_path = f"{path}.{prop_name}" if path else prop_name
                if not _walk_schema_and_validate(
                    child,
                    prop_schema,
                    root,
                    child_path,
                    errors,
                    schema_definitions,
                    registry,
                ):
                    return False

    if "additionalProperties" in schema_node:
        additional_properties_schema = schema_node["additionalProperties"]

        for key, child in node.items():
            if key.startswith("$"):
                continue

            # TODO: handle key in any of anyOf, or even maintain evaluated_properties keys
            if properties is not None and key in properties:
                continue

            child_path = f"{path}.{key}" if path else key
            if not _walk_schema_and_validate(
                child,
                additional_properties_schema,
                root,
                child_path,
                errors,
                schema_definitions,
                registry,
            ):
                return False

    if "patternProperties" in schema_node:
        for pattern_str, properties_schema in schema_node["patternProperties"].items():
            pattern = re.compile(pattern_str)
            for key, child in node.items():
                if not pattern.match(key):
                    continue

                child_path = f"{path}.{key}" if path else key
                if not _walk_schema_and_validate(
                    child,
                    properties_schema,
                    root,
                    child_path,
                    errors,
                    schema_definitions,
                    registry,
                ):
                    return False

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
                    if not _walk_schema_and_validate(
                        sub_node,
                        sub_schema,
                        root,
                        path,
                        errors,
                        schema_definitions,
                        registry,
                    ):
                        return False
                    continue
            if not _walk_schema_and_validate(
                node,
                sub_schema,
                root,
                path,
                errors,
                schema_definitions,
                registry,
            ):
                return False

    # TODO: handle unevaluatedProperties
    return True


def validate_semantics(root: dict, registry: FileRegistry | None = None) -> list[str]:
    """Run semantic validation on the root data against the schema.

    Uses the global registry to resolve copy_configs references.

    Returns a list of error strings (empty if all OK).
    """
    errors: list[str] = []
    schema = _load_schema()
    definitions = schema.get("definitions", {})
    if not _walk_schema_and_validate(
        root,
        schema,
        root,
        "",
        errors,
        definitions,
        registry,
    ):
        # This kind of situation should not happen
        click.echo("Something went wrong...", err=True)
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

    # Global registry, shared across all semantic validation runs
    registry = FileRegistry()

    # Phase 1: Register all .ui files in the registry
    for ui_file in ui_files:
        try:
            registry.register_from_path(ui_file)
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
                semantic_errors = validate_semantics(data, registry)
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