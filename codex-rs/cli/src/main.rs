use clap::Parser;
use codex_cli::LandlockCommand;
use codex_cli::SeatbeltCommand;
use codex_cli::login::run_login_with_chatgpt;
use codex_cli::proto;
use codex_common::CliConfigOverrides;
use codex_exec::Cli as ExecCli;
use codex_tui::Cli as TuiCli;
use std::path::PathBuf;
use anyhow::Context;

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
    use codex_core::config::Config;
    use codex_core::codex_wrapper::init_codex;
    use codex_core::protocol::{Op, InputItem};
    use std::time::{Duration, Instant};
    use tokio::time::sleep;
    
    println!("🚀 Starting autonomous mode...");
    println!("📁 Config file: {:?}", autonomous_cli.config_file);
    println!("⏰ Duration: {} minutes", autonomous_cli.duration);
    println!("🤖 Driver model: {}", autonomous_cli.driver_model);
    
    // Load config file
    let config_content = std::fs::read_to_string(&autonomous_cli.config_file)
        .with_context(|| format!("Failed to read config file: {:?}", autonomous_cli.config_file))?;
    
    // Load prompt templates from core directory
    let core_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("core");
    
    let initial_prompt_file = core_dir.join("initial_prompt.txt");
    let continuation_prompt_file = core_dir.join("continuation_prompt.txt");
    let approval_prompt_file = core_dir.join("approval_prompt.txt");
    let bugcrowd_approval_prompt_file = core_dir.join("bugcrowd_approval_prompt.txt");
    
    let initial_prompt_template = std::fs::read_to_string(&initial_prompt_file)
        .with_context(|| format!("Failed to read initial prompt file: {:?}", initial_prompt_file))?;
    
    let continuation_prompt_template = std::fs::read_to_string(&continuation_prompt_file)
        .with_context(|| format!("Failed to read continuation prompt file: {:?}", continuation_prompt_file))?;
    
    let approval_prompt_template = std::fs::read_to_string(&approval_prompt_file)
        .with_context(|| format!("Failed to read approval prompt file: {:?}", approval_prompt_file))?;
    
    let bugcrowd_approval_prompt_template = std::fs::read_to_string(&bugcrowd_approval_prompt_file)
        .with_context(|| format!("Failed to read bugcrowd approval prompt file: {:?}", bugcrowd_approval_prompt_file))?;
    
    println!("📋 Task config loaded");
    println!("📝 Prompt templates loaded");
    
    // Create codex config with overrides, applying full-auto settings if enabled
    let mut config_overrides = codex_core::config::ConfigOverrides::default();
    if autonomous_cli.full_auto {
        config_overrides.approval_policy = Some(codex_core::protocol::AskForApproval::OnFailure);
        config_overrides.sandbox_policy = Some(codex_core::protocol::SandboxPolicy::new_workspace_write_policy());
    }
    
    let config = Config::load_with_cli_overrides(
        autonomous_cli.config_overrides.parse_overrides()
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
    let start_time = Instant::now();
    let duration = Duration::from_secs(autonomous_cli.duration * 60);
    
    // Create session-specific logs directory with timestamp
    let session_timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let session_logs_dir = PathBuf::from("./logs").join(format!("autonomous_session_{}", session_timestamp));
    std::fs::create_dir_all(&session_logs_dir)
        .with_context(|| format!("Failed to create session logs directory: {:?}", session_logs_dir))?;
    
    println!("📁 Session logs directory: {:?}", session_logs_dir);
    
    // Load codex system prompt from prompt.md
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
    
    // Function to save checkpoint log files
    let save_checkpoint = |log: &Vec<serde_json::Value>, iteration_num: u32| {
        let log_json = serde_json::to_string_pretty(log).unwrap_or_else(|_| "[]".to_string());
        
        // Save numbered checkpoint
        let checkpoint_path = session_logs_dir.join(format!("iteration_{:03}.json", iteration_num));
        if let Err(e) = std::fs::write(&checkpoint_path, &log_json) {
            eprintln!("❌ Failed to save checkpoint {}: {}", iteration_num, e);
        } else {
            println!("📝 Checkpoint {} saved to: {:?}", iteration_num, checkpoint_path);
        }
        
        // Also save as latest.json for easy access
        let latest_path = session_logs_dir.join("latest.json");
        if let Err(e) = std::fs::write(&latest_path, &log_json) {
            eprintln!("❌ Failed to save latest.json: {}", e);
        }
        
        // Save session metadata
        let metadata = serde_json::json!({
            "session_start": session_timestamp,
            "current_iteration": iteration_num,
            "elapsed_seconds": start_time.elapsed().as_secs(),
            "last_updated": std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs()
        });
        let metadata_path = session_logs_dir.join("session_info.json");
        if let Err(e) = std::fs::write(&metadata_path, serde_json::to_string_pretty(&metadata).unwrap_or_default()) {
            eprintln!("❌ Failed to save session metadata: {}", e);
        }
    };
    
    // Save initial checkpoint with system message
    save_checkpoint(&conversation_log, 0);
    println!("🚀 Session {} started with {} minute duration", session_timestamp, autonomous_cli.duration);
    
    // Main autonomous loop with error handling
    let loop_result = async {
        while start_time.elapsed() < duration {
            iteration += 1;
            println!("\n🔄 Iteration {} ({}s elapsed)", iteration, start_time.elapsed().as_secs());
        
            // Determine which prompt template to use
            let prompt_template = if iteration == 1 {
                &initial_prompt_template
            } else {
                &continuation_prompt_template
            };
        
            // Inject config and context into prompt template
            let driver_prompt = inject_template_variables(
                prompt_template,
                &config_content,
                &context,
            );
        
            // Generate user prompt using external LLM
            let user_prompt = generate_user_prompt(
                &driver_prompt,
                &autonomous_cli.driver_model,
            ).await?;
            
            println!("💭 Generated user prompt: {}", user_prompt);
            
            // Add user message to conversation log
            conversation_log.push(serde_json::json!({
                "role": "user",
                "content": user_prompt
            }));
        
            // Submit to codex
            let input_items = vec![InputItem::Text { text: user_prompt.clone() }];
            let submission_id = codex.submit(Op::UserInput { items: input_items }).await?;
            
            // Collect codex response and tool calls
            let (codex_response, tool_calls, reasoning, tool_responses) = collect_codex_response_with_tools(&codex, &submission_id, autonomous_cli.full_auto, &autonomous_cli.driver_model, &approval_prompt_template, &bugcrowd_approval_prompt_template).await?;
            
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
                        readable_context.push_str(&format!("SYSTEM: {}\n\n", 
                            msg.get("content").and_then(|c| c.as_str()).unwrap_or("")));
                    }
                    Some("user") => {
                        readable_context.push_str(&format!("USER: {}\n\n", 
                            msg.get("content").and_then(|c| c.as_str()).unwrap_or("")));
                    }
                    Some("assistant") => {
                        if let Some(reasoning) = msg.get("reasoning") {
                            readable_context.push_str(&format!("ASSISTANT_REASONING: {}\n\n", 
                                reasoning.as_str().unwrap_or("")));
                        } else if let Some(tool_calls) = msg.get("tool_calls") {
                            readable_context.push_str(&format!("ASSISTANT_TOOL_CALLS: {}\n\n", 
                                serde_json::to_string_pretty(tool_calls).unwrap_or_default()));
                        } else {
                            readable_context.push_str(&format!("ASSISTANT: {}\n\n", 
                                msg.get("content").and_then(|c| c.as_str()).unwrap_or("")));
                        }
                    }
                    Some("tool") => {
                        readable_context.push_str(&format!("TOOL_RESPONSE: {}\n\n", 
                            msg.get("content").and_then(|c| c.as_str()).unwrap_or("")));
                    }
                    _ => {
                        // Skip unknown roles
                    }
                }
            }
            context = readable_context;
            
            // Save checkpoint after each iteration
            save_checkpoint(&conversation_log, iteration as u32);
        
            // Wait before next iteration
            sleep(Duration::from_secs(10)).await;
        }
        
        println!("✅ Autonomous mode completed after {} iterations", iteration);
        Ok::<(), anyhow::Error>(())
    }.await;
    
    // Save final checkpoint regardless of how we exit
    save_checkpoint(&conversation_log, iteration as u32);
    println!("🏁 Final checkpoint saved for session {}", session_timestamp);
    
    // Return the result
    loop_result
}

