use clap::Parser;
use clap::ValueEnum;
use codex_common::CliConfigOverrides;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(version)]
pub struct Cli {
    /// Optional image(s) to attach to the initial prompt.
    #[arg(long = "image", short = 'i', value_name = "FILE", value_delimiter = ',', num_args = 1..)]
    pub images: Vec<PathBuf>,

    /// Model the agent should use.
    #[arg(long, short = 'm')]
    pub model: Option<String>,

    /// Configuration profile from config.toml to specify default options.
    #[arg(long = "profile", short = 'p')]
    pub config_profile: Option<String>,

    /// Convenience alias for low-friction sandboxed automatic execution (-a on-failure, -c sandbox.mode=workspace-write).
    #[arg(long = "full-auto", default_value_t = false)]
    pub full_auto: bool,

    /// Skip all confirmation prompts and execute commands without sandboxing.
    /// EXTREMELY DANGEROUS. Intended solely for running in environments that are externally sandboxed.
    #[arg(
        long = "dangerously-bypass-approvals-and-sandbox",
        default_value_t = false,
        conflicts_with = "full_auto"
    )]
    pub dangerously_bypass_approvals_and_sandbox: bool,

    /// Tell the agent to use the specified directory as its working root.
    #[clap(long = "cd", short = 'C', value_name = "DIR")]
    pub cwd: Option<PathBuf>,

    /// Allow running Codex outside a Git repository.
    #[arg(long = "skip-git-repo-check", default_value_t = false)]
    pub skip_git_repo_check: bool,

    #[clap(skip)]
    pub config_overrides: CliConfigOverrides,

    /// Specifies color settings for use in the output.
    #[arg(long = "color", value_enum, default_value_t = Color::Auto)]
    pub color: Color,

    /// Specifies file where the last message from the agent should be written.
    #[arg(long = "output-last-message")]
    pub last_message_file: Option<PathBuf>,

    /// Directory to save real-time conversation logs (for supervisor monitoring).
    #[arg(long = "log-session-dir")]
    pub log_session_dir: Option<PathBuf>,

    /// Instance ID for logging (used by supervisor to identify this instance).
    #[arg(long = "instance-id")]
    pub instance_id: Option<String>,

    /// Wait for followup messages from supervisor after each assistant response.
    #[arg(long = "wait-for-followup")]
    pub wait_for_followup: bool,

    /// Mode/specialist to use for prompts.
    #[arg(long = "mode", value_enum, default_value_t = Mode::Generalist)]
    pub mode: Mode,

    /// Initial instructions for the agent. If not provided as an argument (or
    /// if `-` is used), instructions are read from stdin.
    #[arg(value_name = "PROMPT")]
    pub prompt: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "kebab-case")]
pub enum Color {
    Always,
    Never,
    #[default]
    Auto,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "kebab-case")]
pub enum Mode {
    #[default]
    Generalist,
    Verification,
    ActiveDirectory,
    ClientSideWeb,
    Enumeration,
    LinuxPrivesc,
    Shelling,
    WebEnumeration,
    Web,
    WindowsPrivesc,
}

impl std::fmt::Display for Mode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Mode::Generalist => write!(f, "generalist"),
            Mode::Verification => write!(f, "verification"),
            Mode::ActiveDirectory => write!(f, "active_directory"),
            Mode::ClientSideWeb => write!(f, "client_side_web"),
            Mode::Enumeration => write!(f, "enumeration"),
            Mode::LinuxPrivesc => write!(f, "linux_privesc"),
            Mode::Shelling => write!(f, "shelling"),
            Mode::WebEnumeration => write!(f, "web_enumeration"),
            Mode::Web => write!(f, "web"),
            Mode::WindowsPrivesc => write!(f, "windows_privesc"),
        }
    }
}
