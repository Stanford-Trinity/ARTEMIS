mod cli;
mod event_processor;
mod realtime_logger;
mod event_processor_with_human_output;
mod event_processor_with_json_output;

use std::io::IsTerminal;
use std::io::Read;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;

pub use cli::Cli;
use codex_core::BUILT_IN_OSS_MODEL_PROVIDER_ID;
use codex_core::ConversationManager;
use codex_core::NewConversation;
use codex_core::config::Config;
use codex_core::config::ConfigOverrides;
use codex_core::protocol::AskForApproval;
use codex_core::protocol::Event;
use codex_core::protocol::EventMsg;
use codex_core::protocol::InputItem;
use codex_core::protocol::Op;
use codex_core::protocol::TaskCompleteEvent;
use codex_core::util::is_inside_git_repo;
use event_processor::EventProcessor;
use realtime_logger::RealtimeLogger;
use codex_login::AuthManager;
use codex_ollama::DEFAULT_OSS_MODEL;
use codex_protocol::config_types::SandboxMode;
use event_processor_with_human_output::EventProcessorWithHumanOutput;
use event_processor_with_json_output::EventProcessorWithJsonOutput;
use tracing::debug;
use tracing::error;
use tracing::info;
use tracing_subscriber::EnvFilter;

/// Get the system prompt for a specific specialist
fn get_specialist_system_prompt(specialist: &str) -> String {
    match specialist {
        "active_directory" => include_str!("../../core/active_directory.md").to_string(),
        "client_side_web" => include_str!("../../core/client_side_web.md").to_string(),
        "enumeration" => include_str!("../../core/enumeration.md").to_string(),
        "linux_privesc" => include_str!("../../core/linux_privesc.md").to_string(),
        "shelling" => include_str!("../../core/shelling.md").to_string(),
        "web_enumeration" => include_str!("../../core/web_enumeration.md").to_string(),
        "web" => include_str!("../../core/web.md").to_string(),
        "windows_privesc" => include_str!("../../core/windows_privesc.md").to_string(),
        "verification" => include_str!("../../core/prompt.md").to_string(),
        _ => get_default_system_prompt(),
    }
}

/// Get the default system prompt (generalist)
fn get_default_system_prompt() -> String {
    include_str!("../../core/prompt.md").to_string()
}

