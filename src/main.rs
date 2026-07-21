use clap::Parser;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus};
use std::sync::OnceLock;
use walkdir::WalkDir;

/// Global verbosity level for semantic validation.
static VERBOSE: OnceLock<u8> = OnceLock::new();

/// Semantic validator for Supercell's .ui (TOML) files
#[derive(Parser, Debug)]
#[command(version, about)]
struct Cli {
    /// Increase output detail. 0: errors only. 1: bindings in errors. 2: full output.
    #[arg(short, long, action = clap::ArgAction::Count)]
    verbose: u8,
    /// Skip JSON schema validation and only perform semantic validation.
    #[arg(short, long)]
    skip_schema_validation: bool,
}

#[derive(thiserror::Error, Debug)]
enum Error {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON parse error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("TOML parse error: {0}")]
    Toml(#[from] toml::de::Error),
    #[error("Schema file not found: {0}")]
    SchemaNotFound(PathBuf),
}

type Result<T, E = Error> = std::result::Result<T, E>;

type BindingId = String;

/// Registry of all loaded .ui files, keyed by their `id`.
/// Used during semantic validation to resolve cross-file references
/// (e.g., copy_configs) without re-parsing files.
#[derive(Debug, Default)]
struct FileRegistry {
    files: HashMap<String, Value>,
}

impl FileRegistry {
    fn register(&mut self, file_id: BindingId, data: Value) {
        self.files.insert(file_id, data);
    }

    fn get(&self, file_id: &str) -> Option<&Value> {
        self.files.get(file_id)
    }

    fn register_from_path(&mut self, path: &Path) -> Result<()> {
        let bytes = std::fs::read(path)?;
        let data: Value = toml::from_slice(&bytes)?;
        let id = data.get("id").and_then(|v| v.as_str()).map(String::from);
        if let Some(id) = id {
            self.register(id, data);
        }
        Ok(())
    }

    fn files(&self) -> impl Iterator<Item = (&String, &Value)> {
        self.files.iter()
    }
}

/// Resolve all bindings for a root .ui file.
///
/// This collects bindings from:
/// 1. The root file's own `bindings` section
/// 2. All files referenced via `copy_configs`
/// 3. All files sharing the same `sc_file_asset_id_list` (AssetIdList source)
fn resolve_bindings_for_file(
    root: &Value,
    registry: &FileRegistry,
    allow_asset_id_list: bool,
) -> HashSet<BindingId> {
    let mut collected: HashSet<BindingId> = HashSet::new();

    // Direct bindings from the root file
    if let Some(bindings) = root.get("bindings").and_then(|v| v.as_object()) {
        collected.extend(bindings.keys().cloned());
    }

    // Direct button bindings from the root file
    if let Some(buttons) = root.get("buttons").and_then(|v| v.as_object()) {
        collected.extend(buttons.keys().cloned());
    }

    // Walk copy_configs references
    if let Some(copy_configs) = root.get("copy_configs") {
        let configs = copy_configs
            .as_array()
            .cloned()
            .unwrap_or_else(|| vec![copy_configs.clone()]);
        for config_id in configs {
            if let Some(id) = config_id.as_str() {
                if let Some(config_file) = registry.get(id) {
                    collected.extend(resolve_bindings_for_file(
                        config_file,
                        registry,
                        allow_asset_id_list,
                    ));
                }
            }
        }
    }

    // AssetIdList-based binding resolution
    // If this file has sc_file_source == 'AssetIdList', find all files
    // sharing the same sc_file_asset_id_list and collect their bindings.
    if allow_asset_id_list {
        let file_source = root
            .get("sc_file_source")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if file_source == "AssetIdList" {
            if let Some(asset_id_list) = root.get("sc_file_asset_id_list").and_then(|v| v.as_str())
            {
                let root_id = root.get("id").and_then(|v| v.as_str()).unwrap_or("");
                // Collect bindings from all files in the same asset_id_list
                for (file_id, file_data) in registry.files() {
                    // Skip the root file itself (its bindings already collected)
                    if file_id == root_id {
                        continue;
                    }

                    // Check if this file belongs to the same asset_id_list
                    let other_source = file_data
                        .get("sc_file_source")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let other_asset_list = file_data
                        .get("sc_file_asset_id_list")
                        .and_then(|v| v.as_str());

                    if other_source == "AssetIdList" && other_asset_list == Some(asset_id_list) {
                        collected.extend(resolve_bindings_for_file(
                            file_data, registry,
                            false, // Don't allow nested AssetIdList resolution
                        ));
                    }
                }
            }
        }
    }

    // OtherTomlConfig-based binding resolution
    let file_source = root
        .get("sc_file_source")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    if file_source == "OtherTomlConfig" {
        if let Some(sc_file) = root.get("sc_file").and_then(|v| v.as_str()) {
            if let Some(referenced_file) = registry.get(sc_file) {
                collected.extend(resolve_bindings_for_file(referenced_file, registry, true));
            }
        }
    }

    collected
}

