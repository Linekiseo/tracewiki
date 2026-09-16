use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    net::IpAddr,
    path::{Path, PathBuf},
};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder},
    Emitter, Manager,
};
use tauri_plugin_deep_link::DeepLinkExt;
use url::Url;

const PROFILE_SCHEMA: &str = "desktop-connection-profile-v1";
const LLM_PROFILE_SCHEMA: &str = "desktop-llm-profile-v1";
const KEYRING_SERVICE: &str = "com.evidencerag.workbench.llm";
const DEFAULT_BACKEND: &str = "http://127.0.0.1:8765";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum ConnectionMode {
    ManagedLocal,
    Remote,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct ConnectionProfile {
    schema: String,
    mode: ConnectionMode,
    base_url: String,
    last_project_id: Option<String>,
}

impl Default for ConnectionProfile {
    fn default() -> Self {
        Self {
            schema: PROFILE_SCHEMA.to_string(),
            mode: ConnectionMode::ManagedLocal,
            base_url: DEFAULT_BACKEND.to_string(),
            last_project_id: None,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
struct DesktopBootstrap {
    desktop: bool,
    os: String,
    arch: String,
    app_version: String,
    profile: ConnectionProfile,
    native_menu: bool,
    deep_link_scheme: String,
    local_service_command_available: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct LlmProfile {
    schema: String,
    project_id: String,
    provider: String,
    base_url: String,
    model: String,
    key_stored: bool,
}

#[derive(Clone, Debug, Deserialize)]
struct LlmProfileInput {
    schema: String,
    project_id: String,
    provider: String,
    base_url: String,
    model: String,
    api_key: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct ProviderRegistrationInput {
    project_id: String,
    backend_url: String,
    bearer_token: Option<String>,
    acl_refs: Option<String>,
}

fn profile_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let root = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("desktop_config_unavailable:{error}"))?;
    Ok(root.join("connection-v1.json"))
}

fn llm_profile_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let root = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("desktop_config_unavailable:{error}"))?;
    Ok(root.join("llm-providers-v1.json"))
}

fn validate_project_id(value: Option<String>) -> Result<Option<String>, String> {
    let Some(value) = value else { return Ok(None) };
    let value = value.trim();
    if value.is_empty() {
        return Ok(None);
    }
    let valid = value.len() <= 160
        && value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'));
    if !valid {
        return Err("invalid_project_id".into());
    }
    Ok(Some(value.to_string()))
}

fn validate_backend_url(value: &str, mode: &ConnectionMode) -> Result<String, String> {
    let mut parsed = Url::parse(value.trim()).map_err(|_| "invalid_backend_url")?;
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err("backend_url_must_not_contain_credentials_query_or_fragment".into());
    }
    let host = parsed.host_str().ok_or("backend_url_requires_host")?;
    let loopback = host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<IpAddr>()
            .map(|address| address.is_loopback())
            .unwrap_or(false);
    match mode {
        ConnectionMode::ManagedLocal if parsed.scheme() != "http" || !loopback => {
            return Err("managed_local_backend_must_be_loopback_http".into())
        }
        ConnectionMode::Remote if parsed.scheme() != "https" => {
            return Err("remote_backend_requires_https".into())
        }
        _ => {}
    }
    let normalized_path = parsed.path().trim_end_matches('/').to_string();
    parsed.set_path(&normalized_path);
    Ok(parsed.to_string().trim_end_matches('/').to_string())
}

fn validate_provider_url(value: &str) -> Result<String, String> {
    let mut parsed = Url::parse(value.trim()).map_err(|_| "invalid_provider_url")?;
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err("provider_url_must_not_contain_credentials_query_or_fragment".into());
    }
    let host = parsed.host_str().ok_or("provider_url_requires_host")?;
    let loopback = host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<IpAddr>()
            .map(|address| address.is_loopback())
            .unwrap_or(false);
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && loopback) {
        return Err("remote_provider_requires_https".into());
    }
    let normalized_path = parsed.path().trim_end_matches('/').to_string();
    parsed.set_path(&normalized_path);
    Ok(parsed.to_string().trim_end_matches('/').to_string())
}

fn validate_model_id(value: &str) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty()
        || value.len() > 200
        || value.chars().any(|character| character.is_control())
    {
        return Err("invalid_model_id".into());
    }
    Ok(value.to_string())
}

