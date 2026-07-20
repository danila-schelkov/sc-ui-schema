#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["click"]
# ///

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import click

SCHEMA = Path("src/ui.schema.json")


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
    root: dict, registry: FileRegistry | None = None
) -> dict[str, Any]:
    """Resolve all bindings for a root .ui file.

    This collects bindings from:
    1. The root file's own 'bindings' section
    2. All files referenced via 'copy_configs'

    Returns a merged dict of all binding IDs -> values.
    """
    if registry is None:
        registry = _registry

    collected: dict[str, Any] = {}

    # 1. Direct bindings from the root file
    root_bindings = root.get("bindings")
    if root_bindings:
        collected.update(root_bindings)

    # 2. Walk copy_configs references
    copy_configs = root.get("copy_configs")
    if copy_configs:
        configs = copy_configs if isinstance(copy_configs, list) else [copy_configs]
        for config_id in configs:
            config_file = registry.get(config_id)
            if config_file is None:
                continue
            config_bindings = config_file.get("bindings")
            if config_bindings:
                collected.update(config_bindings)
            # Recurse into nested copy_configs (in case configs reference configs)
            _resolve_bindings_for_file(config_file, registry)

    return collected


_verbosity = 0


def _validate_binding_ref(
    node: BindingId, root: dict, path: str, errors: list[str]
) -> None:
    """Validate that a bindingId value (string) exists in root's bindings.

    When node is a string (direct bindingId ref), use it directly.
    When node is a dict (binding wrapper), extract the binding property value.

    Also checks bindings resolved from copy_configs references.
    """
    binding_id = node

    # Resolve all bindings (direct + copy_configs)
    # TODO: pass use registry from outside
    bindings = _resolve_bindings_for_file(root)
    if not bindings:
        errors.append(
            f"{path}: bindingId '{binding_id}' references, but no 'bindings' section exists in root"
        )
        return
    if binding_id not in bindings:
        error_message = f"{path}: bindingId '{binding_id}' not found in root 'bindings'"
        if _verbosity >= 1:
            error_message += f" (available: {list(bindings.keys())})"
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
    seen_refs: set[str] | None = None,
) -> None:
    """Recursively walk the schema tree, applying semantic validators
    whenever a $ref to a registered binding definition is encountered."""
    if seen_refs is None:
        seen_refs = set()

    # Handle arrays at the root level (e.g. set_text, move, replace)
    if isinstance(node, list) and "items" in schema_node:
        for i, item in enumerate(node):
            item_path = f"{path}[{i}]" if path else f"[{i}]"
            _walk_schema_and_validate(
                item,
                schema_node["items"],
                root,
                item_path,
                errors,
                schema_definitions,
                seen_refs.copy(),
            )

    # Resolve $ref — call semantic validator if registered, then
    # always recurse into the referenced definition's properties.
    # Must come BEFORE the dict check since bindingId refs have string nodes.
    if "$ref" in schema_node:
        ref = schema_node["$ref"]
        if ref.startswith("#/definitions/"):
            def_name = ref.split("/")[-1]
            referenced = schema_definitions.get(def_name, {})
            if ref in _semantic_validators and ref not in seen_refs:
                seen_refs.add(ref)
                validator = _semantic_validators[ref]
                validator(node, root, path, errors)
            _walk_schema_and_validate(
                node,
                referenced,
                root,
                path,
                errors,
                schema_definitions,
                seen_refs.copy(),
            )
            return  # Resolved, skip further processing of $ref schema itself

    if not isinstance(node, dict):
        return

    # Recurse into properties / additionalProperties / items / allOf / oneOf
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
                    seen_refs.copy(),
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
                seen_refs.copy(),
            )

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
                seen_refs.copy(),
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
                        seen_refs.copy(),
                    )
                    continue
            _walk_schema_and_validate(
                node,
                sub_schema,
                root,
                path,
                errors,
                schema_definitions,
                seen_refs.copy(),
            )

    if "oneOf" in schema_node:
        for sub_schema in schema_node["oneOf"]:
            _walk_schema_and_validate(
                node,
                sub_schema,
                root,
                path,
                errors,
                schema_definitions,
                seen_refs.copy(),
            )

    # Recurse into nested objects regardless
    if isinstance(node, dict):
        for key, child in node.items():
            if key.startswith("$"):
                continue
            child_path = f"{path}.{key}" if path else key
            _walk_schema_and_validate(
                child,
                {},
                root,
                child_path,
                errors,
                schema_definitions,
                seen_refs.copy(),
            )


def validate_semantics(root: dict, registry: FileRegistry | None = None) -> list[str]:
    """Run semantic validation on the root data against the schema.

    Uses the global registry to resolve copy_configs references.

    Returns a list of error strings (empty if all OK).
    """
    errors: list[str] = []
    schema = _load_schema()
    definitions = schema.get("definitions", {})
    # The root schema itself may have allOf/properties at the top level.
    # We need to walk the schema's root properties against the root data,
    # NOT the schema's allOf (which describes the schema itself).
    root_schema_props = schema.get("properties", {})
    for prop_name, prop_schema in root_schema_props.items():
        data_val = root.get(prop_name)
        if data_val is not None:
            child_path = prop_name
            _walk_schema_and_validate(
                data_val, prop_schema, root, child_path, errors, definitions
            )
    return errors


def _load_schema() -> dict:
    """Load and return the JSON schema."""
    with SCHEMA.open("r", encoding="utf-8") as f:
        return json.load(f)


@click.command()
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="Increase output detail. 0: errors only. 1: bindings in errors. 2: full output.",
)
def main(verbose: int) -> None:
    global _verbosity
    _verbosity = verbose
    exit_code = 0

    # Phase 1: Register all .ui files in the registry
    ui_files = sorted(Path(".").rglob("*.ui"))
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

            result = validate(json_file)
            if result != 0:
                exit_code = result
                click.echo(f"  Schema validation failed: {ui_file}")
            else:
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
