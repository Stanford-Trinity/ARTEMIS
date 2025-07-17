use anyhow::Context;
use clap::Parser;
use codex_cli::LandlockCommand;
use codex_cli::SeatbeltCommand;
use codex_cli::login::run_login_with_chatgpt;
use codex_cli::proto;
use codex_common::CliConfigOverrides;
use codex_exec::Cli as ExecCli;
use codex_tui::Cli as TuiCli;
use std::path::PathBuf;
use tiktoken_rs::o200k_base;

use crate::proto::ProtoCli;

/// Codex CLI
///
/// If no subcommand is specified, options will be forwarded to the interactive CLI.
#[derive(Debug, Parser)]
#[clap(
    author,
    version,
    // If a sub‑command is given, ignore requirements of the default args.
    subcommand_negates_reqs = true
)]
struct MultitoolCli {
    #[clap(flatten)]
    pub config_overrides: CliConfigOverrides,

    #[clap(flatten)]
    interactive: TuiCli,

    #[clap(subcommand)]
    subcommand: Option<Subcommand>,
}

#[derive(Debug, clap::Subcommand)]
enum Subcommand {
    /// Run Codex non-interactively.
    #[clap(visible_alias = "e")]
    Exec(ExecCli),

    /// Login with ChatGPT.
    Login(LoginCommand),

    /// Experimental: run Codex as an MCP server.
    Mcp,

    /// Run Codex in autonomous mode with external LLM driver.
    #[clap(visible_alias = "auto")]
    Autonomous(AutonomousCommand),

    /// Run the Protocol stream via stdin/stdout
    #[clap(visible_alias = "p")]
    Proto(ProtoCli),

    /// Internal debugging commands.
    Debug(DebugArgs),
}

#[derive(Debug, Parser)]
struct DebugArgs {
    #[command(subcommand)]
    cmd: DebugCommand,
}

#[derive(Debug, clap::Subcommand)]
enum DebugCommand {
    /// Run a command under Seatbelt (macOS only).
    Seatbelt(SeatbeltCommand),

    /// Run a command under Landlock+seccomp (Linux only).
    Landlock(LandlockCommand),
}

#[derive(Debug, Parser)]
struct LoginCommand {
    #[clap(skip)]
    config_overrides: CliConfigOverrides,
}

#[derive(Debug, Parser)]
struct AutonomousCommand {
    /// Path to the configuration YAML file.
    #[clap(long, short = 'f', value_name = "FILE")]
    config_file: PathBuf,

    /// Duration to run in autonomous mode (in minutes).
    #[clap(long, short = 'd', default_value = "30")]
    duration: u64,

    /// Model to use for the external LLM driver.
    #[clap(long, short = 'm', default_value = "o3")]
    driver_model: String,

    /// Enable full-auto mode (skip all approvals and use workspace-write sandbox).
    #[clap(long = "full-auto")]
    full_auto: bool,

    /// Resume from an existing autonomous session directory.
    #[clap(long, value_name = "DIR")]
    resume_dir: Option<PathBuf>,

    /// Start hour for active operation (0-23, Pacific time).
    #[clap(long, default_value = "0")]
    work_start_hour: u8,

    /// End hour for active operation (0-23, Pacific time).
    #[clap(long, default_value = "23")]
    work_end_hour: u8,

    #[clap(flatten)]
    config_overrides: CliConfigOverrides,
}

fn main() -> anyhow::Result<()> {
    codex_linux_sandbox::run_with_sandbox(|codex_linux_sandbox_exe| async move {
        cli_main(codex_linux_sandbox_exe).await?;
        Ok(())
    })
}

async fn cli_main(codex_linux_sandbox_exe: Option<PathBuf>) -> anyhow::Result<()> {
    let cli = MultitoolCli::parse();

    match cli.subcommand {
        None => {
            let mut tui_cli = cli.interactive;
            prepend_config_flags(&mut tui_cli.config_overrides, cli.config_overrides);
            codex_tui::run_main(tui_cli, codex_linux_sandbox_exe)?;
        }
        Some(Subcommand::Exec(mut exec_cli)) => {
            prepend_config_flags(&mut exec_cli.config_overrides, cli.config_overrides);
            codex_exec::run_main(exec_cli, codex_linux_sandbox_exe).await?;
        }
        Some(Subcommand::Mcp) => {
            codex_mcp_server::run_main(codex_linux_sandbox_exe).await?;
        }
        Some(Subcommand::Autonomous(mut autonomous_cli)) => {
            prepend_config_flags(&mut autonomous_cli.config_overrides, cli.config_overrides);
            run_autonomous_mode(autonomous_cli, codex_linux_sandbox_exe).await?;
        }
        Some(Subcommand::Login(mut login_cli)) => {
            prepend_config_flags(&mut login_cli.config_overrides, cli.config_overrides);
            run_login_with_chatgpt(login_cli.config_overrides).await;
        }
        Some(Subcommand::Proto(mut proto_cli)) => {
            prepend_config_flags(&mut proto_cli.config_overrides, cli.config_overrides);
            proto::run_main(proto_cli).await?;
        }
        Some(Subcommand::Debug(debug_args)) => match debug_args.cmd {
            DebugCommand::Seatbelt(mut seatbelt_cli) => {
                prepend_config_flags(&mut seatbelt_cli.config_overrides, cli.config_overrides);
                codex_cli::debug_sandbox::run_command_under_seatbelt(
                    seatbelt_cli,
                    codex_linux_sandbox_exe,
                )
                .await?;
            }
            DebugCommand::Landlock(mut landlock_cli) => {
                prepend_config_flags(&mut landlock_cli.config_overrides, cli.config_overrides);
                codex_cli::debug_sandbox::run_command_under_landlock(
                    landlock_cli,
                    codex_linux_sandbox_exe,
                )
                .await?;
            }
        },
    }

    Ok(())
}