fn keyring_entry(project_id: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, project_id)
        .map_err(|error| format!("credential_vault_unavailable:{error}"))
}

fn load_llm_profiles(path: &Path) -> Result<BTreeMap<String, LlmProfile>, String> {
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    let bytes = fs::read(path).map_err(|error| format!("llm_profile_read_failed:{error}"))?;
    serde_json::from_slice(&bytes).map_err(|_| "llm_profile_invalid_json".into())
}

fn publish_llm_profiles(
    path: &Path,
    profiles: &BTreeMap<String, LlmProfile>,
) -> Result<(), String> {
    let parent = path.parent().ok_or("llm_profile_parent_unavailable")?;
    fs::create_dir_all(parent).map_err(|error| format!("llm_profile_parent_failed:{error}"))?;
    let bytes = serde_json::to_vec_pretty(profiles).map_err(|_| "llm_profile_encode_failed")?;
    let temporary = path.with_extension("json.tmp");
    fs::write(&temporary, bytes).map_err(|error| format!("llm_profile_write_failed:{error}"))?;
    fs::rename(&temporary, path).map_err(|error| format!("llm_profile_publish_failed:{error}"))
}

fn load_profile(path: &Path) -> Result<ConnectionProfile, String> {
    if !path.exists() {
        return Ok(ConnectionProfile::default());
    }
    let bytes = fs::read(path).map_err(|error| format!("profile_read_failed:{error}"))?;
    let mut profile: ConnectionProfile =
        serde_json::from_slice(&bytes).map_err(|_| "profile_invalid_json")?;
    if profile.schema != PROFILE_SCHEMA {
        return Err("profile_schema_mismatch".into());
    }
    profile.base_url = validate_backend_url(&profile.base_url, &profile.mode)?;
    profile.last_project_id = validate_project_id(profile.last_project_id)?;
    Ok(profile)
}

fn save_profile(path: &Path, mut profile: ConnectionProfile) -> Result<ConnectionProfile, String> {
    if profile.schema != PROFILE_SCHEMA {
        return Err("profile_schema_mismatch".into());
    }
    profile.base_url = validate_backend_url(&profile.base_url, &profile.mode)?;
    profile.last_project_id = validate_project_id(profile.last_project_id)?;
    let parent = path.parent().ok_or("profile_parent_unavailable")?;
    fs::create_dir_all(parent).map_err(|error| format!("profile_parent_failed:{error}"))?;
    let bytes = serde_json::to_vec_pretty(&profile).map_err(|_| "profile_encode_failed")?;
    let temporary = path.with_extension("json.tmp");
    fs::write(&temporary, bytes).map_err(|error| format!("profile_write_failed:{error}"))?;
    fs::rename(&temporary, path).map_err(|error| format!("profile_publish_failed:{error}"))?;
    Ok(profile)
}

#[tauri::command]
fn desktop_bootstrap(app: tauri::AppHandle) -> Result<DesktopBootstrap, String> {
    let path = profile_path(&app)?;
    let profile = load_profile(&path).unwrap_or_default();
    Ok(DesktopBootstrap {
        desktop: true,
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        app_version: app.package_info().version.to_string(),
        profile,
        native_menu: true,
        deep_link_scheme: "evidence-rag".into(),
        local_service_command_available: std::env::var_os("EVIDENCE_RAG_SERVER_BIN").is_some(),
    })
}

#[tauri::command]
fn set_connection_profile(
    app: tauri::AppHandle,
    profile: ConnectionProfile,
) -> Result<ConnectionProfile, String> {
    let path = profile_path(&app)?;
    save_profile(&path, profile)
}

#[tauri::command]
fn desktop_llm_profile(
    app: tauri::AppHandle,
    project_id: String,
) -> Result<Option<LlmProfile>, String> {
    let project_id = validate_project_id(Some(project_id))?.ok_or("invalid_project_id")?;
    let mut profile = load_llm_profiles(&llm_profile_path(&app)?)?.remove(&project_id);
    if let Some(value) = profile.as_mut() {
        value.key_stored = keyring_entry(&project_id)?.get_password().is_ok();
    }
    Ok(profile)
}