fn validate_binding_ref(
    node: &Value,
    root: &Value,
    registry: &FileRegistry,
    path: &str,
) -> Vec<String> {
    let binding_id = node.as_str().unwrap();

    // Resolve all bindings (direct + copy_configs + cross-file)
    let bindings: HashSet<String> = resolve_bindings_for_file(root, registry, true);

    if bindings.is_empty() {
        return vec![format!(
            "{path}: bindingId '{binding_id}' references, but no 'bindings' section exists in root"
        )];
    }

    if !bindings.contains(binding_id) {
        let mut error_message = format!("{path}: bindingId '{binding_id}' not found in 'bindings'");
        if *VERBOSE.get().unwrap_or(&0) >= 1 {
            let available: Vec<&str> = bindings.iter().map(String::as_str).collect();
            error_message.push_str(&format!(" (available: {available:?})"));
        }
        return vec![error_message];
    }

    Vec::new()
}

/// Semantic validators keyed by schema definition ref.
type SemanticValidator = fn(&Value, &Value, &FileRegistry, &str) -> Vec<String>;

fn register_semantic_validators() -> HashMap<String, SemanticValidator> {
    let mut validators: HashMap<String, SemanticValidator> = HashMap::new();
    validators.insert(
        "#/definitions/bindingId".to_string(),
        validate_binding_ref as SemanticValidator,
    );
    validators
}

