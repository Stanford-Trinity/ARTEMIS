use chrono::DateTime;
use chrono::Utc;
use codex_core::protocol::Event;
use codex_core::protocol::EventMsg;
use serde_json;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::Mutex;
use tracing::debug;

/// Logger that writes events to files in real-time for supervisor monitoring
pub struct RealtimeLogger {
    log_dir: PathBuf,
    instance_id: String,
    model: Option<String>,
    specialist: Option<String>,
    system_prompt: Option<String>,
    tools: Option<serde_json::Value>,
    conversation_log: Arc<Mutex<Vec<serde_json::Value>>>,
    context_file: Arc<Mutex<std::fs::File>>,
    start_time: DateTime<Utc>,
}

impl RealtimeLogger {
    pub fn new(
        log_dir: PathBuf,
        instance_id: String,
        initial_prompt: &str,
        model: Option<String>,
        specialist: Option<String>,
        system_prompt: Option<String>,
        tools: Option<serde_json::Value>,
    ) -> anyhow::Result<Self> {
        // Create log directory if it doesn't exist
        std::fs::create_dir_all(&log_dir)?;

        // Create log files directly in the provided directory
        let context_path = log_dir.join("realtime_context.txt");

        let context_file = Arc::new(Mutex::new(
            OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .open(&context_path)?,
        ));

        let start_time = Utc::now();

        // Initialize conversation with user prompt
        let conversation_log = Arc::new(Mutex::new(vec![serde_json::json!({
            "role": "user",
            "content": initial_prompt,
            "timestamp": start_time.to_rfc3339()
        })]));

        // Write initial context synchronously before creating logger
        {
            let file = context_file.clone();
            let mut guard = file.try_lock().unwrap();
            guard.write_all(
                format!(
                    "=== CODEX INSTANCE: {} ===\nStarted: {}\nTask: {}\n\n",
                    instance_id,
                    start_time.format("%Y-%m-%d %H:%M:%S UTC"),
                    initial_prompt
                )
                .as_bytes(),
            )?;
            guard.flush()?;
        }

        let logger = Self {
            log_dir,
            instance_id: instance_id.clone(),
            model,
            specialist,
            system_prompt,
            tools,
            conversation_log: conversation_log.clone(),
            context_file,
            start_time,
        };

        // Write initial JSON - defer to first log_event call to avoid blocking in sync context

        Ok(logger)
    }

    pub async fn log_event(&self, event: &Event) -> anyhow::Result<()> {
        let timestamp = Utc::now();

        // Skip initial JSON write - we'll only write final result at completion

        match &event.msg {
            EventMsg::AgentMessage(msg) => {
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    log.push(serde_json::json!({
                        "role": "assistant",
                        "content": msg.message,
                        "timestamp": timestamp.to_rfc3339()
                    }));
                }

                // Append to context
                self.append_context(&format!(
                    "[{}] ASSISTANT: {}\n",
                    timestamp.format("%H:%M:%S"),
                    msg.message
                ))
                .await?;
            }

            EventMsg::ExecCommandBegin(cmd) => {
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    log.push(serde_json::json!({
                        "role": "system",
                        "content": format!("Executing command: {:?}", cmd.command),
                        "timestamp": timestamp.to_rfc3339(),
                        "event_type": "exec_command_begin"
                    }));
                }

                self.append_context(&format!(
                    "[{}] EXECUTING: {:?}\n",
                    timestamp.format("%H:%M:%S"),
                    cmd.command
                ))
                .await?;
            }

            EventMsg::ExecCommandEnd(result) => {
                eprintln!(
                    "DEBUG: Processing ExecCommandEnd event with exit code {}",
                    result.exit_code
                );
                let status = if result.exit_code == 0 { "✅" } else { "❌" };

                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    let mut content =
                        format!("Command completed with exit code {}", result.exit_code);

                    if !result.stdout.is_empty() {
                        content.push_str(&format!("\nSTDOUT: {}", result.stdout));
                    }

                    if !result.stderr.is_empty() {
                        content.push_str(&format!("\nSTDERR: {}", result.stderr));
                    }

                    log.push(serde_json::json!({
                        "role": "system",
                        "content": content,
                        "timestamp": timestamp.to_rfc3339(),
                        "event_type": "exec_command_end",
                        "exit_code": result.exit_code
                    }));
                }