#[tauri::command]
fn desktop_save_llm_profile(
    app: tauri::AppHandle,
    input: LlmProfileInput,
) -> Result<LlmProfile, String> {
    if input.schema != LLM_PROFILE_SCHEMA || input.provider != "openai_compatible" {
        return Err("llm_profile_contract_mismatch".into());
    }
    let project_id = validate_project_id(Some(input.project_id))?.ok_or("invalid_project_id")?;
    let base_url = validate_provider_url(&input.base_url)?;
    let model = validate_model_id(&input.model)?;
    let entry = keyring_entry(&project_id)?;
    let previous_key = entry.get_password().ok();
    let replacement = input
        .api_key
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if let Some(api_key) = replacement {
        if api_key.len() > 8192 || api_key.chars().any(|character| character.is_control()) {
            return Err("invalid_api_key".into());
        }
        entry
            .set_password(api_key)
            .map_err(|error| format!("credential_vault_write_failed:{error}"))?;
    } else {
        entry.get_password().map_err(|_| "credential_not_found")?;
    }
    let profile = LlmProfile {
        schema: LLM_PROFILE_SCHEMA.into(),
        project_id: project_id.clone(),
        provider: "openai_compatible".into(),
        base_url,
        model,
        key_stored: true,
    };
    let path = llm_profile_path(&app)?;
    let mut profiles = load_llm_profiles(&path)?;
    profiles.insert(project_id, profile.clone());
    if let Err(error) = publish_llm_profiles(&path, &profiles) {
        if let Some(previous) = previous_key {
            let _ = entry.set_password(&previous);
        } else {
            let _ = entry.delete_credential();
        }
        return Err(error);
    }
    Ok(profile)
}

#[tauri::command]
fn desktop_delete_llm_profile(app: tauri::AppHandle, project_id: String) -> Result<(), String> {
    let project_id = validate_project_id(Some(project_id))?.ok_or("invalid_project_id")?;
    let path = llm_profile_path(&app)?;
    let mut profiles = load_llm_profiles(&path)?;
    profiles.remove(&project_id);
    let entry = keyring_entry(&project_id)?;
    let previous_key = entry.get_password().ok();
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        Err(error) => return Err(format!("credential_vault_delete_failed:{error}")),
    }
    if let Err(error) = publish_llm_profiles(&path, &profiles) {
        if let Some(previous) = previous_key {
            let _ = entry.set_password(&previous);
        }
        return Err(error);
    }
    Ok(())
}

#[tauri::command]
async fn desktop_register_llm_provider(
    app: tauri::AppHandle,
    input: ProviderRegistrationInput,
) -> Result<serde_json::Value, String> {
    let project_id = validate_project_id(Some(input.project_id))?.ok_or("invalid_project_id")?;
    let backend_url = validate_backend_url(&input.backend_url, &ConnectionMode::ManagedLocal)
        .or_else(|_| validate_backend_url(&input.backend_url, &ConnectionMode::Remote))?;
    let key = keyring_entry(&project_id)?
        .get_password()
        .map_err(|_| "credential_not_found")?;
    let path = llm_profile_path(&app)?;
    let profile = load_llm_profiles(&path)?
        .remove(&project_id)
        .ok_or("llm_profile_not_found")?;
    let mut request = reqwest::Client::new()
        .put(format!("{backend_url}/v1/ai/provider"))
        .json(&serde_json::json!({
            "project_id": project_id,
            "provider": profile.provider,
            "base_url": profile.base_url,
            "model": profile.model,
            "api_key": key,
        }));
    if let Some(token) = input.bearer_token.filter(|value| !value.trim().is_empty()) {
        request = request.bearer_auth(token.trim());
    }
    if let Some(refs) = input.acl_refs.filter(|value| !value.trim().is_empty()) {
        request = request.header("X-RAG-ACL-Refs", refs.trim());
    }
    let response = request
        .send()
        .await
        .map_err(|_| "provider_registration_unreachable")?;
    if !response.status().is_success() {
        return Err(format!(
            "provider_registration_failed:{}",
            response.status()
        ));
    }
    response
        .json()
        .await
        .map_err(|_| "provider_registration_invalid_response".into())
}

fn emit_navigation(app: &tauri::AppHandle, route: &str) {
    let _ = app.emit("desktop:navigate", route.to_string());
}