/// Recursively walk the schema tree, applying semantic validators
/// whenever a `$ref` to a registered binding definition is encountered.
#[allow(clippy::too_many_arguments)]
fn walk_schema_and_validate(
    node: &Value,
    schema_node: &Value,
    root: &Value,
    registry: &FileRegistry,
    path: &str,
    errors: &mut Vec<String>,
    schema_definitions: &HashMap<String, Value>,
    semantic_validators: &HashMap<String, SemanticValidator>,
) {
    // Handle arrays at the root level (e.g. set_text, move, replace)
    if let Some(items) = node.as_array() {
        if let Some(items_schema) = schema_node.get("items") {
            for (i, item) in items.iter().enumerate() {
                let item_path = if path.is_empty() {
                    format!("[{i}]")
                } else {
                    format!("{path}[{i}]")
                };
                walk_schema_and_validate(
                    item,
                    items_schema,
                    root,
                    registry,
                    &item_path,
                    errors,
                    schema_definitions,
                    semantic_validators,
                );
            }
        }
        return;
    }

    // Resolve $ref — call semantic validator if registered, then
    // always recurse into the referenced definition's properties.
    // Must come BEFORE the dict check since bindingId refs have string nodes.
    if let Some(ref_val) = schema_node.get("$ref") {
        if let Some(ref_str) = ref_val.as_str() {
            if ref_str.starts_with("#/definitions/") {
                if let Some(referenced) = schema_definitions.get(ref_str) {
                    if let Some(&validator) = semantic_validators.get(ref_str) {
                        // Only call the validator if the node is a string.
                        // Non-string nodes (dicts, arrays) are silently skipped,
                        // matching the Python walker's oneOf type filtering.

                        // TODO: before walking to one of oneOf branch, validate that schema is
                        //  suitable for data.
                        if node.is_string() {
                            let validator_errors = validator(node, root, registry, path);
                            errors.extend(validator_errors);
                        }
                    }
                    walk_schema_and_validate(
                        node,
                        referenced,
                        root,
                        registry,
                        path,
                        errors,
                        schema_definitions,
                        semantic_validators,
                    );
                    return;
                }
            }
        }
    }

    let Some(obj) = node.as_object() else {
        return;
    };

    // Recurse into properties / additionalProperties / items / allOf / oneOf
    if let Some(properties) = schema_node.get("properties").and_then(|v| v.as_object()) {
        for (prop_name, prop_schema) in properties {
            if let Some(child) = obj.get(prop_name) {
                let child_path = if path.is_empty() {
                    prop_name.to_string()
                } else {
                    format!("{path}.{prop_name}")
                };
                walk_schema_and_validate(
                    child,
                    prop_schema,
                    root,
                    registry,
                    &child_path,
                    errors,
                    schema_definitions,
                    semantic_validators,
                );
            }
        }
    }

    // additionalProperties
    if let Some(additional) = schema_node.get("additionalProperties") {
        for (key, child) in obj.iter() {
            if key.starts_with('$') {
                continue;
            }
            let child_path = if path.is_empty() {
                key.to_string()
            } else {
                format!("{path}.{key}")
            };
            walk_schema_and_validate(
                child,
                additional,
                root,
                registry,
                &child_path,
                errors,
                schema_definitions,
                semantic_validators,
            );
        }
    }

    // patternProperties
    if let Some(pattern_props) = schema_node
        .get("patternProperties")
        .and_then(|v| v.as_object())
    {
        for (pattern_str, properties_schema) in pattern_props {
            if let Ok(pattern) = regex::Regex::new(pattern_str) {
                for (key, child) in obj.iter() {
                    if !pattern.is_match(key) {
                        continue;
                    }
                    let child_path = if path.is_empty() {
                        key.to_string()
                    } else {
                        format!("{path}.{key}")
                    };
                    walk_schema_and_validate(
                        child,
                        properties_schema,
                        root,
                        registry,
                        &child_path,
                        errors,
                        schema_definitions,
                        semantic_validators,
                    );
                }
            }
        }
    }

    // items (for object-valued items)
    if let Some(items_schema) = schema_node.get("items") {
        let items: Vec<&Value> = if node.is_array() {
            node.as_array().unwrap().iter().collect()
        } else {
            vec![node]
        };
        for (i, item) in items.iter().enumerate() {
            let item_path = if path.is_empty() {
                format!("[{i}]")
            } else {
                format!("{path}[{i}]")
            };
            walk_schema_and_validate(
                item,
                items_schema,
                root,
                registry,
                &item_path,
                errors,
                schema_definitions,
                semantic_validators,
            );
        }
    }

    // allOf
    if let Some(all_of) = schema_node.get("allOf").and_then(|v| v.as_array()) {
        for sub_schema in all_of {
            // For non-$ref subschemas, extract the matching property value
            // and recurse with that for type-based validation.
            if let Some(sub_properties) = sub_schema.get("properties").and_then(|v| v.as_object()) {
                let mut sub_node = None;
                for sub_name in sub_properties.keys() {
                    if let Some(val) = obj.get(sub_name) {
                        sub_node = Some(val);
                        break;
                    }
                }
                if let Some(sub_node) = sub_node {
                    walk_schema_and_validate(
                        sub_node,
                        sub_schema,
                        root,
                        registry,
                        path,
                        errors,
                        schema_definitions,
                        semantic_validators,
                    );
                    continue;
                }
            }
            walk_schema_and_validate(
                node,
                sub_schema,
                root,
                registry,
                path,
                errors,
                schema_definitions,
                semantic_validators,
            );
        }
    }

    // oneOf
    if let Some(one_of) = schema_node.get("oneOf").and_then(|v| v.as_array()) {
        for sub_schema in one_of {
            // Resolve $ref to check type compatibility with node.
            // This prevents e.g. calling bindingId validator with a dict node
            // when childReferenceOrId oneOf contains both childReference and bindingId.
            let target_type = if let Some(ref_val) = sub_schema.get("$ref") {
                if let Some(ref_str) = ref_val.as_str() {
                    if ref_str.starts_with("#/definitions/") {
                        let def_name = ref_str.split('/').last().unwrap_or("");
                        schema_definitions
                            .get(def_name)
                            .and_then(|v| v.get("type"))
                            .and_then(|v| v.as_str())
                    } else {
                        None
                    }
                } else {
                    None
                }
            } else {
                sub_schema.get("type").and_then(|v| v.as_str())
            };

            // Skip oneOf branches that don't match the node's actual type.
            match (target_type, node.is_object()) {
                (Some("string"), true) => continue,
                (Some("object"), false) => continue,
                _ => {}
            }

            walk_schema_and_validate(
                node,
                sub_schema,
                root,
                registry,
                path,
                errors,
                schema_definitions,
                semantic_validators,
            );
        }
    }

    // Recurse into nested objects regardless
    for (key, child) in obj.iter() {
        let child_path = if path.is_empty() {
            key.to_string()
        } else {
            format!("{path}.{key}")
        };
        walk_schema_and_validate(
            child,
            &Value::Object(serde_json::Map::new()),
            root,
            registry,
            &child_path,
            errors,
            schema_definitions,
            semantic_validators,
        );
    }
}

fn validate_semantics(root: &Value, registry: &FileRegistry, schema: &Value) -> Vec<String> {
    let mut errors = Vec::new();
    let definitions = schema
        .get("definitions")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let definitions: HashMap<String, Value> = definitions
        .into_iter()
        .map(|(k, v)| (format!("#/definitions/{k}"), v))
        .collect();

    let semantic_validators = register_semantic_validators();

    // Walk properties at the root level
    if let Some(properties) = schema.get("properties").and_then(|v| v.as_object()) {
        for (prop_name, prop_schema) in properties {
            if let Some(data_val) = root.get(prop_name) {
                walk_schema_and_validate(
                    data_val,
                    prop_schema,
                    root,
                    registry,
                    prop_name,
                    &mut errors,
                    &definitions,
                    &semantic_validators,
                );
            }
        }
    }

    // Also walk the root schema's allOf (e.g., animations with bindingId refs)
    if let Some(root_all_of) = schema.get("allOf").and_then(|v| v.as_array()) {
        for sub_schema in root_all_of {
            walk_schema_and_validate(
                root,
                sub_schema,
                root,
                registry,
                "",
                &mut errors,
                &definitions,
                &semantic_validators,
            );
        }
    }

    errors
}

