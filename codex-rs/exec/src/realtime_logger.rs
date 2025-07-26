use codex_core::protocol::{Event, EventMsg};
use serde_json;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::Mutex;
use chrono::{DateTime, Utc};

/// Logger that writes events to files in real-time for supervisor monitoring
pub struct RealtimeLogger {
    log_dir: PathBuf,
    instance_id: String,
    conversation_log: Arc<Mutex<Vec<serde_json::Value>>>,
    context_file: Arc<Mutex<std::fs::File>>,
    json_file: Arc<Mutex<std::fs::File>>,
    start_time: DateTime<Utc>,
}

impl RealtimeLogger {
    pub fn new(log_dir: PathBuf, instance_id: String, initial_prompt: &str) -> anyhow::Result<Self> {
        // Create log directory
        std::fs::create_dir_all(&log_dir)?;
        
        // Create log files
        let context_path = log_dir.join("realtime_context.txt");
        let json_path = log_dir.join("realtime_conversation.json");
        
        let context_file = Arc::new(Mutex::new(
            OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .open(&context_path)?
        ));
        
        let json_file = Arc::new(Mutex::new(
            OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .open(&json_path)?
        ));
        
        let start_time = Utc::now();
        
        // Initialize conversation with user prompt
        let conversation_log = Arc::new(Mutex::new(vec![
            serde_json::json!({
                "role": "user",
                "content": initial_prompt,
                "timestamp": start_time.to_rfc3339()
            })
        ]));
        
        // Write initial context synchronously before creating logger
        {
            let mut file = context_file.clone();
            let mut guard = file.try_lock().unwrap();
            guard.write_all(format!(
                "=== CODEX INSTANCE: {} ===\nStarted: {}\nTask: {}\n\n",
                instance_id,
                start_time.format("%Y-%m-%d %H:%M:%S UTC"),
                initial_prompt
            ).as_bytes())?;
            guard.flush()?;
        }
        
        let logger = Self {
            log_dir,
            instance_id: instance_id.clone(),
            conversation_log: conversation_log.clone(),
            context_file,
            json_file: json_file.clone(),
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
                )).await?;
            },
            
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
                )).await?;
            },
            
            EventMsg::ExecCommandEnd(result) => {
                let status = if result.exit_code == 0 { "✅" } else { "❌" };
                
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    let mut content = format!("Command completed with exit code {}", result.exit_code);
                    
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
                )).await?;
                
                if !result.stdout.is_empty() {
                    let preview = if result.stdout.len() > 500 {
                        format!("{}... (truncated)", &result.stdout[..500])
                    } else {
                        result.stdout.clone()
                    };
                    self.append_context(&format!("STDOUT: {}\n", preview)).await?;
                }
                
                if !result.stderr.is_empty() {
                    let preview = if result.stderr.len() > 500 {
                        format!("{}... (truncated)", &result.stderr[..500])
                    } else {
                        result.stderr.clone()
                    };
                    self.append_context(&format!("STDERR: {}\n", preview)).await?;
                }
            },
            
            EventMsg::McpToolCallBegin(tool) => {
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    log.push(serde_json::json!({
                        "role": "system",
                        "content": format!("Tool call: {} ({})", tool.tool, tool.call_id),
                        "timestamp": timestamp.to_rfc3339(),
                        "event_type": "tool_call_begin",
                        "tool_name": tool.tool,
                        "call_id": tool.call_id
                    }));
                }
                
                self.append_context(&format!(
                    "[{}] TOOL CALL: {} ({})\n",
                    timestamp.format("%H:%M:%S"),
                    tool.tool,
                    tool.call_id
                )).await?;
            },
            
            EventMsg::McpToolCallEnd(result) => {
                let status = if result.result.is_ok() { "✅" } else { "❌" };
                
                // Add to conversation log
                {
                    let mut log = self.conversation_log.lock().await;
                    let content = match &result.result {
                        Ok(output) => format!("Tool call completed: {:?}", output),
                        Err(error) => format!("Tool call failed: {:?}", error)
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
                
                self.append_context(&format!(
                    "[{}] TOOL RESULT {}: {}\n",
                    timestamp.format("%H:%M:%S"),
                    status,
                    result.call_id
                )).await?;
            },
            
            EventMsg::TaskComplete(_) => {
                self.append_context(&format!(
                    "[{}] ✅ TASK COMPLETED\n",
                    timestamp.format("%H:%M:%S")
                )).await?;
                
                // Save final result
                self.save_final_result("completed").await?;
            },
            
            EventMsg::Error(err) => {
                self.append_context(&format!(
                    "[{}] ❌ ERROR: {}\n",
                    timestamp.format("%H:%M:%S"),
                    err.message
                )).await?;
                
                // Save final result with error
                self.save_final_result("error").await?;
            },
            
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
                )).await?;
                
            },
            
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
                )).await?;
                
            },
            
            _ => {
                // Log other events in a generic way (context only, not JSON)
                self.append_context(&format!(
                    "[{}] EVENT: {:?}\n",
                    timestamp.format("%H:%M:%S"),
                    event.msg
                )).await?;
            }
        }
        
        Ok(())
    }
    
    async fn append_context(&self, text: &str) -> anyhow::Result<()> {
        let mut file = self.context_file.lock().await;
        file.write_all(text.as_bytes())?;
        file.flush()?;
        Ok(())
    }
    
    async fn save_final_result(&self, status: &str) -> anyhow::Result<()> {
        let final_result = serde_json::json!({
            "instance_id": self.instance_id,
            "status": status,
            "started_at": self.start_time.to_rfc3339(),
            "completed_at": Utc::now().to_rfc3339(),
            "conversation": *self.conversation_log.lock().await
        });
        
        let result_path = self.log_dir.join("final_result.json");
        tokio::fs::write(&result_path, serde_json::to_string_pretty(&final_result)?).await?;
        
        Ok(())
    }
}