fn install_menu(app: &tauri::App) -> tauri::Result<()> {
    let new_query = MenuItemBuilder::with_id("new-query", "新建智能查询")
        .accelerator("CmdOrCtrl+N")
        .build(app)?;
    let wiki = MenuItemBuilder::with_id("open-wiki", "打开 Wiki")
        .accelerator("CmdOrCtrl+Shift+W")
        .build(app)?;
    let projects = MenuItemBuilder::with_id("open-projects", "项目中心").build(app)?;
    let sessions = MenuItemBuilder::with_id("open-sessions", "研发会话").build(app)?;
    let agents = MenuItemBuilder::with_id("open-agents", "智能体").build(app)?;
    let status = MenuItemBuilder::with_id("open-status", "系统状态").build(app)?;
    let reload = MenuItemBuilder::with_id("reload", "重新加载")
        .accelerator("CmdOrCtrl+R")
        .build(app)?;

    let workspace = SubmenuBuilder::new(app, "工作区")
        .items(&[&projects, &new_query, &wiki, &sessions, &agents])
        .build()?;
    let view = SubmenuBuilder::new(app, "查看")
        .items(&[&status, &reload])
        .build()?;
    let menu = MenuBuilder::new(app).items(&[&workspace, &view]).build()?;
    app.set_menu(menu)?;
    app.on_menu_event(|app, event| match event.id().as_ref() {
        "open-projects" => emit_navigation(app, "/projects"),
        "new-query" => emit_navigation(app, "/search"),
        "open-wiki" => emit_navigation(app, "/desktop/current/wiki"),
        "open-sessions" => emit_navigation(app, "/desktop/current/sessions"),
        "open-agents" => emit_navigation(app, "/desktop/current/agents"),
        "open-status" => emit_navigation(app, "/ops"),
        "reload" => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("window.location.reload()");
            }
        }
        _ => {}
    });
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
            let _ = app.emit("desktop:second-instance", argv);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_deep_link::init())
        .setup(|app| {
            install_menu(app)?;
            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                let urls: Vec<String> = event.urls().iter().map(ToString::to_string).collect();
                let _ = handle.emit("desktop:deep-link", urls);
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            desktop_bootstrap,
            set_connection_profile,
            desktop_llm_profile,
            desktop_save_llm_profile,
            desktop_delete_llm_profile,
            desktop_register_llm_provider
        ])
        .run(tauri::generate_context!())
        .expect("Evidence RAG desktop runtime failed");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn managed_local_is_loopback_only() {
        assert!(
            validate_backend_url("http://127.0.0.1:8765/", &ConnectionMode::ManagedLocal).is_ok()
        );
        assert!(
            validate_backend_url("http://localhost:8765", &ConnectionMode::ManagedLocal).is_ok()
        );
        assert!(
            validate_backend_url("http://192.168.1.2:8765", &ConnectionMode::ManagedLocal).is_err()
        );
        assert!(
            validate_backend_url("https://example.com", &ConnectionMode::ManagedLocal).is_err()
        );
    }

    #[test]
    fn remote_requires_https_and_no_embedded_credentials() {
        assert!(
            validate_backend_url("https://rag.example.com/api", &ConnectionMode::Remote).is_ok()
        );
        assert!(validate_backend_url("http://rag.example.com", &ConnectionMode::Remote).is_err());
        assert!(
            validate_backend_url("https://token@rag.example.com", &ConnectionMode::Remote).is_err()
        );
        assert!(
            validate_backend_url("https://rag.example.com?a=secret", &ConnectionMode::Remote)
                .is_err()
        );
    }

    #[test]
    fn project_ids_are_bounded_and_portable() {
        assert_eq!(
            validate_project_id(Some("project-rag_01".into())).unwrap(),
            Some("project-rag_01".into())
        );
        assert!(validate_project_id(Some("../../private".into())).is_err());
        assert!(validate_project_id(Some("项目".into())).is_err());
    }

    #[test]
    fn model_provider_requires_https_except_for_loopback() {
        assert!(validate_provider_url("https://api.example.com/v1/").is_ok());
        assert!(validate_provider_url("http://127.0.0.1:11434/v1").is_ok());
        assert!(validate_provider_url("http://api.example.com/v1").is_err());
        assert!(validate_provider_url("https://secret@api.example.com/v1").is_err());
        assert!(validate_provider_url("https://api.example.com/v1?key=secret").is_err());
    }

    #[test]
    fn model_identifier_is_printable_and_bounded() {
        assert_eq!(
            validate_model_id("gpt-query-planner").unwrap(),
            "gpt-query-planner"
        );
        assert!(validate_model_id("").is_err());
        assert!(validate_model_id("model\nsecret").is_err());
        assert!(validate_model_id(&"x".repeat(201)).is_err());
    }
}