async fn run_autonomous_mode(
    autonomous_cli: AutonomousCommand,
    _codex_linux_sandbox_exe: Option<PathBuf>,
) -> anyhow::Result<()> {
    use codex_core::codex_wrapper::init_codex;
    use codex_core::config::Config;
    use codex_core::protocol::{InputItem, Op};
    use std::time::{Duration, Instant};
    use tokio::time::sleep;
    use chrono::{DateTime, Utc, Timelike};
    use chrono_tz::US::Pacific;

    // Helper functions for work hours
    let is_outside_work_hours = |start_hour: u8, end_hour: u8| -> bool {
        let now_pacific = Utc::now().with_timezone(&Pacific);
        let current_hour = now_pacific.hour() as u8;
        
        if start_hour <= end_hour {
            // Normal case: 9-17 (current < start OR current >= end)
            current_hour < start_hour || current_hour >= end_hour
        } else {
            // Overnight case: 22-6 (current >= end AND current < start)
            current_hour >= end_hour && current_hour < start_hour
        }
    };

    let calculate_next_work_time = |start_hour: u8, end_hour: u8| -> DateTime<chrono_tz::Tz> {
        let now_pacific = Utc::now().with_timezone(&Pacific);
        let current_hour = now_pacific.hour() as u8;
        
        if start_hour <= end_hour {
            // Normal case: 9-17
            if current_hour < start_hour {
                // Same day, wait until start hour
                now_pacific.date_naive().and_hms_opt(start_hour as u32, 0, 0)
                    .unwrap().and_local_timezone(Pacific).unwrap()
            } else {
                // Next day at start hour
                (now_pacific.date_naive() + chrono::Duration::days(1))
                    .and_hms_opt(start_hour as u32, 0, 0)
                    .unwrap().and_local_timezone(Pacific).unwrap()
            }
        } else {
            // Overnight case: 22-6
            if current_hour >= start_hour {
                // Continue tonight until end hour (next day)
                (now_pacific.date_naive() + chrono::Duration::days(1))
                    .and_hms_opt(end_hour as u32, 0, 0)
                    .unwrap().and_local_timezone(Pacific).unwrap()
            } else if current_hour < end_hour {
                // Already in work hours (after midnight)
                now_pacific
            } else {
                // Wait until start hour today
                now_pacific.date_naive().and_hms_opt(start_hour as u32, 0, 0)
                    .unwrap().and_local_timezone(Pacific).unwrap()
            }
        }
    };

    let sleep_until_work_hours = |target_time: DateTime<chrono_tz::Tz>| async move {
        loop {
            let now_pacific = Utc::now().with_timezone(&Pacific);
            if now_pacific >= target_time {
                break;
            }
            
            let sleep_duration = std::cmp::min(
                (target_time - now_pacific).num_seconds(),
                300 // Max 5 minutes between checks
            ) as u64;
            
            println!("😴 Sleeping for {} seconds until work hours resume...", sleep_duration);
            tokio::time::sleep(Duration::from_secs(sleep_duration)).await;
        }
    };

    println!("🚀 Starting autonomous mode...");
    println!("📁 Config file: {:?}", autonomous_cli.config_file);
    if let Some(ref resume_dir) = autonomous_cli.resume_dir {
        println!("🔄 Resuming from: {:?}", resume_dir);
    }
    println!("⏰ Duration: {} minutes", autonomous_cli.duration);
    println!("🤖 Driver model: {}", autonomous_cli.driver_model);

    // Load config file
    let config_content =
        std::fs::read_to_string(&autonomous_cli.config_file).with_context(|| {
            format!(
                "Failed to read config file: {:?}",
                autonomous_cli.config_file
            )
        })?;

    // Load prompt templates from core directory
    let core_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("core");

    let initial_prompt_file = core_dir.join("initial_prompt.txt");
    let continuation_prompt_file = core_dir.join("continuation_prompt.txt");
    let approval_prompt_file = core_dir.join("approval_prompt.txt");
    let bugcrowd_approval_prompt_file = core_dir.join("bugcrowd_approval_prompt.txt");
    let summarization_prompt_file = core_dir.join("summarization_prompt.txt");

    let initial_prompt_template =
        std::fs::read_to_string(&initial_prompt_file).with_context(|| {
            format!(
                "Failed to read initial prompt file: {:?}",
                initial_prompt_file
            )
        })?;

    let continuation_prompt_template = std::fs::read_to_string(&continuation_prompt_file)
        .with_context(|| {
            format!(
                "Failed to read continuation prompt file: {:?}",
                continuation_prompt_file
            )
        })?;

    let approval_prompt_template =
        std::fs::read_to_string(&approval_prompt_file).with_context(|| {
            format!(
                "Failed to read approval prompt file: {:?}",
                approval_prompt_file
            )
        })?;

    let bugcrowd_approval_prompt_template = std::fs::read_to_string(&bugcrowd_approval_prompt_file)
        .with_context(|| {
            format!(
                "Failed to read bugcrowd approval prompt file: {:?}",
                bugcrowd_approval_prompt_file
            )
        })?;

    let summarization_prompt_template = std::fs::read_to_string(&summarization_prompt_file)
        .with_context(|| {
            format!(
                "Failed to read summarization prompt file: {:?}",
                summarization_prompt_file
            )
        })?;

    println!("📋 Task config loaded");
    println!("📝 Prompt templates loaded");

    // Create codex config with overrides, applying full-auto settings if enabled
    let mut config_overrides = codex_core::config::ConfigOverrides::default();
    if autonomous_cli.full_auto {
        config_overrides.approval_policy = Some(codex_core::protocol::AskForApproval::OnFailure);
        config_overrides.sandbox_policy =
            Some(codex_core::protocol::SandboxPolicy::WorkspaceWrite {
                writable_roots: vec![],
                network_access: true,
            });
    }

    let config = Config::load_with_cli_overrides(
        autonomous_cli
            .config_overrides
            .parse_overrides()
            .map_err(anyhow::Error::msg)?,
        config_overrides,
    )
    .with_context(|| "Failed to load codex config")?;

    // Initialize codex session
    let (codex, _init_event, _ctrl_c) = init_codex(config.clone()).await?;
    println!("✅ Codex session initialized");

    // Initialize context accumulator and conversation log
    let mut context = String::new();
    let mut conversation_log = Vec::new();
    let mut iteration = 0;

    // Load resume context if resume directory is provided
    if let Some(ref resume_dir) = autonomous_cli.resume_dir {
        println!("🔄 Loading resume context from {:?}", resume_dir);
        
        // Load context from context_log.txt
        let context_log_file = resume_dir.join("context_log.txt");
        if context_log_file.exists() {
            context = std::fs::read_to_string(&context_log_file)
                .with_context(|| format!("Failed to read context log: {:?}", context_log_file))?;
            println!("✅ Context log loaded ({} bytes)", context.len());
        }
        
        // Load conversation from latest.json
        let latest_file = resume_dir.join("latest.json");
        if latest_file.exists() {
            let latest_content = std::fs::read_to_string(&latest_file)
                .with_context(|| format!("Failed to read latest.json: {:?}", latest_file))?;
            conversation_log = serde_json::from_str(&latest_content)
                .with_context(|| format!("Failed to parse latest.json: {:?}", latest_file))?;
            println!("✅ Conversation log loaded ({} messages)", conversation_log.len());
        }
        
        // Determine next iteration number from existing files
        let mut max_iteration = 0;
        if let Ok(entries) = std::fs::read_dir(resume_dir) {
            for entry in entries {
                if let Ok(entry) = entry {
                    let filename = entry.file_name().to_string_lossy().to_string();
                    if filename.starts_with("iteration_") && filename.ends_with(".json") {
                        if let Ok(iter_num) = filename[10..13].parse::<u32>() {
                            max_iteration = max_iteration.max(iter_num);
                        }
                    }
                }
            }
        }
        iteration = max_iteration + 1;
        println!("✅ Resuming from iteration {}", iteration);
    }
    let start_time = Instant::now();
    let duration = Duration::from_secs(autonomous_cli.duration * 60);

    // Create or use existing session-specific logs directory
    let session_timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    
    let session_logs_dir = if let Some(ref resume_dir) = autonomous_cli.resume_dir {
        // Use existing directory for resume
        resume_dir.clone()
    } else {
        // Create new session directory with timestamp
        let session_logs_dir =
            PathBuf::from("./logs").join(format!("autonomous_session_{}", session_timestamp));
        std::fs::create_dir_all(&session_logs_dir).with_context(|| {
            format!(
                "Failed to create session logs directory: {:?}",
                session_logs_dir
            )
        })?;
        session_logs_dir
    };

    println!("📁 Session logs directory: {:?}", session_logs_dir);

    // Create backup directory in home directory
    let backup_logs_dir = dirs::home_dir()
        .ok_or_else(|| anyhow::anyhow!("Could not find home directory"))?
        .join("codex-logs-backup")
        .join(format!("autonomous_session_{}", session_timestamp));
    std::fs::create_dir_all(&backup_logs_dir).with_context(|| {
        format!(
            "Failed to create backup logs directory: {:?}",
            backup_logs_dir
        )
    })?;
    println!("📁 Backup logs directory: {:?}", backup_logs_dir);

    // Load codex system prompt from prompt.md (only for new sessions)
    if autonomous_cli.resume_dir.is_none() {
        let prompt_md_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("core")
            .join("prompt.md");
        let system_prompt = std::fs::read_to_string(&prompt_md_path)
            .with_context(|| format!("Failed to read system prompt from: {:?}", prompt_md_path))?;

        // Add system message to conversation log
        conversation_log.push(serde_json::json!({
            "role": "system",
            "content": system_prompt
        }));
    }

    // Function to save checkpoint log files and update heartbeat
    let save_checkpoint = |log: &Vec<serde_json::Value>, iteration_num: u32| {
        let log_json = serde_json::to_string_pretty(log).unwrap_or_else(|_| "[]".to_string());

        // Save numbered checkpoint to both locations
        let checkpoint_path = session_logs_dir.join(format!("iteration_{:03}.json", iteration_num));
        let backup_checkpoint_path = backup_logs_dir.join(format!("iteration_{:03}.json", iteration_num));
        
        if let Err(e) = std::fs::write(&checkpoint_path, &log_json) {
            eprintln!("❌ Failed to save checkpoint {}: {}", iteration_num, e);
        } else {
            println!(
                "📝 Checkpoint {} saved to: {:?}",
                iteration_num, checkpoint_path
            );
        }
        
        if let Err(e) = std::fs::write(&backup_checkpoint_path, &log_json) {
            eprintln!("❌ Failed to save backup checkpoint {}: {}", iteration_num, e);
        } else {
            println!("📝 Backup checkpoint {} saved to: {:?}", iteration_num, backup_checkpoint_path);
        }

        // Also save as latest.json for easy access to both locations
        let latest_path = session_logs_dir.join("latest.json");
        let backup_latest_path = backup_logs_dir.join("latest.json");
        
        if let Err(e) = std::fs::write(&latest_path, &log_json) {
            eprintln!("❌ Failed to save latest.json: {}", e);
        }
        
        if let Err(e) = std::fs::write(&backup_latest_path, &log_json) {
            eprintln!("❌ Failed to save backup latest.json: {}", e);
        }

        // Save session metadata to both locations
        let current_time = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let metadata = serde_json::json!({
            "session_start": session_timestamp,
            "current_iteration": iteration_num,
            "elapsed_seconds": start_time.elapsed().as_secs(),
            "last_updated": current_time
        });
        let metadata_path = session_logs_dir.join("session_info.json");
        let backup_metadata_path = backup_logs_dir.join("session_info.json");
        
        if let Err(e) = std::fs::write(
            &metadata_path,
            serde_json::to_string_pretty(&metadata).unwrap_or_default(),
        ) {
            eprintln!("❌ Failed to save session metadata: {}", e);
        }
        
        if let Err(e) = std::fs::write(
            &backup_metadata_path,
            serde_json::to_string_pretty(&metadata).unwrap_or_default(),
        ) {
            eprintln!("❌ Failed to save backup session metadata: {}", e);
        }

        // Save heartbeat file for health monitor
        let heartbeat = serde_json::json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "iteration": iteration_num,
            "session_timestamp": session_timestamp,
            "elapsed_seconds": start_time.elapsed().as_secs(),
            "status": "running",
            "pid": std::process::id(),
            "config_file": autonomous_cli.config_file.to_string_lossy(),
            "duration_minutes": autonomous_cli.duration,
            "driver_model": &autonomous_cli.driver_model,
            "full_auto": autonomous_cli.full_auto
        });

        let heartbeat_json = serde_json::to_string_pretty(&heartbeat).unwrap_or_default();

        // Save heartbeat in session directory and backup
        let heartbeat_path = session_logs_dir.join("heartbeat.json");
        let backup_heartbeat_path = backup_logs_dir.join("heartbeat.json");
        
        if let Err(e) = std::fs::write(&heartbeat_path, &heartbeat_json) {
            eprintln!("❌ Failed to save heartbeat: {}", e);
        }
        
        if let Err(e) = std::fs::write(&backup_heartbeat_path, &heartbeat_json) {
            eprintln!("❌ Failed to save backup heartbeat: {}", e);
        }

        // Also save heartbeat to global location for health monitor
        let global_heartbeat_path = PathBuf::from("./logs/latest_session_heartbeat.json");
        let backup_global_heartbeat_path = dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("codex-logs-backup")
            .join("latest_session_heartbeat.json");
            
        if let Err(e) = std::fs::write(&global_heartbeat_path, &heartbeat_json) {
            eprintln!("❌ Failed to save global heartbeat: {}", e);
        }
        
        if let Err(e) = std::fs::write(&backup_global_heartbeat_path, &heartbeat_json) {
            eprintln!("❌ Failed to save backup global heartbeat: {}", e);
        }
    };

    // Save initial checkpoint with system message
    save_checkpoint(&conversation_log, 0);
    println!(
        "🚀 Session {} started with {} minute duration",
        session_timestamp, autonomous_cli.duration
    );

    // Main autonomous loop with error handling
    let loop_result = async {
        while start_time.elapsed() < duration {
            iteration += 1;
            println!(
                "\n🔄 Iteration {} ({}s elapsed)",
                iteration,
                start_time.elapsed().as_secs()
            );

            // Determine which prompt template to use
            let prompt_template = if iteration == 1 {
                &initial_prompt_template
            } else {
                &continuation_prompt_template
            };

            // Check context token count and summarize if needed
            let mut final_context = context.clone();
            let mut context_was_summarized = false;
            let context_tokens = count_tokens(&context)?;
            const MAX_TOKENS: usize = 200_000;
            const TOKEN_BUFFER: usize = 500;

            if context_tokens > (MAX_TOKENS - TOKEN_BUFFER) {
                println!(
                    "⚠️  Context approaching token limit: {} tokens (max: {})",
                    context_tokens, MAX_TOKENS
                );

                // Summarize the formatted context string (but keep conversation_log intact)
                final_context = summarize_context(
                    &context,
                    &autonomous_cli.driver_model,
                    &summarization_prompt_template,
                )
                .await?;

                context_was_summarized = true;
                println!(
                    "✅ Context summarized from {} to {} tokens",
                    context_tokens,
                    count_tokens(&final_context)?
                );
            }

            // Inject config and context into prompt template
            let driver_prompt =
                inject_template_variables(prompt_template, &config_content, &final_context);

            // Check final driver prompt token count
            let driver_prompt_tokens = count_tokens(&driver_prompt)?;
            println!("📊 Driver prompt tokens: {}", driver_prompt_tokens);

            if driver_prompt_tokens > (MAX_TOKENS - TOKEN_BUFFER) {
                return Err(anyhow::anyhow!(
                    "Driver prompt still too long after summarization: {} tokens (max: {})",
                    driver_prompt_tokens,
                    MAX_TOKENS - TOKEN_BUFFER
                ));
            }

            // Generate user prompt using external LLM
            let (user_prompt, tool_results) =
                generate_user_prompt(&driver_prompt, &autonomous_cli.driver_model, &session_logs_dir).await?;

            println!("💭 Generated user prompt: {}", user_prompt);

            // Handle supervisor LLM tool calls and generate final user prompt
            let final_user_prompt = if !tool_results.is_empty() {
                // Case 2: Supervisor made tool calls - need to get follow-up response
                
                // Add user message with tool calls to conversation log
                conversation_log.push(serde_json::json!({
                    "role": "user",
                    "content": user_prompt,
                    "tool_calls": tool_results.iter().map(|tr| {
                        // Find the original tool call to get the correct tool name
                        let tool_call_id = tr["tool_call_id"].as_str().unwrap_or("");
                        let tool_name = tr.get("tool_name").and_then(|n| n.as_str()).unwrap_or("unknown");
                        serde_json::json!({
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": serde_json::json!({})
                            }
                        })
                    }).collect::<Vec<_>>()
                }));

                // Add tool results to conversation log
                for tool_result in &tool_results {
                    conversation_log.push(serde_json::json!({
                        "role": "tool",
                        "tool_call_id": tool_result["tool_call_id"],
                        "content": tool_result["content"]
                    }));
                }

                // Generate follow-up prompt from supervisor with tool results
                let follow_up_context = format!("{}\n\nTool Results:\n{}", 
                    final_context, 
                    serde_json::to_string_pretty(&tool_results).unwrap_or_default()
                );
                
                let follow_up_driver_prompt = inject_template_variables(
                    &continuation_prompt_template,
                    &config_content,
                    &follow_up_context,
                );

                let (follow_up_prompt, _) = generate_user_prompt(
                    &follow_up_driver_prompt,
                    &autonomous_cli.driver_model,
                    &session_logs_dir,
                ).await?;

                println!("🔄 Supervisor follow-up prompt: {}", follow_up_prompt);

                // Add follow-up user message to conversation log
                conversation_log.push(serde_json::json!({
                    "role": "user",
                    "content": follow_up_prompt
                }));

                // Update context with follow-up conversation
                final_context = format!("{}\n\nUSER: {}\n\nASSISTANT: {}", 
                    final_context, follow_up_prompt, follow_up_prompt);

                follow_up_prompt
            } else {
                // Case 1: No tool calls - use original supervisor message directly
                
                // Add regular user message to conversation log
                conversation_log.push(serde_json::json!({
                    "role": "user",
                    "content": user_prompt
                }));

                user_prompt
            };

            // Submit to codex
            let input_items = vec![InputItem::Text {
                text: final_user_prompt.clone(),
            }];
            let submission_id = codex.submit(Op::UserInput { items: input_items }).await?;

            // Collect codex response and tool calls
            let (codex_response, tool_calls, reasoning, tool_responses) =
                collect_codex_response_with_tools(
                    &codex,
                    &submission_id,
                    autonomous_cli.full_auto,
                    &autonomous_cli.driver_model,
                    &approval_prompt_template,
                    &bugcrowd_approval_prompt_template,
                    &session_logs_dir,
                    &config_content,
                )
                .await?;

            println!("🤖 Codex response collected");

            // Add events in correct chronological order:

            // 1. Assistant reasoning (if present)
            if let Some(reasoning_text) = reasoning {
                conversation_log.push(serde_json::json!({
                    "role": "assistant",
                    "content": "",
                    "reasoning": reasoning_text
                }));
            }

            // 2. Assistant tool calls (if any)
            if !tool_calls.is_empty() {
                conversation_log.push(serde_json::json!({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls
                }));
            }

            // 3. Tool responses
            for tool_response in tool_responses {
                conversation_log.push(tool_response);
            }

            // 4. Final assistant response
            conversation_log.push(serde_json::json!({
                "role": "assistant",
                "content": codex_response
            }));

            // Build readable conversation context
            let mut readable_context = String::new();
            for msg in &conversation_log {
                match msg.get("role").and_then(|r| r.as_str()) {
                    Some("system") => {
                        readable_context.push_str(&format!(
                            "SYSTEM: {}\n\n",
                            msg.get("content").and_then(|c| c.as_str()).unwrap_or("")
                        ));
                    }
                    Some("user") => {
                        readable_context.push_str(&format!(
                            "USER: {}\n\n",
                            msg.get("content").and_then(|c| c.as_str()).unwrap_or("")
                        ));
                    }
                    Some("assistant") => {
                        if let Some(reasoning) = msg.get("reasoning") {
                            readable_context.push_str(&format!(
                                "ASSISTANT_REASONING: {}\n\n",
                                reasoning.as_str().unwrap_or("")
                            ));
                        } else if let Some(tool_calls) = msg.get("tool_calls") {
                            // Filter out system tool calls
                            let empty_vec = vec![];
                            let tool_calls_array = tool_calls.as_array().unwrap_or(&empty_vec);
                            let filtered_tool_calls: Vec<_> = tool_calls_array
                                .iter()
                                .filter(|tool_call| {
                                    tool_call.get("type").and_then(|t| t.as_str()) != Some("system")
                                })
                                .collect();
                            
                            if !filtered_tool_calls.is_empty() {
                                readable_context.push_str(&format!(
                                    "ASSISTANT_TOOL_CALLS: {}\n\n",
                                    serde_json::to_string_pretty(&filtered_tool_calls).unwrap_or_default()
                                ));
                            }
                        } else {
                            readable_context.push_str(&format!(
                                "ASSISTANT: {}\n\n",
                                msg.get("content").and_then(|c| c.as_str()).unwrap_or("")
                            ));
                        }
                    }
                    Some("tool") => {
                        readable_context.push_str(&format!(
                            "TOOL_RESPONSE: {}\n\n",
                            msg.get("content").and_then(|c| c.as_str()).unwrap_or("")
                        ));
                    }
                    _ => {
                        // Skip unknown roles
                    }
                }
            }

            // Use summarized context if we summarized this iteration, otherwise use rebuilt context
            if context_was_summarized {
                context = final_context;
            } else {
                context = readable_context;
            }

            // Save context string to file for testing
            let context_log_path = session_logs_dir.join("context_log.txt");
            if let Err(e) = std::fs::write(&context_log_path, &context) {
                eprintln!("❌ Failed to save context log: {}", e);
            }

            // Save checkpoint after each iteration
            save_checkpoint(&conversation_log, iteration as u32);

            // Check if we should pause for work hours
            if is_outside_work_hours(autonomous_cli.work_start_hour, autonomous_cli.work_end_hour) {
                let next_work_time = calculate_next_work_time(autonomous_cli.work_start_hour, autonomous_cli.work_end_hour);
                println!("⏸️  Outside work hours ({}:00-{}:00 Pacific). Pausing until {}", 
                    autonomous_cli.work_start_hour, autonomous_cli.work_end_hour, 
                    next_work_time.format("%Y-%m-%d %H:%M %Z"));
                
                // Update heartbeat to show paused status
                let paused_heartbeat = serde_json::json!({
                    "timestamp": chrono::Utc::now().to_rfc3339(),
                    "iteration": iteration,
                    "session_timestamp": session_timestamp,
                    "elapsed_seconds": start_time.elapsed().as_secs(),
                    "status": "paused",
                    "resume_time": next_work_time.to_rfc3339(),
                    "pid": std::process::id(),
                    "config_file": autonomous_cli.config_file.to_string_lossy(),
                    "duration_minutes": autonomous_cli.duration,
                    "driver_model": &autonomous_cli.driver_model,
                    "full_auto": autonomous_cli.full_auto
                });
                
                let paused_heartbeat_json = serde_json::to_string_pretty(&paused_heartbeat).unwrap_or_default();
                let global_heartbeat_path = PathBuf::from("./logs/latest_session_heartbeat.json");
                let backup_heartbeat_path = dirs::home_dir()
                    .unwrap_or_default()
                    .join("codex-logs-backup")
                    .join("latest_session_heartbeat.json");
                
                let _ = std::fs::write(&global_heartbeat_path, &paused_heartbeat_json);
                let _ = std::fs::write(&backup_heartbeat_path, &paused_heartbeat_json);
                
                sleep_until_work_hours(next_work_time).await;
                
                println!("▶️  Work hours resumed. Continuing autonomous mode...");
            }

            // Wait before next iteration
            sleep(Duration::from_secs(10)).await;
        }

        println!(
            "✅ Autonomous mode completed after {} iterations",
            iteration
        );
        Ok::<(), anyhow::Error>(())
    }
    .await;

    // Save final checkpoint regardless of how we exit
    save_checkpoint(&conversation_log, iteration as u32);

    // Update final heartbeat with completion status
    let final_status = if loop_result.is_ok() {
        "completed"
    } else {
        "error"
    };
    let final_heartbeat = serde_json::json!({
        "timestamp": chrono::Utc::now().to_rfc3339(),
        "iteration": iteration,
        "session_timestamp": session_timestamp,
        "elapsed_seconds": start_time.elapsed().as_secs(),
        "status": final_status,
        "pid": std::process::id(),
        "config_file": autonomous_cli.config_file.to_string_lossy(),
        "duration_minutes": autonomous_cli.duration,
        "driver_model": &autonomous_cli.driver_model,
        "full_auto": autonomous_cli.full_auto
    });

    let final_heartbeat_json = serde_json::to_string_pretty(&final_heartbeat).unwrap_or_default();
    let global_heartbeat_path = PathBuf::from("./logs/latest_session_heartbeat.json");
    if let Err(e) = std::fs::write(&global_heartbeat_path, &final_heartbeat_json) {
        eprintln!("❌ Failed to save final heartbeat: {}", e);
    }

    println!(
        "🏁 Final checkpoint saved for session {}",
        session_timestamp
    );

    // Return the result
    loop_result
}