pub async fn run_main(cli: Cli, codex_linux_sandbox_exe: Option<PathBuf>) -> anyhow::Result<()> {
    let Cli {
        images,
        model: model_cli_arg,
        oss,
        config_profile,
        full_auto,
        dangerously_bypass_approvals_and_sandbox,
        cwd,
        skip_git_repo_check,
        color,
        last_message_file,
        log_session_dir,
        instance_id,
        wait_for_followup,
        mode,
        json: json_mode,
        sandbox_mode: sandbox_mode_cli_arg,
        prompt,
        config_overrides,
    } = cli;

    // Determine the prompt based on CLI arg and/or stdin.
    let prompt = match prompt {
        Some(p) if p != "-" => p,
        // Either `-` was passed or no positional arg.
        maybe_dash => {
            // When no arg (None) **and** stdin is a TTY, bail out early – unless the
            // user explicitly forced reading via `-`.
            let force_stdin = matches!(maybe_dash.as_deref(), Some("-"));

            if std::io::stdin().is_terminal() && !force_stdin {
                eprintln!(
                    "No prompt provided. Either specify one as an argument or pipe the prompt into stdin."
                );
                std::process::exit(1);
            }

            // Ensure the user knows we are waiting on stdin, as they may
            // have gotten into this state by mistake. If so, and they are not
            // writing to stdin, Codex will hang indefinitely, so this should
            // help them debug in that case.
            if !force_stdin {
                eprintln!("Reading prompt from stdin...");
            }
            let mut buffer = String::new();
            if let Err(e) = std::io::stdin().read_to_string(&mut buffer) {
                eprintln!("Failed to read prompt from stdin: {e}");
                std::process::exit(1);
            } else if buffer.trim().is_empty() {
                eprintln!("No prompt provided via stdin.");
                std::process::exit(1);
            }
            buffer
        }
    };

    let (stdout_with_ansi, stderr_with_ansi) = match color {
        cli::Color::Always => (true, true),
        cli::Color::Never => (false, false),
        cli::Color::Auto => (
            std::io::stdout().is_terminal(),
            std::io::stderr().is_terminal(),
        ),
    };

    let sandbox_mode = if full_auto {
        Some(SandboxMode::WorkspaceWrite)
    } else if dangerously_bypass_approvals_and_sandbox {
        Some(SandboxMode::DangerFullAccess)
    } else {
        sandbox_mode_cli_arg.map(Into::<SandboxMode>::into)
    };

    // When using `--oss`, let the bootstrapper pick the model (defaulting to
    // gpt-oss:20b) and ensure it is present locally. Also, force the built‑in
    // `oss` model provider.
    let model = if let Some(model) = model_cli_arg {
        Some(model)
    } else if oss {
        Some(DEFAULT_OSS_MODEL.to_owned())
    } else {
        None // No model specified, will use the default.
    };

    let model_provider = if oss {
        Some(BUILT_IN_OSS_MODEL_PROVIDER_ID.to_string())
    } else {
        None // No specific model provider override.
    };

    // Load configuration and determine approval policy
    let overrides = ConfigOverrides {
        model: model.clone(),
        config_profile,
        // This CLI is intended to be headless and has no affordances for asking
        // the user for approval.
        approval_policy: Some(AskForApproval::Never),
        sandbox_mode,
        cwd: cwd.map(|p| p.canonicalize().unwrap_or(p)),
        model_provider,
        codex_linux_sandbox_exe,
        specialist: Some(mode.to_string()),
        base_instructions: None,
        include_plan_tool: None,
        include_apply_patch_tool: None,
        disable_response_storage: oss.then_some(true),
        show_raw_agent_reasoning: oss.then_some(true),
        tools_web_search_request: None,
    };
    // Parse `-c` overrides.
    let cli_kv_overrides = match config_overrides.parse_overrides() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Error parsing -c overrides: {e}");
            std::process::exit(1);
        }
    };

    let config = Config::load_with_cli_overrides(cli_kv_overrides, overrides)?;
    // TODO(mbolin): Take a more thoughtful approach to logging.
    let default_level = "error";
    let _ = tracing_subscriber::fmt()
        // Fallback to the `default_level` log filter if the environment
        // variable is not set _or_ contains an invalid value
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .or_else(|_| EnvFilter::try_new(default_level))
                .unwrap_or_else(|_| EnvFilter::new(default_level)),
        )
        .with_ansi(stderr_with_ansi)
        .with_writer(std::io::stderr)
        .try_init();

    let mut event_processor: Box<dyn EventProcessor> = if json_mode {
        Box::new(EventProcessorWithJsonOutput::new(last_message_file.clone()))
    } else {
        Box::new(EventProcessorWithHumanOutput::create_with_ansi(
            stdout_with_ansi,
            &config,
            last_message_file.clone(),
        ))
    };

    if oss {
        codex_ollama::ensure_oss_ready(&config)
            .await
            .map_err(|e| anyhow::anyhow!("OSS setup failed: {e}"))?;
    }

    // Print the effective configuration and prompt so users can see what Codex
    // is using.
    event_processor.print_config_summary(&config, &prompt);

    if !skip_git_repo_check && !is_inside_git_repo(&config.cwd.to_path_buf()) {
        eprintln!("Not inside a trusted directory and --skip-git-repo-check was not specified.");
        std::process::exit(1);
    }

    let conversation_manager = ConversationManager::new(AuthManager::shared(
        config.codex_home.clone(),
        config.preferred_auth_method,
    ));
    let NewConversation {
        conversation_id: _,
        conversation,
        session_configured,
    } = conversation_manager.new_conversation(config.clone()).await?;
    info!("Codex initialized with event: {session_configured:?}");

    // Initialize real-time logger if requested
    let realtime_logger = if let Some(ref log_dir) = log_session_dir {
        let instance_id_str = instance_id
            .clone()
            .unwrap_or_else(|| format!("codex_{}", std::process::id()));

        // Get system prompt - prioritize custom base_instructions if available
        let system_prompt = if let Some(ref base_instructions) = config.base_instructions {
            // Custom system prompt from experimental_instructions_file
            Some(base_instructions.clone())
        } else if let Some(ref specialist) = config.specialist {
            // Specialist-specific system prompt
            Some(get_specialist_system_prompt(specialist))
        } else {
            // Default generalist system prompt
            Some(get_default_system_prompt())
        };

        // Get tools configuration - create ToolsConfig with same parameters as used in Codex
        let tools = {
            use codex_core::openai_tools::{ToolsConfig, ToolsConfigParams, get_openai_tools, create_tools_json_for_responses_api};
            
            let approval_policy = config.approval_policy;
            let sandbox_policy = config.sandbox_policy;
            
            let tools_config = ToolsConfig::new(&ToolsConfigParams {
                model_family: &config.model_family,
                approval_policy,
                sandbox_policy: sandbox_policy.clone(),
                include_plan_tool: config.include_plan_tool,
                include_apply_patch_tool: config.include_apply_patch_tool,
                include_web_search_request: config.tools_web_search_request,
                use_streamable_shell_tool: config.use_experimental_streamable_shell_tool,
            });
            
            let openai_tools = get_openai_tools(&tools_config, None); // No MCP tools for now
            if !openai_tools.is_empty() {
                match create_tools_json_for_responses_api(&openai_tools) {
                    Ok(tools_json) => Some(serde_json::Value::Array(tools_json)),
                    Err(_) => None,
                }
            } else {
                None
            }
        };

        Some(Arc::new(RealtimeLogger::new(
            log_dir.clone(),
            instance_id_str,
            &prompt,
            model.clone(),
            config.specialist.clone(),
            system_prompt,
            tools,
        )?))
    } else {
        None
    };

    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Event>();
    {
        let conversation = conversation.clone();
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {
                        tracing::debug!("Keyboard interrupt");
                        // Immediately notify Codex to abort any in‑flight task.
                        conversation.submit(Op::Interrupt).await.ok();

                        // Exit the inner loop and return to the main input prompt. The codex
                        // will emit a `TurnInterrupted` (Error) event which is drained later.
                        break;
                    }
                    res = conversation.next_event() => match res {
                        Ok(event) => {
                            debug!("Received event: {event:?}");

                            let is_shutdown_complete = matches!(event.msg, EventMsg::ShutdownComplete);
                            if let Err(e) = tx.send(event) {
                                error!("Error sending event: {e:?}");
                                break;
                            }
                            if is_shutdown_complete {
                                info!("Received shutdown event, exiting event loop.");
                                break;
                            }
                        },
                        Err(e) => {
                            error!("Error receiving event: {e:?}");
                            break;
                        }
                    }
                }
            }
        });
    }

    // Send images first, if any.
    if !images.is_empty() {
        let items: Vec<InputItem> = images
            .into_iter()
            .map(|path| InputItem::LocalImage { path })
            .collect();
        let initial_images_event_id = conversation.submit(Op::UserInput { items }).await?;
        info!("Sent images with event ID: {initial_images_event_id}");
        while let Ok(event) = conversation.next_event().await {
            if event.id == initial_images_event_id
                && matches!(
                    event.msg,
                    EventMsg::TaskComplete(TaskCompleteEvent {
                        last_agent_message: _,
                    })
                )
            {
                break;
            }
        }
    }

    // Send the prompt.
    let mut current_prompt = prompt;
    let mut message_index = 0;

    loop {
        let items: Vec<InputItem> = vec![InputItem::Text {
            text: current_prompt.clone(),
        }];
        let task_id = conversation.submit(Op::UserInput { items }).await?;
        info!("Sent prompt with event ID: {task_id}");

        // Run the loop until the task is complete.
        let mut assistant_responded = false;
        while let Some(event) = rx.recv().await {
            let (is_last_event, last_assistant_message) = match &event.msg {
                EventMsg::TaskComplete(TaskCompleteEvent { last_agent_message }) => {
                    (true, last_agent_message.clone())
                }
                _ => (false, None),
            };

            // Check if this is an assistant message
            if matches!(event.msg, EventMsg::AgentMessage(_)) {
                assistant_responded = true;
                message_index += 1;
            }

            // Log event to real-time logger if enabled
            if let Some(ref logger) = realtime_logger {
                if let Err(e) = logger.log_event(&event as &Event).await {
                    error!("Failed to log event to realtime logger: {e:?}");
                }
            }

            event_processor.process_event(event);
            if is_last_event {
                if !wait_for_followup {
                    handle_last_message(last_assistant_message, last_message_file.as_deref())?;
                    return Ok(());
                }
                break;
            }
        }

        // If we're in followup mode and assistant responded, wait for supervisor
        if wait_for_followup && assistant_responded {
            if let Some(ref log_dir) = log_session_dir {
                let instance_id_str = instance_id
                    .as_ref()
                    .map(|s| s.as_str())
                    .unwrap_or("unknown");
                match wait_for_supervisor_followup(
                    log_dir,
                    instance_id_str,
                    message_index,
                    model.as_deref(),
                )
                .await
                {
                    Ok(Some(followup)) => {
                        // Add the followup message to conversation history
                        let followup_items: Vec<InputItem> = vec![InputItem::Text {
                            text: followup.clone(),
                        }];
                        let followup_event_id = conversation.submit(Op::UserInput { items: followup_items }).await?;
                        info!("Sent followup message with event ID: {followup_event_id}");
                        
                        current_prompt = followup;

                        // Update status to indicate we're processing the followup
                        let status_file = log_dir.join("status.json");
                        let mut status_obj = serde_json::json!({
                            "status": "processing",
                            "instance_id": instance_id_str,
                            "last_message_index": message_index,
                            "timestamp": chrono::Utc::now().to_rfc3339()
                        });

                        // Add model information if available
                        if let Some(ref model_name) = model {
                            status_obj["model"] = serde_json::Value::String(model_name.to_string());
                        }

                        let status = status_obj;
                        let _ = std::fs::write(
                            &status_file,
                            serde_json::to_string_pretty(&status).unwrap_or_default(),
                        );
                        info!("Updated status to 'processing' after receiving followup");

                        continue; // Continue the loop with new prompt
                    }
                    Ok(None) => {
                        info!("Supervisor terminated instance");
                        break; // Supervisor wants us to terminate
                    }
                    Err(e) => {
                        error!("Error waiting for supervisor followup: {e:?}");
                        break;
                    }
                }
            }
        } else {
            break;
        }
    }

    Ok(())
}

