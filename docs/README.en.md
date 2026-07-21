# Supercell's *.ui (TOML) files schema

[RU](/README.md)

Schema for validating UI TOML files from Supercell games.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Usage

You can use `https://ext.nulls.gg/mods/schema/ui.schema.json` as `$schema` in any JSON or TOML file.

### Locally

To use the local schema, complete all steps from the [Development](#development) section and specify the full path to `ui.schema.json` in `$schema` using a [file:/// URI](https://en.wikipedia.org/wiki/File_URI_scheme) in any JSON or TOML file.

## Validation

> [!NOTE]
> Currently, two versions of the validator exist in parallel — one in Python 3, the other in Rust. The Python 3 version is documented here.

A special pipeline is used to validate UI TOML files, as [taplo](https://taplo.tamasfe.dev/) does not support modern JSON Schema standards.

### How it works

The validation pipeline is implemented in the [`validate.py`](/validate.py) script and consists of the following steps:

1. Reading `.ui` files — the script recursively finds all files with the `.ui` extension (TOML format).
2. TOML -> JSON conversion — using Python's built-in `tomllib` module, TOML files are converted to JSON.
3. Adding `$schema` — the converted JSON automatically gets a `$schema` field pointing to the local schema (`src/ui.schema.json`).
4. Validation — the resulting JSON file is validated using [`jsonschema`](https://github.com/sourcemeta/jsonschema), a CLI tool from Sourcemeta that correctly handles modern JSON Schema standards, unlike taplo.
5. If validation succeeds, the temporary `.json` file is deleted. If it fails, you can open it with any editor that supports JSON Schema to see the error. The error text is also duplicated in the console.

### Running

```sh
python3 validate.py  # or
cargo run  # or
make validate
```

The script will process all `.ui` files in the current directory and output any validation errors, if present.

> [!NOTE] 
> The `jsonschema` CLI must be installed separately. Install it via `npm install -g @sourcemeta/jsonschema`.

## Semantic Validation

In addition to validating the file structure against JSON Schema, semantic validation is performed — checking the logical correctness of links and dependencies between files.

### What is checked

- **BindingId** — all references to bindingId are resolved within the context of the current file:
  - Direct bindings from the `bindings` section
  - Bindings from files specified in `copy_configs`
  - Bindings from files with the same `sc_file_asset_id_list` (AssetIdList source)
  - Bindings from files referenced by `sc_file` (OtherTomlConfig source)

- **AnimationKey** — all animation references are validated:
  - Direct animations from `animation` or `animations` sections
  - Animations from files specified in `copy_configs`

- **Cross-file references** — a registry of all loaded `.ui` files is used to resolve links between files without re-parsing.

> [!TIP]
> To run only semantic validation, skip JSON Schema validation:
> ```sh
> python3 validate.py --skip-schema-validation  # or
> python3 validate.py -s
> ```
> 
> ```sh
> cargo run -- --skip-schema-validation  # or
> cargo run -- -s
> ```

## Development

```sh
git clone https://github.com/danila-schelkov/sc-ui-schema
cd sc-ui-schema
```

### Publishing

To publish, you can build a minified version of the schema.

```sh
python3 build.py  # or
make
```

After running the script, the `build/` folder will contain the ready-to-use schema bundle.

## TODO

- [ ] Load `.sc` files for `ClientFile` — when using `sc_file_source = "ClientFile"`, corresponding `.sc` files need to be loaded and parsed for full validation.
- [ ] Validate clip frame references — check the correctness of clip frame references in child references.
- [ ] Resolve bindings from `.sc` files — extract and validate bindings defined in `.sc` files for complete semantic checks.

## About the schema

We use Draft 2020-12 as the language for describing JSON Schema, but since the JSON Schema Validator in VS Code does not support versions higher than draft-07, it likely will not work correctly.

More information about the JSON Schema specification is available at: https://json-schema.org/specification

## License

This project is distributed under the MIT License ([LICENSE](/LICENSE) or https://opensource.org/licenses/MIT).  

## Disclaimer

This JSON schema is an independent, community-driven project and is not affiliated with, endorsed, sponsored, or specifically approved by Supercell. Supercell is not responsible for it. This tool is intended for educational and fan development purposes only.
