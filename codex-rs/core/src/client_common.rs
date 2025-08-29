use crate::config_types::Verbosity as VerbosityConfig;
use crate::error::Result;
use crate::model_family::ModelFamily;
use crate::openai_tools::OpenAiTool;
use crate::protocol::TokenUsage;
use codex_apply_patch::APPLY_PATCH_TOOL_INSTRUCTIONS;
use codex_protocol::config_types::ReasoningEffort as ReasoningEffortConfig;
use codex_protocol::config_types::ReasoningSummary as ReasoningSummaryConfig;
use codex_protocol::models::ContentItem;
use codex_protocol::models::ResponseItem;
use futures::Stream;
use serde::Serialize;
use std::borrow::Cow;
use std::pin::Pin;
use std::task::Context;
use std::task::Poll;
use tokio::sync::mpsc;

/// The `instructions` field in the payload sent to a model should always start
/// with this content.
const BASE_INSTRUCTIONS: &str = include_str!("../prompt.md");

/// Specialist prompt files
const ACTIVE_DIRECTORY_INSTRUCTIONS: &str = include_str!("../active_directory.md");
const CLIENT_SIDE_WEB_INSTRUCTIONS: &str = include_str!("../client_side_web.md");
const ENUMERATION_INSTRUCTIONS: &str = include_str!("../enumeration.md");
const LINUX_PRIVESC_INSTRUCTIONS: &str = include_str!("../linux_privesc.md");
const SHELLING_INSTRUCTIONS: &str = include_str!("../shelling.md");
const WEB_ENUMERATION_INSTRUCTIONS: &str = include_str!("../web_enumeration.md");
const WEB_INSTRUCTIONS: &str = include_str!("../web.md");
const WINDOWS_PRIVESC_INSTRUCTIONS: &str = include_str!("../windows_privesc.md");

/// wraps user instructions message in a tag for the model to parse more easily.
const USER_INSTRUCTIONS_START: &str = "<user_instructions>\n\n";
const USER_INSTRUCTIONS_END: &str = "\n\n</user_instructions>";

/// API request payload for a single model turn
#[derive(Default, Debug, Clone)]
pub struct Prompt {
    /// Conversation context input items.
    pub input: Vec<ResponseItem>,

    /// Whether to store response on server side (disable_response_storage = !store).
    pub store: bool,

    /// Tools available to the model, including additional tools sourced from
    /// external MCP servers.
    pub tools: Vec<OpenAiTool>,

    /// Optional override for the built-in BASE_INSTRUCTIONS.
    pub base_instructions_override: Option<String>,
}

impl Prompt {
    pub(crate) fn get_full_instructions(&self, model: &ModelFamily, specialist: Option<&str>) -> Cow<'_, str> {
        // Select base instructions based on specialist
        let base_instructions = match specialist {
            Some("active_directory") => ACTIVE_DIRECTORY_INSTRUCTIONS,
            Some("client_side_web") => CLIENT_SIDE_WEB_INSTRUCTIONS,
            Some("enumeration") => ENUMERATION_INSTRUCTIONS,
            Some("linux_privesc") => LINUX_PRIVESC_INSTRUCTIONS,
            Some("shelling") => SHELLING_INSTRUCTIONS,
            Some("web_enumeration") => WEB_ENUMERATION_INSTRUCTIONS,
            Some("web") => WEB_INSTRUCTIONS,
            Some("windows_privesc") => WINDOWS_PRIVESC_INSTRUCTIONS,
            Some("verification") => BASE_INSTRUCTIONS, // Use prompt.md for verification
            _ => self.base_instructions_override.as_deref().unwrap_or(BASE_INSTRUCTIONS), // Default to generalist
        };

        let mut sections: Vec<&str> = vec![base_instructions];
        // Add apply_patch tool instructions when needed (upstream logic)
        let is_apply_patch_tool_present = self.tools.iter().any(|tool| match tool {
            OpenAiTool::Function(f) => f.name == "apply_patch",
            OpenAiTool::Freeform(f) => f.name == "apply_patch",
            _ => false,
        });
        if specialist.is_none() && self.base_instructions_override.is_none()
            && (model.needs_special_apply_patch_instructions || !is_apply_patch_tool_present)
        {
            sections.push(APPLY_PATCH_TOOL_INSTRUCTIONS);
        }
        Cow::Owned(sections.join("\n"))
    }

    pub(crate) fn get_formatted_input(&self) -> Vec<ResponseItem> {
        self.input.clone()
    }

    /// Creates a formatted user instructions message from a string
    pub(crate) fn format_user_instructions_message(ui: &str) -> ResponseItem {
        ResponseItem::Message {
            id: None,
            role: "user".to_string(),
            content: vec![ContentItem::InputText {
                text: format!("{USER_INSTRUCTIONS_START}{ui}{USER_INSTRUCTIONS_END}"),
            }],
        }
    }
}

