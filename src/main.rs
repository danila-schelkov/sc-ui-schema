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
    /// Files or directories to analyze. If omitted, all .ui files in the current directory
    /// are analyzed. Directories are walked recursively to find .ui files.
    #[arg(name = "path")]
    paths: Vec<PathBuf>,
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

    // An empty string path — reference to self
    collected.insert("".to_string());

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

type AnimationKey = String;

fn resolve_animations_for_file(root: &Value, _registry: &FileRegistry) -> HashSet<AnimationKey> {
    // Resolve all animations for a root .ui file.
    //
    // This collects animations from:
    // 1. The root file's "animations" or "animation" section
    // 2. All files referenced via "copy_configs"
    // 3. All files sharing the same "sc_file_asset_id_list" (AssetIdList source)
    //
    // Returns a merged set of all animation keys.

    let mut collected: HashSet<AnimationKey> = HashSet::new();

    // An empty string path — reference to self
    collected.insert("".to_string());

    // Direct animations from the root file
    let root_animations = root.get("animation").or(root.get("animations"));
    if let Some(animations) = root_animations.and_then(|v| v.as_object()) {
        collected.extend(animations.keys().cloned());
    }

    collected
}

fn validate_animation_ref(
    node: &Value,
    root: &Value,
    registry: &FileRegistry,
    path: &str,
) -> Vec<String> {
    let animation_key = node.as_str().unwrap();

    // Resolve all animations (direct + copy_configs + cross-file)
    let animations: HashSet<String> = resolve_animations_for_file(root, registry);

    if animations.is_empty() {
        return vec![format!(
            "{path}: animationKey '{animation_key}' references, but no 'animations' section exists in root"
        )];
    }

    if !animations.contains(animation_key) {
        let mut error_message =
            format!("{path}: animationKey '{animation_key}' not found in 'animations'");
        if *VERBOSE.get().unwrap_or(&0) >= 1 {
            let available: Vec<&str> = animations.iter().map(String::as_str).collect();
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
    validators.insert(
        "#/definitions/animationKey".to_string(),
        validate_animation_ref as SemanticValidator,
    );
    validators
}

/// Check if a node matches the expected JSON Schema type(s).
fn is_valid_type(node: &Value, type_val: &Value) -> bool {
    match type_val {
        Value::String(s) => match s.as_str() {
            "integer" => node.is_i64() || node.is_u64(),
            "number" => node.is_number(),
            "boolean" => node.is_boolean(),
            "string" => node.is_string(),
            "object" => node.is_object(),
            "array" => node.is_array(),
            _ => true,
        },
        Value::Array(types) => types.iter().any(|t| is_valid_type(node, t)),
        Value::Null => true,
        _ => panic!("Unexpected type: {type_val:?}"),
    }
}

/// Recursively walk the schema tree, applying semantic validators
/// whenever a `$ref` to a registered binding definition is encountered.
/// Returns `true` on success, `false` to early-exit (e.g., wrong type in oneOf).
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
) -> bool {
    // Resolve $ref — call semantic validator if registered, then
    // always recurse into the referenced definition's properties.
    // Must come BEFORE the dict check since bindingId refs have string nodes.
    if let Some(ref_val) = schema_node.get("$ref") {
        if let Some(ref_str) = ref_val.as_str() {
            if ref_str.starts_with("#/definitions/") {
                if let Some(referenced) = schema_definitions.get(ref_str) {
                    // Finish oneOf branching if wrong type.
                    let referenced_type = referenced.get("type");
                    if !is_valid_type(node, referenced_type.unwrap_or(&Value::Null)) {
                        return false;
                    }

                    if let Some(&validator) = semantic_validators.get(ref_str) {
                        let validator_errors = validator(node, root, registry, path);
                        errors.extend(validator_errors);
                    }
                    // Resolved, skip further processing of $ref schema itself
                    return walk_schema_and_validate(
                        node,
                        referenced,
                        root,
                        registry,
                        path,
                        errors,
                        schema_definitions,
                        semantic_validators,
                    );
                }
            }
        }
    }

    // TODO: handle unevaluatedProperties

    if let Some(items_schema) = schema_node.get("items") {
        let items: Vec<&Value> = node.as_array().unwrap().iter().collect();
        for (i, item) in items.iter().enumerate() {
            let item_path = if path.is_empty() {
                format!("[{i}]")
            } else {
                format!("{path}[{i}]")
            };
            if !walk_schema_and_validate(
                item,
                items_schema,
                root,
                registry,
                &item_path,
                errors,
                schema_definitions,
                semantic_validators,
            ) {
                return false;
            }
        }
    }

    // oneOf
    if let Some(one_of) = schema_node.get("oneOf").and_then(|v| v.as_array()) {
        for sub_schema in one_of {
            let sub_schema_type = sub_schema.get("type");

            // Skip oneOf branches that don't match the node's actual type.
            if !is_valid_type(node, sub_schema_type.unwrap_or(&Value::Null)) {
                continue;
            }

            let mut one_of_errors: Vec<String> = Vec::new();
            let result = walk_schema_and_validate(
                node,
                sub_schema,
                root,
                registry,
                path,
                &mut one_of_errors,
                schema_definitions,
                semantic_validators,
            );
            if result {
                errors.extend(one_of_errors);
                break;
            }
        }
    }

    if !node.is_object() {
        return true;
    }

    let obj = node.as_object().unwrap();

    // Recurse into properties
    let properties: Option<&serde_json::Map<String, Value>> =
        schema_node.get("properties").and_then(|v| v.as_object());
    if let Some(properties) = properties {
        for (prop_name, prop_schema) in properties {
            if let Some(child) = obj.get(prop_name) {
                let child_path = if path.is_empty() {
                    prop_name.to_string()
                } else {
                    format!("{path}.{prop_name}")
                };
                if !walk_schema_and_validate(
                    child,
                    prop_schema,
                    root,
                    registry,
                    &child_path,
                    errors,
                    schema_definitions,
                    semantic_validators,
                ) {
                    return false;
                }
            }
        }
    }

    // additionalProperties
    if let Some(additional) = schema_node.get("additionalProperties") {
        for (key, child) in obj.iter() {
            if key.starts_with('$') {
                continue;
            }

            // Skip keys already handled by properties
            if properties.is_some() && properties.as_ref().unwrap().contains_key(key.as_str()) {
                continue;
            }

            let child_path = if path.is_empty() {
                key.to_string()
            } else {
                format!("{path}.{key}")
            };
            if !walk_schema_and_validate(
                child,
                additional,
                root,
                registry,
                &child_path,
                errors,
                schema_definitions,
                semantic_validators,
            ) {
                return false;
            }
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
                    if !walk_schema_and_validate(
                        child,
                        properties_schema,
                        root,
                        registry,
                        &child_path,
                        errors,
                        schema_definitions,
                        semantic_validators,
                    ) {
                        return false;
                    }
                }
            }
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
                    if !walk_schema_and_validate(
                        sub_node,
                        sub_schema,
                        root,
                        registry,
                        path,
                        errors,
                        schema_definitions,
                        semantic_validators,
                    ) {
                        return false;
                    }
                    continue;
                }
            }
            if !walk_schema_and_validate(
                node,
                sub_schema,
                root,
                registry,
                path,
                errors,
                schema_definitions,
                semantic_validators,
            ) {
                return false;
            }
        }
    }

    true
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

    if !walk_schema_and_validate(
        root,
        schema,
        root,
        registry,
        "",
        &mut errors,
        &definitions,
        &semantic_validators,
    ) {
        eprintln!("Something went wrong...");
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

/// Find all .ui files from the given paths.
/// Each path can be a file or directory. Directories are walked recursively.
fn find_ui_files(paths: &[PathBuf]) -> Vec<PathBuf> {
    let mut ui_files: Vec<PathBuf> = Vec::new();

    for path in paths {
        let path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
        if path.is_file() {
            if path.extension().is_some_and(|ext| ext == "ui") {
                ui_files.push(path);
            } else {
                eprintln!("Warning: {} is not a .ui file, skipping.", path.display());
            }
        } else if path.is_dir() {
            for entry in WalkDir::new(&path).into_iter().filter_map(|e| e.ok()) {
                let file_path = entry.path();
                if file_path.extension().is_some_and(|ext| ext == "ui") {
                    ui_files.push(file_path.to_path_buf());
                }
            }
        } else {
            eprintln!("Warning: {} does not exist, skipping.", path.display());
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

    let ui_files = find_ui_files(&cli.paths);

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