async fn collect_codex_response_with_tools(
    codex: &codex_core::Codex,
    submission_id: &str,
    _full_auto: bool,
    driver_model: &str,
    approval_prompt_template: &str,
    bugcrowd_approval_prompt_template: &str,
    session_logs_dir: &std::path::Path,
    config_content: &str,
) -> anyhow::Result<(
    String,
    Vec<serde_json::Value>,
    Option<String>,
    Vec<serde_json::Value>,
)> {
    use codex_core::protocol::EventMsg;
    let mut assistant_content = String::new();
    let mut reasoning_content = String::new();
    let mut tool_calls = Vec::new();
    let mut tool_responses = Vec::new();
    let mut task_complete = false;
    let mut denied_tool_calls = std::collections::HashSet::new();

    // Collect events until task is complete
    while !task_complete {
        match codex.next_event().await {
            Ok(event) => {
                if event.id == submission_id {
                    match event.msg {
                        EventMsg::AgentMessage(msg) => {
                            println!("🤖 Agent: {}", msg.message);
                            assistant_content.push_str(&msg.message);
                            assistant_content.push('\n');
                        }
                        EventMsg::AgentReasoning(reasoning) => {
                            println!("🧠 Reasoning: {}", reasoning.text);
                            reasoning_content.push_str(&reasoning.text);
                            reasoning_content.push('\n');
                        }
                        EventMsg::ExecCommandBegin(cmd) => {
                            println!("⚡ Executing: {:?}", cmd.command);
                            // Add bash command as a tool call
                            tool_calls.push(serde_json::json!({
                                "id": format!("exec_{}", cmd.call_id),
                                "type": "function",
                                "function": {
                                    "name": "bash",
                                    "arguments": serde_json::to_string(&serde_json::json!({
                                        "command": cmd.command
                                    })).unwrap_or_default()
                                },
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }));
                        }
                        EventMsg::ExecCommandEnd(result) => {
                            let stdout_preview = if result.stdout.len() > 200 {
                                &result.stdout[..200]
                            } else {
                                &result.stdout
                            };
                            println!("📊 Command result: {}", stdout_preview);
                            // Add bash command result as a tool response
                            tool_responses.push(serde_json::json!({
                                "role": "tool",
                                "tool_call_id": format!("exec_{}", result.call_id),
                                "content": serde_json::to_string(&serde_json::json!({
                                    "exit_code": result.exit_code,
                                    "stdout": result.stdout,
                                    "stderr": result.stderr
                                })).unwrap_or_default(),
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }));
                        }
                        EventMsg::McpToolCallBegin(tool) => {
                            println!("🔧 Calling tool: {}", tool.tool);

                            // Check if this is a bugcrowd_submit call - always require external LLM approval
                            if tool.tool == "bugcrowd_submit" {
                                println!(
                                    "🤖 Requesting approval from external LLM for bugcrowd_submit tool..."
                                );

                                // Use the specialized bugcrowd approval prompt
                                let tool_approval_prompt = inject_bugcrowd_approval_variables(
                                    bugcrowd_approval_prompt_template,
                                    &tool.tool,
                                    &tool.arguments,
                                );

                                match generate_user_prompt(&tool_approval_prompt, driver_model, &session_logs_dir)
                                    .await
                                {
                                    Ok((response, _)) => {
                                        println!("🤖 External LLM response: {}", response);
                                        let (approved, reasoning) =
                                            parse_approval_response(&response);

                                        if approved {
                                            println!(
                                                "✅ Bugcrowd submission approved by external LLM: {}",
                                                reasoning
                                            );
                                            // Let the tool call proceed normally
                                        } else {
                                            println!(
                                                "❌ Bugcrowd submission denied by external LLM: {}",
                                                reasoning
                                            );

                                            // Track this call as denied so we ignore its McpToolCallEnd event
                                            denied_tool_calls.insert(tool.call_id.clone());

                                            // Create a fake tool response with the denial reasoning
                                            // This prevents the actual MCP tool from being called
                                            tool_responses.push(serde_json::json!({
                                                "role": "tool",
                                                "tool_call_id": tool.call_id,
                                                "content": format!("❌ Bugcrowd submission denied by security review: {}", reasoning)
                                            }));

                                            // Skip to next event - don't let this tool call proceed
                                            continue;
                                        }
                                    }
                                    Err(e) => {
                                        println!(
                                            "❌ Error getting approval from external LLM: {}",
                                            e
                                        );

                                        // Create a tool response with the error
                                        tool_responses.push(serde_json::json!({
                                            "role": "tool",
                                            "tool_call_id": tool.call_id,
                                            "content": format!("❌ Bugcrowd submission failed due to approval error: {}", e)
                                        }));

                                        // Skip to next event - don't let this tool call proceed
                                        continue;
                                    }
                                }
                            }

                            // Add tool call to OpenAI format
                            tool_calls.push(serde_json::json!({
                                "id": tool.call_id,
                                "type": "function",
                                "function": {
                                    "name": tool.tool,
                                    "arguments": serde_json::to_string(&tool.arguments).unwrap_or_default()
                                },
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }));
                        }
                        EventMsg::McpToolCallEnd(result) => {
                            // Skip results for denied tool calls (we already added the denial response)
                            if denied_tool_calls.contains(&result.call_id) {
                                println!(
                                    "🚫 Ignoring result for denied tool call: {}",
                                    result.call_id
                                );
                                continue;
                            }

                            match &result.result {
                                Ok(success) => {
                                    println!("✅ Tool result: {:?}", success);
                                    // Add tool response to conversation log
                                    tool_responses.push(serde_json::json!({
                                        "role": "tool",
                                        "tool_call_id": result.call_id,
                                        "content": serde_json::to_string(success).unwrap_or_default(),
                                        "timestamp": chrono::Utc::now().to_rfc3339()
                                    }));
                                }
                                Err(err) => {
                                    println!("❌ Tool error: {}", err);
                                    // Add tool error to conversation log
                                    tool_responses.push(serde_json::json!({
                                        "role": "tool",
                                        "tool_call_id": result.call_id,
                                        "content": format!("Error: {}", err),
                                        "timestamp": chrono::Utc::now().to_rfc3339()
                                    }));
                                }
                            }
                        }
                        EventMsg::ExecApprovalRequest(approval) => {
                            println!("🔍 Approval requested for command: {:?}", approval.command);

                            // Add approval request as a tool call
                            let approval_id = format!(
                                "approval_{}",
                                std::time::SystemTime::now()
                                    .duration_since(std::time::UNIX_EPOCH)
                                    .unwrap()
                                    .as_millis()
                            );
                            tool_calls.push(serde_json::json!({
                                "id": approval_id.clone(),
                                "type": "function",
                                "function": {
                                    "name": "request_approval",
                                    "arguments": serde_json::to_string(&approval).unwrap_or_default()
                                }
                            }));

                            // Check if it's a bugcrowd_submit call - always require external LLM approval
                            let is_bugcrowd_submit = approval.command.iter().any(|arg| {
                                arg.contains("bugcrowd_submit") || arg.contains("bugcrowd-submit")
                            });

                            // Generate approval prompt with task context
                            let approval_prompt = inject_approval_variables_with_context(
                                approval_prompt_template,
                                &approval.command,
                                &approval.cwd,
                                &approval.reason,
                                &config_content,
                            );

                            let context_info = if is_bugcrowd_submit {
                                " (BUGCROWD SUBMISSION - Requires careful review)"
                            } else {
                                ""
                            };

                            println!(
                                "🤖 Requesting approval from external LLM{}...",
                                context_info
                            );

                            let decision =
                                match generate_user_prompt(&approval_prompt, driver_model, &session_logs_dir).await {
                                    Ok((response, _)) => {
                                        println!("🤖 External LLM response: {}", response);
                                        if response.to_lowercase().contains("approve") {
                                            println!("✅ Approved by external LLM");
                                            codex_core::protocol::ReviewDecision::Approved
                                        } else {
                                            println!("❌ Denied by external LLM");
                                            codex_core::protocol::ReviewDecision::Denied
                                        }
                                    }
                                    Err(e) => {
                                        println!(
                                            "❌ Error getting approval from external LLM: {}",
                                            e
                                        );
                                        codex_core::protocol::ReviewDecision::Denied
                                    }
                                };

                            // Add approval decision as a tool response
                            tool_responses.push(serde_json::json!({
                                "role": "tool",
                                "tool_call_id": approval_id,
                                "content": serde_json::to_string(&serde_json::json!({
                                    "decision": decision,
                                    "llm_response": match &decision {
                                        codex_core::protocol::ReviewDecision::Approved => "✅ Approved by external LLM",
                                        codex_core::protocol::ReviewDecision::Denied => "❌ Denied by external LLM",
                                        _ => "❓ Unknown decision"
                                    }
                                })).unwrap_or_default()
                            }));

                            // Submit the approval decision back to codex
                            if let Err(e) = codex
                                .submit(codex_core::protocol::Op::ExecApproval {
                                    id: event.id.clone(),
                                    decision,
                                })
                                .await
                            {
                                println!("❌ Failed to submit approval decision: {}", e);
                            } else {
                                println!("✅ Approval decision submitted");
                            }
                        }
                        EventMsg::ApplyPatchApprovalRequest(patch_approval) => {
                            println!("🔍 Patch approval requested for {} files", patch_approval.changes.len());

                            // Add patch approval request as a tool call
                            let approval_id = format!(
                                "patch_approval_{}",
                                std::time::SystemTime::now()
                                    .duration_since(std::time::UNIX_EPOCH)
                                    .unwrap()
                                    .as_millis()
                            );
                            tool_calls.push(serde_json::json!({
                                "id": approval_id.clone(),
                                "type": "function",
                                "function": {
                                    "name": "request_patch_approval",
                                    "arguments": serde_json::to_string(&patch_approval).unwrap_or_default()
                                }
                            }));

                            // Generate patch approval prompt with task context
                            let patch_approval_prompt = inject_patch_approval_variables_with_context(
                                approval_prompt_template,
                                &patch_approval.changes,
                                &patch_approval.reason,
                                &config_content,
                            );

                            println!("🤖 Requesting patch approval from external LLM...");

                            let decision =
                                match generate_user_prompt(&patch_approval_prompt, driver_model, &session_logs_dir).await {
                                    Ok((response, _)) => {
                                        println!("🤖 External LLM response: {}", response);
                                        if response.to_lowercase().contains("approve") {
                                            println!("✅ Patch approved by external LLM");
                                            codex_core::protocol::ReviewDecision::Approved
                                        } else {
                                            println!("❌ Patch denied by external LLM");
                                            codex_core::protocol::ReviewDecision::Denied
                                        }
                                    }
                                    Err(e) => {
                                        println!(
                                            "❌ Error getting patch approval from external LLM: {}",
                                            e
                                        );
                                        codex_core::protocol::ReviewDecision::Denied
                                    }
                                };

                            // Add patch approval decision as a tool response
                            tool_responses.push(serde_json::json!({
                                "role": "tool",
                                "tool_call_id": approval_id,
                                "content": serde_json::to_string(&serde_json::json!({
                                    "decision": decision,
                                    "llm_response": match &decision {
                                        codex_core::protocol::ReviewDecision::Approved => "✅ Patch approved by external LLM",
                                        codex_core::protocol::ReviewDecision::Denied => "❌ Patch denied by external LLM",
                                        _ => "❓ Unknown decision"
                                    }
                                })).unwrap_or_default()
                            }));

                            // Submit the patch approval decision back to codex
                            if let Err(e) = codex
                                .submit(codex_core::protocol::Op::PatchApproval {
                                    id: event.id.clone(),
                                    decision,
                                })
                                .await
                            {
                                println!("❌ Failed to submit patch approval decision: {}", e);
                            } else {
                                println!("✅ Patch approval decision submitted");
                            }
                        }
                        EventMsg::TaskStarted => {
                            println!("📝 Event: TaskStarted");
                            // Add as a system event
                            tool_calls.push(serde_json::json!({
                                "id": format!("event_taskstarted_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis()),
                                "type": "system",
                                "function": {
                                    "name": "task_started",
                                    "arguments": "{}"
                                }
                            }));
                        }
                        EventMsg::TokenCount(token_usage) => {
                            println!("📝 Event: TokenCount({:?})", token_usage);
                            // Add as a system event
                            tool_calls.push(serde_json::json!({
                                "id": format!("event_tokencount_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis()),
                                "type": "system",
                                "function": {
                                    "name": "token_count",
                                    "arguments": serde_json::to_string(&token_usage).unwrap_or_default()
                                }
                            }));
                        }
                        EventMsg::BackgroundEvent(bg_event) => {
                            println!("📝 Event: BackgroundEvent({})", bg_event.message);
                            // Add as a system event
                            tool_calls.push(serde_json::json!({
                                "id": format!("event_background_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis()),
                                "type": "system",
                                "function": {
                                    "name": "background_event",
                                    "arguments": serde_json::to_string(&bg_event).unwrap_or_default()
                                }
                            }));
                        }
                        EventMsg::PatchApplyBegin(patch_event) => {
                            println!("🔧 Applying patch: {}", patch_event.call_id);
                            // Add as a tool call
                            tool_calls.push(serde_json::json!({
                                "id": format!("patch_{}", patch_event.call_id),
                                "type": "function",
                                "function": {
                                    "name": "apply_patch",
                                    "arguments": serde_json::to_string(&patch_event).unwrap_or_default()
                                },
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }));
                        }
                        EventMsg::PatchApplyEnd(patch_result) => {
                            println!("✅ Patch applied: {}", patch_result.call_id);
                            // Add as a tool response
                            tool_responses.push(serde_json::json!({
                                "role": "tool",
                                "tool_call_id": format!("patch_{}", patch_result.call_id),
                                "content": serde_json::to_string(&patch_result).unwrap_or_default(),
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }));
                        }
                        EventMsg::TaskComplete(_) => {
                            println!("✅ Task completed");
                            task_complete = true;
                        }
                        EventMsg::Error(err) => {
                            println!("❌ Error: {}", err.message);
                            task_complete = true;
                        }
                        _ => {
                            // Log other events for debugging
                            println!("📝 Event: {:?}", event.msg);
                        }
                    }
                }
            }
            Err(e) => {
                return Err(anyhow::anyhow!("Error receiving event: {}", e));
            }
        }
    }

    let reasoning = if reasoning_content.trim().is_empty() {
        None
    } else {
        Some(reasoning_content.trim().to_string())
    };

    Ok((
        assistant_content.trim().to_string(),
        tool_calls,
        reasoning,
        tool_responses,
    ))
}

fn inject_template_variables(template: &str, config_yaml: &str, context: &str) -> String {
    template
        .replace("{config_yaml}", config_yaml)
        .replace("{context}", context)
}

fn inject_approval_variables(
    template: &str,
    command: &[String],
    cwd: &std::path::Path,
    reason: &Option<String>,
) -> String {
    let command_str = format!("{:?}", command);
    let cwd_str = format!("{:?}", cwd);
    let reason_str = reason.as_deref().unwrap_or("No reason provided");

    template
        .replace("{command}", &command_str)
        .replace("{cwd}", &cwd_str)
        .replace("{reason}", reason_str)
}

fn inject_approval_variables_with_context(
    template: &str,
    command: &[String],
    cwd: &std::path::Path,
    reason: &Option<String>,
    config_content: &str,
) -> String {
    let command_str = format!("{:?}", command);
    let cwd_str = format!("{:?}", cwd);
    let reason_str = reason.as_deref().unwrap_or("No reason provided");

    template
        .replace("{command}", &command_str)
        .replace("{cwd}", &cwd_str)
        .replace("{reason}", reason_str)
        .replace("{task_context}", config_content)
}

fn inject_patch_approval_variables_with_context(
    template: &str,
    changes: &std::collections::HashMap<std::path::PathBuf, codex_core::protocol::FileChange>,
    reason: &Option<String>,
    config_content: &str,
) -> String {
    let changes_str = format!("{:#?}", changes);
    let reason_str = reason.as_deref().unwrap_or("No reason provided");

    template
        .replace("{command}", &format!("Apply patch to {} files", changes.len()))
        .replace("{cwd}", ".")
        .replace("{reason}", reason_str)
        .replace("{task_context}", config_content)
        .replace("{changes}", &changes_str)
}

fn inject_bugcrowd_approval_variables(
    template: &str,
    tool: &str,
    arguments: &Option<serde_json::Value>,
) -> String {
    let arguments_str = match arguments {
        Some(args) => serde_json::to_string_pretty(args).unwrap_or_default(),
        None => "No arguments provided".to_string(),
    };

    template
        .replace("{tool}", tool)
        .replace("{arguments}", &arguments_str)
}

fn parse_approval_response(response: &str) -> (bool, String) {
    let response = response.trim();

    // Check if the response starts with APPROVE or DENY
    if response.to_lowercase().starts_with("approve") {
        // Extract reasoning after "APPROVE" (usually after " - " or just after the word)
        let reasoning = if let Some(pos) = response.find(" - ") {
            response[pos + 3..].trim().to_string()
        } else if let Some(pos) = response.find("APPROVE") {
            response[pos + 7..].trim().to_string()
        } else if let Some(pos) = response.find("approve") {
            response[pos + 7..].trim().to_string()
        } else {
            "No reasoning provided".to_string()
        };

        (true, reasoning)
    } else if response.to_lowercase().starts_with("deny") {
        // Extract reasoning after "DENY"
        let reasoning = if let Some(pos) = response.find(" - ") {
            response[pos + 3..].trim().to_string()
        } else if let Some(pos) = response.find("DENY") {
            response[pos + 4..].trim().to_string()
        } else if let Some(pos) = response.find("deny") {
            response[pos + 4..].trim().to_string()
        } else {
            "No reasoning provided".to_string()
        };

        (false, reasoning)
    } else {
        // If the response doesn't clearly start with APPROVE or DENY, auto-deny for safety
        (
            false,
            format!(
                "Unclear response format - auto-denied for safety: {}",
                response
            ),
        )
    }
}

fn count_tokens(text: &str) -> anyhow::Result<usize> {
    let bpe = o200k_base().context("Failed to load o200k_base encoding")?;
    let tokens = bpe.encode_with_special_tokens(text);
    Ok(tokens.len())
}

async fn summarize_context(
    context: &str,
    model: &str,
    summarization_prompt_template: &str,
) -> anyhow::Result<String> {
    let summarization_prompt = summarization_prompt_template.replace("{context}", context);

    println!(
        "🔄 Context too long ({} tokens), summarizing...",
        count_tokens(context).unwrap_or(0)
    );
    let (summary, _) = generate_user_prompt(&summarization_prompt, model, &std::path::Path::new("./logs")).await?;
    println!(
        "✅ Context summarized from {} to {} tokens",
        count_tokens(context).unwrap_or(0),
        count_tokens(&summary).unwrap_or(0)
    );

    Ok(summary)
}

async fn generate_user_prompt(driver_prompt: &str, model: &str, session_logs_dir: &std::path::Path) -> anyhow::Result<(String, Vec<serde_json::Value>)> {
    use codex_core::client::ModelClient;
    use codex_core::client_common::Prompt;
    use codex_core::config_types::{ReasoningEffort, ReasoningSummary};
    use codex_core::model_provider_info::{ModelProviderInfo, WireApi};
    use codex_core::models::{ContentItem, ResponseItem, FunctionCallOutputPayload};
    use futures::StreamExt;
    use std::collections::HashMap;

    println!("🔄 Calling {} with driver prompt...", model);

    // Create model provider info
    let provider = ModelProviderInfo {
        name: "OpenAI".to_string(),
        base_url: "https://api.openai.com/v1".to_string(),
        env_key: Some("OPENAI_API_KEY".to_string()),
        env_key_instructions: None,
        wire_api: WireApi::Chat,
        query_params: None,
        env_http_headers: None,
        http_headers: None,
    };

    // Create model client
    let client = ModelClient::new(
        model,
        provider,
        ReasoningEffort::Medium,
        ReasoningSummary::None,
    );

    // Create prompt with driver prompt as user message
    let user_message = ResponseItem::Message {
        role: "user".to_string(),
        content: vec![ContentItem::InputText {
            text: driver_prompt.to_string(),
        }],
    };

    // Create note-taking tools
    let mut extra_tools = std::collections::HashMap::new();
    
    // Tool to write a note
    extra_tools.insert("write_note".to_string(), mcp_types::Tool {
        name: "write_note".to_string(),
        description: Some("Write a note to remember important information, observations, or decisions for future reference".to_string()),
        annotations: None,
        input_schema: mcp_types::ToolInputSchema {
            r#type: "object".to_string(),
            properties: Some(serde_json::json!({
                "content": {
                    "type": "string",
                    "description": "Content to write to the note"
                }
            })),
            required: Some(vec!["content".to_string()]),
        },
    });
    
    // Tool to read notes
    extra_tools.insert("read_notes".to_string(), mcp_types::Tool {
        name: "read_notes".to_string(),
        description: Some("Read all existing notes to recall previous observations and decisions".to_string()),
        annotations: None,
        input_schema: mcp_types::ToolInputSchema {
            r#type: "object".to_string(),
            properties: Some(serde_json::json!({})),
            required: Some(vec![]),
        },
    });

    let prompt = Prompt {
        input: vec![user_message.clone()],
        prev_id: None,
        user_instructions: None,
        store: false,
        extra_tools,
    };

    // Make the API call
    let mut response_stream = client
        .stream(&prompt)
        .await
        .with_context(|| "Failed to create response stream")?;

    let mut response_text = String::new();
    let mut tool_calls = Vec::new();

    // Collect the response
    while let Some(event) = response_stream.next().await {
        match event {
            Ok(response_event) => {
                match response_event {
                    codex_core::client_common::ResponseEvent::OutputItemDone(item) => {
                        match item {
                            ResponseItem::Message { content, .. } => {
                                for content_item in content {
                                    match content_item {
                                        ContentItem::OutputText { text } => {
                                            response_text.push_str(&text);
                                        }
                                        _ => {}
                                    }
                                }
                            }
                            ResponseItem::FunctionCall { name, arguments, call_id } => {
                                tool_calls.push(serde_json::json!({
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": arguments
                                    }
                                }));
                            }
                            _ => {}
                        }
                    }
                    codex_core::client_common::ResponseEvent::Completed { .. } => {
                        break;
                    }
                    _ => {
                        // Ignore other events like Created
                    }
                }
            }
            Err(e) => {
                return Err(anyhow::anyhow!("Error in response stream: {}", e));
            }
        }
    }

    // Handle tool calls
    if !tool_calls.is_empty() {
        let tool_results = handle_supervisor_tool_calls(&tool_calls, session_logs_dir).await?;
        
        // Add tool calls and results to conversation and get new instruction
        let mut conversation = vec![user_message];
        
        // Add the assistant's response with tool calls
        conversation.push(ResponseItem::Message {
            role: "assistant".to_string(),
            content: if response_text.trim().is_empty() {
                vec![]
            } else {
                vec![ContentItem::OutputText { text: response_text.trim().to_string() }]
            },
        });
        
        // Add function calls
        for tool_call in &tool_calls {
            conversation.push(ResponseItem::FunctionCall {
                name: tool_call["function"]["name"].as_str().unwrap_or("unknown").to_string(),
                arguments: serde_json::to_string(&tool_call["function"]["arguments"]).unwrap_or("{}".to_string()),
                call_id: tool_call["id"].as_str().unwrap_or("unknown").to_string(),
            });
        }
        
        // Add tool results
        for tool_result in &tool_results {
            conversation.push(ResponseItem::FunctionCallOutput {
                call_id: tool_result["tool_call_id"].as_str().unwrap_or("unknown").to_string(),
                output: FunctionCallOutputPayload {
                    content: tool_result["content"].as_str().unwrap_or("").to_string(),
                    success: Some(true),
                },
            });
        }
        
        // Make another call to get the follow-up instruction
        let follow_up_prompt = Prompt {
            input: conversation,
            prev_id: None,
            user_instructions: None,
            store: false,
            extra_tools: HashMap::new(), // No tools for follow-up
        };
        
        let mut follow_up_stream = client
            .stream(&follow_up_prompt)
            .await
            .with_context(|| "Failed to create follow-up response stream")?;
        
        let mut follow_up_text = String::new();
        
        // Collect follow-up response
        while let Some(event) = follow_up_stream.next().await {
            match event {
                Ok(response_event) => {
                    match response_event {
                        codex_core::client_common::ResponseEvent::OutputItemDone(item) => {
                            match item {
                                ResponseItem::Message { content, .. } => {
                                    for content_item in content {
                                        match content_item {
                                            ContentItem::OutputText { text } => {
                                                follow_up_text.push_str(&text);
                                            }
                                            _ => {}
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                        codex_core::client_common::ResponseEvent::Completed { .. } => {
                            break;
                        }
                        _ => {}
                    }
                }
                Err(e) => {
                    return Err(anyhow::anyhow!("Error in follow-up response stream: {}", e));
                }
            }
        }
        
        return Ok((follow_up_text.trim().to_string(), tool_results));
    }

    if response_text.is_empty() {
        return Err(anyhow::anyhow!("No response received from external LLM"));
    }

    Ok((response_text.trim().to_string(), Vec::new()))
}

async fn handle_supervisor_tool_calls(tool_calls: &[serde_json::Value], session_logs_dir: &std::path::Path) -> anyhow::Result<Vec<serde_json::Value>> {
    let mut tool_results = Vec::new();
    let notes_dir = session_logs_dir.join("notes");
    
    // Ensure notes directory exists
    std::fs::create_dir_all(&notes_dir).with_context(|| "Failed to create notes directory")?;
    
    for tool_call in tool_calls {
        let tool_id = tool_call["id"].as_str().unwrap_or("unknown");
        let tool_name = tool_call["function"]["name"].as_str().unwrap_or("unknown");
        let arguments = &tool_call["function"]["arguments"];
        
        println!("🔧 Processing tool call: id={}, name={}", tool_id, tool_name);
        println!("🔧 Debug tool_call structure: {}", serde_json::to_string_pretty(&tool_call).unwrap_or("invalid".to_string()));
        match tool_name {
            "write_note" => {
                let content = arguments["content"].as_str().unwrap_or("");
                let timestamp = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S UTC");
                let note_content = format!("[{}] {}\n", timestamp, content);
                
                // Generate a timestamped filename
                let filename = format!("note_{}.txt", chrono::Utc::now().format("%Y%m%d_%H%M%S"));
                let note_path = notes_dir.join(&filename);
                
                match std::fs::write(&note_path, &note_content) {
                    Ok(_) => {
                        tool_results.push(serde_json::json!({
                            "tool_call_id": tool_id,
                            "tool_name": tool_name,
                            "content": format!("Note written successfully to {}", filename)
                        }));
                        println!("📝 Supervisor wrote note: {}", filename);
                    }
                    Err(e) => {
                        tool_results.push(serde_json::json!({
                            "tool_call_id": tool_id,
                            "tool_name": tool_name,
                            "content": format!("Error writing note: {}", e)
                        }));
                    }
                }
            }
            "read_notes" => {
                let mut all_notes = String::new();
                
                match std::fs::read_dir(&notes_dir) {
                    Ok(entries) => {
                        let mut note_files: Vec<_> = entries
                            .filter_map(|entry| {
                                let entry = entry.ok()?;
                                let path = entry.path();
                                if path.extension()?.to_str()? == "txt" {
                                    Some(path)
                                } else {
                                    None
                                }
                            })
                            .collect();
                        
                        // Sort by filename (which includes timestamp)
                        note_files.sort();
                        
                        if note_files.is_empty() {
                            all_notes = "No notes yet.".to_string();
                        } else {
                            for note_path in note_files {
                                match std::fs::read_to_string(&note_path) {
                                    Ok(content) => {
                                        all_notes.push_str(&content);
                                        if !content.ends_with('\n') {
                                            all_notes.push('\n');
                                        }
                                    }
                                    Err(e) => {
                                        all_notes.push_str(&format!("Error reading {}: {}\n", note_path.display(), e));
                                    }
                                }
                            }
                        }
                    }
                    Err(_) => {
                        all_notes = "No notes yet.".to_string();
                    }
                }
                
                tool_results.push(serde_json::json!({
                    "tool_call_id": tool_id,
                    "tool_name": tool_name,
                    "content": all_notes
                }));
                println!("📖 Supervisor read notes");
            }
            _ => {
                tool_results.push(serde_json::json!({
                    "tool_call_id": tool_id,
                    "tool_name": tool_name,
                    "content": format!("Unknown tool: {}", tool_name)
                }));
            }
        }
    }
    
    Ok(tool_results)
}

/// Prepend root-level overrides so they have lower precedence than
/// CLI-specific ones specified after the subcommand (if any).
fn prepend_config_flags(
    subcommand_config_overrides: &mut CliConfigOverrides,
    cli_config_overrides: CliConfigOverrides,
) {
    subcommand_config_overrides
        .raw_overrides
        .splice(0..0, cli_config_overrides.raw_overrides);
}