#[derive(Debug)]
pub enum ResponseEvent {
    Created,
    OutputItemDone(ResponseItem),
    Completed {
        response_id: String,
        token_usage: Option<TokenUsage>,
    },
    OutputTextDelta(String),
    ReasoningSummaryDelta(String),
    ReasoningContentDelta(String),
    ReasoningSummaryPartAdded,
    WebSearchCallBegin {
        call_id: String,
        query: Option<String>,
    },
}

#[derive(Debug, Serialize)]
pub(crate) struct Reasoning {
    pub(crate) effort: ReasoningEffortConfig,
    pub(crate) summary: ReasoningSummaryConfig,
}

/// Controls under the `text` field in the Responses API for GPT-5.
#[derive(Debug, Serialize, Default, Clone, Copy)]
pub(crate) struct TextControls {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) verbosity: Option<OpenAiVerbosity>,
}

#[derive(Debug, Serialize, Default, Clone, Copy)]
#[serde(rename_all = "lowercase")]
pub(crate) enum OpenAiVerbosity {
    Low,
    #[default]
    Medium,
    High,
}

impl From<VerbosityConfig> for OpenAiVerbosity {
    fn from(v: VerbosityConfig) -> Self {
        match v {
            VerbosityConfig::Low => OpenAiVerbosity::Low,
            VerbosityConfig::Medium => OpenAiVerbosity::Medium,
            VerbosityConfig::High => OpenAiVerbosity::High,
        }
    }
}

/// Request object that is serialized as JSON and POST'ed when using the
/// Responses API.
#[derive(Debug, Serialize)]
pub(crate) struct ResponsesApiRequest<'a> {
    pub(crate) model: &'a str,
    pub(crate) instructions: &'a str,
    // TODO(mbolin): ResponseItem::Other should not be serialized. Currently,
    // we code defensively to avoid this case, but perhaps we should use a
    // separate enum for serialization.
    pub(crate) input: &'a Vec<ResponseItem>,
    pub(crate) tools: &'a [serde_json::Value],
    pub(crate) tool_choice: &'static str,
    pub(crate) parallel_tool_calls: bool,
    pub(crate) reasoning: Option<Reasoning>,
    /// true when using the Responses API.
    pub(crate) store: bool,
    pub(crate) stream: bool,
    pub(crate) include: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) prompt_cache_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) text: Option<TextControls>,
}

pub(crate) fn create_reasoning_param_for_request(
    model_family: &ModelFamily,
    effort: ReasoningEffortConfig,
    summary: ReasoningSummaryConfig,
) -> Option<Reasoning> {
    if model_family.supports_reasoning_summaries {
        Some(Reasoning { effort, summary })
    } else {
        None
    }
}

pub(crate) fn create_text_param_for_request(
    verbosity: Option<VerbosityConfig>,
) -> Option<TextControls> {
    verbosity.map(|v| TextControls {
        verbosity: Some(v.into()),
    })
}

pub struct ResponseStream {
    pub(crate) rx_event: mpsc::Receiver<Result<ResponseEvent>>,
}

impl Stream for ResponseStream {
    type Item = Result<ResponseEvent>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.rx_event.poll_recv(cx)
    }
}

#[cfg(test)]
mod tests {
    use crate::model_family::find_family_for_model;

    use super::*;

    #[test]
    fn get_full_instructions_no_user_content() {
        let prompt = Prompt {
            ..Default::default()
        };
        let expected = format!("{BASE_INSTRUCTIONS}\n{APPLY_PATCH_TOOL_INSTRUCTIONS}");
        let model_family = find_family_for_model("gpt-4.1").expect("known model slug");
        let full = prompt.get_full_instructions(&model_family);
        assert_eq!(full, expected);
    }

    #[test]
    fn serializes_text_verbosity_when_set() {
        let input: Vec<ResponseItem> = vec![];
        let tools: Vec<serde_json::Value> = vec![];
        let req = ResponsesApiRequest {
            model: "gpt-5",
            instructions: "i",
            input: &input,
            tools: &tools,
            tool_choice: "auto",
            parallel_tool_calls: false,
            reasoning: None,
            store: true,
            stream: true,
            include: vec![],
            prompt_cache_key: None,
            text: Some(TextControls {
                verbosity: Some(OpenAiVerbosity::Low),
            }),
        };

        let v = serde_json::to_value(&req).expect("json");
        assert_eq!(
            v.get("text")
                .and_then(|t| t.get("verbosity"))
                .and_then(|s| s.as_str()),
            Some("low")
        );
    }

    #[test]
    fn omits_text_when_not_set() {
        let input: Vec<ResponseItem> = vec![];
        let tools: Vec<serde_json::Value> = vec![];
        let req = ResponsesApiRequest {
            model: "gpt-5",
            instructions: "i",
            input: &input,
            tools: &tools,
            tool_choice: "auto",
            parallel_tool_calls: false,
            reasoning: None,
            store: true,
            stream: true,
            include: vec![],
            prompt_cache_key: None,
            text: None,
        };

        let v = serde_json::to_value(&req).expect("json");
        assert!(v.get("text").is_none());
    }
}