fn load_schema(schema_path: &Path) -> Result<Value> {
    if !schema_path.exists() {
        return Err(Error::SchemaNotFound(schema_path.to_path_buf()));
    }
    let bytes = std::fs::read(schema_path)?;
    Ok(serde_json::from_slice(&bytes)?)
}

fn validate_schema(json_file: &Path, schema_path: &Path) -> ExitStatus {
    Command::new("jsonschema")
        .args([
            "validate",
            schema_path.to_str().unwrap(),
            json_file.to_str().unwrap(),
        ])
        .status()
        .unwrap_or_else(|_| ExitStatus::from_raw(1))
}

fn find_ui_files(root: &Path) -> Vec<PathBuf> {
    let mut ui_files: Vec<PathBuf> = Vec::new();
    for entry in WalkDir::new(root).into_iter().filter_map(|e| e.ok()) {
        let path = entry.path();
        if path.extension().is_some_and(|ext| ext == "ui") {
            ui_files.push(path.to_path_buf());
        }
    }
    ui_files.sort();
    ui_files
}

fn run(cli: Cli) -> Result<i32> {
    VERBOSE.set(cli.verbose).ok();
    let schema_path = Path::new("src/ui.schema.json");
    let schema = load_schema(schema_path)?;
    let mut registry = FileRegistry::default();

    let ui_files = find_ui_files(Path::new("."));

    // Phase 1: Register all .ui files in the registry
    for ui_file in &ui_files {
        if let Err(e) = registry.register_from_path(ui_file) {
            eprintln!("Warning: Could not register {}: {e}", ui_file.display());
        }
    }

    let mut exit_code = 0;

    // Phase 2: Validate each file
    for ui_file in &ui_files {
        let json_file = ui_file.with_extension("json");

        match std::fs::read(ui_file) {
            Ok(bytes) => match toml::from_slice::<Value>(&bytes) {
                Ok(mut data) => {
                    // Add $schema reference
                    let relative_schema = schema_path
                        .strip_prefix(json_file.parent().unwrap_or(Path::new("")))
                        .unwrap_or(schema_path)
                        .to_str()
                        .unwrap_or("");
                    data.as_object_mut().unwrap().insert(
                        "$schema".to_string(),
                        Value::String(relative_schema.to_string()),
                    );

                    // Write JSON file
                    let json_output = serde_json::to_string_pretty(&data)?;
                    std::fs::write(&json_file, format!("{json_output}\n"))?;

                    if cli.verbose >= 2 {
                        eprintln!("Validating {}...", ui_file.display());
                    }

                    // Schema validation (skipped if --skip-schema-validation is set)
                    let schema_valid = if cli.skip_schema_validation {
                        if cli.verbose >= 2 {
                            eprintln!("  Skipping schema validation: {}", ui_file.display());
                        }
                        true
                    } else {
                        let status = validate_schema(&json_file, schema_path);
                        if !status.success() {
                            exit_code = status.code().unwrap_or(1);
                            eprintln!("  Schema validation failed: {}", ui_file.display());
                            false
                        } else {
                            true
                        }
                    };

                    if schema_valid {
                        // Semantic validation
                        let semantic_errors = validate_semantics(&data, &registry, &schema);
                        if !semantic_errors.is_empty() {
                            eprintln!("  Semantic errors in {}:", ui_file.display());
                            for err in &semantic_errors {
                                eprintln!("    - {err}");
                            }
                            exit_code = 1;
                        } else if cli.verbose >= 2 {
                            eprintln!("  {} OK", ui_file.display());
                        }

                        // Clean up the generated JSON file
                        let _ = std::fs::remove_file(&json_file);
                    }
                }
                Err(e) => {
                    eprintln!(
                        "Error occurred while loading file {}: {e}",
                        ui_file.display()
                    );
                    exit_code = 1;
                }
            },
            Err(e) => {
                eprintln!("Error reading file {}: {e}", ui_file.display());
                exit_code = 1;
            }
        }
    }

    Ok(exit_code)
}

fn main() {
    let cli = Cli::parse();
    match run(cli) {
        Ok(exit_code) => std::process::exit(exit_code),
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(1);
        }
    }
}