async fn collect_codex_response_with_tools(codex: &codex_core::Codex, submission_id: &str, _full_auto: bool, driver_model: &str, approval_prompt_template: &str, bugcrowd_approval_prompt_template: &str) -> anyhow::Result<(String, Vec<serde_json::Value>, Option<String>, Vec<serde_json::Value>)> {
    use codex_core::protocol::EventMsg;
    let mut assistant_content = String::new();
    let mut reasoning_content = String::new();
    let mut tool_calls = Vec::new();
    let mut tool_responses = Vec::new();
    let mut task_complete = false;
    
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
                                }
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
                                })).unwrap_or_default()
                            }));
                        }
                        EventMsg::McpToolCallBegin(tool) => {
                            println!("🔧 Calling tool: {}", tool.tool);
                            
                            // Check if this is a bugcrowd_submit call - always require external LLM approval
                            if tool.tool == "bugcrowd_submit" {
                                println!("🤖 Requesting approval from external LLM for bugcrowd_submit tool...");
                                
                                // Use the specialized bugcrowd approval prompt
                                let tool_approval_prompt = inject_bugcrowd_approval_variables(
                                    bugcrowd_approval_prompt_template,
                                    &tool.tool,
                                    &tool.arguments
                                );
                                
                                match generate_user_prompt(&tool_approval_prompt, driver_model).await {
                                    Ok(response) => {
                                        println!("🤖 External LLM response: {}", response);
                                        let (approved, reasoning) = parse_approval_response(&response);
                                        
                                        if approved {
                                            println!("✅ Bugcrowd submission approved by external LLM: {}", reasoning);
                                            // Let the tool call proceed normally
                                        } else {
                                            println!("❌ Bugcrowd submission denied by external LLM: {}", reasoning);
                                            
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
                                        println!("❌ Error getting approval from external LLM: {}", e);
                                        
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
                                }
                            }));
                        }
                        EventMsg::McpToolCallEnd(result) => {
                            match &result.result {
                                Ok(success) => {
                                    println!("✅ Tool result: {:?}", success);
                                    // Add tool response to conversation log
                                    tool_responses.push(serde_json::json!({
                                        "role": "tool",
                                        "tool_call_id": result.call_id,
                                        "content": serde_json::to_string(success).unwrap_or_default()
                                    }));
                                }
                                Err(err) => {
                                    println!("❌ Tool error: {}", err);
                                    // Add tool error to conversation log
                                    tool_responses.push(serde_json::json!({
                                        "role": "tool",
                                        "tool_call_id": result.call_id,
                                        "content": format!("Error: {}", err)
                                    }));
                                }
                            }
                        }
                        EventMsg::ExecApprovalRequest(approval) => {
                            println!("🔍 Approval requested for command: {:?}", approval.command);
                            
                            // Add approval request as a tool call
                            let approval_id = format!("approval_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis());
                            tool_calls.push(serde_json::json!({
                                "id": approval_id.clone(),
                                "type": "function",
                                "function": {
                                    "name": "request_approval",
                                    "arguments": serde_json::to_string(&approval).unwrap_or_default()
                                }
                            }));
                            
                            // Check if it's a bugcrowd_submit call - always require external LLM approval
                            let is_bugcrowd_submit = approval.command.iter().any(|arg| 
                                arg.contains("bugcrowd_submit") || arg.contains("bugcrowd-submit")
                            );
                            
                            // Generate approval prompt
                            let approval_prompt = inject_approval_variables(
                                approval_prompt_template,
                                &approval.command,
                                &approval.cwd,
                                &approval.reason
                            );
                            
                            let context_info = if is_bugcrowd_submit {
                                " (BUGCROWD SUBMISSION - Requires careful review)"
                            } else {
                                ""
                            };
                            
                            println!("🤖 Requesting approval from external LLM{}...", context_info);
                            
                            let decision = match generate_user_prompt(&approval_prompt, driver_model).await {
                                Ok(response) => {
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
                                    println!("❌ Error getting approval from external LLM: {}", e);
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
                            if let Err(e) = codex.submit(codex_core::protocol::Op::ExecApproval { 
                                id: event.id.clone(),
                                decision 
                            }).await {
                                println!("❌ Failed to submit approval decision: {}", e);
                            } else {
                                println!("✅ Approval decision submitted");
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
                                }
                            }));
                        }
                        EventMsg::PatchApplyEnd(patch_result) => {
                            println!("✅ Patch applied: {}", patch_result.call_id);
                            // Add as a tool response
                            tool_responses.push(serde_json::json!({
                                "role": "tool",
                                "tool_call_id": format!("patch_{}", patch_result.call_id),
                                "content": serde_json::to_string(&patch_result).unwrap_or_default()
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
    
    Ok((assistant_content.trim().to_string(), tool_calls, reasoning, tool_responses))
}

fn inject_template_variables(
    template: &str,
    config_yaml: &str,
    context: &str,
) -> String {
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
        (false, format!("Unclear response format - auto-denied for safety: {}", response))
    }
}

async fn generate_user_prompt(
    driver_prompt: &str,
    model: &str,
) -> anyhow::Result<String> {
    use codex_core::client::ModelClient;
    use codex_core::model_provider_info::{ModelProviderInfo, WireApi};
    use codex_core::config_types::{ReasoningEffort, ReasoningSummary};
    use codex_core::client_common::Prompt;
    use codex_core::models::{ResponseItem, ContentItem};
    use futures::StreamExt;
    
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
    
    let prompt = Prompt {
        input: vec![user_message],
        prev_id: None,
        user_instructions: None,
        store: false,
        extra_tools: std::collections::HashMap::new(),
    };
    
    // Make the API call
    let mut response_stream = client.stream(&prompt).await
        .with_context(|| "Failed to create response stream")?;
    
    let mut response_text = String::new();
    
    // Collect the response
    while let Some(event) = response_stream.next().await {
        match event {
            Ok(response_event) => {
                match response_event {
                    codex_core::client_common::ResponseEvent::OutputItemDone(item) => {
                        if let ResponseItem::Message { content, .. } = item {
                            for content_item in content {
                                if let ContentItem::OutputText { text } = content_item {
                                    response_text.push_str(&text);
                                }
                            }
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
    
    if response_text.is_empty() {
        return Err(anyhow::anyhow!("No response received from external LLM"));
    }
    
    Ok(response_text.trim().to_string())
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