                self.append_context(&format!(
                    "[{}] COMMAND RESULT {}: Exit code {}\n",
                    timestamp.format("%H:%M:%S"),
                    status,
                    result.exit_code
                ))
                .await?;

                if !result.stdout.is_empty() {
                    self.append_context(&format!("STDOUT: {}\n", result.stdout))
                        .await?;
                }

                if !result.stderr.is_empty() {
                    self.append_context(&format!("STDERR: {}\n", result.stderr))
                        .await?;
                }

                // Also log if both are empty but we got a non-zero exit code
                if result.stdout.is_empty() && result.stderr.is_empty() && result.exit_code != 0 {
                    self.append_context(&format!(
                        "(No output, but command failed with exit code {})\n",
                        result.exit_code
                    ))
                    .await?;
                }
            }

            EventMsg::McpToolCallBegin(tool) => {
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    log.push(serde_json::json!({
                        "role": "system",
                        "content": format!("Tool call: {} ({})", tool.invocation.tool, tool.call_id),
                        "timestamp": timestamp.to_rfc3339(),
                        "event_type": "tool_call_begin",
                        "tool_name": tool.invocation.tool,
                        "call_id": tool.call_id
                    }));
                }

                self.append_context(&format!(
                    "[{}] TOOL CALL: {} ({})\n",
                    timestamp.format("%H:%M:%S"),
                    tool.invocation.tool,
                    tool.call_id
                ))
                .await?;
            }

            EventMsg::McpToolCallEnd(result) => {
                let status = if result.result.is_ok() { "✅" } else { "❌" };

                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    let content = match &result.result {
                        Ok(output) => format!("Tool call completed: {:?}", output),
                        Err(error) => format!("Tool call failed: {:?}", error),
                    };

                    log.push(serde_json::json!({
                        "role": "system",
                        "content": content,
                        "timestamp": timestamp.to_rfc3339(),
                        "event_type": "tool_call_end",
                        "call_id": result.call_id,
                        "success": result.result.is_ok()
                    }));
                }

                // Log the actual tool result content
                match &result.result {
                    Ok(output) => {
                        self.append_context(&format!(
                            "[{}] TOOL RESULT {}: {}\nOUTPUT: {:?}\n",
                            timestamp.format("%H:%M:%S"),
                            status,
                            result.call_id,
                            output
                        ))
                        .await?;
                    }
                    Err(error) => {
                        self.append_context(&format!(
                            "[{}] TOOL RESULT {}: {}\nERROR: {:?}\n",
                            timestamp.format("%H:%M:%S"),
                            status,
                            result.call_id,
                            error
                        ))
                        .await?;
                    }
                }
            }

            EventMsg::TaskComplete(_) => {
                self.append_context(&format!(
                    "[{}] ✅ TASK COMPLETED\n",
                    timestamp.format("%H:%M:%S")
                ))
                .await?;

                // Save final result
                self.save_final_result("completed").await?;
            }

            EventMsg::Error(err) => {
                self.append_context(&format!(
                    "[{}] ❌ ERROR: {}\n",
                    timestamp.format("%H:%M:%S"),
                    err.message
                ))
                .await?;

                // Save final result with error
                self.save_final_result("error").await?;
            }

            // Handle specific events we want in the JSON conversation log
            EventMsg::TokenCount(usage) => {
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    log.push(serde_json::json!({
                        "role": "system",
                        "content": format!("Token usage - Input: {}, Output: {}, Total: {}", 
                                         usage.input_tokens, usage.output_tokens, usage.total_tokens),
                        "timestamp": timestamp.to_rfc3339(),
                        "event_type": "token_count",
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens
                    }));
                }

                self.append_context(&format!(
                    "[{}] EVENT: {:?}\n",
                    timestamp.format("%H:%M:%S"),
                    event.msg
                ))
                .await?;
            }

            EventMsg::AgentReasoning(reasoning) => {
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    log.push(serde_json::json!({
                        "role": "system",
                        "content": format!("Agent reasoning: {}", reasoning.text),
                        "timestamp": timestamp.to_rfc3339(),
                        "event_type": "agent_reasoning"
                    }));
                }

                self.append_context(&format!(
                    "[{}] EVENT: {:?}\n",
                    timestamp.format("%H:%M:%S"),
                    event.msg
                ))
                .await?;
            }

            EventMsg::TaskStarted(_) => {
                self.append_context(&format!(
                    "[{}] 🚀 TASK STARTED\n",
                    timestamp.format("%H:%M:%S")
                ))
                .await?;
            }

            EventMsg::SessionConfigured(_) => {
                self.append_context(&format!(
                    "[{}] ⚙️ SESSION CONFIGURED\n",
                    timestamp.format("%H:%M:%S")
                ))
                .await?;
            }

            EventMsg::ExecApprovalRequest(req) => {
                self.append_context(&format!(
                    "[{}] 🔐 APPROVAL REQUEST: {:?}\n",
                    timestamp.format("%H:%M:%S"),
                    req.command
                ))
                .await?;
            }

            EventMsg::ApplyPatchApprovalRequest(req) => {
                let reason = req.reason.as_deref().unwrap_or("No reason");
                self.append_context(&format!(
                    "[{}] 📝 PATCH APPROVAL REQUEST: {}\n",
                    timestamp.format("%H:%M:%S"),
                    reason
                ))
                .await?;
            }

            EventMsg::PatchApplyBegin(patch) => {
                self.append_context(&format!(
                    "[{}] 📝 APPLYING PATCH: call_id={}\n",
                    timestamp.format("%H:%M:%S"),
                    patch.call_id
                ))
                .await?;
            }

            EventMsg::PatchApplyEnd(result) => {
                let status = if result.success { "✅" } else { "❌" };
                self.append_context(&format!(
                    "[{}] PATCH RESULT {}: call_id={}\n",
                    timestamp.format("%H:%M:%S"),
                    status,
                    result.call_id
                ))
                .await?;

                if !result.stdout.is_empty() {
                    self.append_context(&format!("STDOUT: {}\n", result.stdout))
                        .await?;
                }

                if !result.stderr.is_empty() {
                    self.append_context(&format!("STDERR: {}\n", result.stderr))
                        .await?;
                }
            }

            EventMsg::BackgroundEvent(bg) => {
                self.append_context(&format!(
                    "[{}] 🔄 BACKGROUND: {}\n",
                    timestamp.format("%H:%M:%S"),
                    bg.message
                ))
                .await?;
            }

            EventMsg::GetHistoryEntryResponse(_) => {
                self.append_context(&format!(
                    "[{}] 📜 HISTORY ENTRY RESPONSE\n",
                    timestamp.format("%H:%M:%S")
                ))
                .await?;
            }
            
            // Handle all other event types with default behavior
            _ => {
                // Log unhandled events for debugging
                debug!("Unhandled event type in realtime logger: {:?}", event.msg);
            }
        }

        Ok(())
    }

    async fn append_context(&self, text: &str) -> anyhow::Result<()> {
        // Add debug logging to track what's being written
        if text.contains("COMMAND RESULT") {
            eprintln!("DEBUG: Writing command result: {}", text.trim());
        }

        let mut file = self.context_file.lock().await;
        file.write_all(text.as_bytes())?;
        file.flush()?;
        Ok(())
    }

    async fn save_final_result(&self, status: &str) -> anyhow::Result<()> {
        let mut final_result = serde_json::json!({
            "instance_id": self.instance_id,
            "status": status,
            "started_at": self.start_time.to_rfc3339(),
            "completed_at": Utc::now().to_rfc3339(),
            "conversation": *self.conversation_log.lock().await
        });

        // Add model information if available
        if let Some(ref model_name) = self.model {
            final_result["model"] = serde_json::Value::String(model_name.clone());
        }

        // Add specialist information if available
        if let Some(ref specialist_name) = self.specialist {
            final_result["specialist"] = serde_json::Value::String(specialist_name.clone());
        }

        // Add system prompt if available
        if let Some(ref system_prompt) = self.system_prompt {
            final_result["system_prompt"] = serde_json::Value::String(system_prompt.clone());
        }

        // Add tools if available
        if let Some(ref tools) = self.tools {
            final_result["tools"] = tools.clone();
        }

        let result_path = self.log_dir.join("final_result.json");
        tokio::fs::write(&result_path, serde_json::to_string_pretty(&final_result)?).await?;

        Ok(())
    }
}
