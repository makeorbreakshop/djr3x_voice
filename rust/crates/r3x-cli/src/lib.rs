//! `r3x-cli` is a thin client: every line goes to the runtime's command console
//! (`intent.console`, parsed server-side by `r3x_brain::console`, the same parser the panel's
//! command line uses) and the reply comes back as an `ops.console` event. Only leaving the
//! client is local.

/// What the client does with a typed line.
#[derive(Debug, Clone, PartialEq)]
pub enum Local {
    Empty,
    Quit,
    /// Send this line to the runtime's console.
    Send(String),
}

pub fn classify(line: &str) -> Local {
    let line = line.trim();
    match line.to_ascii_lowercase().as_str() {
        "" => Local::Empty,
        "q" | "quit" | "exit" => Local::Quit,
        _ => Local::Send(line.to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_quit_is_local() {
        assert_eq!(classify("  "), Local::Empty);
        assert_eq!(classify("Q"), Local::Quit);
        assert_eq!(classify("exit"), Local::Quit);
        assert_eq!(classify(" st "), Local::Send("st".into()));
        assert_eq!(classify("eye pattern thinking"), Local::Send("eye pattern thinking".into()));
    }
}