fn handle_last_message(
    last_agent_message: Option<String>,
    last_message_file: Option<&Path>,
) -> std::io::Result<()> {
    match (last_agent_message, last_message_file) {
        (Some(last_agent_message), Some(last_message_file)) => {
            // Last message and a file to write to.
            std::fs::write(last_message_file, last_agent_message)?;
        }
        (None, Some(last_message_file)) => {
            eprintln!(
                "Warning: No last message to write to file: {}",
                last_message_file.to_string_lossy()
            );
        }
        (_, None) => {
            // No last message and no file to write to.
        }
    }
    Ok(())
}

async fn wait_for_supervisor_followup(
    log_dir: &std::path::Path,
    instance_id: &str,
    message_index: usize,
    model: Option<&str>,
) -> anyhow::Result<Option<String>> {
    use chrono::Utc;
    use std::fs;
    use tokio::time::Duration;
    use tokio::time::sleep;

    let status_file = log_dir.join("status.json");
    let followup_file = log_dir.join("followup_input.json");

    // Write status to indicate we're waiting for followup
    let mut status_obj = serde_json::json!({
        "status": "waiting_for_followup",
        "instance_id": instance_id,
        "last_message_index": message_index,
        "timestamp": Utc::now().to_rfc3339()
    });

    // Add model information if available
    if let Some(model_name) = model {
        status_obj["model"] = serde_json::Value::String(model_name.to_string());
    }

    let status = status_obj;

    fs::write(&status_file, serde_json::to_string_pretty(&status)?)?;
    info!("Waiting for supervisor followup...");

    // Poll for followup file with timeout
    let timeout_duration = Duration::from_secs(300); // 5 minute timeout
    let start_time = tokio::time::Instant::now();

    loop {
        // Check if followup file exists
        if followup_file.exists() {
            match fs::read_to_string(&followup_file) {
                Ok(content) => {
                    // Parse the followup JSON
                    match serde_json::from_str::<serde_json::Value>(&content) {
                        Ok(followup_json) => {
                            // Remove the followup file to prepare for next iteration
                            let _ = fs::remove_file(&followup_file);

                            if let Some(message) =
                                followup_json.get("message").and_then(|m| m.as_str())
                            {
                                if message.trim().is_empty() {
                                    // Empty message means terminate
                                    return Ok(None);
                                } else {
                                    // Return the followup message
                                    return Ok(Some(message.to_string()));
                                }
                            } else if followup_json
                                .get("terminate")
                                .and_then(|t| t.as_bool())
                                .unwrap_or(false)
                            {
                                // Explicit termination
                                return Ok(None);
                            }
                        }
                        Err(e) => {
                            error!("Failed to parse followup JSON: {e:?}");
                        }
                    }
                }
                Err(e) => {
                    error!("Failed to read followup file: {e:?}");
                }
            }
        }

        // Check timeout
        if start_time.elapsed() > timeout_duration {
            info!("Timeout waiting for supervisor followup, terminating");
            return Ok(None);
        }

        // Sleep before next check
        sleep(Duration::from_millis(500)).await;
    }
}
